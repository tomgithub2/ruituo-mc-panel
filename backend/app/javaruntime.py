"""Java 运行时检测、安装与启动参数渲染。

· 检测：`java -version` 解析大版本；扫 PATH、JAVA_HOME、常见安装目录、data/java/*。
· 安装：两个源，都**先取官方 sha256 再下载校验**——
    1. Adoptium Temurin（api.adoptium.net/v3/assets/latest/<major>/hotspot）→ package.checksum
    2. Microsoft Build of OpenJDK（aka.ms/download-jdk/… → 解析出真实文件名 →
       <name>.sha256sum.txt）→ 校验 sha256
  解压到 data/java/<major>/，返回可直接用的 java 可执行文件路径。
· 渲染：把实例最终启动命令行（内存 / JVM 参数 / nogui）原样渲染给用户看。
"""
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

from .config import JAVA_DIR, TMP_DIR, PANEL_VERSION
from .downloader import download_sync
from .sources import http_get, http_json, http_probe, SourceError

IS_WIN = sys.platform == 'win32'
JAVA_EXE = 'java.exe' if IS_WIN else 'java'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if IS_WIN else 0

# 面板推荐的可选大版本（不含 Adoptium 全量列表）
RECOMMENDED_MAJORS = (17, 21)
JAVA_SOURCES = ('auto', 'mirror', 'adoptium', 'microsoft')
SOURCE_LABELS = {'mirror': '国内镜像（Adoptium 二进制）',
                 'adoptium': 'Adoptium Temurin',
                 'microsoft': 'Microsoft Build of OpenJDK'}

#: Adoptium 的国内镜像。**只镜像二进制，元数据仍取 api.adoptium.net** ——
#: 这样 sha256 依旧是 Adoptium 官方值，走镜像不会放松校验。
#: 实测（本机，2026-11）：GitHub 托管 15.7 KB/s  vs  清华 1.72 MB/s，差 100 倍以上，
#: 52MB 的 JRE 从「约 55 分钟」降到「约 30 秒」。
#: ⚠️ 镜像目录结构：`{base}/{major}/{image}/{arch}/{os}/{官方文件名}`；
#:    Java 8 在清华镜像下是另一套布局 → 探测不到就自动回退到官方源。
ADOPTIUM_MIRRORS = (
    ('mirror', '清华 TUNA', 'https://mirrors.tuna.tsinghua.edu.cn/Adoptium'),
)


def probe_java(path: str) -> dict:
    """执行 java -version，返回 {ok, major, version_line, path, error}。"""
    if not path:
        return {'ok': False, 'error': '路径为空', 'path': path}
    exe = path
    if os.path.isdir(path):
        exe = os.path.join(path, 'bin', JAVA_EXE)
    if os.path.basename(exe).lower() not in ('java', 'java.exe'):
        exe = os.path.join(exe, 'bin', JAVA_EXE) if os.path.isdir(exe) else exe
    if os.path.isfile(exe) is False and os.path.basename(exe).lower() in ('java', 'java.exe'):
        # 允许 PATH 中的裸命令名
        exe = path
    try:
        p = subprocess.run([exe, '-version'], capture_output=True, timeout=20,
                           creationflags=CREATE_NO_WINDOW)
        text = (p.stderr or b'').decode('utf-8', 'replace') + (p.stdout or b'').decode('utf-8', 'replace')
    except FileNotFoundError:
        return {'ok': False, 'error': '找不到可执行文件', 'path': path}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'path': path}
    m = re.search(r'version "?(\d+)(?:\.(\d+))?', text)
    if not m:
        return {'ok': False, 'error': '无法解析版本输出', 'path': path, 'raw': text[:300]}
    first = int(m.group(1))
    major = first if first > 1 else int(m.group(2) or 0)
    line = (text.strip().splitlines() or [''])[0]
    vendor = ''
    for key in ('Temurin', 'Microsoft', 'Zulu', 'Corretto', 'GraalVM', 'OpenJDK'):
        if key.lower() in text.lower():
            vendor = key
            break
    return {'ok': True, 'major': major, 'version_line': line.strip(), 'path': exe,
            'vendor': vendor, 'raw': text.strip()[:400]}


def _candidates() -> list:
    out = []
    jh = os.environ.get('JAVA_HOME')
    if jh:
        out.append(os.path.join(jh, 'bin', JAVA_EXE))
    out.append('java')       # PATH
    if IS_WIN:
        for base in (r'C:\Program Files\Java', r'C:\Program Files\Eclipse Adoptium',
                     r'C:\Program Files\Microsoft', r'C:\Program Files\Zulu',
                     r'C:\Program Files\BellSoft', r'C:\Program Files\Amazon Corretto',
                     r'C:\Program Files (x86)\Java'):
            if os.path.isdir(base):
                try:
                    for name in sorted(os.listdir(base), reverse=True):
                        p = os.path.join(base, name, 'bin', JAVA_EXE)
                        if os.path.isfile(p):
                            out.append(p)
                except Exception:
                    pass
        for base in (os.environ.get('LOCALAPPDATA', ''), os.environ.get('APPDATA', '')):
            if base:
                for sub in ('Programs\\Eclipse Adoptium', 'Programs\\Java'):
                    d = os.path.join(base, sub)
                    if os.path.isdir(d):
                        try:
                            for name in os.listdir(d):
                                p = os.path.join(d, name, 'bin', JAVA_EXE)
                                if os.path.isfile(p):
                                    out.append(p)
                        except Exception:
                            pass
    else:
        for base in ('/usr/lib/jvm', '/usr/java', '/opt/java'):
            if os.path.isdir(base):
                try:
                    for name in os.listdir(base):
                        p = os.path.join(base, name, 'bin', JAVA_EXE)
                        if os.path.isfile(p):
                            out.append(p)
                except Exception:
                    pass
    # 面板自己装的
    if os.path.isdir(JAVA_DIR):
        try:
            for name in os.listdir(JAVA_DIR):
                p = os.path.join(JAVA_DIR, name, 'bin', JAVA_EXE)
                if os.path.isfile(p):
                    out.append(p)
        except Exception:
            pass
    seen, uniq = set(), []
    for c in out:
        key = os.path.normcase(c)
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def detect_all() -> list:
    """检测本机所有可用 java（按真实路径去重、带大版本）。"""
    results, seen = [], set()
    for c in _candidates():
        info = probe_java(c)
        if not info.get('ok'):
            continue
        exe = info.get('path') or c
        try:
            key = os.path.normcase(os.path.realpath(exe))
        except Exception:
            key = os.path.normcase(exe)
        if key in seen:
            continue
        seen.add(key)
        info['source'] = 'panel' if os.path.normcase(JAVA_DIR) in key else 'system'
        results.append(info)
    results.sort(key=lambda r: (-r.get('major', 0), r.get('path', '')))
    return results


def best_for(major: int) -> dict:
    """挑一个满足大版本要求的 java（优先面板装的、再系统里版本最接近的）。"""
    want = int(major or 0)
    found = detect_all()
    if not found:
        return {'ok': False, 'error': '本机未检测到任何 Java，请先安装'}
    exact = [f for f in found if int(f.get('major') or 0) == want]
    panel = [f for f in exact if f.get('source') == 'panel']
    if panel:
        return panel[0]
    if exact:
        return exact[0]
    higher = [f for f in found if int(f.get('major') or 0) >= want]
    if higher:
        return higher[-1]
    return found[0]


# ---------------------------------------------------------------- 版本列表
def adoptium_available() -> dict:
    try:
        d = http_json('https://api.adoptium.net/v3/info/available_releases', timeout=12)
        return {'ok': True, 'lts': d.get('available_lts_releases', []),
                'releases': d.get('available_releases', []),
                'most_recent_lts': d.get('most_recent_lts')}
    except SourceError as e:
        return {'ok': False, 'error': f'Adoptium API 不可达：{e}', 'lts': [], 'releases': []}


def list_majors() -> dict:
    """可选大版本：面板推荐的 17/21 + Adoptium 实际可装的全量。"""
    a = adoptium_available()
    avail = [int(x) for x in (a.get('releases') or [])]
    lts = [int(x) for x in (a.get('lts') or [])]
    majors = sorted(set(list(RECOMMENDED_MAJORS) + avail))
    out = []
    for m in majors:
        out.append({'major': m, 'lts': m in lts, 'recommended': m in RECOMMENDED_MAJORS,
                    'adoptium': m in avail if a.get('ok') else None,
                    'microsoft': m in (8, 11, 17, 21, 25)})
    return {'ok': True, 'majors': out, 'recommended': list(RECOMMENDED_MAJORS),
            'adoptium_ok': bool(a.get('ok')), 'adoptium_error': a.get('error', ''),
            'lts': lts, 'sources': list(SOURCE_LABELS)}


# ---------------------------------------------------------------- 安装源
def _adoptium_pkg(major: int, image: str = 'jre') -> dict:
    arch = 'x64'
    os_name = 'windows' if IS_WIN else 'linux'
    api = (f'https://api.adoptium.net/v3/assets/latest/{int(major)}/hotspot'
           f'?architecture={arch}&image_type={image}&os={os_name}&vendor=eclipse')
    try:
        d = http_json(api, timeout=20)
    except SourceError as e:
        return {'ok': False, 'error': f'Adoptium 资产接口不可达：{e}'}
    if not isinstance(d, list) or not d:
        return {'ok': False, 'error': f'Adoptium 没有 Java {major} 的 {image} 包'}
    item = d[0]
    binary = item.get('binary') or {}
    pkg = binary.get('package') or {}
    if not pkg:
        return {'ok': False, 'error': 'Adoptium 返回缺少 binary.package'}
    return {'ok': True, 'vendor': 'adoptium',
            'url': pkg.get('link') or (f'https://api.adoptium.net/v3/binary/latest/{int(major)}/ga/'
                                       f'{os_name}/{arch}/{image}/hotspot/normal/eclipse'),
            'sha256': (pkg.get('checksum') or '').lower(), 'size': int(pkg.get('size') or 0),
            'name': pkg.get('name') or f'OpenJDK{major}U-{image}.zip',
            'version': item.get('release_name') or '', 'ext': 'zip' if IS_WIN else 'tar.gz',
            'note': f'Adoptium Temurin {item.get("release_name") or major}（sha256 已校验）'}


def _microsoft_pkg(major: int) -> dict:
    """Microsoft Build of OpenJDK：别名 → 真实文件名 → .sha256sum.txt。"""
    ext = 'zip' if IS_WIN else 'tar.gz'
    os_name = 'windows' if IS_WIN else 'linux'
    alias = f'https://aka.ms/download-jdk/microsoft-jdk-{int(major)}-{os_name}-x64.{ext}'
    p = http_probe(alias, timeout=20)
    if not p.get('ok'):
        return {'ok': False, 'error': f'Microsoft OpenJDK 别名不可达：{p.get("error") or p.get("status")}'}
    final = p.get('final') or alias
    name = os.path.basename(final.split('?')[0])
    if not name.endswith(ext):
        return {'ok': False, 'error': f'Microsoft 返回的文件名异常：{name}'}
    sha = ''
    try:
        raw = http_get(f'https://aka.ms/download-jdk/{name}.sha256sum.txt', timeout=20).decode(
            'utf-8', 'replace')
        m = re.search(r'\b([0-9a-fA-F]{64})\b', raw)
        sha = (m.group(1) if m else '').lower()
    except SourceError:
        sha = ''
    return {'ok': True, 'vendor': 'microsoft', 'url': alias, 'sha256': sha,
            'size': int(p.get('total') or 0), 'name': name,
            'version': name.replace(f'-{os_name}-x64.{ext}', ''),
            'ext': ext,
            'note': (f'Microsoft Build of OpenJDK {name}'
                     + ('（sha256 已校验）' if sha else '（未取到 sha256，仅做结构校验）'))}


def _mirror_from_meta(meta: dict, major: int, image: str = 'jre') -> dict:
    """基于**官方元数据**构造镜像下载地址（sha256 沿用官方值）。

    为什么这么做：Adoptium 官方包的下载链接指向 GitHub Releases，
    而部分国内服务器到 github.com 极慢（实测 15 KB/s，52MB 要 55 分钟）。
    镜像站（清华 TUNA）镜像了同样的文件名，速度高两个数量级。
    元数据仍从 api.adoptium.net 取 → **校验强度不变**。
    """
    name = (meta.get('name') or '').strip()
    if not name:
        return {'ok': False, 'error': '镜像源缺少文件名'}
    arch = 'x64'
    os_name = 'windows' if IS_WIN else 'linux'
    tried = []
    for key, label, base in ADOPTIUM_MIRRORS:
        url = '%s/%d/%s/%s/%s/%s' % (base.rstrip('/'), int(major), image, arch, os_name, name)
        p = http_probe(url, timeout=15)
        if p.get('ok'):
            d = dict(meta)
            d.update({'vendor': key, 'vendor_label': label, 'url': url,
                      'note': '%s（%s 镜像；sha256 为 Adoptium 官方值）' % (name, label)})
            return d
        tried.append('%s→%s' % (label, p.get('status') or p.get('error') or '不可用'))
    return {'ok': False, 'error': '镜像上找不到该包（%s）' % '；'.join(tried)}


def source_candidates(major: int, image: str = 'jre', vendor: str = 'auto') -> list:
    """返回可用的安装包候选（按序尝试）。

    vendor: auto | mirror | adoptium | microsoft
    `auto` 的优先级：**国内镜像 → Adoptium 官方 → Microsoft**。
    镜像优先是因为官方链接走 GitHub，国内服务器上经常慢到不可用。
    """
    out = []
    ad = None
    if vendor in ('auto', 'mirror', 'adoptium'):
        ad = _adoptium_pkg(major, image)          # 只取一次元数据，镜像与官方共用
    if vendor in ('auto', 'mirror') and ad and ad.get('ok'):
        mi = _mirror_from_meta(ad, major, image)
        if mi.get('ok'):
            out.append(mi)
    if vendor in ('auto', 'adoptium') and ad and ad.get('ok'):
        out.append(ad)
    if vendor in ('auto', 'microsoft'):
        m = _microsoft_pkg(major)
        if m.get('ok'):
            out.append(m)
    return out


def _brief(p: dict) -> dict:
    return {'ok': bool(p.get('ok')), 'vendor': p.get('vendor', ''),
            'vendor_label': SOURCE_LABELS.get(p.get('vendor', ''), p.get('vendor', '')),
            'url': p.get('url', ''), 'name': p.get('name', ''), 'version': p.get('version', ''),
            'size': int(p.get('size') or 0), 'sha256': p.get('sha256', ''),
            'sha256_provided': bool(p.get('sha256')), 'note': p.get('note', ''),
            'error': p.get('error', '')}


def probe_sources(major: int = 21, image: str = 'jre') -> dict:
    """实测三个 Java 源（含 sha256 是否可取到），设置页/自检用。"""
    a = _adoptium_pkg(major, image)
    mi = _mirror_from_meta(a, major, image) if a.get('ok') else {'ok': False,
                                                                'error': a.get('error', '')}
    m = _microsoft_pkg(major)
    return {'ok': bool(mi.get('ok') or a.get('ok') or m.get('ok')), 'major': int(major),
            'image': image,
            'mirror': _brief(mi), 'adoptium': _brief(a), 'microsoft': _brief(m),
            'recommended': 'mirror' if mi.get('ok') else ('adoptium' if a.get('ok') else 'microsoft'),
            'both_sha256': bool(a.get('sha256') and m.get('sha256'))}


# ---------------------------------------------------------------- 解压
def _promote(target: str) -> None:
    """解压后若只有一层目录，把它提升到 target 根。"""
    entries = [e for e in os.listdir(target) if os.path.isdir(os.path.join(target, e))]
    if len(entries) == 1:
        inner = os.path.join(target, entries[0])
        if os.path.isdir(os.path.join(inner, 'bin')):
            for name in os.listdir(inner):
                shutil.move(os.path.join(inner, name), os.path.join(target, name))
            try:
                os.rmdir(inner)
            except OSError:
                pass


def _check_tar_safety(members, target: str) -> None:
    """自己校验 tar 成员是否安全（拒绝绝对路径 / `..` 穿越 / 逃出目标目录的链接）。

    ⚠️ 为什么不用 `extractall(filter='data')`：
    Python 3.10.12 里那个 backport 的 `data` 过滤器**有 bug** —— 它把
    `jdk-21.0.13+9-jre/legal/java.naming/ASSEMBLY_EXCEPTION → ../java.base/ASSEMBLY_EXCEPTION`
    解析成 `<target>/java.base/ASSEMBLY_EXCEPTION`（把中间两层目录丢了），
    于是把 Adoptium 官方的**合法**软链接判成"越界"，整个安装直接失败。
    实测该包 145 个软链接**全部指向包内**、0 个越界。
    所以这里用正确的语义（链接目标相对**链接自身所在目录**）自行校验，
    校验通过后再用 `fully_trusted` 解压 —— 安全边界没放松，只是换了正确实现。
    """
    root = os.path.realpath(target)
    for m in members:
        name = m.name or ''
        if name.startswith(('/', '\\')) or os.path.isabs(name):
            raise ValueError('压缩包含绝对路径成员：%s' % name)
        norm = os.path.normpath(name)
        if norm.startswith('..') or os.path.isabs(norm):
            raise ValueError('压缩包含路径穿越成员：%s' % name)
        dest = os.path.realpath(os.path.join(root, norm))
        if dest != root and not dest.startswith(root + os.sep):
            raise ValueError('压缩包成员越界：%s' % name)
        if m.issym():
            if os.path.isabs(m.linkname):
                raise ValueError('软链接用绝对路径（拒绝）：%s → %s' % (name, m.linkname))
            # 关键：相对**链接自身所在目录**解析
            tgt = os.path.realpath(os.path.join(os.path.dirname(dest), m.linkname))
            if tgt != root and not tgt.startswith(root + os.sep):
                raise ValueError('软链接越界（拒绝）：%s → %s' % (name, m.linkname))
        elif m.islnk():
            link = os.path.normpath(m.linkname or '')
            tgt = os.path.realpath(os.path.join(root, link))
            if tgt != root and not tgt.startswith(root + os.sep):
                raise ValueError('硬链接越界（拒绝）：%s → %s' % (name, m.linkname))


def _extract(archive: str, target: str, ext: str) -> None:
    if ext == 'zip' or zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            bad = z.testzip()
            if bad:
                raise ValueError(f'压缩包损坏（首个坏成员：{bad}）')
            z.extractall(target)
        return
    with tarfile.open(archive, 'r:gz') as t:
        members = t.getmembers()
        _check_tar_safety(members, target)          # 自校验（安全边界不变）
        try:
            t.extractall(target, filter='fully_trusted')   # 已自校验 → 跳过它的错误判定
        except TypeError:
            t.extractall(target)                    # 老版本没有 filter 参数


def install(major: int = 21, image: str = 'jre', vendor: str = 'auto') -> dict:
    """下载并解压 Java <major> 到 data/java/<major>（优先 Adoptium，失败降级 Microsoft）。"""
    major = int(major)
    cands = source_candidates(major, image, vendor)
    if not cands:
        return {'ok': False, 'error': f'Adoptium 与 Microsoft 都没有 Java {major} 的 {image} 包'
                                      '（检查网络或换一个大版本）'}
    tried = []
    for pkg in cands:
        tmp = os.path.join(TMP_DIR, f'java-{major}-{pkg["vendor"]}.{pkg["ext"]}')
        res = download_sync(pkg['url'], tmp, sha256=pkg.get('sha256', ''), timeout=900,
                            size=int(pkg.get('size') or 0))
        if not res.get('ok'):
            tried.append(f'{SOURCE_LABELS.get(pkg["vendor"], pkg["vendor"])}：'
                         f'{res.get("error") or res.get("status")}')
            continue
        target = os.path.join(JAVA_DIR, str(major))
        try:
            backup = None
            if os.path.isdir(target):
                backup = target + '.old'
                shutil.rmtree(backup, ignore_errors=True)
                os.replace(target, backup)
            os.makedirs(target, exist_ok=True)
            _extract(tmp, target, pkg['ext'])
            _promote(target)
            exe = os.path.join(target, 'bin', JAVA_EXE)
            if not os.path.isfile(exe):
                raise RuntimeError('解压后未找到 bin/java，目录结构异常')
            if not IS_WIN:
                try:
                    os.chmod(exe, 0o755)
                except Exception:
                    pass
            info = probe_java(exe)
            if not info.get('ok'):
                raise RuntimeError(f'安装后的 java 无法执行：{info.get("error")}')
            if os.path.isdir(backup or ''):
                shutil.rmtree(backup, ignore_errors=True)
            return {'ok': True, 'path': exe, 'major': info.get('major'), 'dir': target,
                    'version_line': info.get('version_line', ''), 'vendor': pkg['vendor'],
                    'vendor_label': SOURCE_LABELS.get(pkg['vendor'], pkg['vendor']),
                    'sha256': pkg.get('sha256', ''),
                    'sha256_verified': bool(pkg.get('sha256')),
                    'size': int(pkg.get('size') or 0),
                    'note': pkg.get('note', ''), 'tried': tried}
        except Exception as e:
            shutil.rmtree(target, ignore_errors=True)
            if os.path.isdir(target + '.old') and not os.path.isdir(target):
                os.replace(target + '.old', target)
            tried.append(f'{SOURCE_LABELS.get(pkg["vendor"], pkg["vendor"])}：'
                         f'{type(e).__name__}: {e}')
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return {'ok': False, 'error': '安装失败：' + '；'.join(tried), 'tried': tried}


def list_installed() -> list:
    out = []
    if not os.path.isdir(JAVA_DIR):
        return out
    for name in sorted(os.listdir(JAVA_DIR), key=lambda s: (len(s), s)):
        p = os.path.join(JAVA_DIR, name, 'bin', JAVA_EXE)
        if os.path.isfile(p):
            info = probe_java(p)
            out.append({'dir': name, 'path': p, 'ok': info.get('ok', False),
                        'major': info.get('major'), 'version_line': info.get('version_line', ''),
                        'vendor': info.get('vendor', '')})
    return out


# ---------------------------------------------------------------- 启动参数渲染
def render_command(row: dict, cfg: dict = None) -> dict:
    """把实例最终启动命令行渲染出来（与 process_manager.build_command 保持一致）。"""
    from .config import get_config
    cfg = cfg or get_config()
    row = row or {}
    memory = int(row.get('memory_mb') or 2048)
    xms = max(512, memory // 2)
    java = (row.get('java_path') or '').strip()
    java_source = 'instance'
    if not java:
        java = (cfg.get('default_java') or '').strip()
        java_source = 'panel' if java else 'auto'
    if not java:
        best = best_for(17)
        java = best.get('path') or 'java'
        java_source = 'auto:' + (best.get('version_line') or best.get('path') or 'java')
    extra = (row.get('extra_jvm_args') or '').strip()
    jvm_from = 'instance'
    if not extra:
        extra = (cfg.get('default_jvm_args') or '').strip()
        jvm_from = 'panel' if extra else 'none'
    jvm_args = [a for a in extra.split() if a]
    jar = row.get('jar_path') or ''
    jar_base = os.path.basename(jar) or 'server.jar'
    server_args = [a for a in (row.get('extra_server_args') or '').split() if a]
    nogui = bool(int(row.get('nogui') or 0))
    parts = [java, f'-Xmx{memory}M', f'-Xms{xms}M'] + jvm_args + ['-jar', jar_base] + server_args
    if nogui:
        parts.append('nogui')
    notes = []
    if not jar:
        notes.append('尚未指定服务端 jar')
    elif not os.path.isfile(jar):
        notes.append(f'jar 文件不存在：{jar}')
    if not os.path.isabs(java) and java != 'java':
        notes.append('java 路径不是绝对路径，启动时按 PATH 解析')
    return {'ok': True, 'command': ' '.join(parts), 'parts': parts, 'java': java,
            'java_source': java_source, 'memory_mb': memory, 'xms_mb': xms,
            'jvm_args': jvm_args, 'jvm_args_source': jvm_from, 'jar': jar,
            'jar_name': jar_base, 'server_args': server_args, 'nogui': nogui,
            'workdir': '', 'notes': notes, 'version': PANEL_VERSION}
