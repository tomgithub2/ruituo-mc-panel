"""引擎调度与写入实现。

三套执行路径（规格 §3 两套 + 实测补的原版路径）：
  · ``offline``       引擎 B：实例停止时直接读写 Anvil region 文件（默认首选）
  · ``plugin``        引擎 A：WorldEdit/FAWE + RCON（实例运行中）
  · ``vanilla_place`` 原版 ``/place template`` + RCON（需要目标区块已加载；实测见汇报）

旋转/镜像采用 Minecraft 自身的 =Y 轴变换语义（与 WorldEdit `//rotate` `//flip` 等价）：
  90° 顺时针：``(x, z) → (z, -x)``（先按顺时针看下去，Y 轴不变），同时改 facing/axis 等属性。
"""
import os
import re
import shutil
import time

from . import anvil, formats, mapping, plan, tasks
from .formats import Block, Frame, PaletteEntry, Schematic
from .nbt import Tag, cget, cget_int, cget_list, cget_str, compress

try:
    from .. import serverctl
    from ..config import get_config, instance_dir
    from ..process_manager import manager
    from ..rcon import try_command
except Exception:                                     # pragma: no cover
    serverctl = None
    manager = None

    def get_config():
        return {}

    def instance_dir(row):
        return (row or {}).get('dir') or ''

    def try_command(*a, **k):
        return False, 'rcon 不可用'

# 需要随旋转改变的属性
_FACING_CW = {'north': 'east', 'east': 'south', 'south': 'west', 'west': 'north',
              'up': 'up', 'down': 'down'}
_FACING_CCW = {v: k for k, v in _FACING_CW.items()}
_RAIL_SHAPE_CW = {
    'north_south': 'east_west', 'east_west': 'north_south',
    'ascending_north': 'ascending_east', 'ascending_east': 'ascending_south',
    'ascending_south': 'ascending_west', 'ascending_west': 'ascending_north',
    'south_east': 'south_west', 'south_west': 'north_west',
    'north_west': 'north_east', 'north_east': 'south_east',
}
_STAIR_SHAPE_CW = {
    'straight': 'straight',
    'inner_left': 'inner_left', 'inner_right': 'inner_right',
    'outer_left': 'outer_left', 'outer_right': 'outer_right',
}


# ---------------------------------------------------------------- 变换

def rotate_point(x, z, size_x, size_z, rotate: int):
    """把 (x,z) 旋转 ``rotate`` 度（顺时针），返回新坐标与新的尺寸。"""
    r = int(rotate) % 360
    if r == 90:
        return z, size_x - 1 - x, size_z, size_x
    if r == 180:
        return size_x - 1 - x, size_z - 1 - z, size_x, size_z
    if r == 270:
        return size_z - 1 - z, x, size_z, size_x
    return x, z, size_x, size_z


def mirror_point(x, z, size_x, size_z, mirror: str):
    m = (mirror or 'none').lower()
    if m in ('x', 'left_right', 'left-right'):
        return size_x - 1 - x, z, size_x, size_z
    if m in ('z', 'front_back', 'front-back'):
        return x, size_z - 1 - z, size_x, size_z
    if m in ('xy', 'both'):
        return size_x - 1 - x, size_z - 1 - z, size_x, size_z
    return x, z, size_x, size_z


def transform_props(props: dict, rotate: int, mirror: str) -> dict:
    """按 Minecraft 的世界变换规则改方块状态属性。"""
    r = int(rotate) % 360
    m = (mirror or 'none').lower()
    out = dict(props or {})
    flip_x = m in ('x', 'left_right', 'left-right', 'xy', 'both')
    flip_z = m in ('z', 'front_back', 'front-back', 'xy', 'both')
    # 1) 旋转
    if r in (90, 180, 270):
        steps = {90: 1, 180: 2, 270: 3}[r]
        if 'facing' in out:
            f = out['facing']
            for _ in range(steps):
                f = _FACING_CW.get(f, f)
            out['facing'] = f
        if 'axis' in out and out['axis'] in ('x', 'z'):
            out['axis'] = 'z' if out['axis'] == 'x' else 'x'
        if 'rotation' in out:
            try:
                v = int(float(out['rotation']))
                out['rotation'] = str((v + steps * 4) % 16)
            except Exception:
                pass
        if 'shape' in out:
            out['shape'] = _RAIL_SHAPE_CW.get(out['shape'], out['shape'])
        if 'hinge' in out and steps % 2 == 1:
            out['hinge'] = 'right' if out['hinge'] == 'left' else 'left'
        if 'orientation' in out and steps % 2 == 1:
            pass
    # 2) 镜像（先 X 后 Z，方便组合）
    if flip_x:
        if 'facing' in out:
            out['facing'] = {'east': 'west', 'west': 'east'}.get(out['facing'], out['facing'])
        if 'rotation' in out:
            try:
                out['rotation'] = str((16 - int(float(out['rotation']))) % 16)
            except Exception:
                pass
        if 'hinge' in out:
            out['hinge'] = 'right' if out['hinge'] == 'left' else 'left'
        if 'shape' in out:
            out['shape'] = {'north_east': 'north_west', 'north_west': 'north_east',
                            'south_east': 'south_west', 'south_west': 'south_east'}.get(
                out['shape'], out['shape'])
        # 楼梯的 inner/outer 会互换
        if 'shape' in out:
            out['shape'] = {'inner_left': 'inner_right', 'inner_right': 'inner_left',
                            'outer_left': 'outer_right', 'outer_right': 'outer_left'}.get(
                out['shape'], out['shape'])
    if flip_z:
        if 'facing' in out:
            out['facing'] = {'north': 'south', 'south': 'north'}.get(out['facing'],
                                                                     out['facing'])
        if 'rotation' in out:
            try:
                out['rotation'] = str((8 - int(float(out['rotation']))) % 16)
            except Exception:
                pass
        if 'shape' in out:
            out['shape'] = {'north_east': 'south_east', 'south_east': 'north_east',
                            'north_west': 'south_west', 'south_west': 'north_west'}.get(
                out['shape'], out['shape'])
        if 'shape' in out:
            out['shape'] = {'inner_left': 'inner_right', 'inner_right': 'inner_left',
                            'outer_left': 'outer_right', 'outer_right': 'outer_left'}.get(
                out['shape'], out['shape'])
    return out


def apply_transform(sch: Schematic, rotate: int, mirror: str) -> Schematic:
    """对整份 Schematic 施加旋转/镜像，返回新的 Schematic（尺寸与坐标都更新）。"""
    if (int(rotate) % 360) == 0 and (mirror or 'none').lower() in ('none', ''):
        return sch
    out = Schematic(sch.format, [], sch.data_version, sch.version, source=sch.source)
    out.warnings = list(sch.warnings)
    out.unsupported = list(sch.unsupported)
    for fr in sch.regions:
        sx, sy, sz = fr.size
        # 先旋转再镜像，尺寸按顺序推
        new = Frame((sx, sy, sz), name=fr.name, origin=fr.origin)
        pal_map = {}
        for entry in fr.palette:
            props = transform_props(entry.properties, rotate, mirror)
            ne = PaletteEntry(entry.name, props)
            pal_map[entry.state_string()] = new.add_palette(ne)
        for b in fr.blocks:
            x, z, nx, nz = rotate_point(b.x, b.z, sx, sz, rotate)
            x, z, nx, nz = mirror_point(x, z, nx, nz, mirror)
            si = 0
            if 0 <= b.state < len(fr.palette):
                key = fr.palette[b.state].state_string()
                si = pal_map.get(key, 0)
            nb = Block(x, b.y, z, si, b.nbt)
            if nb.nbt is not None:
                nb.nbt = Tag.compound(dict(nb.nbt.value))
                nb.nbt.value['x'] = Tag.int(x)
                nb.nbt.value['z'] = Tag.int(z)
            new.blocks.append(nb)
        new.size = (nx, sy, nz)
        out.regions.append(new)
    return out


# ---------------------------------------------------------------- 主流程

def run_import(task, progress, check_cancel):
    """执行一次导入（由 :mod:`tasks` 的 worker 线程调用）。失败抛异常。"""
    progress(task, stage=tasks.ST_PARSING, message='解析建筑文件')
    inst = _instance_row(task.instance_id)
    if not inst:
        raise RuntimeError('实例不存在')
    path = task.result.get('upload_path') or task.result.get('path')
    if not path or not os.path.isfile(path):
        raise formats.FormatError('上传的建筑文件不存在或已被清理')

    limits = _limits_for(task)
    sch = formats.parse_file(path, limits, member=task.member)
    total_blocks = sch.blocks_total
    progress(task, stage=tasks.ST_PARSING, total=total_blocks, done=0,
             message=f'已解析 {sch.format}：{sch.size()[0]}×{sch.size()[1]}×{sch.size()[2]}，'
                     f'{total_blocks} 个方块')
    check_cancel(task)

    if (int(task.rotate) % 360) or (str(task.mirror or 'none').lower() not in ('none', '')):
        sch = apply_transform(sch, task.rotate, task.mirror)
        progress(task, message=f'已应用 rotate={task.rotate} mirror={task.mirror}，'
                               f'新尺寸 {sch.size()}')

    progress(task, stage=tasks.ST_MAPPING, message='映射方块状态到目标版本')
    check_cancel(task)
    origin = task.origin
    report = plan.analyze(sch, origin, inst, dimension=task.dimension,
                          place_mode=task.place_mode, replace_list=task.replace_list,
                          include_entities=task.include_entities,
                          mc_version=inst.get('mc_version') or '', engine=task.engine)
    task.report = report
    # 严格模式：预检不通过（未映射 + 用户要求中止）时不允许写
    if task.result.get('abort_on_unmapped') and report.get('blocks_unmapped'):
        raise plan.PreviewError(f'有 {report["blocks_unmapped"]} 个方块未映射，'
                                f'按请求中止导入（unmapped 清单见预检报告）')
    if report.get('blocks_affected', 0) == 0 and not task.result.get('allow_empty'):
        progress(task, message='没有方块需要写入（预检 blocks_affected=0），直接完成')
        task.result.update({'blocks_written': 0, 'chunks_written': 0, 'engine': 'none',
                            'report': report})
        return task

    engine = _pick_engine(task, inst, report)
    task.result['engine'] = engine
    if engine == 'none':
        raise plan.PreviewError(report.get('engine_detail', {}).get('reason')
                                or '没有可用的导入引擎')
    progress(task, message=f'使用引擎 {engine} 写入（{report["blocks_affected"]} 个方块）')

    if engine == 'offline':
        _import_offline(task, sch, inst, report, progress, check_cancel)
    elif engine == 'plugin':
        _import_plugin(task, sch, inst, report, progress, check_cancel)
    else:
        _import_vanilla_place(task, sch, inst, report, progress, check_cancel)


def _limits_for(task):
    from .nbt import NbtLimits
    return NbtLimits()


def _instance_row(iid: int) -> dict:
    try:
        from ..database import query
        return query('SELECT * FROM instances WHERE id=?', (iid,), one=True) or {}
    except Exception:
        return {}


def _pick_engine(task, inst, report) -> str:
    if task.engine and task.engine != 'auto':
        if task.engine in (report.get('engine_available') or []):
            return task.engine
        # 允许显式指定 vanilla_place / plugin，即使预检没列出来也试一次
        if task.engine in ('offline', 'plugin', 'vanilla_place'):
            return task.engine
    return report.get('engine_recommended') or 'none'


# ---------------------------------------------------------------- 引擎 B：离线 Anvil

def _mapped_palette(reg, frame):
    """帧调色板 → [(PaletteEntry, state_id|None)]。"""
    out = []
    for entry in frame.palette:
        if str(entry.name).endswith(':air') or entry.name in ('minecraft:air',
                                                              'minecraft:cave_air',
                                                              'minecraft:void_air'):
            out.append((entry, 0))
            continue
        sid, _notes = reg.resolve(entry.name, entry.properties)
        out.append((entry, sid if (sid is not None and sid >= 0) else None))
    return out


def _import_offline(task, sch, inst, report, progress, check_cancel):
    st = manager.status(task.instance_id) if manager else {'running': False}
    if st.get('running'):
        raise RuntimeError('实例正在运行：离线写入需要先停止实例，否则服务端会把内存里的'
                           '旧区块回写覆盖我们的改动')
    world_root = anvil.world_dir(instance_dir(inst), task.dimension)
    os.makedirs(os.path.join(world_root, 'region'), exist_ok=True)
    version = inst.get('mc_version') or ''
    reg = mapping.registry_for(version, inst)
    dv = anvil.level_dat_data_version(world_root) or plan._guess_data_version(inst)

    bbox = report['bbox']
    decide = plan._place_decider(task.place_mode, task.replace_list)
    # 用与预检完全一致的坐标推导，按区块分桶（便于进度/取消/备份）
    by_chunk = {}
    for fr, b, wx, wy, wz, entry in plan.iter_blocks(sch, task.origin):
        by_chunk.setdefault((anvil.chunk_of(wx), anvil.chunk_of(wz)), []).append(
            (wx, wy, wz, fr, b, entry))
    chunks = sorted(by_chunk)
    total = len(chunks)
    del bbox

    # 世界里还不存在的区块：**绝不**自作主张写一个空区块（会留下没有地形的空洞）。
    # 先数清楚，除非用户显式允许，否则拒绝并告诉用户怎么先让服务端生成。
    missing = []
    existing = []
    for (cx, cz) in chunks:
        p = anvil.region_path(world_root, task.dimension, cx * 16, cz * 16)
        has = False
        if os.path.isfile(p):
            try:
                with anvil.RegionFile(p) as rf:
                    has = rf.has_chunk(cx, cz)
            except Exception:
                has = False
        (existing if has else missing).append((cx, cz))
    task.result['chunks_missing'] = [[c[0], c[1]] for c in missing[:64]]
    if missing and not task.result.get('allow_missing_chunks'):
        raise plan.PreviewError(
            f'目标区域里有 {len(missing)} 个区块在存档中还不存在（例如 ({missing[0][0]},'
            f'{missing[0][1]})）。离线写入不会替你生成地形，直接写会留下空洞。'
            f'请先启动实例并用 /forceload add 加载目标区域（或用 RCON 执行），'
            f'确认区块已生成后停止实例再导入；确实想强制写入空白区块时传 '
            f'allow_missing_chunks=true。')

    # -------- 备份（强制）
    region_paths = [anvil.region_path(world_root, task.dimension, cx * 16, cz * 16)
                    for cx, cz in chunks]
    created_regions = [p for p in region_paths if not os.path.isfile(p)]
    if task.do_backup:
        progress(task, stage=tasks.ST_BACKUP, total=total, done=0,
                 message=f'备份将被写入的 {len(region_paths)} 个 region 文件')
        man = tasks.backup_regions(task, region_paths, world_root)
        progress(task, message=f'备份完成：{len(man["files"])} 个文件 → {task.backup_dir}')
    else:
        progress(task, message='警告：本次导入未开启备份（backup=false）')

    # -------- 写入
    progress(task, stage=tasks.ST_WRITING, total=total, done=0,
             message=f'写入 {total} 个区块')
    written = 0
    blocks_written = 0
    blocks_skipped = 0
    samples = []
    touched_files = []
    for i, (cx, cz) in enumerate(chunks):
        check_cancel(task)
        path = anvil.region_path(world_root, task.dimension, cx * 16, cz * 16)
        created = not os.path.isfile(path)
        rf = anvil.RegionFile(path)
        try:
            chunk = rf.read_chunk(cx, cz)
            if chunk is None:
                if not task.result.get('allow_missing_chunks'):
                    raise plan.PreviewError(
                        f'区块 ({cx},{cz}) 在读的时候消失了（可能被并发改动），已中止写入')
                chunk = anvil.new_chunk(cx, cz, dv, task.dimension)
                ed = anvil.ChunkEditor(chunk, task.dimension)
                ed.fill_all_air()
            else:
                ed = anvil.ChunkEditor(chunk, task.dimension)
            for (wx, wy, wz, fr, b, entry) in by_chunk[(cx, cz)]:
                sid, _n = reg.resolve(entry.name, entry.properties)
                if sid is None or sid < 0:
                    blocks_skipped += 1
                    continue
                lx, lz = anvil.local_in_chunk(wx), anvil.local_in_chunk(wz)
                old = ed.get_block(lx, wy, lz)
                old_name = (old or {}).get('Name') if old else None
                if not decide(old_name, entry.name):
                    continue
                ed.set_block(lx, wy, lz, entry)
                blocks_written += 1
                if b.nbt is not None and task.include_entities:
                    nb = Tag.compound(dict(b.nbt.value))
                    nb.value['x'] = Tag.int(wx)
                    nb.value['y'] = Tag.int(wy)
                    nb.value['z'] = Tag.int(wz)
                    ed.set_block_entity(wx, wy, wz, nb)
                if len(samples) < 12:
                    samples.append({'pos': [wx, wy, wz], 'state': entry.state_string(),
                                    'id': sid})
            ed.renumber(cx, cz, dv)
            ed.flush()
            rf.write_chunk(cx, cz, chunk, 2)
            rf.flush()
        finally:
            rf.close()
        touched_files.append({'region': path, 'chunk': [cx, cz], 'created': created,
                              'blocks': len(by_chunk[(cx, cz)])})
        progress(task, done=i + 1, message=f'已写入区块 {i + 1}/{total} '
                                           f'({cx},{cz})，累计 {blocks_written} 个方块')

    task.result.update({
        'blocks_written': blocks_written,
        'blocks_skipped': blocks_skipped,
        'chunks_written': total,
        'created_regions': created_regions,
        'regions': [p for p in region_paths],
        'world_dir': world_root,
        'data_version': dv,
        'samples': samples,
        'note': f'离线写入完成：{blocks_written} 个方块 / {total} 个区块',
    })
    task.samples = samples


# ---------------------------------------------------------------- 引擎 A：插件 + RCON

WE_SCHEM_DIRS = ('plugins/WorldEdit/schematics', 'plugins/FastAsyncWorldEdit/schematics',
                 'plugins/worldedit/schematics', 'schematics')


def _rcon(inst, cmd, timeout=20.0):
    cfg = get_config()
    port = int(inst.get('rcon_port') or cfg.get('rcon_port') or 25575)
    pwd = inst.get('rcon_password') or cfg.get('rcon_password') or ''
    return try_command('127.0.0.1', port, pwd, cmd, timeout)


def _import_plugin(task, sch, inst, report, progress, check_cancel):
    if not int(inst.get('rcon_enabled') or 0) or not inst.get('rcon_password'):
        raise RuntimeError('实例未开启 RCON（或未设置密码），无法使用引擎 A')
    root = instance_dir(inst)
    scheme_dir = None
    for cand in WE_SCHEM_DIRS:
        p = os.path.join(root, cand.replace('/', os.sep))
        if os.path.isdir(p):
            scheme_dir = p
            break
    if scheme_dir is None:
        scheme_dir = os.path.join(root, 'plugins', 'WorldEdit', 'schematics')
        os.makedirs(scheme_dir, exist_ok=True)
    name = 'mcpanel_' + task.id[:10]
    # 先备份被触及的 region（运行中的服务端会在保存时覆盖，热备份只能尽力而为）
    if task.do_backup:
        world_root = anvil.world_dir(root, task.dimension)
        paths = [anvil.region_path(world_root, task.dimension, cx * 16, cz * 16)
                 for cx, cz in _chunks_of(report['bbox'])]
        progress(task, stage=tasks.ST_BACKUP, message='（运行中实例）尽力备份 region 文件')
        tasks.backup_regions(task, paths, world_root)
    # 写 schematic（Sponge v3）+ 把 .nbt 也放一份（原版 /place template 备用）
    schem_path = os.path.join(scheme_dir, name + '.schem')
    write_sponge_v3(sch, schem_path, version=mc_data_version(inst))
    progress(task, stage=tasks.ST_WRITING, message=f'已写入 {schem_path}')
    cmds = [
        f'//schem load {name}',
        f'//pos1 {task.origin[0]} {task.origin[1]} {task.origin[2]}',
        f'//pos2 {task.origin[0]} {task.origin[1]} {task.origin[2]}',
        '//paste -o',
    ]
    out = []
    for cmd in cmds:
        check_cancel(task)
        ok, text = _rcon(inst, cmd)
        out.append({'cmd': cmd, 'ok': ok, 'response': text})
        progress(task, message=f'RCON {cmd} → {text or "(无输出)"}')
        if not ok:
            break
    task.result.update({'blocks_written': report.get('blocks_affected', 0),
                        'chunks_written': report.get('chunks_touched', 0),
                        'rcon': out, 'schematic': schem_path,
                        'note': '引擎 A（插件 + RCON）已完成，实际落盘以服务端保存为准'})


def _chunks_of(bbox):
    seen = set()
    for x in range(bbox['min'][0], bbox['max'][0] + 1, 16):
        for z in range(bbox['min'][2], bbox['max'][2] + 1, 16):
            seen.add((anvil.chunk_of(x), anvil.chunk_of(z)))
    # 补上 bbox 的最后一个角，避免步进漏掉
    seen.add((anvil.chunk_of(bbox['max'][0]), anvil.chunk_of(bbox['max'][2])))
    return sorted(seen)


# ---------------------------------------------------------------- 原版 /place template

PLACE_ROT = {0: 'none', 90: 'clockwise_90', 180: '180', 270: 'counterclockwise_90'}
PLACE_MIRROR = {'none': 'none', 'x': 'left_right', 'z': 'front_back'}


def _import_vanilla_place(task, sch, inst, report, progress, check_cancel):
    """把 .nbt 结构放进世界目录 → RCON forceload → /place template。

    实测（1.21.4 原版服务端，无玩家在线）：
      · 目标区块未加载 → 返回 “That position is not loaded”，直接失败；
      · 先 ``forceload add`` 再 place → 成功；
      · 该命令不需要任何玩家/OP 在线，RCON（控制台）即有权限。
    """
    if not int(inst.get('rcon_enabled') or 0) or not inst.get('rcon_password'):
        raise RuntimeError('实例未开启 RCON，无法使用原版 /place template 路径')
    root = instance_dir(inst)
    world_root = anvil.world_dir(root, task.dimension)
    ns = 'mcpanel'
    name = 'p' + task.id[:10]
    # ⚠️ 关键：结构必须放进**数据包**里，不能放进 world/generated/minecraft/structures/。
    #    实测（1.21.x 原版服务端，实例全程运行中）：
    #      · 写进 generated/… 后直接 /place template → `Unknown structure`（服务端只在
    #        **启动时**扫描那个目录，运行中写进去不认，等于偷偷要求重启一次）；
    #      · 写进 `world/datapacks/<pack>/data/<ns>/structure/<name>.nbt` 再 `/reload`
    #        → `/place template <ns>:<name>` 成功，返回 `Loaded template "…" at x, y, z`
    #        （原版权威确认），**全程不需要重启服务端** —— 这才是真正的"免插件免停服"。
    dc = get_config()
    cfg_dir = str((dc or {}).get('datapack_dir') or 'mcpanel_hot').strip() or 'mcpanel_hot'
    safe_dir = ''.join(c for c in cfg_dir if c.isalnum() or c in '-_.') or 'mcpanel_hot'
    pack_dir = os.path.join(world_root, 'datapacks', safe_dir)
    ns_dir = os.path.join(pack_dir, 'data', ns, 'structure')
    os.makedirs(ns_dir, exist_ok=True)
    nbt_path = os.path.join(ns_dir, name + '.nbt')
    write_vanilla_structure(sch, nbt_path)
    meta_path = os.path.join(pack_dir, 'pack.mcmeta')
    if not os.path.isfile(meta_path):
        try:
            with open(meta_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write('{"pack":{"pack_format":48,"description":"mcpanel hot import"}}')
        except Exception:
            pass
    # 清理过期结构：这个数据包是面板专用的，只保留最近 20 个，避免无限膨胀
    try:
        olds = sorted((os.path.join(ns_dir, f) for f in os.listdir(ns_dir)
                       if f.endswith('.nbt')), key=os.path.getmtime, reverse=True)
        for old in olds[20:]:
            os.remove(old)
    except Exception:
        pass
    progress(task, stage=tasks.ST_WRITING,
             message=f'结构已写入数据包 {nbt_path}（将用 /reload 热加载）')

    if task.do_backup:
        paths = [anvil.region_path(world_root, task.dimension, cx * 16, cz * 16)
                 for cx, cz in _chunks_of(report['bbox'])]
        progress(task, stage=tasks.ST_BACKUP, message='（运行中实例）尽力备份 region 文件')
        tasks.backup_regions(task, paths, world_root)

    cx0, cz0 = anvil.chunk_of(report['bbox']['min'][0]), anvil.chunk_of(report['bbox']['min'][2])
    cx1, cz1 = anvil.chunk_of(report['bbox']['max'][0]), anvil.chunk_of(report['bbox']['max'][2])
    out = []
    # ① 先 /reload 让服务端重新扫描数据包 —— 没有这一步，刚写进去的结构不会被认到
    #    （实测：不 reload 会报 Unknown structure）。/reload 只重载数据包，不会重启服务器。
    cmds = ['reload']
    # /forceload 单次最多 256 个区块（实测："Too many chunks in the specified area
    # (maximum 256, ...)"），所以按 256 分块下发
    row = 0
    cz = cz0
    while cz <= cz1:
        span = max(1, 256 // max(1, (cx1 - cx0 + 1)))
        cz_end = min(cz1, cz + span - 1)
        cmds.append(f'forceload add {cx0 * 16} {cz * 16} {cx1 * 16 + 15} {cz_end * 16 + 15}')
        cz = cz_end + 1
        row += 1
        if row > 512:
            break
    rot = PLACE_ROT.get(int(task.rotate) % 360, 'none')
    mir = PLACE_MIRROR.get(str(task.mirror or 'none').lower(), 'none')
    # 数据包里的结构是带命名空间的（<ns>:<name>），不能只写裸名字
    place = f'place template {ns}:{name} {task.origin[0]} {task.origin[1]} {task.origin[2]} {rot}'
    if mir != 'none':
        place += f' {mir}'
    cmds.append(place)
    for cmd in cmds:
        check_cancel(task)
        ok, text = _rcon(inst, cmd, timeout=60.0)
        out.append({'cmd': cmd, 'ok': ok, 'response': text})
        progress(task, message=f'RCON {cmd} → {text or "(无输出)"}')
        if not ok:
            raise RuntimeError(f'RCON 执行失败：{cmd} → {text}')
        if cmd.startswith('place template'):
            low = (text or '').lower()
            if 'failed' in low or 'not loaded' in low or 'unknown' in low:
                raise RuntimeError(f'原版 /place template 失败：{text}'
                                   f'（目标区块未加载时请先 forceload，'
                                   f'或改用引擎 B 离线写入）')
    task.result.update({'blocks_written': report.get('blocks_affected', 0),
                        'chunks_written': report.get('chunks_touched', 0),
                        'structure_file': nbt_path, 'rcon': out,
                        'note': '原版 /place template 已执行（服务端保存后落盘）'})


# ---------------------------------------------------------------- 导出结构文件

def int_list3(vals):
    """原版结构文件的 ``size`` / ``pos`` 必须是 **TAG_List of TAG_Int**。

    实测（1.21.4 原版服务端 + RCON）：写成 TAG_Int_Array 时 `/place template` 会返回
    ``Failed to place template``（模板被解析成空），改成 list 立刻成功。
    原因：游戏侧走 NbtOps 读这两个字段，只接受 list。
    """
    return Tag.list_([Tag.int(int(v)) for v in vals], 'int')


def write_vanilla_structure(sch: Schematic, path: str):
    """把 Schematic 写成原版结构文件（.nbt，gzip）。"""
    frames = sch.regions
    if len(frames) == 1:
        fr = frames[0]
        size = fr.size
        offs = [(0, 0, 0)]
    else:
        size = sch.size()
        base = sch.bbox_origin()
        offs = [(f.origin[0] - base[0], f.origin[1] - base[1], f.origin[2] - base[2])
                for f in frames]
    pal = []
    pal_idx = {}
    blocks = []

    def pid(entry):
        key = entry.state_string()
        if key not in pal_idx:
            pal_idx[key] = len(pal)
            pal.append(entry)
        return pal_idx[key]

    for fr, (dx, dy, dz) in zip(frames, offs):
        for b in fr.blocks:
            if not (0 <= b.state < len(fr.palette)):
                continue
            entry = fr.palette[b.state]
            if plan._is_air(entry.name):
                continue
            d = {'pos': int_list3([b.x + dx, b.y + dy, b.z + dz]),
                 'state': Tag.int(pid(entry))}
            if b.nbt is not None:
                d['nbt'] = Tag.compound(dict(b.nbt.value))
            blocks.append(Tag.compound(d))
    root = Tag.compound({
        'DataVersion': Tag.int(4189),
        'size': int_list3(size),
        'palette': Tag.list_([e.to_nbt() for e in pal], 'compound'),
        'blocks': Tag.list_(blocks, 'compound'),
        'entities': Tag.list_([], 'compound'),
    })
    from . import nbt as nbtlib
    raw = nbtlib.write(root)
    with open(path, 'wb') as f:
        f.write(compress(raw, 'gzip'))
    return path


def write_sponge_v3(sch: Schematic, path: str, version: str = '', name: str = ''):
    """把 Schematic 写成 Sponge v3 .schem（WorldEdit 可直接 //schem load）。"""
    from . import nbt as nbtlib
    frames = sch.regions
    if len(frames) == 1:
        fr = frames[0]
        size = fr.size
        offs = [(0, 0, 0)]
    else:
        size = sch.size()
        base = sch.bbox_origin()
        offs = [(f.origin[0] - base[0], f.origin[1] - base[1], f.origin[2] - base[2])
                for f in frames]
    sx, sy, sz = size
    volume = sx * sy * sz
    # 调色板
    pal = [PaletteEntry('minecraft:air')]
    pal_idx = {pal[0].state_string(): 0}
    for fr in frames:
        for entry in fr.palette:
            key = entry.state_string()
            if key not in pal_idx:
                pal_idx[key] = len(pal)
                pal.append(entry)
    data = [0] * volume
    for fr, (dx, dy, dz) in zip(frames, offs):
        for b in fr.blocks:
            if not (0 <= b.state < len(fr.palette)):
                continue
            key = fr.palette[b.state].state_string()
            x, y, z = b.x + dx, b.y + dy, b.z + dz
            if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
                continue
            data[x + z * sx + y * sx * sz] = pal_idx.get(key, 0)
    # varint 编码
    out = bytearray()
    for v in data:
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
    pal_nbt = {}
    for entry in pal:
        pal_nbt[entry.state_string()] = Tag.int(pal_idx[entry.state_string()])
    block_entities = []
    for fr, (dx, dy, dz) in zip(frames, offs):
        for b in fr.blocks:
            if b.nbt is None:
                continue
            d = Tag.compound(dict(b.nbt.value))
            d.value['Pos'] = Tag.int_array([b.x + dx, b.y + dy, b.z + dz])
            d.value['Id'] = Tag.string(str(cget_str(d, 'id', 'minecraft:chest')))
            block_entities.append(d)
    container = Tag.compound({
        'Palette': Tag.compound(pal_nbt),
        'Data': Tag.byte_array(bytes(out)),
        'BlockEntities': Tag.list_(block_entities, 'compound'),
    })
    inner = Tag.compound({'Version': Tag.int(3), 'DataVersion': Tag.int(4189),
                          'Width': Tag.short(sx), 'Height': Tag.short(sy),
                          'Length': Tag.short(sz),
                          'Offset': Tag.int_array([0, 0, 0]),
                          'Blocks': container,
                          'Metadata': Tag.compound({'Name': Tag.string(name or 'mcpanel')})})
    root = Tag.compound({'Schematic': inner, 'Version': Tag.int(3), 'DataVersion': Tag.int(4189)})
    raw = nbtlib.write(root)
    with open(path, 'wb') as f:
        f.write(compress(raw, 'gzip'))
    return path


def mc_data_version(inst: dict) -> int:
    return plan._guess_data_version(inst)
