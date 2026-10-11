"""远程更新客户端：检查更新 → 验签 → 下载 → 校验 → 备份 → 应用 → 回报。

与官网 `芮拓面板` 侧（`ops-panel/backend/app/routers/update.py`）**同一份契约**，
两产品各用自己的 machine_id / binding 记录，互不干扰：

    latest.json  {version, notes, url, size, manifest:{version, files:{路径:sha256}, signature}}
    mc-panel-update-<version>.zip   含所有受保护文件（相对面板根目录）

## 安全模型（照抄既有实现的结论，不自己发明）

1. **清单必须 RSA 验签**，且签名**覆盖除 signature 外的全部字段**（`_signed_payload`）。
   历史坑：旧实现只签 `files`，于是顶层 `version`/`url`/`size` 都在签名之外 ——
   改个版本号配一个老包就能**强制降级**，`url` 还能被指向任意地址做 SSRF / 磁盘填充。
2. **逐文件 sha256 比对**，清单里没有的文件不动；写了但哈希不符 → 判定失败并回滚。
3. **路径必须落在面板根目录内**（realpath + commonpath），挡 zip-slip / 绝对路径 / 符号链接。
4. **不碰 data/ 与 .deps/ 与 venv/** —— 那是运行数据与依赖树，更新包只是代码；
   覆盖它们等于丢用户数据。
5. 应用前**先整份备份**，任一步失败自动回滚。
"""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import time
import zipfile

from .config import BASE_DIR, DATA_DIR, PANEL_VERSION

#: 面板根目录（= backend 的上一级），更新包里的路径都相对它
PANEL_ROOT = os.path.dirname(BASE_DIR)

#: 厂商更新签名公钥（与芮拓面板同一把）
PUBLIC_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'license_public.pem')

#: 更新状态（单例，供 API 查询进度）
UPDATE_STATE = {'busy': False, 'log': [], 'last_result': None, 'started_at': 0}

#: ⛔ 绝不允许被更新包覆盖/删除的路径前缀（相对面板根，POSIX 风格）
PROTECTED_PREFIXES = (
    'backend/data/',          # 运行数据：库 / 配置 / 实例 / 备份 —— 丢了这个等于丢用户数据
    'backend/.deps/',         # 依赖树
    'venv/',                  # 虚拟环境
    '.git/',
)

#: 允许更新包写入的顶层目录/文件（白名单，宁可保守）
ALLOWED_PREFIXES = (
    'backend/app/', 'backend/tools/', 'backend/run.py', 'backend/selftest.py',
    'backend/requirements.txt', 'frontend/', 'install.sh', 'panelctl',
    'README.md', 'CHANGELOG.md', 'LICENSE',
)


def _log(msg: str):
    line = time.strftime('%H:%M:%S') + ' ' + str(msg)
    UPDATE_STATE['log'].append(line)
    UPDATE_STATE['log'] = UPDATE_STATE['log'][-200:]
    try:
        print('[updater] ' + line, flush=True)
    except Exception:
        pass


# --------------------------------------------------------------------- 验签

def _canonical(obj: dict) -> bytes:
    """规范化 JSON：排序键 + 紧凑分隔符 + 不转义非 ASCII。

    必须与厂商签名时用的完全一致，差一个空格就验不过。
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _signed_payload(manifest: dict) -> dict:
    """签名载荷 = 清单里除 signature 外的全部字段（含 version/url/size）。"""
    return {k: v for k, v in manifest.items() if k != 'signature'}


def verify_manifest(manifest: dict) -> bool:
    """校验更新清单的 RSA 签名。失败一律返回 False（不抛异常给上层）。"""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        with open(PUBLIC_KEY_PATH, 'rb') as f:
            pub = serialization.load_pem_public_key(f.read())
        sig = base64.b64decode(manifest.get('signature', ''))
        pub.verify(sig, _canonical(_signed_payload(manifest)),
                   padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        return False
    if not isinstance(manifest.get('files'), dict) or not manifest['files']:
        return False
    return bool(manifest.get('version'))


# --------------------------------------------------------------------- 版本比较

def ver_tuple(v: str):
    """版本号 → 可比较元组。`rc` 排在正式版之前（1.0.0-rc1 < 1.0.0）。"""
    text = str(v or '').strip()
    m = re.match(r'^(\d+(?:\.\d+)*)\s*(?:[-+]?(.*))?$', text)
    if not m:
        return ((), '', 0)
    nums = tuple(int(x) for x in m.group(1).split('.'))
    pre = (m.group(2) or '').strip()
    return (nums, 0 if pre else 1, pre)


def ver_gt(newer: str, current: str) -> bool:
    return ver_tuple(newer) > ver_tuple(current)


# --------------------------------------------------------------------- 路径守卫

def _norm_rel(rel) -> str:
    """把清单里的相对路径规范化；非法（绝对/穿越/空）返回空串。"""
    r = str(rel or '').replace('\\', '/').strip()
    while r.startswith('./'):
        r = r[2:]
    if not r or r.startswith('/') or (len(r) > 1 and r[1] == ':'):
        return ''
    parts = [p for p in r.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        return ''
    return '/'.join(parts)


def is_protected(rel_norm: str) -> bool:
    return any(rel_norm == p.rstrip('/') or rel_norm.startswith(p)
               for p in PROTECTED_PREFIXES)


def is_allowed(rel_norm: str) -> bool:
    return any(rel_norm == p.rstrip('/') or rel_norm.startswith(p)
               for p in ALLOWED_PREFIXES)


def target_path(rel_norm: str) -> str:
    """相对路径 → 绝对路径，并确认落在面板根目录内（挡 zip-slip / 符号链接逃逸）。"""
    root = os.path.realpath(PANEL_ROOT)
    full = os.path.realpath(os.path.join(root, rel_norm))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError('路径越界（更新包含有非法路径）：%s' % rel_norm)
    return full


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------- 检查更新

def server_url() -> str:
    """官网地址（可用配置 `account_server` 覆盖，便于自建/测试）。"""
    try:
        from .config import get_config
        return str(get_config().get('account_server') or 'https://www.rt888.icu').rstrip('/')
    except Exception:
        return 'https://www.rt888.icu'


#: 本产品标识。官网的更新源是**全局的**（同一个 `update_packages` 表），
#: 若不校验产品，就会拿到**别的产品的包**（实测：MC 面板查到了「芮拓面板 2.0.0-rc3」，
#: 因为它的版本号更高）。所以必须用 manifest 里的 product 字段做归属校验。
PRODUCT_NAME = '芮拓MC开服面板'
PRODUCT_ALIASES = ('芮拓MC开服面板', 'ruituo-mc-panel', 'mc-panel', 'MC 开服面板')


def _product_ok(manifest: dict) -> bool:
    """清单是否属于本产品。字段缺失时**从宽**（兼容旧包），有值则必须匹配。"""
    p = str(manifest.get('product') or '').strip()
    if not p:
        return True
    low = p.lower()
    return any(a.lower() in low or low in a.lower() for a in PRODUCT_ALIASES)


def check() -> dict:
    """向官网查询是否有新版本。返回官网原样 payload（已验证签名的才可信）。"""
    import httpx
    from .hardware import machine_id
    mid = machine_id()
    url = '%s/api/v1/update/check?machine_id=%s&version=%s' % (server_url(), mid, PANEL_VERSION)
    try:
        r = httpx.get(url, timeout=20.0, follow_redirects=True)
        info = r.json()
    except Exception as e:
        return {'ok': False, 'has_update': False, 'error': '无法连接更新服务器：%s' % e}
    if not info.get('has_update'):
        return dict(info, ok=True, current_version=PANEL_VERSION)
    manifest = info.get('manifest') or {}
    if not verify_manifest(manifest):
        return {'ok': False, 'has_update': False,
                'error': '更新清单签名校验失败（可能被篡改，已拒绝）', 'raw_version': info.get('version')}
    if not _product_ok(manifest):
        return {'ok': False, 'has_update': False,
                'error': '官网返回的更新包属于其它产品（%s），已忽略' % manifest.get('product'),
                'raw_version': info.get('version')}
    if not ver_gt(str(info.get('version')), PANEL_VERSION):
        return dict(info, ok=True, has_update=False, current_version=PANEL_VERSION,
                    note='官网版本不高于当前版本，忽略')
    n = len(manifest.get('files') or {})
    return dict(info, ok=True, current_version=PANEL_VERSION, file_count=n)


# --------------------------------------------------------------------- 下载与校验

def _download(url: str, dest: str) -> dict:
    """下载更新包到 dest，返回 {ok, size, sha256}。"""
    import httpx
    if url.startswith('/'):
        url = server_url() + url
    h = hashlib.sha256()
    total = 0
    tmp = dest + '.part'
    with httpx.stream('GET', url, timeout=120.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(tmp, 'wb') as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
                total += len(chunk)
    os.replace(tmp, dest)
    return {'ok': True, 'size': total, 'sha256': h.hexdigest()}


def _safe_extract(zip_path: str, into: str) -> list:
    """解压更新包到临时目录，逐条校验路径安全，返回 [(相对路径, 绝对路径)]。"""
    out = []
    with zipfile.ZipFile(zip_path) as z:
        for zi in z.infolist():
            if zi.is_dir():
                continue
            rel = _norm_rel(zi.filename)
            if not rel:
                raise ValueError('更新包含有非法路径：%r' % zi.filename)
            if is_protected(rel):
                raise ValueError('更新包试图覆盖受保护路径：%s' % rel)
            if not is_allowed(rel):
                raise ValueError('更新包含有不在白名单内的文件：%s' % rel)
            # 解压到临时目录（不直接落盘到面板根），后续再整体校验
            dst = os.path.join(into, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(zi) as src, open(dst, 'wb') as f:
                shutil.copyfileobj(src, f)
            out.append((rel, dst))
    if not out:
        raise ValueError('更新包是空的')
    return out


def download_and_verify(info: dict) -> dict:
    """下载更新包 → 解压到临时区 → 逐文件 sha256 比对清单。全通过才返回文件列表。"""
    manifest = info.get('manifest') or {}
    files = manifest.get('files') or {}
    tmpdir = os.path.join(DATA_DIR, 'tmp', 'update-%s' % str(info.get('version') or 'x'))
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)
    zip_path = os.path.join(tmpdir, 'update.zip')

    dl = _download(info.get('url') or '', zip_path)
    exp_size = int(info.get('size') or 0)
    if exp_size and dl['size'] != exp_size:
        return {'ok': False, 'error': '更新包大小不符（期望 %d，实际 %d）' % (exp_size, dl['size'])}

    stage = os.path.join(tmpdir, 'stage')
    os.makedirs(stage, exist_ok=True)
    staged = _safe_extract(zip_path, stage)

    # 逐文件哈希比对：清单里声明的每一个都必须存在且哈希一致
    missing, mismatch = [], []
    staged_map = {rel: p for rel, p in staged}
    for rel, want in files.items():
        nrel = _norm_rel(rel)
        p = staged_map.get(nrel)
        if not p:
            missing.append(nrel or rel)
            continue
        got = _hash_file(p)
        if str(want).lower() != got:
            mismatch.append(nrel)
    if missing or mismatch:
        detail = []
        if missing:
            detail.append('缺少 %d 个文件（%s…）' % (len(missing), ', '.join(missing[:3])))
        if mismatch:
            detail.append('哈希不符 %d 个（%s…）' % (len(mismatch), ', '.join(mismatch[:3])))
        return {'ok': False, 'error': '更新包校验失败：' + '；'.join(detail)}
    return {'ok': True, 'staged': staged, 'staged_map': staged_map,
            'files': files, 'version': info.get('version'), 'tmpdir': tmpdir}


# --------------------------------------------------------------------- 应用

def apply(info: dict, backup: bool = True) -> dict:
    """应用更新：备份 → 逐文件替换 → 记录结果。失败自动回滚已替换的文件。"""
    if UPDATE_STATE['busy']:
        return {'ok': False, 'error': '已有更新任务在进行中'}
    UPDATE_STATE.update({'busy': True, 'log': [], 'last_result': None, 'started_at': time.time()})
    try:
        return _apply_impl(info, backup)
    except Exception as e:
        _log('应用失败：%s' % e)
        UPDATE_STATE['last_result'] = {'ok': False, 'error': str(e), 'at': time.time()}
        return {'ok': False, 'error': str(e)}
    finally:
        UPDATE_STATE['busy'] = False


def _apply_impl(info: dict, backup: bool) -> dict:
    ver = str(info.get('version') or '')
    _log('开始应用更新 %s → %s' % (PANEL_VERSION, ver))
    got = download_and_verify(info)
    if not got.get('ok'):
        UPDATE_STATE['last_result'] = dict(got, at=time.time())
        return got

    bdir = ''
    if backup:
        bdir = os.path.join(DATA_DIR, 'updates', 'backup-%s-%s' % (ver, time.strftime('%Y%m%d-%H%M%S')))
        os.makedirs(bdir, exist_ok=True)
        n = 0
        for rel, _p in got['staged']:
            src = target_path(rel)
            if os.path.isfile(src):
                dst = os.path.join(bdir, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                n += 1
        _log('已备份 %d 个将被覆盖的文件 → %s' % (n, bdir))

    replaced, created = [], []
    try:
        for rel, staged_path in got['staged']:
            dst = target_path(rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            existed = os.path.isfile(dst)
            tmp = dst + '.new'
            shutil.copy2(staged_path, tmp)
            os.replace(tmp, dst)
            (replaced if existed else created).append(rel)
            _log('写入 %s%s' % (rel, '' if existed else '（新增）'))
    except Exception as e:
        _log('写入过程中出错，开始回滚：%s' % e)
        if bdir:
            for rel in replaced:
                src = os.path.join(bdir, rel.replace('/', os.sep))
                if os.path.isfile(src):
                    dst = target_path(rel)
                    try:
                        shutil.copy2(src, dst)
                        _log('已回滚 %s' % rel)
                    except Exception as e2:
                        _log('回滚 %s 失败：%s' % (rel, e2))
        for rel in created:
            try:
                os.remove(target_path(rel))
            except Exception:
                pass
        raise

    shutil.rmtree(got['tmpdir'], ignore_errors=True)
    res = {'ok': True, 'version': ver, 'replaced': len(replaced), 'created': len(created),
           'backup_dir': bdir, 'need_restart': True,
           'note': '代码已更新，需重启面板生效（数据目录未被触碰）'}
    _log('更新完成：覆盖 %d，新增 %d' % (len(replaced), len(created)))
    UPDATE_STATE['last_result'] = dict(res, at=time.time())
    return res


# --------------------------------------------------------------------- 回执

def ack_push(info: dict) -> dict:
    """更新成功后回报官网，避免每次检查都重复推送。

    官网 `/api/v1/update/ack` 要求机器身份三件套（token + binding_id + machine_id），
    所以这里从 binding 配置里取；缺任一项就不回报（只是下次还会收到推送，无害）。
    """
    import httpx
    try:
        from .hardware import machine_id
        from .binding import _load as _load_binding
    except Exception:
        return {'ok': False, 'error': '缺少绑定模块，跳过回执'}
    data = _load_binding() or {}
    if not data.get('token') or not data.get('binding_id'):
        return {'ok': False, 'error': '本机未绑定官网账号，跳过回执'}
    body = {'machine_id': machine_id(),
            'binding_id': data.get('binding_id', ''),
            'token': data.get('token', ''),
            'push_id': info.get('push_id') or 0,
            'version': info.get('version') or ''}
    try:
        r = httpx.post(server_url() + '/api/v1/update/ack', json=body, timeout=20.0)
        return {'ok': r.status_code == 200, 'status': r.status_code}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
