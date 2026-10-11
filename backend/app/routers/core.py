"""核心下载源、下载任务、Java 运行时、TPS/MSPT 采集。"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from .. import audit as audit_mod, javaruntime, rcon as rcon_mod
from ..auth import get_current_user
from ..config import JAVA_DIR, instance_dir
from ..database import query
from ..downloader import get as dl_get, list_tasks
from .. import permissions as perms
from ..permissions import require_instance, require_perm
from ..process_manager import manager
from ..sources import (SOURCE_LABELS, SOURCES, list_builds, list_versions, probe_all,
                       verify_download)

router = APIRouter(prefix='/api', tags=['core'])


class JavaInstallIn(BaseModel):
    major: int = 21
    image: str = 'jre'
    source: str = 'auto'


class JavaTestIn(BaseModel):
    path: str


class JavaPreviewIn(BaseModel):
    instance_id: int = None
    memory_mb: int = None
    java_path: str = None
    extra_jvm_args: str = None
    extra_server_args: str = None
    nogui: bool = None
    jar_path: str = None


# ---------------------------------------------------------------- 下载源
@router.get('/cores')
def cores(user: dict = Depends(get_current_user)):
    # 建实例向导要用核心清单 → 能建实例的角色即可读
    require_perm(user, perms.P_INSTANCE_CREATE)
    return {'ok': True, 'sources': [{'id': s, 'label': SOURCE_LABELS[s]} for s in SOURCES]}


@router.get('/cores/{source}/versions')
def core_versions(source: str, user: dict = Depends(get_current_user)):
    require_perm(user, perms.P_INSTANCE_CREATE)
    if source not in SOURCES:
        raise HTTPException(status_code=404, detail=f'未知下载源：{source}')
    return list_versions(source)


@router.get('/cores/{source}/builds')
def core_builds(source: str, mc: str, user: dict = Depends(get_current_user)):
    require_perm(user, perms.P_INSTANCE_CREATE)
    return list_builds(source, mc)


@router.get('/cores/{source}/resolve')
def core_resolve(source: str, mc: str, build: str = '', probe: int = Query(default=0),
                 user: dict = Depends(get_current_user)):
    require_perm(user, perms.P_INSTANCE_CREATE)
    from ..sources import resolve as _resolve
    r = _resolve(source, mc, build)
    if not r.get('ok'):
        raise HTTPException(status_code=502, detail=r.get('error', '解析失败'))
    if probe:
        # 实测每个候选直链（Range 200/206），把可达性如实回报给前端/自检
        r['download'] = verify_download(r)
    return r


@router.get('/cores-probe')
def cores_probe(user: dict = Depends(get_current_user)):
    """逐个源实测可达性（列版本 → 解析直链 → 探测直链；可能较慢，前端给 loading）。"""
    require_perm(user, perms.P_INSTANCE_CREATE)
    return {'ok': True, 'results': probe_all()}


# ---------------------------------------------------------------- 下载任务
@router.get('/downloads')
def downloads(user: dict = Depends(get_current_user)):
    tasks = list_tasks()
    for t in tasks:
        r = dl_get(t['id'])
        t['percent'] = r.get('percent', 0)
        t['speed'] = r.get('speed', 0)
    return {'ok': True, 'downloads': tasks}


@router.get('/downloads/{tid}')
def download_status(tid: str, user: dict = Depends(get_current_user)):
    r = dl_get(tid)
    if 'id' not in r:
        raise HTTPException(status_code=404, detail='任务不存在')
    return r


# ---------------------------------------------------------------- TPS / MSPT
def _inst_row(iid: int, user: dict = None) -> dict:
    return require_instance(user, iid)


def _instance_log_lines(row: dict, limit: int = 1500) -> list:
    """优先用内存环形缓冲；面板重启后缓冲为空则直接读 latest.log 兜底。"""
    lines = [r.get('line', '') for r in manager.get(row['id']).tail(limit)]
    if lines:
        return lines
    try:
        p = os.path.join(instance_dir(row), 'logs', 'latest.log')
        if os.path.isfile(p):
            size = os.path.getsize(p)
            with open(p, 'rb') as f:
                f.seek(max(0, size - 512 * 1024))
                return f.read().decode('utf-8', 'replace').splitlines()
    except Exception:
        pass
    return []


@router.get('/instances/{iid}/tps')
def instance_tps(iid: int, refresh: int = Query(default=0),
                 user: dict = Depends(get_current_user)):
    """TPS / MSPT：优先 RCON（forge/neoforge/spark/tps），退化到日志解析。

    结构：`{ok, available, tps:{1m,5m,15m}|null, mspt:{avg,p95}|null,
            source:'rcon:forge'|'rcon:spark'|'rcon:tps'|'log'|null, note}`
    **拿不到就是 null，绝不回 0。**
    """
    row = _inst_row(iid, user)
    if refresh:
        rcon_mod.clear_tps_cache(iid)
    # TTL 25 秒：这个接口是前端手点/定时刷新用的，5 秒缓存会让频繁刷新
    # 直接踩穿缓存、每次新开一条 RCON 连接（服务端控制台刷屏）。
    out = rcon_mod.collect_tps(row, log_lines=_instance_log_lines(row), ttl=25.0)
    st = manager.status(iid)
    out['instance'] = row.get('name')
    out['instance_id'] = iid
    out['running'] = st.get('running')
    out['status'] = st.get('status')
    return out


# ---------------------------------------------------------------- Java
@router.get('/java')
def java_list(user: dict = Depends(get_current_user)):
    return {'ok': True, 'detected': javaruntime.detect_all(),
            'installed': javaruntime.list_installed(), 'java_dir': JAVA_DIR}


@router.get('/java/adoptium')
def java_adoptium(user: dict = Depends(get_current_user)):
    return javaruntime.adoptium_available()


@router.get('/java/majors')
def java_majors(user: dict = Depends(get_current_user)):
    """可选大版本（面板推荐 17/21 + Adoptium 实际可装的全量）。"""
    return javaruntime.list_majors()


@router.get('/java/sources')
def java_sources(major: int = Query(default=21, ge=8, le=30),
                 user: dict = Depends(get_current_user)):
    """实测 Adoptium / Microsoft 两个 Java 源（含是否提供 sha256）。"""
    return javaruntime.probe_sources(int(major))


@router.post('/java/preview')
def java_preview(body: JavaPreviewIn, user: dict = Depends(get_current_user)):
    """渲染实例最终启动命令行（内存 / JVM 参数 / nogui），给用户看。"""
    row = {}
    if body.instance_id:
        row = dict(_inst_row(int(body.instance_id), user))
    for key in ('memory_mb', 'java_path', 'extra_jvm_args', 'extra_server_args',
                'nogui', 'jar_path'):
        val = getattr(body, key, None)
        if val is not None:
            row[key] = val
    return javaruntime.render_command(row)


@router.post('/java/install')
def java_install(body: JavaInstallIn, request: Request, user: dict = Depends(get_current_user)):
    # Java 运行时是**全机共享资源**（装在面板 java/ 目录，所有实例共用）→ 只有超管能装
    require_perm(user, perms.P_SYSTEM_SETTINGS)
    if int(body.major) not in (8, 11, 16, 17, 21, 22, 23, 24, 25, 26):
        raise HTTPException(status_code=400, detail='仅支持 8/11/16/17/21~26')
    if body.source not in javaruntime.JAVA_SOURCES:
        raise HTTPException(status_code=400,
                            detail='source 只能是 auto/adoptium/microsoft')
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'java.install', detail=f'下载安装 Java {body.major}'
                                           f'（{body.image}/{body.source}）',
                    level='warn')
    r = javaruntime.install(int(body.major), body.image, body.source)
    if not r.get('ok'):
        raise HTTPException(status_code=502, detail=r.get('error', '安装失败'))
    return r


@router.post('/java/test')
def java_test(body: JavaTestIn, user: dict = Depends(get_current_user)):
    require_perm(user, perms.P_SYSTEM_SETTINGS)
    return javaruntime.probe_java(body.path)


@router.delete('/java/{major}')
def java_delete(major: int, request: Request, user: dict = Depends(get_current_user)):
    import shutil
    require_perm(user, perms.P_SYSTEM_SETTINGS)
    d = os.path.join(JAVA_DIR, str(int(major)))
    if not os.path.isdir(d):
        raise HTTPException(status_code=404, detail='未安装该版本')
    in_use = query('SELECT id,name FROM instances WHERE java_path LIKE ?', (d + '%',))
    if in_use:
        raise HTTPException(status_code=409,
                            detail=f'仍有实例使用该 Java：{", ".join(i["name"] for i in in_use)}')
    shutil.rmtree(d, ignore_errors=True)
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'java.delete', detail=f'删除 Java {major}')
    return {'ok': True}
