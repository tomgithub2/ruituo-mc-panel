"""建筑文件格式解析：.schem(v1/v2/v3) / .schematic(MCEdit) / .nbt(原版) / .litematic / .zip。

统一内部结构::

    Schematic:
      format        源格式名（schem / schematic / nbt / litematic / zip）
      version       格式内部版本号（尽可能填）
      data_version  源文件声明的 MC DataVersion（0 = 未知）
      regions       [Frame]
      warnings      [str]
      unsupported   [str]    识别但不支持的东西（如 .mcstructure、实体、生物群系）

    Frame:
      name, origin(源坐标偏移), size(x,y,z),
      palette       [{'name': ..., 'properties': {...}}]
      blocks        [Block]  (x,y,z 均为 frame 内相对坐标, 0-based)
      block_entities 由 blocks[].nbt 携带（合并后写入区块）

关于索引公式（都按各方权威实现来的，见汇报里的引用）：
  · Sponge / MCEdit / 原版结构: x + z*sizeX + y*sizeX*sizeZ
  · Litematica:               (y*sizeZ + z)*sizeX + x，等价于同一个公式
  · Litematica 的位打包是 **tight**（条目可跨 long 边界），Anvil 是 **aligned**（不跨），
    两者分别由 :func:`unpack_litematica` / :mod:`anvil` 处理。
"""
import json
import os
import posixpath
import zipfile

from . import nbt as nbtlib
from .nbt import (NbtError, NbtLimits, Tag, cget, cget_compound, cget_int, cget_list,
                  cget_str, unwrap)

# ---------------------------------------------------------------- 限制

MAX_BLOCKS = 6_000_000            # 单次导入方块总数上限
MAX_FRAMES = 512                  # 一个文件里的区域/建筑数量上限
MAX_PALETTE = 1 << 16             # 调色板条目上限
MAX_ZIP_MEMBERS = 512             # zip 成员数上限
MAX_ZIP_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ZIP_RATIO = 400               # 单成员压缩比上限（防 zip 炸弹）
SUPPORTED_EXTS = ('.schem', '.schematic', '.nbt', '.litematic', '.bdx')
ARCHIVE_EXTS = ('.zip',)
RECOGNIZED_EXTS = SUPPORTED_EXTS + ARCHIVE_EXTS + ('.mcstructure', '.snbt')


class FormatError(Exception):
    """建筑文件解析失败（消息面向用户，中文）。"""


# ---------------------------------------------------------------- 数据结构

class PaletteEntry:
    __slots__ = ('name', 'properties', 'raw')

    def __init__(self, name: str, properties: dict = None, raw: str = ''):
        self.name = str(name or 'minecraft:air')
        if ':' not in self.name:
            self.name = 'minecraft:' + self.name
        self.properties = dict(properties or {})
        self.raw = raw or self.name

    def state_string(self) -> str:
        if not self.properties:
            return self.name
        inner = ','.join(f'{k}={v}' for k, v in sorted(self.properties.items()))
        return f'{self.name}[{inner}]'

    def to_nbt(self) -> Tag:
        d = {'Name': Tag.string(self.name)}
        if self.properties:
            d['Properties'] = Tag.compound({k: Tag.string(v) for k, v in self.properties.items()})
        return Tag.compound(d)

    def __repr__(self):
        return f'PaletteEntry({self.state_string()})'


class Block:
    """一个方块：frame 内相对坐标 + 调色板索引 + 可选方块实体 NBT。"""

    __slots__ = ('x', 'y', 'z', 'state', 'nbt')

    def __init__(self, x, y, z, state, nbt_=None):
        self.x = int(x)
        self.y = int(y)
        self.z = int(z)
        self.state = int(state)
        self.nbt = nbt_


class Frame:
    def __init__(self, size, name='', origin=(0, 0, 0)):
        self.name = name
        self.origin = tuple(int(v) for v in origin)
        self.size = tuple(int(v) for v in size)     # (x, y, z)，均 > 0
        self.palette = []                            # [PaletteEntry]
        self.blocks = []                             # [Block]
        self._pal_map = {}

    @property
    def volume(self) -> int:
        return self.size[0] * self.size[1] * self.size[2]

    def add_palette(self, entry: PaletteEntry) -> int:
        idx = self._pal_map.get(entry.state_string())
        if idx is None:
            idx = len(self.palette)
            if idx >= MAX_PALETTE:
                raise FormatError(f'调色板条目超过上限（{MAX_PALETTE}）')
            self.palette.append(entry)
            self._pal_map[entry.state_string()] = idx
        return idx

    def append(self, x, y, z, state, nbt_=None):
        if len(self.blocks) >= MAX_BLOCKS:
            raise FormatError(f'方块总数超过上限（{MAX_BLOCKS}），请拆分建筑或缩小范围')
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1] and 0 <= z < self.size[2]):
            return None
        b = Block(x, y, z, state, nbt_)
        self.blocks.append(b)
        return b

    def count_by_state(self) -> dict:
        out = {}
        for b in self.blocks:
            out[b.state] = out.get(b.state, 0) + 1
        return out

    def state_at(self, x, y, z):
        for b in self.blocks:
            if b.x == x and b.y == y and b.z == z:
                return self.palette[b.state]
        return None


class Schematic:
    def __init__(self, fmt: str, regions=None, data_version: int = 0, version: int = 0,
                 source: str = ''):
        self.format = fmt
        self.version = int(version or 0)
        self.data_version = int(data_version or 0)
        self.regions = list(regions or [])
        self.warnings = []
        self.unsupported = []
        self.source = source

    @property
    def blocks_total(self) -> int:
        return sum(len(r.blocks) for r in self.regions)

    def size(self):
        """整个文件的包围盒尺寸（多区域按各自 origin 展开）。"""
        if not self.regions:
            return (0, 0, 0)
        mins = [min(r.origin[i] for r in self.regions) for i in range(3)]
        maxs = [max(r.origin[i] + r.size[i] for r in self.regions) for i in range(3)]
        return tuple(max(0, maxs[i] - mins[i]) for i in range(3))

    def bbox_origin(self):
        if not self.regions:
            return (0, 0, 0)
        return tuple(min(r.origin[i] for r in self.regions) for i in range(3))

    def summary(self) -> dict:
        return {
            'format': self.format,
            'version': self.version,
            'data_version': self.data_version,
            'regions': len(self.regions),
            'size': list(self.size()),
            'blocks_total': self.blocks_total,
            'warnings': list(self.warnings),
            'unsupported': list(self.unsupported),
        }


# ---------------------------------------------------------------- 位打包（读取）

def unpack_tight(longs, count: int, bits: int):
    """Litematica 的 tight 打包：条目可跨 long 边界。"""
    if bits <= 0:
        bits = 1
    mask = (1 << bits) - 1
    out = [0] * count
    total_bits = count * bits
    buf = 0
    nbits = 0
    li = 0
    for i in range(count):
        while nbits < bits:
            if li < len(longs):
                v = longs[li] & 0xFFFFFFFFFFFFFFFF
                li += 1
            else:
                v = 0
            buf |= v << nbits
            nbits += 64
        out[i] = buf & mask
        buf >>= bits
        nbits -= bits
    del total_bits
    return out


def bits_for_palette(palette_len: int) -> int:
    """Litematica：max(2, ceil(log2(n)))。"""
    n = int(palette_len)
    if n <= 1:
        return 2
    return max(2, (n - 1).bit_length())


# ---------------------------------------------------------------- 调色板解析

_LEGACY_META_CACHE = {}


def _split_state_string(s: str):
    """``minecraft:oak_stairs[facing=east,half=bottom]`` → (name, {props})。"""
    s = str(s or '').strip()
    if not s:
        return 'minecraft:air', {}
    if '[' not in s:
        name, props = s, {}
    else:
        name, rest = s.split('[', 1)
        rest = rest.rsplit(']', 1)[0]
        props = {}
        for part in rest.split(','):
            part = part.strip()
            if not part or '=' not in part:
                continue
            k, v = part.split('=', 1)
            props[k.strip()] = v.strip()
    name = name.strip() or 'minecraft:air'
    if ':' not in name:
        name = 'minecraft:' + name
    return name, props


def palette_entry_from_tag(t: Tag) -> PaletteEntry:
    """来自 NBT 的调色板条目（原版 / litematic 形态：Name + Properties）。"""
    if t is None or t.type != 'compound':
        return PaletteEntry('minecraft:air')
    name = cget_str(t, 'Name', '') or cget_str(t, 'name', '')
    props = {}
    pt = cget(t, 'Properties') or cget(t, 'properties')
    if isinstance(pt, Tag) and pt.type == 'compound':
        for k, v in pt.value.items():
            if isinstance(v, Tag):
                props[str(k)] = str(v.value)
            else:
                props[str(k)] = str(v)
    return PaletteEntry(name, props)


# ---------------------------------------------------------------- Sponge .schem

def _sponge_container(root: Tag, container: Tag, size, name: str, dv: int) -> Frame:
    w, h, l = size
    frame = Frame((w, h, l), name=name)
    # 调色板：v3 允许 {} 空字典
    pal_tag = cget(container, 'Palette') or cget(container, 'BlockPalette')
    if pal_tag is None or pal_tag.type != 'compound':
        raise FormatError('schem 缺少 Palette/BlockPalette，无法确定方块状态')
    idx_to_state = {}
    for k, v in pal_tag.value.items():
        if not isinstance(v, Tag):
            continue
        if v.type == 'int':
            idx_to_state[int(v.value)] = k
        elif v.type == 'compound':
            # 少数实现把 palette 写成 {state: {index: n}}；这里兜底取 index/Index
            i = cget_int(v, 'index', -1)
            if i < 0:
                i = cget_int(v, 'Index', -1)
            if i >= 0:
                idx_to_state[i] = k
    if not idx_to_state:
        raise FormatError('schem 调色板为空')

    entries = {}
    for i, st in idx_to_state.items():
        nm, props = _split_state_string(st)
        entries[i] = frame.add_palette(PaletteEntry(nm, props, raw=st))

    # 方块数据：ByteArray（varint）
    data_tag = cget(container, 'Data') or cget(container, 'BlockData')
    if data_tag is None or data_tag.type != 'bytes':
        raise FormatError('schem 缺少 Data/BlockData 方块数据数组')
    raw = data_tag.value
    total = w * h * l
    if len(raw) > total * 5 + 16:
        raise FormatError('schem 方块数据长度异常（超过体积 × 5）')
    states = _read_varints(raw, total)
    for y in range(h):
        for z in range(l):
            base = y * l * w + z * w
            for x in range(w):
                si = states[base + x]
                if si not in entries:
                    si = 0 if 0 in entries else min(entries)
                frame.append(x, y, z, entries[si])

    # 方块实体
    be_list = cget_list(container, 'BlockEntities') or cget_list(container, 'TileEntities')
    for be in be_list:
        if be.type != 'compound':
            continue
        pos_t = cget(be, 'Pos')
        if pos_t is not None and pos_t.type == 'ints':
            pos = list(pos_t.value)
        elif pos_t is not None and pos_t.type == 'list':
            pos = [t.as_int(0) for t in pos_t.as_list()]
        else:
            pos = [cget_int(be, 'x', 0), cget_int(be, 'y', 0), cget_int(be, 'z', 0)]
        if len(pos) < 3:
            continue
        bx, by, bz = int(pos[0]), int(pos[1]), int(pos[2])
        bid = cget_str(be, 'Id', '') or cget_str(be, 'id', '')
        data = cget(be, 'Data') or cget(be, 'data')
        merged = Tag.compound({})
        if isinstance(data, Tag) and data.type == 'compound':
            merged = Tag.compound(dict(data.value))
        if bid:
            merged.value['id'] = Tag.string(bid)
        merged.value['x'] = Tag.int(bx)
        merged.value['y'] = Tag.int(by)
        merged.value['z'] = Tag.int(bz)
        found = None
        for b in frame.blocks:
            if b.x == bx and b.y == by and b.z == bz:
                found = b
                break
        if found is not None:
            found.nbt = merged
        else:
            frame.append(bx, by, bz, 0, merged)
    return frame


def _read_varints(raw: bytes, expected: int):
    """解析 varint[]，最多 expected 个；多出来的忽略（防御异常文件）。"""
    out = []
    i = 0
    n = len(raw)
    while i < n and len(out) < expected:
        value = 0
        shift = 0
        while True:
            if i >= n:
                raise FormatError('schem 方块数据的 varint 被截断')
            b = raw[i]
            i += 1
            value |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift > 35:
                raise FormatError('schem 方块数据的 varint 过长（数据损坏）')
        out.append(value)
    if len(out) < expected:
        raise FormatError(f'schem 方块数据不足：期望 {expected} 个，实际 {len(out)} 个')
    return out


def parse_sponge(path: str, root: Tag, limits: NbtLimits) -> Schematic:
    """Sponge 规格 v1/v2/v3。

    v3: 根 → ``Schematic`` (BlockContainer)
    v1/v2: 根直接就是 BlockContainer（Palette/Data/BlockEntities 平铺）
    """
    head = root
    container = root
    inner = cget(root, 'Schematic')
    if isinstance(inner, Tag) and inner.type == 'compound':
        container = inner
    version = cget_int(container, 'Version', cget_int(head, 'Version', 3 if container is not root else 2))
    dv = cget_int(head, 'DataVersion', cget_int(container, 'DataVersion', 0))
    w = cget_int(container, 'Width', -1)
    h = cget_int(container, 'Height', -1)
    l = cget_int(container, 'Length', -1)
    if min(w, h, l) <= 0:
        raise FormatError('schem 尺寸非法（Width/Height/Length 必须为正整数）')
    if w * h * l > MAX_BLOCKS * 4:
        raise FormatError(f'schem 尺寸过大（{w}×{h}×{l}），拒绝解析')
    # 少数实现把 BlockContainer 放在 Blocks 子 compound 里
    blocks_container = container
    if cget(container, 'Palette') is None and cget(container, 'BlockPalette') is None:
        sub = cget_compound(container, 'Blocks')
        if cget(sub, 'Palette') is not None or cget(sub, 'BlockPalette') is not None:
            blocks_container = Tag.compound(sub)
    frame = _sponge_container(root, blocks_container, (w, h, l), os.path.basename(path), dv)
    origin = cget(container, 'Offset')
    if isinstance(origin, Tag) and origin.type == 'ints' and len(origin.value) >= 3:
        frame.origin = (int(origin.value[0]), int(origin.value[1]), int(origin.value[2]))
    s = Schematic('schem', [frame], dv, version, source=path)
    if cget(container, 'Biomes') is not None:
        s.unsupported.append('生物群系（MVP 不写入）')
    if cget_list(root, 'Entities') or cget_list(container, 'Entities'):
        s.unsupported.append('实体（默认不写入）')
    return s


# ---------------------------------------------------------------- MCEdit .schematic

def _read_nibbles(raw: bytes, count: int):
    out = [0] * count
    for i in range(min(count, len(raw) * 2)):
        b = raw[i >> 1]
        out[i] = (b >> 4) if (i & 1) else (b & 0x0F)
    return out


def parse_mcedit(path: str, root: Tag, limits: NbtLimits) -> Schematic:
    w = cget_int(root, 'Width', -1)
    h = cget_int(root, 'Height', -1)
    l = cget_int(root, 'Length', -1)
    if min(w, h, l) <= 0:
        raise FormatError('schematic 尺寸非法（Width/Height/Length 必须为正整数）')
    total = w * h * l
    if total > MAX_BLOCKS * 4:
        raise FormatError(f'schematic 尺寸过大（{w}×{h}×{l}），拒绝解析')
    bt = cget(root, 'Blocks')
    if bt is None or bt.type != 'bytes':
        raise FormatError('schematic 缺少 Blocks 数组')
    blocks = bt.value
    if len(blocks) < total:
        raise FormatError(f'schematic Blocks 长度不足（{len(blocks)} < {total}）')
    mtag = cget(root, 'Data')
    metas = mtag.value if (isinstance(mtag, Tag) and mtag.type == 'bytes') else b'\x00' * total
    if len(metas) < total:
        metas = bytes(metas) + b'\x00' * (total - len(metas))
    atag = cget(root, 'AddBlocks')
    adds = _read_nibbles(atag.value, total) if (isinstance(atag, Tag) and atag.type == 'bytes') \
        else [0] * total
    add_neg = bool(cget_int(root, 'Add', 0))
    frame = Frame((w, h, l), name=os.path.basename(path))
    # 先在 frame 里登记 palette（延迟到 legacy 解析时填充，用占位 air）
    frame.add_palette(PaletteEntry('minecraft:air'))
    legacy = []
    from . import mapping
    for y in range(h):
        for z in range(l):
            for x in range(w):
                i = (y * l + z) * w + x
                bid = blocks[i]
                if adds[i]:
                    bid = (bid & 0xFF) | ((adds[i] << 8) if not add_neg else (adds[i] << 12))
                meta = metas[i] if i < len(metas) else 0
                legacy.append((x, y, z, int(bid), int(meta)))
    for x, y, z, bid, meta in legacy:
        entry = mapping.legacy_block_entry(bid, meta)
        si = frame.add_palette(entry)
        frame.append(x, y, z, si)
    # 方块实体（老格式里叫 TileEntities，Pos 是绝对世界坐标）
    be_list = cget_list(root, 'TileEntities')
    if be_list:
        minx = miny = minz = None
        positions = []
        for be in be_list:
            pt = cget(be, 'Pos')
            if pt is None:
                continue
            pos = [t.as_int(0) for t in pt.as_list()] if pt.type == 'list' else list(pt.value)
            if len(pos) < 3:
                continue
            positions.append((int(pos[0]), int(pos[1]), int(pos[2]), be))
        if positions:
            minx = min(p[0] for p in positions)
            miny = min(p[1] for p in positions)
            minz = min(p[2] for p in positions)
        for px, py, pz, be in positions:
            data = Tag.compound({k: v for k, v in be.value.items() if k != 'Pos'})
            data.value['x'] = Tag.int(px)
            data.value['y'] = Tag.int(py)
            data.value['z'] = Tag.int(pz)
            lx, ly, lz = px - (minx or 0), py - (miny or 0), pz - (minz or 0)
            hit = None
            for b in frame.blocks:
                if b.x == lx and b.y == ly and b.z == lz:
                    hit = b
                    break
            if hit is not None:
                hit.nbt = data
            else:
                frame.append(lx, ly, lz, 0, data)
        frame.origin = (0, 0, 0)
    s = Schematic('schematic', [frame], 0, 0, source=path)
    s.warnings.append('MCEdit 旧格式：方块 id/数据值按 1.12 语义映射到现代方块状态，'
                      '复杂方块（红石、多格方块）可能不精确')
    if cget_list(root, 'Entities'):
        s.unsupported.append('实体（默认不写入）')
    return s


# ---------------------------------------------------------------- 原版结构 .nbt

def _read_int3(t):
    """读一个 3 元整数向量，兼容 TAG_List of TAG_Int、TAG_Int_Array 与 TAG_Byte_Array。

    原版结构文件写的是 TAG_List（游戏的 NbtOps 只认 list），但第三方工具可能写
    TAG_Int_Array，两种都要能吃。
    """
    if not isinstance(t, Tag):
        return None
    if t.type in ('ints', 'longs', 'bytes'):
        vals = list(t.value)
    elif t.type == 'list':
        vals = [v.as_int(0) if isinstance(v, Tag) else int(v) for v in t.value.get('items') or []]
    else:
        return None
    if len(vals) < 3:
        return None
    return int(vals[0]), int(vals[1]), int(vals[2])


def parse_vanilla(path: str, root: Tag, limits: NbtLimits) -> Schematic:
    st = cget(root, 'size')
    dims = _read_int3(st)
    if dims is None:
        raise FormatError('结构文件缺少 size 数组（3 个整数），可能不是结构方块导出的 .nbt')
    w, h, l = dims
    if min(w, h, l) <= 0:
        raise FormatError('结构文件尺寸非法')
    dv = cget_int(root, 'DataVersion', 0)
    frame = Frame((w, h, l), name=os.path.basename(path))
    pal_list = cget_list(root, 'palette')
    if not pal_list:
        raise FormatError('结构文件缺少 palette')
    for t in pal_list:
        frame.add_palette(palette_entry_from_tag(t))
    states = [0] * (w * h * l)
    pos_to_nbt = {}
    for bt in cget_list(root, 'blocks'):
        if bt.type != 'compound':
            continue
        pt = cget(bt, 'pos')
        if pt is None:
            continue
        pos = [t.as_int(0) for t in pt.as_list()] if pt.type == 'list' else list(pt.value)
        if len(pos) < 3:
            continue
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        si = cget_int(bt, 'state', 0)
        if not (0 <= x < w and 0 <= y < h and 0 <= z < l):
            continue
        states[(y * l + z) * w + x] = si
        nbt_t = cget(bt, 'nbt')
        if isinstance(nbt_t, Tag) and nbt_t.type == 'compound':
            pos_to_nbt[(x, y, z)] = Tag.compound(dict(nbt_t.value))
    for y in range(h):
        for z in range(l):
            for x in range(w):
                si = states[(y * l + z) * w + x]
                if si >= len(frame.palette):
                    si = 0
                bnb = pos_to_nbt.get((x, y, z))
                if bnb is not None:
                    bnb.value.setdefault('x', Tag.int(x))
                    bnb.value.setdefault('y', Tag.int(y))
                    bnb.value.setdefault('z', Tag.int(z))
                frame.append(x, y, z, si, bnb)
    # 1.13 之前的结构文件把方块实体放在 block_entities
    for be in cget_list(root, 'block_entities'):
        if be.type != 'compound':
            continue
        pt = cget(be, 'pos')
        if pt is None:
            continue
        pos = [t.as_int(0) for t in pt.as_list()] if pt.type == 'list' else list(pt.value)
        if len(pos) < 3:
            continue
        x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
        data = Tag.compound({k: v for k, v in be.value.items() if k != 'pos'})
        data.value['x'] = Tag.int(x)
        data.value['y'] = Tag.int(y)
        data.value['z'] = Tag.int(z)
        hit = None
        for b in frame.blocks:
            if b.x == x and b.y == y and b.z == z:
                hit = b
                break
        if hit is not None:
            hit.nbt = data
        else:
            frame.append(x, y, z, 0, data)
    s = Schematic('nbt', [frame], dv, 0, source=path)
    if cget_list(root, 'entities'):
        s.unsupported.append('实体（默认不写入）')
    return s


# ---------------------------------------------------------------- Litematica

def parse_litematic(path: str, root: Tag, limits: NbtLimits) -> Schematic:
    version = cget_int(root, 'Version', 0)
    dv = cget_int(root, 'MinecraftDataVersion', 0)
    regions_tag = cget(root, 'Regions')
    if not isinstance(regions_tag, Tag) or regions_tag.type != 'compound' or not regions_tag.value:
        raise FormatError('litematic 缺少 Regions 区域数据')
    frames = []
    total_blocks = 0
    for name, rt in regions_tag.value.items():
        if not isinstance(rt, Tag) or rt.type != 'compound':
            continue
        sz = cget(rt, 'Size')
        pos = cget(rt, 'Position')
        if sz is None or pos is None:
            frames.append(None)
            continue
        sv = [t.as_int(0) for t in sz.as_list()] if sz.type == 'list' else list(sz.value)
        pv = [t.as_int(0) for t in pos.as_list()] if pos.type == 'list' else list(pos.value)
        if len(sv) < 3 or len(pv) < 3:
            frames.append(None)
            continue
        size = (abs(int(sv[0])), abs(int(sv[1])), abs(int(sv[2])))
        if min(size) <= 0:
            frames.append(None)
            continue
        # 负尺寸 = 该轴翻转（Litematica 语义：起点是区域最大角）
        flip = (sv[0] < 0, sv[1] < 0, sv[2] < 0)
        pos = tuple(int(v) for v in pv[:3])
        frame = Frame(size, name=str(name), origin=pos)
        pal_list = cget_list(rt, 'BlockStatePalette')
        if not pal_list:
            frames.append(None)
            continue
        for t in pal_list:
            frame.add_palette(palette_entry_from_tag(t))
        ls = cget(rt, 'BlockStates')
        if ls is None or ls.type != 'longs':
            frames.append(None)
            continue
        volume = size[0] * size[1] * size[2]
        if volume > MAX_BLOCKS * 4:
            raise FormatError(f'litematic 区域 {name} 体积过大（{volume}），拒绝解析')
        bits = bits_for_palette(len(frame.palette))
        need = (volume * bits + 63) // 64
        longs = list(ls.value)
        if len(longs) < need:
            raise FormatError(f'litematic 区域 {name} 的 BlockStates 长度不足'
                              f'（{len(longs)} < {need}）')
        vals = unpack_tight(longs, volume, bits)
        sxx, syy, szz = size
        for y in range(syy):
            for z in range(szz):
                row = (y * szz + z) * sxx
                for x in range(sxx):
                    si = vals[row + x]
                    if si >= len(frame.palette):
                        continue
                    if frame.palette[si].name.endswith(':air'):
                        continue
                    fx = (sxx - 1 - x) if flip[0] else x
                    fy = (syy - 1 - y) if flip[1] else y
                    fz = (szz - 1 - z) if flip[2] else z
                    frame.append(fx, fy, fz, si)
        # 方块实体：Pos 是「整个结构内的绝对坐标」（相对 schematic 原点）
        for be in cget_list(rt, 'TileEntities'):
            if be.type != 'compound':
                continue
            pt = cget(be, 'Pos')
            if pt is None:
                continue
            p = [t.as_int(0) for t in pt.as_list()] if pt.type == 'list' else list(pt.value)
            if len(p) < 3:
                continue
            lx = int(p[0]) - pos[0]
            ly = int(p[1]) - pos[1]
            lz = int(p[2]) - pos[2]
            if flip[0]:
                lx = sxx - 1 - lx
            if flip[1]:
                ly = syy - 1 - ly
            if flip[2]:
                lz = szz - 1 - lz
            data = Tag.compound({k: v for k, v in be.value.items() if k not in ('Pos', 'pos')})
            data.value['x'] = Tag.int(lx)
            data.value['y'] = Tag.int(ly)
            data.value['z'] = Tag.int(lz)
            hit = None
            for b in frame.blocks:
                if b.x == lx and b.y == ly and b.z == lz:
                    hit = b
                    break
            if hit is not None:
                hit.nbt = data
            else:
                frame.append(lx, ly, lz, 0, data)
        total_blocks += len(frame.blocks)
        if total_blocks > MAX_BLOCKS:
            raise FormatError(f'方块总数超过上限（{MAX_BLOCKS}）')
        frames.append(frame)
    good = [f for f in frames if f is not None]
    if not good:
        raise FormatError('litematic 里没有可解析的区域')
    s = Schematic('litematic', good, dv, version, source=path)
    if len(good) > 1:
        s.warnings.append(f'litematic 含 {len(good)} 个区域，已全部合并为一个建筑'
                          f'（按各区域 Position 摆放）')
    md = cget(root, 'Metadata')
    if isinstance(md, Tag):
        nm = cget_str(md, 'Name', '')
        if nm and len(good) == 1:
            good[0].name = nm
    return s


# ---------------------------------------------------------------- zip

def safe_zip_members(zf: zipfile.ZipFile):
    """返回 [(info, safe_name)]；任何 zip-slip / 符号链接 / 超限直接抛 FormatError。"""
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise FormatError(f'zip 成员数过多（{len(infos)} > {MAX_ZIP_MEMBERS}），拒绝解压')
    out = []
    total = 0
    for info in infos:
        name = info.filename
        if info.is_dir():
            continue
        norm = name.replace('\\', '/')
        if norm.startswith('/') or (len(norm) > 1 and norm[1] == ':'):
            raise FormatError(f'zip 成员使用绝对路径，已拒绝：{name}')
        if norm.startswith('../') or '/../' in norm or norm == '..':
            raise FormatError(f'zip 成员包含路径穿越（zip-slip），已拒绝：{name}')
        parts = [p for p in norm.split('/') if p not in ('', '.')]
        if any(p == '..' for p in parts):
            raise FormatError(f'zip 成员包含路径穿越（zip-slip），已拒绝：{name}')
        if posixpath.isabs(norm):
            raise FormatError(f'zip 成员使用绝对路径，已拒绝：{name}')
        mode = (info.external_attr >> 16) & 0xF000
        if mode == 0xA000:
            raise FormatError(f'zip 成员是符号链接，已拒绝：{name}')
        size = int(info.file_size or 0)
        if size > MAX_ZIP_MEMBER_BYTES:
            raise FormatError(f'zip 成员解压后过大（{name}：{size} 字节），疑似 zip 炸弹')
        comp = max(1, int(info.compress_size or 1))
        if size > 1 << 20 and size / comp > MAX_ZIP_RATIO:
            raise FormatError(f'zip 成员压缩比异常（{name}：{size // comp}:1），疑似 zip 炸弹')
        total += size
        if total > MAX_ZIP_TOTAL_BYTES:
            raise FormatError('zip 解压总量超过上限，疑似 zip 炸弹')
        out.append((info, '/'.join(parts)))
    if not out:
        raise FormatError('zip 里没有文件')
    return out


def read_zip_member(zf: zipfile.ZipFile, info, hard_cap: int = MAX_ZIP_MEMBER_BYTES) -> bytes:
    """带硬上限的成员读取（防止 file_size 造假的 zip 炸弹）。"""
    buf = bytearray()
    with zf.open(info, 'r') as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            buf += chunk
            if len(buf) > hard_cap:
                raise FormatError(f'zip 成员 {info.filename} 实际解压体积超过上限')
    return bytes(buf)


# ---------------------------------------------------------------- 入口

def detect_format(path: str, root: Tag = None) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == '.schem':
        return 'schem'
    if ext == '.schematic':
        return 'schematic'
    if ext == '.litematic':
        return 'litematic'
    if ext == '.mcstructure':
        return 'mcstructure'
    if ext == '.nbt':
        return 'nbt'
    if ext == '.bdx':
        return 'bdx'
    if root is not None:
        if cget(root, 'Regions') is not None and cget(root, 'MinecraftDataVersion') is not None:
            return 'litematic'
        if cget(root, 'Schematic') is not None or cget(root, 'Palette') is not None \
                or cget(root, 'BlockPalette') is not None:
            return 'schem'
        if cget(root, 'Blocks') is not None and cget(root, 'Width') is not None:
            return 'schematic'
        if cget(root, 'size') is not None and cget(root, 'palette') is not None:
            return 'nbt'
    return 'nbt'


def parse_nbt_bytes(raw: bytes, name: str, limits: NbtLimits = None) -> Schematic:
    """解析单个（已读入内存的）NBT 建筑文件。"""
    limits = limits or NbtLimits()
    try:
        root, _kind = nbtlib.parse_bytes(raw, limits)
    except NbtError as e:
        raise FormatError(f'NBT 解析失败：{e}')
    fmt = detect_format(name, root)
    if fmt == 'schem':
        return parse_sponge(name, root, limits)
    if fmt == 'schematic':
        return parse_mcedit(name, root, limits)
    if fmt == 'litematic':
        return parse_litematic(name, root, limits)
    if fmt == 'nbt':
        return parse_vanilla(name, root, limits)
    raise FormatError(f'无法识别的建筑文件格式：{fmt}')


def parse_file(path: str, limits: NbtLimits = None, member: str = '') -> Schematic:
    """解析磁盘上的建筑文件（自动识别格式与压缩）。"""
    limits = limits or NbtLimits()
    if not os.path.isfile(path):
        raise FormatError('文件不存在')
    ext = os.path.splitext(path)[1].lower()
    if ext in ARCHIVE_EXTS:
        return parse_zip(path, limits, member=member)
    if ext == '.bdx':
        # BDX 是网易中国版的「操作码流」格式：'BD@' + brotli 压缩，
        # 与 NBT 系格式完全不同，所以在这里分流，不走 parse_nbt_bytes。
        from . import bdx as _bdx
        try:
            return _bdx.parse_bdx(path, limits, source=path)
        except _bdx.BdxError as e:
            raise FormatError('BDX 解析失败：%s' % e)
    if ext == '.mcstructure':
        raise FormatError('基岩版 .mcstructure 需要先转换为 Java 版结构（本版本仅识别，不支持导入）')
    if ext not in SUPPORTED_EXTS:
        raise FormatError(f'不支持的扩展名 {ext}；支持 {"、".join(RECOGNIZED_EXTS)}')
    with open(path, 'rb') as f:
        raw = f.read(limits.input_bytes + 1)
    if len(raw) > limits.input_bytes:
        raise FormatError(f'文件过大（超过 {limits.input_bytes // 1048576} MiB）')
    return parse_nbt_bytes(raw, os.path.basename(path), limits)


def list_zip(path: str) -> list:
    """列出 zip 里的建筑成员（供前端让用户挑选）。"""
    out = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info, safe in safe_zip_members(zf):
                ext = os.path.splitext(safe)[1].lower()
                out.append({'name': info.filename, 'safe_name': safe,
                            'size': int(info.file_size or 0),
                            'supported': ext in SUPPORTED_EXTS,
                            'ext': ext,
                            'recognized': ext in RECOGNIZED_EXTS})
    except zipfile.BadZipFile:
        raise FormatError('不是合法的 zip 文件')
    return out


def parse_zip(path: str, limits: NbtLimits = None, member: str = '') -> Schematic:
    """解析 zip：member 为空则取第一个可解析的建筑；否则按成员名解析。"""
    limits = limits or NbtLimits()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise FormatError('不是合法的 zip 文件')
    with zf:
        members = safe_zip_members(zf)
        chosen = None
        for info, safe in members:
            ext = os.path.splitext(safe)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            if member and (info.filename == member or safe == member):
                chosen = (info, safe)
                break
            if not member and chosen is None:
                chosen = (info, safe)
        if chosen is None:
            if member:
                raise FormatError(f'zip 里没有名为 {member} 的可导入建筑')
            raise FormatError('zip 里没有可导入的建筑文件'
                              f'（支持 {"、".join(SUPPORTED_EXTS)}）')
        info, safe = chosen
        raw = read_zip_member(zf, info, MAX_ZIP_MEMBER_BYTES)
    sch = parse_nbt_bytes(raw, safe, limits)
    sch.source = f'{os.path.basename(path)}::{info.filename}'
    sch.warnings.insert(0, f'来自压缩包成员：{info.filename}')
    others = [s for _, s in members if os.path.splitext(s)[1].lower() in SUPPORTED_EXTS]
    if len(others) > 1:
        sch.warnings.append(f'压缩包内共有 {len(others)} 个建筑，本次导入 '
                            f'{info.filename}（可在 preview 传 member 指定其它成员）')
    return sch
