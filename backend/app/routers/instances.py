"""实例 CRUD、生命周期、控制台 WebSocket、状态与监控。"""
import asyncio
import json
import logging
import os
import re
import shutil
import time

from fastapi import (APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import audit as audit_mod
from ..auth import get_current_user, try_get_user
from ..config import BASE_DIR, DATA_DIR, INSTANCE_DIR, get_config, instance_dir
from ..database import execute, now, query
from ..downloader import active_for, get as dl_get, start_download
from ..pathguard import assert_inside, assert_safe_instance_dir
from .. import permissions as perms
from ..permissions import require_instance, require_perm
from ..process_manager import (ST_CRASHED, ST_RUNNING, ST_STARTING, ST_STOPPED, ST_STOPPING,
                               manager, parse_uptime)
from .. import serverctl
from ..sources import resolve as source_resolve, SOURCE_LABELS

logger = logging.getLogger('mcpanel.instances')

router = APIRouter(prefix='/api/instances', tags=['instances'])

_NAME_SAFE = re.compile(r'[^A-Za-z0-9_\-]')


class CreateIn(BaseModel):
    name: str
    core_type: str = 'vanilla'
    mc_version: str = ''
    core_version: str = ''
    memory_mb: int = 2048
    port: int = 25565
    java_path: str = ''
    auto_restart: bool = False
    accept_eula: bool = True
    download: bool = True
    extra_jvm_args: str = ''
    nogui: bool = True
    rcon_enabled: bool = False
    rcon_port: int = 25575
    rcon_password: str = ''
    note: str = ''
    #: 归属用户。留空 = 自己；只有超级管理员可以填别人的 id（规格 §3）。
    owner_id: int = 0
    #: 自定义存放目录（绝对路径）。留空 = 用默认 <数据目录>/instances/<id>_<名称>。
    #: 非空时会过 `assert_safe_instance_dir` 的安全校验（挡系统目录 / 面板自身目录）。
    dir: str = ''


class UpdateIn(BaseModel):
    name: str = None
    memory_mb: int = None
    port: int = None
    java_path: str = None
    auto_restart: bool = None
    extra_jvm_args: str = None
    extra_server_args: str = None
    nogui: bool = None
    rcon_enabled: bool = None
    rcon_port: int = None
    rcon_password: str = None
    jar_path: str = None
    note: str = None


class CommandIn(BaseModel):
    command: str


class JarIn(BaseModel):
    jar_path: str


def _row_or_404(iid: int) -> dict:
    row = query('SELECT * FROM instances WHERE id=?', (iid,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail='实例不存在')
    return row


def _safe_dir_name(iid: int, name: str) -> str:
    clean = _NAME_SAFE.sub('_', str(name or 'instance'))[:40] or 'instance'
    return f'{iid}_{clean}'


def _decorate(row: dict) -> dict:
    st = manager.status(row['id'])
    out = dict(row)
    out.pop('rcon_password', None)
    out['has_rcon_password'] = bool(row.get('rcon_password'))
    out['status'] = st['status']
    out['pid'] = st['pid']
    out['running'] = st['running']
    out['uptime'] = st['uptime']
    out['uptime_text'] = parse_uptime(st['uptime'])
    out['cpu'] = st['cpu']
    out['mem_mb'] = st['mem_mb']
    out['players'] = st['players']
    out['max_players'] = st['max_players']
    out['tps'] = st['tps']
    out['mspt'] = st['mspt']
    out['tps_available'] = st['tps_available']
    out['online_names'] = st['online_names']
    out['dir'] = instance_dir(row)
    out['core_label'] = SOURCE_LABELS.get(row.get('core_type'), row.get('core_type'))
    try:
        out['dir_size'] = serverctl.dir_size(out['dir'])
    except Exception:
        out['dir_size'] = 0
    out['eula'] = serverctl.eula_status(row)
    return out


# ---------------------------------------------------------------- 列表 / 详情
def _visible_rows(user: dict) -> list:
    """当前用户**有权看到**的实例行（超管/管理员=全部，其余=自己名下）。

    与 `list_instances` 同一口径 —— 实时推送通道必须复用这条，否则会把
    别人的实例状态推给普通用户。
    """
    if perms.is_admin(user):
        return query('SELECT * FROM instances ORDER BY id ASC')
    return query('SELECT * FROM instances WHERE owner_id=? ORDER BY id ASC',
                 (int(user['id']),))


@router.get('')
def list_instances(user: dict = Depends(get_current_user),
                   owner: int = Query(default=0, description='按归属用户筛选（仅超管/管理员有效）'),
                   all: int = Query(default=0, description='=1 且为超管/管理员时返回全部实例')):
    """实例列表：**普通用户/只读默认只看得到自己的实例**（规格 §3）。

    超管/管理员可见全部，并可用 `?owner=<uid>` 筛选某人的实例；
    他们若想只列自己的，用 `?owner=<自己的 id>`。
    """
    where, args = '', ()
    if not perms.is_admin(user):
        where = 'WHERE owner_id=?'
        args = (int(user['id']),)
    elif owner:
        where = 'WHERE owner_id=?'
        args = (int(owner),)
    rows = query(f'SELECT * FROM instances {where} ORDER BY id ASC', args)
    counts = perms.instance_counts()
    owners = {}
    if perms.is_admin(user):
        for u in query('SELECT id, username, role FROM users'):
            owners[int(u['id'])] = {'id': int(u['id']), 'username': u['username'],
                                    'role': u['role'],
                                    'role_name': perms.role_name(u['role'])}
    out = []
    for r in rows:
        d = _decorate(r)
        d['owner_id'] = int(r.get('owner_id') or 0)
        if perms.is_admin(user):
            d['owner'] = owners.get(d['owner_id'])
        out.append(d)
    return {'ok': True, 'instances': out,
            'total': len(out),
            'scope': 'all' if perms.is_admin(user) and not owner else 'mine',
            'owners': sorted(owners.values(), key=lambda x: x['id']) if perms.is_admin(user) else [],
            'instance_counts': {str(k): v for k, v in counts.items()} if perms.is_admin(user) else {}}


@router.get('/{iid}')
def get_instance(iid: int, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid)
    d = _decorate(row)
    d['owner_id'] = int(row.get('owner_id') or 0)
    return {'ok': True, 'instance': d}


@router.get('/{iid}/status')
def status(iid: int, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid)
    st = manager.status(iid)
    st['ok'] = True
    st['eula'] = serverctl.eula_status(row)
    st['uptime_text'] = parse_uptime(st['uptime'])
    return st


# ---------------------------------------------------------------- 创建 / 删除
@router.post('')
def create_instance(body: CreateIn, request: Request, user: dict = Depends(get_current_user)):
    require_perm(user, perms.P_INSTANCE_CREATE)
    name = (body.name or '').strip()
    if not 1 <= len(name) <= 40:
        raise HTTPException(status_code=400, detail='实例名称长度需在 1~40 之间')
    if not 1 <= int(body.memory_mb) <= 65536:
        raise HTTPException(status_code=400, detail='内存需在 1~65536 MB 之间')
    port = int(body.port)
    if not (1024 <= port <= 65535):
        raise HTTPException(status_code=400, detail='端口需在 1024~65535 之间')
    # 归属：默认给自己；只有超管能把实例挂在别人名下
    owner_id = int(user['id'])
    if body.owner_id:
        if not perms.is_super(user):
            raise HTTPException(status_code=403, detail='权限不足：只有超级管理员可以指定实例归属')
        target = query('SELECT id, username FROM users WHERE id=?', (int(body.owner_id),), one=True)
        if not target:
            raise HTTPException(status_code=400, detail='指定的归属用户不存在')
        owner_id = int(target['id'])
    # 配额逐项校验（403 + 中文原因）；超管不受限
    quota_user = query('SELECT * FROM users WHERE id=?', (owner_id,), one=True) or user
    perms.check_quota(quota_user, 'instance')
    perms.check_quota(quota_user, 'memory', amount=int(body.memory_mb))
    dup = query('SELECT id,name FROM instances WHERE port=?', (port,), one=True)
    if dup:
        raise HTTPException(status_code=409, detail=f'端口 {port} 已被实例「{dup["name"]}」占用')
    iid = execute(
        'INSERT INTO instances (name, owner_id, core_type, mc_version, core_version, jar_path, '
        'java_path, memory_mb, port, dir, status, created_at, auto_restart, extra_jvm_args, '
        'nogui, rcon_enabled, rcon_port, rcon_password, note) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (name, owner_id, body.core_type, body.mc_version, body.core_version, '', body.java_path,
         int(body.memory_mb), port, '', ST_STOPPED, now(), 1 if body.auto_restart else 0,
         body.extra_jvm_args, 1 if body.nogui else 0, 1 if body.rcon_enabled else 0,
         int(body.rcon_port), body.rcon_password, body.note))
    # 存放目录：用户指定则用他的（先过安全检查），否则用默认位置。
    # ⚠️ 必须在**建目录之前**校验并 realpath —— 否则符号链接/`..` 可能把实例指到别处。
    custom = str(getattr(body, 'dir', '') or '').strip()
    if custom:
        # 保留区：面板的数据目录与后端程序目录 —— 实例绝不能建在里面
        # （删除实例时会连带删目录，建在数据目录里可能把库/别的实例一起带走）
        d = assert_safe_instance_dir(custom, data_dir=DATA_DIR,
                                    extra_reserved=(BASE_DIR, os.path.dirname(BASE_DIR)))
    else:
        d = os.path.join(INSTANCE_DIR, _safe_dir_name(iid, name))
    try:
        os.makedirs(d, exist_ok=True)
        os.makedirs(os.path.join(d, 'logs'), exist_ok=True)
    except OSError as e:
        # 建目录失败要把实例记录回滚掉，否则库里留一条指向不存在目录的僵尸实例
        execute('DELETE FROM instances WHERE id=?', (iid,))
        raise HTTPException(status_code=400,
                            detail=f'创建存放目录失败：{d}（{e.strerror or e}）')
    execute('UPDATE instances SET dir=? WHERE id=?', (os.path.realpath(d), iid))
    row = _row_or_404(iid)
    if body.accept_eula:
        serverctl.accept_eula(row)
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.create', iid,
                    f'创建实例 {name}（{body.core_type} {body.mc_version}，{body.memory_mb}MB，端口 {port}）')
    # 端口 / RCON 落到 server.properties（否则服务端按默认 25565 起，多实例撞端口）
    try:
        sp = serverctl.init_server_properties(row, port=port, rcon_enabled=body.rcon_enabled,
                                              rcon_port=int(body.rcon_port),
                                              rcon_password=body.rcon_password, motd=name)
    except Exception as e:
        sp = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
    result = {'ok': True, 'id': iid, 'instance': _decorate(_row_or_404(iid)),
              'server_properties': sp}
    # 下载核心（用户可取消下载，仅建目录）
    if body.download and body.mc_version:
        info = source_resolve(body.core_type, body.mc_version, body.core_version)
        if not info.get('ok'):
            result['download'] = {'ok': False, 'state': 'failed', 'progress': 0,
                                  'accepted': False,
                                  'error': info.get('error', '解析下载地址失败'),
                                  'via': info.get('via', ''),
                                  'tried': info.get('tried', [])}
        else:
            fname = info.get('filename') or 'server.jar'
            dest = os.path.join(d, fname)
            tid = start_download(info['url'], dest, label=fname, sha1=info.get('sha1', ''),
                                 sha256=info.get('sha256', ''), md5=info.get('md5', ''),
                                 size=info.get('size', 0), urls=info.get('urls'), kind='jar')
            execute('UPDATE instances SET jar_path=? WHERE id=?', (dest, iid))
            st = dl_get(tid)
            state = st.get('state') or 'running'
            progress = st.get('progress') or 0
            result['download'] = {
                # ⚠️ `ok` 的语义 = **这个下载任务被接受并已启动**，不是"已经下载完成"。
                # 下载是异步的，建实例这一刻它必然还在 running；如果这里用
                # `state == 'done'`，创建响应永远是 ok:false，而前端会把 ok:false 当成
                # "下载没启动"、于是**不渲染进度条**（界面上还会出现
                # "核心下载未开始：核心正在后台下载中（0%）"这种自相矛盾的话）。
                # 是否下完看 `state`（running/done/failed）。
                'ok': True,
                'state': state,
                'progress': progress,
                'accepted': True,
                'started': True,
                'task_id': tid,
                'filename': fname,
                'kind': info.get('kind'),
                'note': info.get('note', ''),
                'dest': dest,
                'via': info.get('via', ''),
                'source_used': info.get('source_used', ''),
                'mirror_used': bool(info.get('mirror_used')),
                'urls': info.get('urls', []),
                # 失败时才是真错误；进行中这里为空（提示文案由前端按 state 自己组织）
                'error': st.get('error', '') if state == 'failed' else '',
            }
    return result


@router.delete('/{iid}')
def delete_instance(iid: int, request: Request, purge: bool = Query(default=False),
                    user: dict = Depends(get_current_user)):
    row = require_instance(user, iid, perms.P_INSTANCE_DELETE)
    st = manager.status(iid)
    if st['running']:
        raise HTTPException(status_code=409, detail='实例正在运行，请先停止')
    execute('DELETE FROM instances WHERE id=?', (iid,))
    execute('DELETE FROM metrics WHERE instance_id=?', (iid,))
    execute('DELETE FROM cron_jobs WHERE instance_id=?', (iid,))
    execute('DELETE FROM backups WHERE instance_id=?', (iid,))
    manager.forget(iid)
    note = '仅删除面板记录，实例目录已保留'
    if purge:
        d = instance_dir(row)
        if d and os.path.isdir(d) and os.path.realpath(d).startswith(os.path.realpath(INSTANCE_DIR)):
            shutil.rmtree(d, ignore_errors=True)
            note = '实例目录已一并删除'
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.delete', iid, f'删除实例 {row["name"]}；{note}', level='warn')
    return {'ok': True, 'note': note}


@router.patch('/{iid}')
def update_instance(iid: int, body: UpdateIn, request: Request,
                    user: dict = Depends(get_current_user)):
    cur = require_instance(user, iid)
    fields, args, changed = [], [], []
    for key, val in body.dict(exclude_unset=True).items():
        if val is None:
            continue
        if key == 'name':
            val = str(val).strip()
            if not 1 <= len(val) <= 40:
                raise HTTPException(status_code=400, detail='实例名称长度需在 1~40 之间')
        if key == 'memory_mb':
            if not 1 <= int(val) <= 65536:
                raise HTTPException(status_code=400, detail='内存需在 1~65536 MB 之间')
            # 内存是配额项：按"差额"校验（同口径见 permissions.check_quota 注释）
            owner = query('SELECT * FROM users WHERE id=?',
                          (int(cur.get('owner_id') or 0),), one=True) or user
            delta = max(0, int(val) - int(cur.get('memory_mb') or 0))
            if delta:
                perms.check_quota(owner, 'memory', amount=delta)
        if key == 'port':
            val = int(val)
            if not (1024 <= val <= 65535):
                raise HTTPException(status_code=400, detail='端口需在 1024~65535 之间')
            # 改端口/绑定属于「网络设置」，普通用户没有这个权限点（规格 §2）
            require_instance(user, iid, perms.P_INSTANCE_NETWORK)
            dup = query('SELECT id,name FROM instances WHERE port=? AND id<>?', (val, iid), one=True)
            if dup:
                raise HTTPException(status_code=409, detail=f'端口 {val} 已被实例「{dup["name"]}」占用')
        if key == 'rcon_port':
            require_instance(user, iid, perms.P_INSTANCE_NETWORK)
        if key == 'jar_path' and val:
            val = assert_inside(val, instance_dir(_row_or_404(iid)))
        if key in ('auto_restart', 'nogui', 'rcon_enabled'):
            val = 1 if val else 0
        fields.append(f'{key}=?')
        args.append(val)
        changed.append(key)
    if not fields:
        return {'ok': True, 'changed': []}
    args.append(iid)
    execute(f'UPDATE instances SET {", ".join(fields)} WHERE id=?', tuple(args))
    # ⚠️ RCON / 端口这类「要进 server.properties 才生效」的字段，光写库是不够的。
    #    实测踩到：通过 API 开了 RCON、库里 rcon_enabled=1，但 server.properties 里
    #    `rcon.password=` 仍是空、`rcon.port` 仍是 25565 → 服务端根本不监听 RCON，
    #    于是「免插件免停服导入」（引擎 C，靠 RCON 下发 /place template）用不了。
    #    成因：init_server_properties() 的约定是"只补缺键、不覆盖已有值"，
    #    而**空值也算"键存在"**，所以永远补不上 → 先清空键再让它补。
    #    另外：**服务端运行中会在退出时回写 server.properties**，此时写盘会被覆盖，
    #    所以只在实例停止时落盘，运行中则明确提示「下次启动生效」。
    props_note = ''
    if any(k in changed for k in ('rcon_enabled', 'rcon_port', 'rcon_password', 'port')):
        row2 = _row_or_404(iid)
        if int(row2.get('pid') or 0):
            props_note = '实例正在运行：这些设置会在下次启动时写入 server.properties 并生效'
        else:
            try:
                cl = serverctl.clear_empty_rcon_keys(row2)
                r = serverctl.init_server_properties(
                    row2, rcon_enabled=int(row2.get('rcon_enabled') or 0),
                    rcon_port=row2.get('rcon_port'), rcon_password=row2.get('rcon_password'))
                notes = [(cl or {}).get('note'), r.get('note')]
                # ⚠️ 用户**显式改过**的字段必须强制写盘：init_server_properties() 的约定是
                #    "只补缺键、不覆盖已有值"，所以像 `rcon.port=25565`（服务端默认值，
                #    但面板里是 25575）这种"有值但不对"的情况它不会改 —— 实测踩到。
                #    这里只对本次 PATCH 明确提交的键做覆盖，不碰其它键。
                from .. import mcprops
                force = {}
                if 'rcon_port' in changed:
                    force['rcon.port'] = str(int(row2.get('rcon_port') or 25575))
                if 'rcon_password' in changed and row2.get('rcon_password'):
                    force['rcon.password'] = str(row2.get('rcon_password'))
                if 'rcon_enabled' in changed:
                    force['enable-rcon'] = 'true' if int(row2.get('rcon_enabled') or 0) else 'false'
                if 'port' in changed:
                    force['server-port'] = str(int(row2.get('port') or 25565))
                if force:
                    wp = os.path.join(serverctl.workdir_of(row2), 'server.properties')
                    wr = mcprops.write_props(wp, force)
                    notes.append('已按本次修改强制写入 %s' % '、'.join(sorted(force)))
                props_note = '；'.join(x for x in notes if x)
            except Exception as e:                            # noqa: BLE001
                props_note = f'写入 server.properties 失败：{e}'
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.update', iid, f'修改实例配置：{", ".join(changed)}')
    return {'ok': True, 'changed': changed, 'props_note': props_note,
            'instance': _decorate(_row_or_404(iid))}


# ---------------------------------------------------------------- 生命周期
@router.post('/{iid}/start')
def start(iid: int, request: Request, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid, perms.P_INSTANCE_START)
    # P0-1：核心还在下载时，明确回 409 + 中文百分比，而不是让上层报"jar 不存在"
    act = active_for([row.get('jar_path'), instance_dir(row)])
    if act:
        st = dl_get(act.get('id'))
        pct = int(round(float(st.get('progress') or 0)))
        raise HTTPException(
            status_code=409,
            detail=f'核心仍在下载中（{pct}%），请稍候；下载完成后即可启动。'
                   f'（任务 {act.get("id")}，可在下载列表查看进度）')
    r = manager.start(iid, by=user['username'])
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('error', '启动失败'))
    return r


@router.post('/{iid}/stop')
def stop(iid: int, request: Request, user: dict = Depends(get_current_user)):
    require_instance(user, iid, perms.P_INSTANCE_STOP)
    return manager.stop(iid, by=user['username'])


@router.post('/{iid}/restart')
def restart(iid: int, request: Request, user: dict = Depends(get_current_user)):
    require_instance(user, iid, perms.P_INSTANCE_START)
    require_instance(user, iid, perms.P_INSTANCE_STOP)
    return manager.restart(iid, by=user['username'])


@router.post('/{iid}/kill')
def kill(iid: int, request: Request, user: dict = Depends(get_current_user)):
    require_instance(user, iid, perms.P_INSTANCE_STOP)
    return manager.kill(iid, by=user['username'])


@router.post('/{iid}/command')
def command(iid: int, body: CommandIn, request: Request, user: dict = Depends(get_current_user)):
    require_instance(user, iid, perms.P_INSTANCE_COMMAND)
    cmd = (body.command or '').strip()
    if not cmd:
        raise HTTPException(status_code=400, detail='命令不能为空')
    if len(cmd) > 1000:
        raise HTTPException(status_code=400, detail='命令过长')
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.command', iid, f'控制台命令：{cmd[:200]}')
    r = manager.send_command(iid, cmd)
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('error', '下发失败'))
    return r


@router.post('/{iid}/rcon')
def rcon_command(iid: int, body: CommandIn, request: Request,
                 user: dict = Depends(get_current_user)):
    row = require_instance(user, iid, perms.P_INSTANCE_COMMAND)
    cfg = get_config()
    from ..rcon import try_command
    port = int(row.get('rcon_port') or cfg.get('rcon_port') or 25575)
    pwd = row.get('rcon_password') or cfg.get('rcon_password') or ''
    if not pwd:
        raise HTTPException(status_code=400, detail='未配置 RCON 密码')
    ok, text = try_command(cfg.get('rcon_host', '127.0.0.1'), port, pwd, body.command.strip())
    if not ok:
        raise HTTPException(status_code=502, detail=f'RCON 不可用：{text}')
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.rcon', iid, f'RCON：{body.command.strip()[:200]}')
    return {'ok': True, 'result': text}


# ---------------------------------------------------------------- 日志
@router.get('/{iid}/log')
def get_log(iid: int, lines: int = Query(default=300, ge=1, le=5000),
            user: dict = Depends(get_current_user)):
    require_instance(user, iid)
    inst = manager.get(iid)
    return {'ok': True, 'lines': inst.tail(lines), 'status': manager.status(iid)}


@router.get('/{iid}/log/download')
def download_log(iid: int, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid)
    path = os.path.join(instance_dir(row), 'logs', 'latest.log')
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='日志文件不存在')
    return FileResponse(path, filename=f'{row["name"]}-latest.log',
                        media_type='text/plain; charset=utf-8')


# ---------------------------------------------------------------- WebSocket 控制台
@router.websocket('/{iid}/console/ws')
async def console_ws(ws: WebSocket, iid: int):
    user = try_get_user(ws)
    if not user:
        await ws.close(code=4401)
        return
    row = query('SELECT * FROM instances WHERE id=?', (iid,), one=True)
    if not row:
        await ws.close(code=4404)
        return
    # 控制台也是资源：必须过实例级权限（否则任何登录用户都能看他人的控制台、下命令）
    if not perms.can_view_logs(user, row):
        await ws.close(code=4403)
        return
    can_cmd = perms.is_admin(user) or (
        int(row.get('owner_id') or 0) == int(user['id'])
        and perms.has_perm(user, perms.P_INSTANCE_COMMAND))
    await ws.accept()
    inst = manager.get(iid)
    q = inst.subscribe()
    try:
        await ws.send_json({'type': 'hello', 'instance': row['name'], 'id': iid,
                            'status': manager.status(iid),
                            'readonly': not can_cmd,
                            'role': user.get('role'),
                            'history': inst.tail(200)})
        stop_flag = asyncio.Event()

        async def sender():
            try:
                while not stop_flag.is_set():
                    if q:
                        rec = q.popleft()
                        await ws.send_json({'type': 'log', **rec})
                    else:
                        await asyncio.sleep(0.15)
            except Exception:
                stop_flag.set()

        async def receiver():
            try:
                while True:
                    raw = await ws.receive_text()
                    if len(raw) > 4000:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        msg = {'type': 'cmd', 'data': raw}
                    mtype = msg.get('type')
                    if mtype == 'cmd':
                        cmd = str(msg.get('data') or '').strip()
                        if not cmd:
                            continue
                        if not can_cmd:
                            await ws.send_json({'type': 'ack', 'ok': False, 'cmd': cmd,
                                                'error': '权限不足：当前角色不能向该实例下发命令'})
                            continue
                        res = manager.send_command(iid, cmd)
                        await ws.send_json({'type': 'ack', 'ok': res.get('ok', False),
                                            'error': res.get('error', ''), 'cmd': cmd})
                        audit_mod.audit(user['username'], '', 'instance.command', iid,
                                        f'控制台命令：{cmd[:200]}')
                    elif mtype == 'ping':
                        await ws.send_json({'type': 'pong', 'ts': time.time()})
                    elif mtype == 'status':
                        await ws.send_json({'type': 'status', 'status': manager.status(iid)})
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                stop_flag.set()

        t1 = asyncio.create_task(sender())
        t2 = asyncio.create_task(receiver())
        done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        stop_flag.set()
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        inst.unsubscribe(q)
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------- 监控曲线
@router.get('/{iid}/metrics')
def metrics(iid: int, hours: int = Query(default=1, ge=1, le=168),
            user: dict = Depends(get_current_user)):
    require_instance(user, iid)
    since = now() - hours * 3600
    rows = query('SELECT ts, cpu, mem_mb, players, tps, mspt FROM metrics '
                 'WHERE instance_id=? AND ts>=? ORDER BY ts ASC', (iid, since))
    # 降采样到最多 720 点，避免曲线接口过大
    if len(rows) > 720:
        step = max(1, len(rows) // 720)
        rows = rows[::step]
    tps_vals = [r['tps'] for r in rows if r['tps'] is not None]
    return {'ok': True, 'hours': hours, 'points': rows,
            'tps_available': bool(tps_vals),
            'tps_note': '' if tps_vals else 'TPS 不可用：该服务端未在日志中输出 TPS，且未启用 RCON',
            'summary': {
                'cpu_avg': round(sum(r['cpu'] or 0 for r in rows) / len(rows), 1) if rows else 0,
                'cpu_max': round(max((r['cpu'] or 0) for r in rows), 1) if rows else 0,
                'mem_max': round(max((r['mem_mb'] or 0) for r in rows), 1) if rows else 0,
                'players_max': max((r['players'] or 0) for r in rows) if rows else 0,
                'tps_min': round(min(tps_vals), 2) if tps_vals else None,
            }}


# ---------------------------------------------------------------- 崩溃记录
@router.get('/{iid}/crashes')
def crashes(iid: int, limit: int = Query(default=20, ge=1, le=200),
            user: dict = Depends(get_current_user)):
    require_instance(user, iid)
    rows = query('SELECT * FROM crash_logs WHERE instance_id=? ORDER BY ts DESC LIMIT ?',
                 (iid, limit))
    return {'ok': True, 'crashes': rows}


# ---------------------------------------------------------------- jar 管理
@router.post('/{iid}/jar')
def set_jar(iid: int, body: JarIn, request: Request, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid, perms.P_INSTANCE_FILES)
    target = assert_inside(body.jar_path, instance_dir(row))
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail='jar 文件不存在于实例目录中')
    execute('UPDATE instances SET jar_path=? WHERE id=?', (target, iid))
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.set_jar', iid, f'指定服务端 jar：{os.path.basename(target)}')
    return {'ok': True, 'jar_path': target}


@router.get('/{iid}/jars')
def list_jars(iid: int, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid)
    root = instance_dir(row)
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('logs', 'cache', 'backups', '.git')]
        for fn in files:
            if fn.lower().endswith('.jar') and 'libraries' not in base.replace('\\', '/'):
                p = os.path.join(base, fn)
                out.append({'path': p, 'rel': os.path.relpath(p, root).replace('\\', '/'),
                            'size': os.path.getsize(p),
                            'mtime': os.path.getmtime(p)})
        if len(out) > 200:
            break
    out.sort(key=lambda x: -x['mtime'])
    return {'ok': True, 'jars': out, 'current': row.get('jar_path', '')}


@router.post('/{iid}/install-core')
def install_core(iid: int, body: dict = Body(...), request: Request = None,
                 user: dict = Depends(get_current_user)):
    """重新下载 / 更换核心版本（Forge/NeoForge 会自动跑安装器）。"""
    row = require_instance(user, iid, perms.P_INSTANCE_FILES)
    source = body.get('source') or row['core_type']
    mc = body.get('mc_version') or row['mc_version']
    build = str(body.get('build') or '')
    info = source_resolve(source, mc, build)
    if not info.get('ok'):
        return {'ok': False, 'error': info.get('error', '解析失败')}
    root = instance_dir(row)
    fname = info.get('filename') or 'server.jar'
    dest = os.path.join(root, fname)
    tid = start_download(info['url'], dest, label=fname, sha1=info.get('sha1', ''),
                         sha256=info.get('sha256', ''), kind='jar')
    execute('UPDATE instances SET core_type=?, mc_version=?, core_version=?, jar_path=? WHERE id=?',
            (source, mc, build, dest, iid))
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'instance.install_core', iid, f'安装核心 {source} {mc} {build}')
    return {'ok': True, 'task_id': tid, 'filename': fname, 'dest': dest,
            'kind': info.get('kind'), 'note': info.get('note', ''),
            'installer': info.get('kind') == 'installer'}


@router.post('/{iid}/run-installer')
def run_installer(iid: int, request: Request, user: dict = Depends(get_current_user)):
    """对已下载的 Forge/NeoForge 安装器执行 --installServer。"""
    row = require_instance(user, iid, perms.P_INSTANCE_FILES)
    jar = row.get('jar_path') or ''
    if not jar or not os.path.isfile(jar):
        raise HTTPException(status_code=400, detail='未找到安装器 jar')
    if 'installer' not in os.path.basename(jar).lower() and 'forge' not in os.path.basename(jar).lower():
        raise HTTPException(status_code=400, detail='当前 jar 不是 Forge/NeoForge 安装器')
    inst = manager.get(iid)
    res = serverctl.run_installer(row, jar, log=lambda s: inst.push('[安装器] ' + s, 'panel'))
    if res.get('ok'):
        # 记录从 run 脚本解析出的启动方式
        args = res.get('args') or []
        main = ''
        for i, a in enumerate(args):
            if a.startswith('@') or a.endswith('.jar'):
                main = a
                break
        execute('UPDATE instances SET extra_server_args=?, note=? WHERE id=?',
                (' '.join(args[:0]), row.get('note', ''), iid))
        audit_mod.audit(user['username'], request.client.host if request.client else '',
                        'instance.run_installer', iid, f'安装器完成，脚本 {res.get("scripts")}')
    return res


@router.get('/{iid}/rcon/test')
def rcon_test(iid: int, user: dict = Depends(get_current_user)):
    row = require_instance(user, iid)
    cfg = get_config()
    from ..rcon import try_command
    port = int(row.get('rcon_port') or cfg.get('rcon_port') or 25575)
    pwd = row.get('rcon_password') or cfg.get('rcon_password') or ''
    if not pwd:
        return {'ok': False, 'error': '未配置 RCON 密码'}
    ok, text = try_command(cfg.get('rcon_host', '127.0.0.1'), port, pwd, 'list')
    return {'ok': ok, 'result': text, 'error': '' if ok else text}


# ---------------------------------------------------------------- 仪表盘实时推送
#: 推给前端的实时字段（都能从内存直接读，不做磁盘扫描）。
#: `dir_size` / `eula` 这类要访问磁盘的字段**故意不推**，由 HTTP 列表负责，
#: 否则每 2 秒给每个实例扫一遍目录。
LIVE_FIELDS = ('status', 'pid', 'running', 'uptime', 'cpu', 'mem_mb', 'players',
               'max_players', 'tps', 'mspt', 'tps_available')

#: 推送间隔。1 秒足够"看起来连续"，而 `manager.status()` 是纯内存读取，成本很低
#: （实例数量 × 每秒一次），不会给服务端造成压力。可用配置 `dashboard_push_ms` 调整。
DEFAULT_PUSH_MS = 1000


def _live_snapshot(user: dict) -> dict:
    """一次实时快照：实例状态 + 主机资源。

    实例范围走 `_visible_rows`（与列表同口径），主机资源只给超管/管理员看
    （普通用户看到别人的机器负载没有意义，也属信息泄露）。
    """
    import psutil
    rows = _visible_rows(user)
    insts = []
    for r in rows:
        st = manager.status(r['id'])
        item = {'id': int(r['id']), 'name': r['name'],
                'owner_id': int(r.get('owner_id') or 0)}
        for f in LIVE_FIELDS:
            item[f] = st.get(f)
        item['uptime_text'] = parse_uptime(st.get('uptime') or 0)
        item['online_names'] = st.get('online_names') or []
        insts.append(item)
    host = {}
    if perms.is_admin(user):
        try:
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage(str(INSTANCE_DIR))
            host = {'cpu_percent': psutil.cpu_percent(interval=None),
                    'cpu_count': psutil.cpu_count(),
                    'mem_percent': vm.percent,
                    'mem_used_mb': round(vm.used / 1048576),
                    'mem_total_mb': round(vm.total / 1048576),
                    'disk_percent': disk.percent,
                    'disk_free_gb': round(disk.free / 1073741824, 1)}
        except Exception:
            host = {}
    running = [i for i in insts if i.get('running')]
    return {
        'type': 'live',
        'ts': time.time(),
        'instances': insts,
        'host': host,
        'counts': {
            'total': len(insts),
            'running': len(running),
            'players': sum(int(i.get('players') or 0) for i in running),
            'mem_mb': round(sum(float(i.get('mem_mb') or 0) for i in running), 1),
        },
    }


# 独立前缀：放在 `/api/instances/{iid}` 命名空间**之外**。
# 否则 `dashboard` 会被 `/{iid}` 兄弟路由先匹配到、因"不是整数"报 422。
live_router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])


@live_router.websocket('/ws')
async def dashboard_ws(ws: WebSocket):
    """仪表盘实时通道：**一条连接推送全部实例**（按权限过滤）。

    为什么不用「每实例一条 WS」：仪表盘可能同时看几十个实例，逐实例建连会把
    浏览器连接数、服务端任务数都打满；而且这里要的只是**轻量状态**，
    与按实例的 `/console/ws`（要日志流）是不同用途，分开更清楚。

    服务端每 2 秒推一帧；客户端**按 id 原地更新 DOM**，不整页重绘。
    """
    user = try_get_user(ws)
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        cfg = get_config()
        push_ms = max(250, int(cfg.get('dashboard_push_ms') or DEFAULT_PUSH_MS))
        push_s = push_ms / 1000.0
        while True:
            await ws.send_json(_live_snapshot(user))
            # 收消息只为尽快感知断开；客户端发 ping 就回 pong
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=push_s)
                if raw and '"ping"' in raw:
                    await ws.send_json({'type': 'pong', 'ts': time.time()})
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:                                  # noqa: BLE001
        try:
            logger.debug('dashboard ws 结束: %s', exc)
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
