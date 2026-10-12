"""建筑导入 API（规格 §7）。只暴露一个 ``router``。

| 方法 | 路径 |
|---|---|
| POST | ``/api/instances/{iid}/build/upload`` |
| POST | ``/api/instances/{iid}/build/preview`` |
| POST | ``/api/instances/{iid}/build/import`` |
| GET  | ``/api/instances/{iid}/build/tasks/{task_id}`` |
| POST | ``/api/instances/{iid}/build/tasks/{task_id}/cancel`` |
| POST | ``/api/instances/{iid}/build/rollback/{task_id}`` |
| GET  | ``/api/build/formats`` |
| GET  | ``/api/instances/{iid}/build/tasks``（列表，附加） |
| GET  | ``/api/instances/{iid}/build/backups``（导入备份列表，附加） |
| GET  | ``/api/instances/{iid}/build/uploads``（已上传文件列表，附加） |
| DELETE | ``/api/instances/{iid}/build/uploads/{upload_id}``（删除上传，附加） |
"""
import hashlib
import io
import json
import os
import re
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from .. import audit as audit_mod
from ..auth import get_current_user
from ..build import anvil, engines, formats, mapping, plan, tasks
from ..config import DATA_DIR, instance_dir
from ..pathguard import assert_inside, safe_join
from .. import permissions as perms
from ..permissions import require_instance
from ..process_manager import manager

router = APIRouter(tags=['building-import'])

UPLOAD_DIR = os.path.join(DATA_DIR, 'build', 'uploads')
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
UPLOAD_KEEP_SECONDS = 7 * 86400
_ID_RE = re.compile(r'^[0-9a-f]{16,32}$')


# ---------------------------------------------------------------- 基础

def _row_or_404(iid: int, user: dict = None) -> dict:
    """取实例行 + `instance.build_import` 实例级权限校验。

    建筑导入会**直接改写世界文件**，整条链路（上传/预览/导入/任务/回滚）
    都要求属主或管理员，且拥有 `instance.build_import`；`viewer` 一律 403。
    """
    if user is None:
        raise HTTPException(status_code=401, detail='未登录')
    return require_instance(user, iid, perms.P_INSTANCE_BUILD)


def _upload_root(iid: int) -> str:
    p = os.path.join(UPLOAD_DIR, str(int(iid)))
    os.makedirs(p, exist_ok=True)
    return os.path.realpath(p)


def _safe_filename(name: str) -> str:
    name = os.path.basename(str(name or 'building.schem'))
    name = re.sub(r'[^A-Za-z0-9._\-\u4e00-\u9fff]', '_', name)[:120]
    return name or 'building.schem'


def _cleanup_uploads():
    try:
        for iid in os.listdir(UPLOAD_DIR):
            d = os.path.join(UPLOAD_DIR, iid)
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                p = os.path.join(d, fn)
                try:
                    if os.path.isfile(p) and time.time() - os.path.getmtime(p) > UPLOAD_KEEP_SECONDS:
                        os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass


def _upload_users() -> list:
    try:
        return os.listdir(UPLOAD_DIR)
    except Exception:
        return []


def _load_upload(iid: int, upload_id: str) -> dict:
    """读取上传元数据 + 文件路径（全程 pathguard 校验）。"""
    if not _ID_RE.match(str(upload_id or '')):
        raise HTTPException(status_code=400, detail='upload_id 非法')
    root = _upload_root(iid)
    meta_path = safe_join(root, f'{upload_id}.json')
    if not os.path.isfile(meta_path):
        raise HTTPException(status_code=404, detail='上传记录不存在（可能已过期清理），请重新上传')
    try:
        with io.open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail='上传元数据损坏')
    path = assert_inside(os.path.join(root, meta.get('stored') or ''), root)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='上传文件已丢失，请重新上传')
    meta['path'] = path
    meta['root'] = root
    return meta


def _save_upload(iid: int, filename: str, data: bytes) -> dict:
    root = _upload_root(iid)
    uid = uuid.uuid4().hex[:24]
    safe = _safe_filename(filename)
    stored = f'{uid}_{safe}'
    dest = safe_join(root, stored)
    tmp = dest + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, dest)
    sha = hashlib.sha256(data).hexdigest()
    meta = {'upload_id': uid, 'filename': safe, 'stored': stored, 'size': len(data),
            'sha256': sha, 'created_at': time.time(), 'instance_id': int(iid)}
    with io.open(safe_join(root, f'{uid}.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


# ---------------------------------------------------------------- 上传

@router.post('/api/instances/{iid}/build/upload')
async def upload(iid: int, request: Request, file: UploadFile = File(...),
                 path: str = Form(default=''), user: dict = Depends(get_current_user)):
    """multipart 上传（``file`` + 可选 ``path`` 保留原名）。"""
    _row_or_404(iid, user)
    _cleanup_uploads()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail='上传内容为空')
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail=f'文件超过上限（{MAX_UPLOAD_BYTES // 1048576} MiB）')
    # 配额：`allow_build_import` 开关 + 上传体积（规格 §3 逐项校验）
    perms.check_quota(user, 'build_import', amount=len(data),
                      limit=MAX_UPLOAD_BYTES // 1048576)
    perms.check_quota(user, 'upload', amount=len(data))
    name = path or file.filename or 'building.schem'
    meta = _save_upload(iid, name, data)
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'build.upload', iid,
                    f'上传建筑 {meta["filename"]}（{meta["size"]} 字节，sha256 '
                    f'{meta["sha256"][:16]}…）')
    return {'ok': True, 'upload_id': meta['upload_id'], 'filename': meta['filename'],
            'size': meta['size'], 'sha256': meta['sha256'],
            'content_type': file.content_type or '',
            'formats': _format_hint(meta['filename'])}


def _format_hint(name: str) -> dict:
    ext = os.path.splitext(str(name or ''))[1].lower()
    known = {
        '.schem': 'Sponge Schematic（v1/v2/v3）',
        '.schematic': 'MCEdit 旧格式',
        '.nbt': '原版结构文件（结构方块导出）',
        '.litematic': 'Litematica',
        '.zip': '压缩包（内部含以上任一）',
        '.bdx': '网易中国版 BDX（操作码流，含命令块，会自动转 Java 命令）',
    '.mcstructure': '基岩版（需转换，暂不支持导入）',
    }
    return {'ext': ext, 'label': known.get(ext, '未知格式'),
            'supported': ext in formats.SUPPORTED_EXTS + formats.ARCHIVE_EXTS,
            'recognized': ext in formats.RECOGNIZED_EXTS}


@router.get('/api/instances/{iid}/build/uploads')
def list_uploads(iid: int, user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    root = _upload_root(iid)
    out = []
    try:
        for fn in sorted(os.listdir(root), reverse=True):
            if not fn.endswith('.json'):
                continue
            try:
                with io.open(os.path.join(root, fn), encoding='utf-8') as f:
                    m = json.load(f)
            except Exception:
                continue
            m.pop('stored', None)
            out.append(m)
    except Exception:
        pass
    return {'ok': True, 'uploads': out}


@router.delete('/api/instances/{iid}/build/uploads/{upload_id}')
def delete_upload(iid: int, upload_id: str, request: Request,
                  user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    meta = _load_upload(iid, upload_id)
    for p in (meta['path'], safe_join(meta['root'], f'{upload_id}.json')):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'build.upload.delete', iid, f'删除上传 {meta.get("filename")}')
    return {'ok': True}


# ---------------------------------------------------------------- 预检

class PreviewIn(BaseModel):
    upload_id: str
    x: int = 0
    y: int = 64
    z: int = 0
    dimension: str = 'overworld'
    rotate: int = 0
    mirror: str = 'none'
    place_mode: str = 'only_air'
    replace_list: list = []
    include_entities: bool = False
    include_biome: bool = False
    mc_version: str = ''
    engine: str = 'auto'
    member: str = ''
    strict: bool = False
    # 命令方块里的命令如何转换（仅 .bdx 生效）：
    #   'be2java' 基岩→Java（默认，BDX 里的命令是基岩语法，导入 Java 服要转）
    #   'java2be' Java→基岩（把 Java 语法的命令改成基岩语法）
    #   'none'    不转换，原样保留
    cmd_convert: str = 'be2java'


def _load_schematic(iid: int, body) -> formats.Schematic:
    meta = _load_upload(iid, body.upload_id)
    # 命令转换方向透传到解析层（只有 .bdx 会用到；其它格式的命令按各自默认处理）
    direction = str(getattr(body, 'cmd_convert', '') or 'be2java').lower()
    if direction not in ('be2java', 'java2be', 'none'):
        direction = 'be2java'
    try:
        sch = formats.parse_file(meta['path'], member=getattr(body, 'member', '') or '',
                                 cmd_convert=direction)
    except formats.FormatError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'解析失败：{type(e).__name__}: {e}')
    sch.sha256 = meta.get('sha256')
    return sch


def _validate_common(body):
    if body.dimension not in anvil.DIMENSIONS:
        raise HTTPException(status_code=400, detail='dimension 只能是 overworld / nether / end')
    if int(body.rotate) % 90 != 0 or int(body.rotate) % 360 not in (0, 90, 180, 270):
        raise HTTPException(status_code=400, detail='rotate 只能是 0 / 90 / 180 / 270')
    if str(body.mirror).lower() not in ('none', 'x', 'z', 'xy', '', 'left_right', 'front_back'):
        raise HTTPException(status_code=400, detail='mirror 只能是 none / x / z / xy')
    if body.place_mode not in ('only_air', 'replace_all', 'replace_list'):
        raise HTTPException(status_code=400, detail='place_mode 只能是 only_air / replace_all / '
                                                   'replace_list')
    if not (-30_000_000 <= int(body.x) <= 30_000_000 and -30_000_000 <= int(body.z) <= 30_000_000):
        raise HTTPException(status_code=400, detail='坐标超出世界边界（±30000000）')


@router.post('/api/instances/{iid}/build/preview')
def preview(iid: int, body: PreviewIn, user: dict = Depends(get_current_user)):
    inst = _row_or_404(iid, user)
    _validate_common(body)
    sch = _load_schematic(iid, body)
    if (int(body.rotate) % 360) or str(body.mirror).lower() not in ('none', ''):
        sch = engines.apply_transform(sch, body.rotate, body.mirror)
    try:
        report = plan.analyze(sch, (body.x, body.y, body.z), inst, dimension=body.dimension,
                              place_mode=body.place_mode, replace_list=body.replace_list,
                              include_entities=body.include_entities,
                              mc_version=body.mc_version or inst.get('mc_version') or '',
                              engine=body.engine)
    except plan.PreviewError as e:
        raise HTTPException(status_code=400, detail=str(e))
    st = manager.status(iid)
    report['instance_running'] = bool(st.get('running'))
    report['upload'] = {'filename': getattr(sch, 'source', ''), 'member': body.member}
    if report.get('engine_recommended') == 'vanilla_place' or 'vanilla_place' in (
            report.get('engine_available') or []):
        report['vanilla_place_hint'] = _template_hint(inst, sch)
    if body.strict and report.get('blocks_unmapped'):
        report['ok'] = False
        report['warnings'] = list(report.get('warnings') or []) + [
            f'strict=true：有 {report["blocks_unmapped"]} 个方块未映射，导入会被拒绝']
    return report


def _template_hint(inst: dict, sch) -> dict:
    """原版 /place template 路径的前置提示（实测：模板只在启动时扫描）。"""
    root = instance_dir(inst)
    struct_dir = os.path.join(root, 'world', 'generated', 'minecraft', 'structures')
    exists = os.path.isdir(struct_dir)
    return {
        'structures_dir': struct_dir,
        'exists': exists,
        'note': '原版 /place template 只接受 vanilla .nbt 结构；模板只在服务端启动时扫描，'
                '因此面板会在导入时把结构写进上面的目录，然后需要重启一次实例（或用数据包 '
                '+ /reload）。目标区块必须先 forceload；<pos> 是最小角且会无条件覆盖已有方块。',
        'size': list(sch.size()),
    }


# ---------------------------------------------------------------- 导入

class ImportIn(PreviewIn):
    backup: bool = True
    allow_empty: bool = False
    abort_on_unmapped: bool = False
    allow_missing_chunks: bool = False


@router.post('/api/instances/{iid}/build/import')
def do_import(iid: int, body: ImportIn, request: Request,
              user: dict = Depends(get_current_user)):
    inst = _row_or_404(iid, user)
    _validate_common(body)
    busy = tasks.instance_busy(iid)
    if busy is not None:
        raise HTTPException(status_code=409,
                            detail=f'该实例已有导入任务 {busy.id} 正在进行'
                                   f'（{busy.stage}），请等待其结束或先取消')
    sch = _load_schematic(iid, body)
    task = tasks.BuildTask(
        tasks.new_id(), iid, username=user.get('username', ''),
        ip=request.client.host if request.client else '',
        filename=os.path.basename(getattr(sch, 'source', '') or 'building'),
        sha256=getattr(sch, 'sha256', '') or '',
        upload_id=body.upload_id, origin=(body.x, body.y, body.z), dimension=body.dimension,
        rotate=body.rotate, mirror=body.mirror, place_mode=body.place_mode,
        replace_list=body.replace_list, include_entities=body.include_entities,
        engine=body.engine, backup=body.backup, member=body.member)
    task.result['upload_path'] = _load_upload(iid, body.upload_id)['path']
    task.result['allow_empty'] = bool(body.allow_empty)
    task.result['abort_on_unmapped'] = bool(body.abort_on_unmapped)
    task.result['allow_missing_chunks'] = bool(body.allow_missing_chunks)
    task.result['requested'] = {
        'x': body.x, 'y': body.y, 'z': body.z, 'dimension': body.dimension,
        'rotate': body.rotate, 'mirror': body.mirror, 'place_mode': body.place_mode,
        'replace_list': list(body.replace_list or []),
        'include_entities': bool(body.include_entities),
        'include_biome': bool(body.include_biome), 'engine': body.engine,
        'backup': bool(body.backup), 'member': body.member,
        # 记录用户选的命令转换方向（BDX）：be2java / java2be / none
        'cmd_convert': str(getattr(body, 'cmd_convert', 'be2java')),
    }
    _DIR_LABEL = {'be2java': '基岩→Java', 'java2be': 'Java→基岩', 'none': '不转换'}
    _dirv = str(getattr(body, 'cmd_convert', 'be2java'))
    _dirtext = _DIR_LABEL.get(_dirv, _dirv)
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'build.import.request', iid,
                    f'请求导入 {task.filename} → 维度 {body.dimension} 坐标 '
                    f'({body.x},{body.y},{body.z}) 引擎 {body.engine} 备份 {body.backup}'
                    f' 命令转换 {_dirtext}',
                    level='warn')
    tasks.start(task)
    return {'ok': True, 'task_id': task.id, 'status': task.status,
            'instance_running': bool(manager.status(iid).get('running')),
            'note': '导入为异步任务，轮询 GET /api/instances/%d/build/tasks/%s'
                    % (iid, task.id)}


# ---------------------------------------------------------------- 任务

@router.get('/api/instances/{iid}/build/tasks')
def list_tasks(iid: int, limit: int = Query(default=30, ge=1, le=200),
               user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    return {'ok': True, 'tasks': tasks.list_tasks(iid, limit)}


@router.get('/api/instances/{iid}/build/tasks/{task_id}')
def get_task(iid: int, task_id: str, user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    t = tasks.get(task_id)
    if not t or int(t.instance_id) != int(iid):
        raise HTTPException(status_code=404, detail='任务不存在')
    d = t.to_dict()
    d['ok'] = True
    return d


@router.post('/api/instances/{iid}/build/tasks/{task_id}/cancel')
def cancel_task(iid: int, task_id: str, request: Request,
                user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    t = tasks.get(task_id)
    if not t or int(t.instance_id) != int(iid):
        raise HTTPException(status_code=404, detail='任务不存在')
    r = tasks.cancel(task_id)
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('error'))
    return r


@router.post('/api/instances/{iid}/build/rollback/{task_id}')
def rollback(iid: int, task_id: str, request: Request,
             user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    t = tasks.get(task_id)
    if not t or int(t.instance_id) != int(iid):
        raise HTTPException(status_code=404, detail='任务不存在')
    if manager.status(iid).get('running'):
        raise HTTPException(status_code=409,
                            detail='实例正在运行：请先停止实例再回滚，否则服务端保存时会覆盖还原结果')
    r = tasks.rollback(t, by=user.get('username', ''),
                       ip=request.client.host if request.client else '')
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('error') or r.get('note'))
    return r


@router.get('/api/instances/{iid}/build/backups')
def list_backups(iid: int, user: dict = Depends(get_current_user)):
    _row_or_404(iid, user)
    return {'ok': True, 'backups': tasks.list_backups(iid)}


# ---------------------------------------------------------------- 格式说明

@router.get('/api/build/formats')
def formats_info(user: dict = Depends(get_current_user)):
    return {
        'ok': True,
        'max_upload_bytes': MAX_UPLOAD_BYTES,
        'formats': [
            {'ext': '.schem', 'label': 'Sponge Schematic', 'versions': 'v1 / v2 / v3',
             'supported': True,
             'note': 'WorldEdit / FAWE 通用；v1/v2 是扁平结构，v3 多一层 Schematic 包裹'},
            {'ext': '.schematic', 'label': 'MCEdit 旧格式', 'versions': 'legacy',
             'supported': True,
             'note': '数字 id + 数据值按 1.12 语义映射到现代方块状态，复杂方块可能不精确'},
            {'ext': '.nbt', 'label': '原版结构文件（结构方块导出）', 'versions': '1.13+',
             'supported': True, 'note': '无跨版本调色板问题；也是原版 /place template 的输入'},
            {'ext': '.litematic', 'label': 'Litematica', 'versions': 'v1-v4', 'supported': True,
             'note': '可含多区域；多区域会合并为一个建筑（按各区域 Position 摆放）'},
            {'ext': '.bdx', 'label': '网易中国版 BDX', 'versions': '-', 'supported': True,
     'note': '操作码流格式；命令块里的基岩命令会自动转换成 Java 命令'},
    {'ext': '.zip', 'label': '压缩包', 'versions': '-', 'supported': True,
             'note': '内部含以上任一格式；默认取第一个可解析成员，可用 member 指定'},
            {'ext': '.mcstructure', 'label': '基岩版结构', 'versions': '-', 'supported': False,
             'note': '需先转换为 Java 版结构，本版本只识别并提示'},
        ],
        'limits': {
            'zip_members': formats.MAX_ZIP_MEMBERS,
            'zip_member_bytes': formats.MAX_ZIP_MEMBER_BYTES,
            'zip_total_bytes': formats.MAX_ZIP_TOTAL_BYTES,
            'zip_ratio': formats.MAX_ZIP_RATIO,
            'blocks': formats.MAX_BLOCKS,
            'nbt_depth': 64,
            'nbt_array_items': 1 << 24,
            'decompressed_bytes': 512 * 1024 * 1024,
        },
        # BDX 命令块里的命令如何转换（前端据此渲染选项，别在前端硬编码）
        'cmd_convert': {
            'default': 'be2java',
            'applies_to': ['.bdx'],
            'options': [
                {'id': 'be2java', 'label': '基岩 → Java（推荐）',
                 'note': 'BDX 里存的是基岩版命令语法，导入 Java 服需要转换'},
                {'id': 'java2be', 'label': 'Java → 基岩',
                 'note': '把已是 Java 语法的命令改回基岩语法'},
                {'id': 'none', 'label': '不转换',
                 'note': '原样保留命令块内容'},
            ],
        },
        'engines': [
            {'id': 'offline', 'label': '离线写入 Anvil（引擎 B）',
             'requires': '实例已停止', 'note': '不依赖插件，直接读写 region 文件；强制备份 + 可回滚'},
            {'id': 'vanilla_place', 'label': '原版 /place template（引擎 A）',
             'requires': '实例运行中 + 已开 RCON + 结构已就位',
             'note': '零插件零机器人；模板只在启动时扫描，需要重启一次实例（或数据包 + /reload）；'
                     '目标区块必须先 forceload；<pos> 是最小角且会无条件覆盖'},
            {'id': 'plugin', 'label': 'WorldEdit/FAWE + RCON',
             'requires': '插件 + RCON', 'available': False,
             'note': '**实测不可用**：Paper 1.21.4 下 WorldEdit 7.4.x / FAWE 2.15.4 的控制台'
                     '执行 // 命令返回 “Unknown or incomplete command”，且 //paste 需要 '
                     'Locatable actor。仅保留接口，不再作为可用引擎。'},
        ],
        'security': [
            'zip：成员数 / 单成员 / 总解压体积 / 压缩比 全部设上限，拒绝绝对路径、.. 穿越与符号链接',
            'NBT：嵌套深度、集合与数组长度、解压后体积都有硬上限',
            '解析在独立线程执行并带超时；单实例同时只允许一个导入任务',
            '写入前强制备份受影响的 region 文件；所有写操作审计 level=warn',
        ],
    }


# ---------------------------------------------------------------- 未映射诊断

@router.get('/api/instances/{iid}/build/registry')
def registry_info(iid: int, mc_version: str = '', user: dict = Depends(get_current_user)):
    """查看该实例目标版本的方块注册表来源（排查未映射用）。"""
    inst = _row_or_404(iid, user)
    ver = mc_version or inst.get('mc_version') or ''
    reg = mapping.registry_for(ver, inst)
    return {'ok': True, 'version': reg.version, 'source': reg.source, 'sparse': reg.sparse,
            'blocks': len(reg.blocks), 'available': bool(reg.blocks),
            'detail': mapping.registry_available(ver, inst),
            'note': '注册表来源：缓存 → 实例内 reports → 用实例 jar 现场生成 → 世界调色板稀疏回退'}
