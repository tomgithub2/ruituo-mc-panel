"""实例目录内的高层操作：eula、玩家名单、备份、文件、安装器。

所有文件操作都必须先过 pathguard（实例目录 realpath + commonpath 限制）。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

from fastapi import HTTPException

from .config import BACKUP_DIR, get_config, instance_dir
from .database import execute, now, query
from . import pathguard

IS_WIN = sys.platform == 'win32'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if IS_WIN else 0

TEXT_EXT = {'.txt', '.properties', '.json', '.yml', '.yaml', '.log', '.conf', '.cfg', '.ini',
            '.md', '.sh', '.bat', '.toml', '.xml', '.csv', '.tsv', '.env', '.mcmeta'}
MAX_EDIT_BYTES = 4 * 1024 * 1024
PLAYER_FILES = {
    'whitelist': 'whitelist.json',
    'ops': 'ops.json',
    'banned-players': 'banned-players.json',
    'banned-ips': 'banned-ips.json',
}


# ---------------------------------------------------------------- 基础
def workdir_of(row: dict) -> str:
    d = instance_dir(row)
    os.makedirs(d, exist_ok=True)
    return os.path.realpath(d)


def log_dir_of(row: dict) -> str:
    d = os.path.join(workdir_of(row), 'logs')
    os.makedirs(d, exist_ok=True)
    return d


def resolve(row: dict, rel: str) -> str:
    return pathguard.safe_join(workdir_of(row), rel)


# ---------------------------------------------------------------- eula
def accept_eula(row: dict) -> dict:
    d = workdir_of(row)
    path = os.path.join(d, 'eula.txt')
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    content = (f'# 由 芮拓MC开服面板于 {ts} 自动接受 Minecraft EULA\n'
               f'# https://aka.ms/MinecraftEULA\n'
               f'eula=true\n')
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    execute('UPDATE instances SET accept_eula=1 WHERE id=?', (row['id'],))
    return {'ok': True, 'path': path}


def eula_status(row: dict) -> dict:
    path = os.path.join(workdir_of(row), 'eula.txt')
    if not os.path.isfile(path):
        return {'exists': False, 'accepted': False}
    try:
        with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception:
        return {'exists': True, 'accepted': False}
    m = re.search(r'^\s*eula\s*=\s*(true|false)', text, re.I | re.M)
    return {'exists': True, 'accepted': bool(m and m.group(1).lower() == 'true'),
            'raw': text[:800]}


def clear_empty_rcon_keys(row: dict) -> dict:
    """把 server.properties 里**值为空的** `rcon.password` / `rcon.port` / `enable-rcon` 键去掉。

    为什么要这一步：`init_server_properties()` 的约定是"只补**缺键**、绝不覆盖用户已有值"，
    于是值为空的 `rcon.password=`（键在、值是空）会被当成"已设置"而**跳过**，
    结果开的 RCON 永远落不到文件里 —— 实测踩到：库里 `rcon_enabled=1` 但
    `server.properties` 里密码是空、端口还是 25565，服务端根本不监听 RCON，
    「免插件免停服导入」（引擎 C 靠 RCON 下发 `/place template`）因此用不了。

    这里**只清空值**，不动用户手工填过的非空值（保持"不覆盖用户值"的原则）。
    """
    from . import mcprops
    path = os.path.join(workdir_of(row), 'server.properties')
    model = mcprops.read_props(path)
    if not model.get('exists'):
        return {'ok': True, 'cleared': [], 'note': 'server.properties 还不存在'}
    props = dict(model.get('props') or {})
    # 找出"键在但值为空"的
    empty = [k for k in ('rcon.password', 'rcon.port', 'enable-rcon')
             if k in props and str(props.get(k) or '').strip() == '']
    if not empty:
        return {'ok': True, 'cleared': [], 'note': '无空 RCON 键'}
    try:
        text = io.open(path, 'r', encoding='utf-8', errors='replace').read()
        for key in empty:
            # 连带注释行一起删，避免留下孤立的 '#' 说明行
            text = re.sub(r'^\s*#?\s*%s\s*=.*\n?' % re.escape(key), '', text, flags=re.M)
        io.open(path, 'w', encoding='utf-8', newline='\n').write(text)
    except Exception as e:                                 # noqa: BLE001
        return {'ok': False, 'cleared': empty, 'error': str(e)}
    return {'ok': True, 'cleared': empty,
            'note': '已清掉空的 RCON 键：%s' % '、'.join(empty)}


def init_server_properties(row: dict, port=None, rcon_enabled=None, rcon_port=None,
                           rcon_password=None, motd: str = '') -> dict:
    """把面板里的端口 / RCON 设置落到 server.properties。

    只写"文件不存在"或"缺键"的情况，**绝不覆盖用户已有值**。
    不做这一步的话，服务端会以默认 25565 启动 —— 多实例必然撞端口
    （实测：两个实例都起在 25565，第二个报 FAILED TO BIND TO PORT）。
    """
    from . import mcprops
    path = os.path.join(workdir_of(row), 'server.properties')
    model = mcprops.read_props(path)
    exists = bool(model.get('exists'))
    props = dict(model.get('props') or {})
    p = int(port or row.get('port') or 25565)
    want = {}
    if not exists or 'server-port' not in props:
        want['server-port'] = str(p)
    if motd and (not exists or 'motd' not in props):
        want['motd'] = str(motd)[:120]
    if rcon_enabled:
        if not exists or props.get('enable-rcon') != 'true':
            want['enable-rcon'] = 'true'
        if not exists or not props.get('rcon.port'):
            want['rcon.port'] = str(int(rcon_port or 25575))
        if rcon_password and (not exists or not props.get('rcon.password')):
            want['rcon.password'] = str(rcon_password)
    if not want:
        return {'ok': True, 'written': 0, 'path': path,
                'note': 'server.properties 已存在且端口/RCON 齐全，未改动'}
    r = mcprops.write_props(path, want)
    r['written_keys'] = sorted(want)
    r['note'] = '已按面板设置写入 server-port/RCON，避免服务端用默认 25565 撞端口'
    return r


# ---------------------------------------------------------------- 玩家名单
def _read_json(path: str, default):
    try:
        if os.path.isfile(path):
            with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_json(path: str, data) -> dict:
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return {'ok': True}


_UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?'
                      r'[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$')
_NAME_RE = re.compile(r'^[A-Za-z0-9_]{1,16}$')


def player_list(row: dict, which: str) -> list:
    path = os.path.join(workdir_of(row), PLAYER_FILES[which])
    data = _read_json(path, [])
    return data if isinstance(data, list) else []


def player_add(row: dict, which: str, name: str, uuid: str = '', level: int = 4,
               reason: str = 'Banned by an operator.', ip: str = '') -> dict:
    path = os.path.join(workdir_of(row), PLAYER_FILES[which])
    data = _read_json(path, [])
    if not isinstance(data, list):
        data = []
    name = str(name or '').strip()
    if which != 'banned-ips' and not _NAME_RE.match(name):
        return {'ok': False, 'error': '玩家名只能是 1-16 位字母/数字/下划线'}
    if which == 'banned-ips':
        if not ip and not re.match(r'^[0-9a-fA-F:.]{3,45}$', name):
            return {'ok': False, 'error': '请填写合法的 IP 地址'}
    if uuid and not _UUID_RE.match(uuid):
        return {'ok': False, 'error': 'UUID 格式不正确'}
    entry = {'name': name, 'uuid': uuid}
    if which == 'ops':
        entry = {'uuid': uuid, 'name': name, 'level': int(level), 'bypassesPlayerLimit': False}
    elif which == 'banned-players':
        entry = {'uuid': uuid, 'name': name, 'created': time.strftime('%Y-%m-%d %H:%M:%S +0000'),
                 'source': 'Server', 'expires': 'forever', 'reason': reason}
    elif which == 'banned-ips':
        entry = {'ip': ip or name, 'created': time.strftime('%Y-%m-%d %H:%M:%S +0000'),
                 'source': 'Server', 'expires': 'forever', 'reason': reason}
    key = 'ip' if which == 'banned-ips' else 'name'
    data = [d for d in data if str(d.get(key, '')).lower() != str(entry[key]).lower()]
    data.append(entry)
    _write_json(path, data)
    return {'ok': True, 'entry': entry}


def player_remove(row: dict, which: str, name: str) -> dict:
    path = os.path.join(workdir_of(row), PLAYER_FILES[which])
    data = _read_json(path, [])
    if not isinstance(data, list):
        data = []
    key = 'ip' if which == 'banned-ips' else 'name'
    before = len(data)
    data = [d for d in data if str(d.get(key, '')).lower() != str(name or '').lower()]
    _write_json(path, data)
    return {'ok': True, 'removed': before - len(data)}


# ---------------------------------------------------------------- 文件
def list_dir(row: dict, rel: str = '') -> dict:
    root = workdir_of(row)
    target = resolve(row, rel)
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail='目录不存在')
    items = []
    try:
        with os.scandir(target) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    is_dir = e.is_dir()
                    items.append({
                        'name': e.name, 'dir': is_dir,
                        'size': 0 if is_dir else st.st_size,
                        'mtime': st.st_mtime,
                        'symlink': e.is_symlink(),
                        'editable': (not is_dir) and
                                    os.path.splitext(e.name)[1].lower() in TEXT_EXT,
                    })
                except Exception:
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail='没有权限读取该目录')
    items.sort(key=lambda x: (not x['dir'], x['name'].lower()))
    return {'path': pathguard.rel_to(target, root), 'abs': target, 'items': items,
            'parent': pathguard.rel_to(os.path.dirname(target), root) if target != root else None}


def read_text(row: dict, rel: str, limit: int = MAX_EDIT_BYTES) -> dict:
    target = resolve(row, rel)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail='文件不存在')
    size = os.path.getsize(target)
    if size > limit:
        raise HTTPException(status_code=413, detail=f'文件过大（{size} 字节），请下载后编辑')
    try:
        with io.open(target, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'读取失败：{e}')
    return {'path': pathguard.rel_to(target, workdir_of(row)), 'size': size, 'content': text,
            'encoding': 'utf-8'}


def write_text(row: dict, rel: str, content: str) -> dict:
    target = resolve(row, rel)
    if os.path.isdir(target):
        raise HTTPException(status_code=400, detail='目标是目录')
    if len(content.encode('utf-8')) > MAX_EDIT_BYTES:
        raise HTTPException(status_code=413, detail='内容过大')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    os.replace(tmp, target)
    return {'ok': True, 'size': os.path.getsize(target)}


def mkdir(row: dict, rel: str) -> dict:
    target = resolve(row, rel)
    if os.path.exists(target):
        raise HTTPException(status_code=400, detail='已存在同名文件或目录')
    os.makedirs(target, exist_ok=False)
    return {'ok': True, 'path': pathguard.rel_to(target, workdir_of(row))}


def delete(row: dict, rel: str) -> dict:
    root = workdir_of(row)
    target = resolve(row, rel)
    if target == root:
        raise HTTPException(status_code=400, detail='不允许删除实例根目录')
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail='不存在')
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target, ignore_errors=False)
    else:
        os.remove(target)
    return {'ok': True}


def rename(row: dict, rel: str, new_name: str) -> dict:
    target = resolve(row, rel)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail='不存在')
    if '/' in new_name or '\\' in new_name or new_name in ('.', '..'):
        raise HTTPException(status_code=400, detail='新名称不合法')
    new_path = os.path.join(os.path.dirname(target), new_name)
    pathguard.assert_inside(new_path, workdir_of(row))
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail='目标已存在')
    os.rename(target, new_path)
    return {'ok': True, 'path': pathguard.rel_to(new_path, workdir_of(row))}


def unzip(row: dict, rel: str, target_rel: str = '') -> dict:
    src = resolve(row, rel)
    if not os.path.isfile(src) or not zipfile.is_zipfile(src):
        raise HTTPException(status_code=400, detail='不是合法的 zip 文件')
    root = workdir_of(row)
    dest = resolve(row, target_rel) if target_rel else os.path.dirname(src)
    os.makedirs(dest, exist_ok=True)
    count = 0
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            # zip slip 防护
            name = info.filename.replace('\\', '/')
            if name.startswith('/') or '..' in name.split('/'):
                continue
            out = os.path.realpath(os.path.join(dest, name))
            if not pathguard.inside(out, root):
                continue
            if info.is_dir():
                os.makedirs(out, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as s, open(out, 'wb') as t:
                shutil.copyfileobj(s, t)
            count += 1
    return {'ok': True, 'extracted': count, 'dest': pathguard.rel_to(dest, root)}


def save_upload(row: dict, rel_dir: str, filename: str, data: bytes) -> dict:
    safe = os.path.basename(filename)
    if not safe or safe in ('.', '..'):
        raise HTTPException(status_code=400, detail='文件名不合法')
    target = resolve(row, os.path.join(rel_dir or '', safe))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'wb') as f:
        f.write(data)
    return {'ok': True, 'path': pathguard.rel_to(target, workdir_of(row)),
            'size': len(data)}


def dir_size(path: str, cap: int = 50 * 1024 * 1024 * 1024) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except Exception:
                pass
        if total > cap:
            break
    return total


# ---------------------------------------------------------------- 插件/模组
PLUGIN_DIRS = ('plugins', 'mods')


def plugins(row: dict, kind: str = '') -> list:
    root = workdir_of(row)
    out = []
    for d in PLUGIN_DIRS:
        if kind and d != kind:
            continue
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            if not os.path.isfile(p):
                continue
            base, ext = os.path.splitext(name)
            enabled = ext.lower() in ('.jar', '.litemod')
            if name.lower().endswith('.jar.disabled'):
                enabled = False
            out.append({'dir': d, 'name': name, 'size': os.path.getsize(p),
                        'mtime': os.path.getmtime(p), 'enabled': enabled,
                        'kind': 'jar' if name.lower().endswith('.jar') else 'disabled'})
    return out


def plugin_toggle(row: dict, kind: str, name: str, enable: bool) -> dict:
    if kind not in PLUGIN_DIRS:
        raise HTTPException(status_code=400, detail='类型必须是 plugins 或 mods')
    root = workdir_of(row)
    src = resolve(row, os.path.join(kind, os.path.basename(name)))
    if not os.path.isfile(src):
        raise HTTPException(status_code=404, detail='文件不存在')
    if enable and src.endswith('.disabled'):
        dst = src[:-len('.disabled')]
    elif (not enable) and not src.endswith('.disabled'):
        dst = src + '.disabled'
    else:
        return {'ok': True, 'note': '状态未变化'}
    pathguard.assert_inside(dst, root)
    if os.path.exists(dst):
        raise HTTPException(status_code=400, detail='目标文件名已存在')
    os.rename(src, dst)
    return {'ok': True, 'path': pathguard.rel_to(dst, root)}


def plugin_delete(row: dict, kind: str, name: str) -> dict:
    if kind not in PLUGIN_DIRS:
        raise HTTPException(status_code=400, detail='类型必须是 plugins 或 mods')
    target = resolve(row, os.path.join(kind, os.path.basename(name)))
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail='文件不存在')
    os.remove(target)
    return {'ok': True}


# ---------------------------------------------------------------- 备份
def backup_dir_of(row: dict) -> str:
    d = os.path.join(BACKUP_DIR, str(row['id']))
    os.makedirs(d, exist_ok=True)
    return d


def create_backup(row: dict, kind: str = 'manual', note: str = '') -> dict:
    root = workdir_of(row)
    bdir = backup_dir_of(row)
    ts = time.strftime('%Y%m%d-%H%M%S')
    name = f'{kind}-{ts}.tar.gz'
    target = os.path.join(bdir, name)
    excludes = {'.mcpanel.pid'}
    skip_dirs = {'logs', 'cache', 'crash-reports', 'backups'}
    tmp = target + '.part'
    count = 0
    with tarfile.open(tmp, 'w:gz', compresslevel=5) as tar:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if fn in excludes or fn.endswith('.part'):
                    continue
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, root)
                try:
                    tar.add(full, arcname=rel, recursive=False)
                    count += 1
                except Exception:
                    continue
    os.replace(tmp, target)
    size = os.path.getsize(target)
    # 打包后立刻自检（能列出成员 = gzip+tar 结构完整）
    chk = verify_backup(target)
    if not chk.get('ok'):
        try:
            os.remove(target)
        except Exception:
            pass
        return {'ok': False, 'error': f'备份完整性自检失败：{chk.get("error")}'}
    bid = execute('INSERT INTO backups (instance_id, file, size, created_at, kind, note) '
                  'VALUES (?,?,?,?,?,?)', (row['id'], target, size, now(), kind, note))
    kept = rotate(row)
    return {'ok': True, 'id': bid, 'file': target, 'name': name, 'size': size,
            'files': count, 'members': chk.get('members'),
            'removed': kept.get('removed', [])}


def verify_backup(path: str) -> dict:
    """校验 tar.gz 完整性：能打开 + 能列出成员（并统计）。"""
    if not os.path.isfile(path):
        return {'ok': False, 'error': '备份文件不存在'}
    try:
        with tarfile.open(path, 'r:gz') as tar:
            names = tar.getnames()
        return {'ok': True, 'members': len(names), 'sample': names[:10],
                'size': os.path.getsize(path), 'error': ''}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def rotate(row: dict) -> dict:
    keep = int(get_config().get('backup_keep', 5))
    rows = query('SELECT * FROM backups WHERE instance_id=? ORDER BY created_at DESC', (row['id'],))
    removed = []
    for r in rows[keep:]:
        try:
            if os.path.isfile(r['file']):
                os.remove(r['file'])
            removed.append(os.path.basename(r['file']))
        except Exception:
            pass
        execute('DELETE FROM backups WHERE id=?', (r['id'],))
    return {'removed': removed, 'kept': min(len(rows), keep)}


def restore_backup(row: dict, backup_id: int, safe: bool = True) -> dict:
    b = query('SELECT * FROM backups WHERE id=? AND instance_id=?', (backup_id, row['id']), one=True)
    if not b or not os.path.isfile(b['file']):
        return {'ok': False, 'error': '备份文件不存在'}
    chk = verify_backup(b['file'])
    if not chk.get('ok'):
        return {'ok': False, 'error': f'备份文件损坏，无法还原：{chk.get("error")}'}
    from .process_manager import manager, ST_RUNNING
    st = manager.status(row['id'])
    if st.get('running'):
        return {'ok': False, 'error': '请先停止实例再还原'}
    pre = None
    if safe:
        pre = create_backup(row, kind='pre-restore', note=f'还原 #{backup_id} 前的自动备份')
    root = workdir_of(row)
    extracted = 0
    with tarfile.open(b['file'], 'r:gz') as tar:
        members = tar.getmembers()
        for m in members:
            name = m.name.replace('\\', '/')
            if name.startswith('/') or '..' in name.split('/'):
                return {'ok': False, 'error': f'备份内含非法路径：{name}'}
            out = os.path.realpath(os.path.join(root, name))
            if not pathguard.inside(out, root):
                return {'ok': False, 'error': f'备份内含越界路径：{name}'}
        try:
            tar.extractall(root, filter='data')     # py3.12+：过滤设备文件/越界链接
        except TypeError:
            tar.extractall(root)
        extracted = len(members)
    execute('INSERT INTO crash_logs (instance_id, ts, exit_code, tail, action) VALUES (?,?,?,?,?)',
            (row['id'], now(), None, f'restored from {os.path.basename(b["file"])}', 'restore'))
    return {'ok': True, 'restored': b['file'], 'members': chk.get('members'),
            'extracted': extracted,
            'has_jar': bool(row.get('jar_path') and os.path.isfile(row.get('jar_path'))),
            'pre_backup': (pre or {}).get('file')}


def delete_backup(row: dict, backup_id: int) -> dict:
    b = query('SELECT * FROM backups WHERE id=? AND instance_id=?', (backup_id, row['id']), one=True)
    if not b:
        return {'ok': False, 'error': '备份不存在'}
    try:
        if os.path.isfile(b['file']):
            os.remove(b['file'])
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    execute('DELETE FROM backups WHERE id=?', (backup_id,))
    return {'ok': True}


# ---------------------------------------------------------------- 安装器（Forge / NeoForge / Quilt）
RUN_SCRIPT_NAMES = ('run.bat', 'run.sh', 'start.bat', 'start.sh')
INSTALLER_OUTPUTS = ('run.bat', 'run.sh', 'start.bat', 'start.sh', 'quilt-server-launch.jar',
                     'server.jar', 'libraries')


def installer_args(row: dict, jar_path: str) -> dict:
    """按安装器类型给出正确的安装参数（Forge/NeoForge 用 --installServer，
    Quilt 用 `install server <mc> --download-server`）。"""
    name = os.path.basename(jar_path or '').lower()
    mc = str(row.get('mc_version') or '').strip()
    if 'quilt' in name:
        loader = str(row.get('core_version') or '').strip()
        args = ['install', 'server']
        if mc:
            args.append(mc)
        if loader and loader.lower() not in ('latest', 'none'):
            args.append(f'--loader-version={loader}')
        args.append('--download-server')
        args.append('--install-dir=.')
        return {'ok': True, 'kind': 'quilt', 'args': args,
                'cmd_hint': f'java -jar {os.path.basename(jar_path)} ' + ' '.join(args)}
    return {'ok': True, 'kind': 'forge' if 'forge' in name else 'installer',
            'args': ['--installServer'],
            'cmd_hint': f'java -jar {os.path.basename(jar_path)} --installServer'}


def run_installer(row: dict, jar_path: str, timeout: int = 900, log=None) -> dict:
    """在实例目录执行安装器（Forge/NeoForge --installServer；Quilt install server）。"""
    root = workdir_of(row)
    java = row.get('java_path') or get_config().get('default_java') or 'java'
    spec = installer_args(row, jar_path)
    cmd = [java, '-jar', os.path.basename(jar_path)] + spec['args']
    try:
        p = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return {'ok': False, 'error': f'安装器启动失败：{e}', 'cmd': ' '.join(cmd)}
    out = []
    t0 = time.time()
    try:
        while True:
            line = p.stdout.readline()
            if not line:
                break
            s = line.decode('utf-8', 'replace').rstrip()
            out.append(s)
            if log:
                log(s)
            if time.time() - t0 > timeout:
                p.kill()
                return {'ok': False, 'error': '安装器超时', 'output': out[-60:],
                        'cmd': ' '.join(cmd)}
        code = p.wait()
    except Exception as e:
        return {'ok': False, 'error': f'安装器执行异常：{e}', 'output': out[-60:],
                'cmd': ' '.join(cmd)}
    scripts = [s for s in RUN_SCRIPT_NAMES if os.path.isfile(os.path.join(root, s))]
    # 解析启动参数（run.sh/run.bat 内含 @libraries/... 与 main class）
    args = parse_run_script(root, scripts[0]) if scripts else []
    launch_jar = ''
    for cand in ('quilt-server-launch.jar', 'server.jar'):
        if os.path.isfile(os.path.join(root, cand)):
            launch_jar = os.path.join(root, cand)
            break
    produced = [n for n in INSTALLER_OUTPUTS if os.path.exists(os.path.join(root, n))]
    ok = code == 0 and bool(scripts or args or launch_jar)
    return {'ok': ok, 'exit_code': code, 'scripts': scripts, 'args': args,
            'launch_jar': launch_jar, 'produced': produced, 'cmd': ' '.join(cmd),
            'installer_kind': spec['kind'],
            'error': '' if ok else f'安装器退出码 {code}；未找到启动脚本/启动 jar',
            'output': out[-60:]}


def parse_run_script(root: str, script: str) -> list:
    """从 run.sh / run.bat 中提取 java 启动参数（@libraries/... 与主类）。"""
    path = os.path.join(root, script)
    try:
        with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception:
        return []
    text = text.replace('^', ' ').replace('\\\r\n', ' ').replace('\\\n', ' ')
    m = re.search(r'java\s+(.+?)(?:\r?\n|$)', text)
    if not m:
        return []
    raw = m.group(1)
    raw = re.sub(r'@(\S+)', lambda mm: mm.group(0), raw)
    parts = re.findall(r'@[^\s"]+|[^\s"]+', raw)
    out = []
    for p in parts:
        p = p.strip('"')
        if not p or p == 'java':
            continue
        out.append(p)
    return out
