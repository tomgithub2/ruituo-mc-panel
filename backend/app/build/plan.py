"""预检报告（规格 §5）：不落盘、不改动世界，只回答「能不能导、会覆盖什么、多少映射不上」。

字段与规格 §5 的 JSON 一一对应，另外附加若干调试/前端友好的字段（不影响契约）。
"""
import os

from . import anvil, formats, mapping

MIN_Y = {k: v['min_y'] for k, v in anvil.DIMENSIONS.items()}
HEIGHT = {k: v['height'] for k, v in anvil.DIMENSIONS.items()}
MAX_Y = {k: MIN_Y[k] + HEIGHT[k] - 1 for k in MIN_Y}


class PreviewError(Exception):
    """预检拒绝（消息面向用户，中文）。"""


def overall_bbox(sch: formats.Schematic, origin):
    """把所有 region 按各自 origin 平移到统一坐标，返回 ``(bbox, offsets)``。"""
    ox, oy, oz = origin
    base = sch.bbox_origin()
    xs, ys, zs = [], [], []
    offsets = []
    for fr in sch.regions:
        dx = fr.origin[0] - base[0]
        dy = fr.origin[1] - base[1]
        dz = fr.origin[2] - base[2]
        offsets.append((dx, dy, dz))
        xs += [ox + dx, ox + dx + fr.size[0] - 1]
        ys += [oy + dy, oy + dy + fr.size[1] - 1]
        zs += [oz + dz, oz + dz + fr.size[2] - 1]
    if not xs:
        return {'min': [ox, oy, oz], 'max': [ox, oy, oz]}, []
    return ({'min': [min(xs), min(ys), min(zs)], 'max': [max(xs), max(ys), max(zs)]}, offsets)


def iter_blocks(sch: formats.Schematic, origin):
    """产出 ``(frame, block, 世界 x, 世界 y, 世界 z, PaletteEntry)``。"""
    ox, oy, oz = origin
    base = sch.bbox_origin()
    for fr in sch.regions:
        dx = ox + fr.origin[0] - base[0]
        dy = oy + fr.origin[1] - base[1]
        dz = oz + fr.origin[2] - base[2]
        pal = fr.palette
        for b in fr.blocks:
            if 0 <= b.state < len(pal):
                yield fr, b, dx + b.x, dy + b.y, dz + b.z, pal[b.state]


def _is_air(name) -> bool:
    n = str(name or '')
    return (not n) or n.endswith(':air') or n in ('minecraft:air', 'minecraft:cave_air',
                                                  'minecraft:void_air')


def _place_decider(place_mode: str, replace_list):
    repl = {mapping.normalize_name(x) for x in (replace_list or []) if str(x).strip()}

    def decide(old_name, new_name):
        if _is_air(new_name):
            return False
        if place_mode == 'replace_all':
            return True
        if place_mode == 'replace_list':
            return bool(old_name) and mapping.normalize_name(old_name) in repl
        return old_name is None or _is_air(old_name)
    return decide


def analyze(sch: formats.Schematic, origin, instance: dict, dimension: str = 'overworld',
            place_mode: str = 'only_air', replace_list=None, include_entities: bool = False,
            mc_version: str = '', engine: str = 'auto', extra_warnings=None) -> dict:
    """生成 §5 预检报告。前置条件不满足时抛 :class:`PreviewError`。"""
    if dimension not in anvil.DIMENSIONS:
        raise PreviewError(f'未知维度 {dimension}（只能是 overworld / nether / end）')
    if not sch.regions:
        raise PreviewError('建筑文件里没有可导入的区域')
    ox, oy, oz = (int(origin[0]), int(origin[1]), int(origin[2]))
    bbox, _offsets = overall_bbox(sch, (ox, oy, oz))
    size = sch.size()
    warnings = list(sch.warnings)

    # ---------------- 边界保护（§4）
    min_y, max_y = MIN_Y[dimension], MAX_Y[dimension]
    if bbox['min'][1] < min_y:
        raise PreviewError(f'目标 Y={bbox["min"][1]} 低于 {dimension} 世界下限 {min_y}，'
                           f'已拒绝（请把 y 调整到 {min_y} 以上）')
    if bbox['max'][1] > max_y:
        raise PreviewError(f'建筑顶部 Y={bbox["max"][1]} 超过 {dimension} 世界上限 {max_y}，'
                           f'已拒绝（建筑高 {size[1]}，请下移）')
    for i, ax in enumerate('xyz'):
        if abs(bbox['min'][i]) > 30_000_000 or abs(bbox['max'][i]) > 30_000_000:
            raise PreviewError(f'{ax} 坐标超出世界边界（±30000000），已拒绝')

    # ---------------- 映射
    version = str(mc_version or instance.get('mc_version') or '').strip()
    reg = mapping.registry_for(version, instance,
                               allow_generate=bool(instance),
                               allow_world_fallback=bool(instance))
    if reg.sparse:
        warnings.append('方块注册表来自世界存档调色板（稀疏回退）：只能识别世界里出现过的方块状态')
    elif not reg.blocks:
        warnings.append(f'未能取得 {version or "目标版本"} 的方块状态注册表，'
                        f'所有非空气方块都会列为未映射')

    pal_total = 0
    unmapped_blocks = {}          # state 串 → 出现次数（方块实例数）
    unmapped_states = []          # 调色板里出现但映射不上的状态
    state_to_sid = {}
    resolve_notes = []
    seen = set()
    for fr in sch.regions:
        for entry in fr.palette:
            key = entry.state_string()
            if key in seen:
                continue
            seen.add(key)
            pal_total += 1
            if _is_air(entry.name):
                state_to_sid[key] = 0
                continue
            sid, notes = reg.resolve(entry.name, entry.properties)
            if sid is None or sid < 0:
                unmapped_states.append(key)
                state_to_sid[key] = None
            else:
                state_to_sid[key] = sid
                for n in notes:
                    if n not in resolve_notes:
                        resolve_notes.append(n)
    if resolve_notes:
        warnings.extend(resolve_notes[:6])

    # ---------------- 逐方块真实统计
    world_root = anvil.world_dir(instance_dir_of(instance), dimension)
    editors = {}
    decide = _place_decider(place_mode, replace_list)
    blocks_total = 0
    blocks_affected = 0
    blocks_overwrite = 0
    blocks_out_of_range = 0
    blocks_unmapped = 0
    block_entities = 0
    touched = set()
    overwrite_samples = []
    new_chunks = 0
    existing_chunks = 0

    for fr, b, wx, wy, wz, entry in iter_blocks(sch, (ox, oy, oz)):
        blocks_total += 1
        cx, cz = anvil.chunk_of(wx), anvil.chunk_of(wz)
        touched.add((cx, cz))
        if not (min_y <= wy <= max_y):
            blocks_out_of_range += 1
            continue
        sid = state_to_sid.get(entry.state_string())
        if sid is None:
            blocks_unmapped += 1
            key = entry.state_string()
            unmapped_blocks[key] = unmapped_blocks.get(key, 0) + 1
            continue
        if _is_air(entry.name):
            continue
        if b.nbt is not None:
            block_entities += 1
        ed = editors.get((cx, cz))
        if ed is None:
            ed, created = _open_editor(world_root, cx, cz, dimension, instance)
            editors[(cx, cz)] = ed
            if created:
                new_chunks += 1
            else:
                existing_chunks += 1
        lx, lz = anvil.local_in_chunk(wx), anvil.local_in_chunk(wz)
        old = ed.get_block(lx, wy, lz)
        old_name = (old or {}).get('Name') if old else None
        if not decide(old_name, entry.name):
            continue
        blocks_affected += 1
        if old_name and not _is_air(old_name):
            blocks_overwrite += 1
            if len(overwrite_samples) < 8:
                overwrite_samples.append({'pos': [wx, wy, wz], 'block': old_name})
    editors.clear()

    # ---------------- 引擎
    eng = engines_available(instance, dimension, mc_version=version)
    recommended = eng['recommended']
    if engine and engine != 'auto':
        if engine in eng['available']:
            recommended = engine
        else:
            warnings.append(f'指定引擎 {engine} 当前不可用，已回退到 {recommended}')

    if blocks_overwrite:
        warnings.append(f'目标区域内有 {blocks_overwrite} 个已有方块将被覆盖')
    if blocks_unmapped:
        warnings.append(f'{blocks_unmapped} 个方块（{len(unmapped_states)} 种状态）在 '
                        f'{version or "目标版本"} 找不到对应状态，导入时会跳过')
    if place_mode == 'only_air':
        warnings.append('place_mode=only_air：只填充空气，已有方块不会被覆盖')
    if include_entities:
        warnings.append('include_entities=true：方块实体（箱子/告示牌等）会一并写入')
    if blocks_out_of_range:
        warnings.append(f'{blocks_out_of_range} 个方块超出世界高度，已跳过')
    if new_chunks:
        warnings.append(f'其中 {new_chunks} 个区块世界里还不存在，导入时会新建区块文件')
    if extra_warnings:
        warnings.extend(extra_warnings)
    if blocks_affected == 0 and not blocks_unmapped:
        warnings.append('根据当前 place_mode，该位置没有任何方块会被写入')

    unmapped_palette = []
    for key in unmapped_states:
        unmapped_palette.append({'name': key,
                                 'count': unmapped_blocks.get(key, 0)})
    unmapped_palette.sort(key=lambda x: -x['count'])

    return {
        'ok': True,
        'format': sch.format,
        'format_version': sch.version,
        'source_data_version': sch.data_version,
        'target_mc_version': version,
        'size': {'x': size[0], 'y': size[1], 'z': size[2]},
        'blocks_total': blocks_total,
        'blocks_affected': blocks_affected,
        'dimension': dimension,
        'origin': [ox, oy, oz],
        'bbox': bbox,
        'palette_total': pal_total,
        'palette_unmapped': unmapped_palette,
        'chunks_touched': len(touched),
        'chunks': [[c[0], c[1]] for c in sorted(touched)[:64]],
        'engine_available': eng['available'],
        'engine_recommended': recommended,
        'warnings': warnings,
        'requires_backup': bool(blocks_affected),
        # ---- 附加字段（前端/排查用，非契约要求）
        'place_mode': place_mode,
        'engine_detail': eng['detail'],
        'blocks_overwrite': blocks_overwrite,
        'blocks_out_of_range': blocks_out_of_range,
        'blocks_unmapped': blocks_unmapped,
        'block_entities': block_entities,
        'chunks_new': new_chunks,
        'chunks_existing': existing_chunks,
        'world_dir': world_root,
        'world_ready': os.path.isdir(os.path.join(world_root, 'region')),
        'registry': {'source': reg.source, 'blocks': len(reg.blocks), 'sparse': reg.sparse,
                     'version': reg.version},
        'overwrite_samples': overwrite_samples,
        'unsupported': sch.unsupported,
    }


def instance_dir_of(instance: dict) -> str:
    try:
        from ..config import instance_dir
        return instance_dir(instance)
    except Exception:
        return (instance or {}).get('dir') or ''


def _open_editor(world_root, cx, cz, dimension, instance):
    """返回 ``(ChunkEditor, 是否新建)``；区块不存在时新建一个全空气区块。"""
    path = anvil.region_path(world_root, dimension, cx * 16, cz * 16)
    chunk = None
    if os.path.isfile(path):
        try:
            with anvil.RegionFile(path) as rf:
                chunk = rf.read_chunk(cx, cz)
        except Exception:
            chunk = None
    if chunk is None:
        dv = anvil.level_dat_data_version(world_root) or _guess_data_version(instance)
        chunk = anvil.new_chunk(cx, cz, dv, dimension)
        ed = anvil.ChunkEditor(chunk, dimension)
        ed.fill_all_air()
        return ed, True
    return anvil.ChunkEditor(chunk, dimension), False


def _guess_data_version(instance: dict) -> int:
    known = {
        '1.21.4': 4189, '1.21.3': 4082, '1.21.1': 3955, '1.21': 3953, '1.20.6': 3839,
        '1.20.4': 3700, '1.20.1': 3465, '1.19.4': 3337, '1.18.2': 2975, '1.17.1': 2730,
        '1.16.5': 2586, '1.12.2': 1343,
    }
    return known.get(str((instance or {}).get('mc_version') or ''), 4189)


# ---------------------------------------------------------------- 引擎可用性

def engines_available(instance: dict, dimension: str = 'overworld', mc_version: str = '') -> dict:
    """返回 ``{'available': [...], 'recommended': str, 'detail': {...}}``。"""
    from ..process_manager import manager
    iid = int((instance or {}).get('id') or 0)
    running = False
    if iid:
        try:
            running = bool(manager.status(iid).get('running'))
        except Exception:
            running = False
    plugin = 'none'
    plugin_files = []
    try:
        from .. import serverctl
        for item in serverctl.plugins(instance or {}):
            if item.get('dir') != 'plugins':
                continue
            plugin_files.append(item.get('name'))
            nm = str(item.get('name') or '').lower()
            if not item.get('enabled'):
                continue
            if nm.startswith('fastasyncworldedit') or nm.startswith('fawe'):
                plugin = 'fawe'
            elif 'worldedit' in nm and plugin != 'fawe':
                plugin = 'worldedit'
    except Exception:
        pass
    rcon_ready = bool(int((instance or {}).get('rcon_enabled') or 0)
                      and (instance or {}).get('rcon_password'))
    available = []
    detail = {
        'running': running,
        'plugin': plugin,
        'plugin_files': plugin_files,
        'rcon': rcon_ready,
    }
    if running and plugin != 'none' and rcon_ready:
        available.append('plugin')
    if not running:
        available.append('offline')
    if running and rcon_ready:
        available.append('vanilla_place')
        detail['vanilla_place_note'] = ('原版 /place template 需要目标区块已加载'
                                        '（可先 forceload add），且只接受 .nbt 结构文件；'
                                        '实测：区块未加载会返回 “That position is not loaded”')
    if not running:
        recommended = 'offline'
    elif 'plugin' in available:
        recommended = 'plugin'
    elif 'vanilla_place' in available:
        recommended = 'vanilla_place'
    else:
        recommended = 'none'
    detail['reason'] = _engine_reason(running, plugin, rcon_ready, available)
    return {'available': available, 'recommended': recommended, 'detail': detail}


def _engine_reason(running, plugin, rcon_ready, available) -> str:
    if not running:
        return '实例已停止 → 引擎 B（离线写 Anvil 区域文件），不依赖插件'
    if plugin != 'none' and rcon_ready:
        return f'实例运行中且已装插件（{plugin}）→ 引擎 A（RCON 下发 //schem load + //paste）'
    if not rcon_ready:
        # ⚠️ 别说成"只能停服"：实例不停机也有正路 —— 开启 RCON 就能走原版 /place template
        #    （引擎 C：不需要任何插件，也不需要关服）。这条提示以前漏了它，
        #    用户看到"请停止实例"就以为没有免重启的办法了。
        return ('实例运行中但未开启 RCON：两个选择 —— ① 在「实例详情 → 配置」里开启 RCON 后重试，'
                '即可用引擎 C（原版 /place template，无需插件、无需停服）；'
                '② 或停止实例走离线导入（引擎 B）')
    return ('实例运行中且未安装 WorldEdit：三个选择 —— ① 直接用引擎 C（原版 /place template，'
            '无需插件、无需停服）；② 停止实例走离线导入（引擎 B）；'
            '③ 或安装 WorldEdit 后用引擎 A')
