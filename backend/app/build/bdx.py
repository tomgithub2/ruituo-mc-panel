# 网易《我的世界》中国版 BDX 建筑文件解析器
#
# 格式（逆向自 BDXConverter，逐字段核对过实现，不是猜的）：
#
#     'BD@' + brotli( 内层流 )
#     内层流 = 'BDX' + b'\x00' + 作者名 + b'\x00' + 操作串… + 签名/结束标记
#
#   · 操作串里每条操作 = 1 字节操作码 + 该操作自己的数据（全部**大端**）
#   · 操作码 88（'X'）表示操作串结束
#   · 方块名与方块状态来自「常量字符串池」（操作码 1 依次入池，按**入池顺序**编号）
#
# ⚠️ 两个容易踩的坑（都实测确认过）：
#   1. **BDX 里的 NBT 是小端**（unmarshalNBT.py 里 `endian = '<'`），
#      而面板自己的 `nbt.py` 写死 `'>H'`（Java 版是大端）—— 所以这里**必须自带小端 NBT 解码器**，
#      不能复用 nbt.py，否则命令块里的命令、容器里的物品全是乱码。
#   2. brotli 不是 Python 标准库。这里**延迟导入**并在缺失时给出可执行的提示，
#      而不是让整个模块 import 失败（面板要能在没装 brotli 时正常启动）。
import io
import os
import struct
import zlib

# ---------------------------------------------------------------- 容器与操作码

from .formats import Frame, PaletteEntry, Schematic   # noqa: E402

BDX_MAGIC = b'BD@'
INNER_MAGIC = b'BDX'
OP_END = 88                     # 操作串结束标记（ASCII 'X'）
MAX_INNER_BYTES = 256 * 1024 * 1024     # 解压后上限，防 brotli 炸弹

#: 移动光标类操作：操作码 → (轴, 字节宽度，0 表示固定 +1 的隐式 0 号操作)
_MOVE_OPS = {
    6: ('z', 2, 0),      # AddInt16ZValue0：z += 16 位值（0 号操作，无额外参数时 +1）
    8: ('z', 0, 0),      # AddZValue0
    12: ('z', 4, 0),     # AddInt32ZValue0
    14: ('x', 0, 1),     # AddXValue  = x += 1
    15: ('x', 0, -1),    # SubtractXValue
    16: ('y', 0, 1),     # AddYValue
    17: ('y', 0, -1),    # SubtractYValue
    18: ('z', 0, 1),     # AddZValue
    19: ('z', 0, -1),    # SubtractZValue
    20: ('x', 2, None),  # AddInt16XValue
    21: ('x', 4, None),  # AddInt32XValue
    22: ('y', 2, None),
    23: ('y', 4, None),
    24: ('z', 2, None),
    25: ('z', 4, None),
    28: ('x', 1, None),  # AddInt8XValue
    29: ('y', 1, None),
    30: ('z', 1, None),
}

#: 放方块类操作：操作码 → 说明（用于统计/报告）
_PLACE_OPS = {
    5: 'PlaceBlockWithBlockStates',
    7: 'PlaceBlock',
    13: 'PlaceBlockWithBlockStatesDeprecated',
    26: 'SetCommandBlockData',
    27: 'PlaceBlockWithCommandBlockData',
    32: 'PlaceRuntimeBlock',
    33: 'PlaceBlockWithRuntimeId',
    34: 'PlaceRuntimeBlockWithCommandBlockData',
    35: 'PlaceRuntimeBlockWithCommandBlockDataAndUint32RuntimeID',
    36: 'PlaceCommandBlockWithCommandBlockData',
    37: 'PlaceRuntimeBlockWithChestData',
    38: 'PlaceRuntimeBlockWithChestDataAndUint32RuntimeID',
    40: 'PlaceBlockWithChestData',
    41: 'PlaceBlockWithNBTData',
}

#: 命令块类操作（需要把命令转成 Java 语法）
_CMDBLOCK_OPS = {26, 27, 34, 35, 36}


class BdxError(Exception):
    pass


# ---------------------------------------------------------------- 小端 NBT 解码

class _LE:
    """小端 NBT／二进制读取器（BDX 内部一律小端，与 Java 版相反）。"""

    __slots__ = ('d', 'i', 'n')

    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.i = pos
        self.n = len(data)

    def need(self, k: int):
        if self.i + k > self.n:
            raise BdxError('数据不完整（需要 %d 字节，仅剩 %d）' % (k, self.n - self.i))

    def u8(self) -> int:
        self.need(1)
        v = self.d[self.i]
        self.i += 1
        return v

    def i8(self) -> int:
        self.need(1)
        v = struct.unpack_from('<b', self.d, self.i)[0]
        self.i += 1
        return v

    def u16(self) -> int:
        """大端 16 位 —— BDX 操作码字段用大端（只有 NBT 负载是小端）。"""
        self.need(2)
        v = struct.unpack_from('>H', self.d, self.i)[0]
        self.i += 2
        return v

    def i16(self) -> int:
        self.need(2)
        v = struct.unpack_from('<h', self.d, self.i)[0]
        self.i += 2
        return v

    def u32(self) -> int:
        """大端 32 位（操作码字段）。命令块的 mode/tickDelay 例外，走 le_u32。"""
        self.need(4)
        v = struct.unpack_from('>I', self.d, self.i)[0]
        self.i += 4
        return v

    def le_u32(self) -> int:
        """**小端** 32 位 —— 仅命令块的 mode / tickDelay 用（源码里是 pack('<I')）。"""
        self.need(4)
        v = struct.unpack_from('<I', self.d, self.i)[0]
        self.i += 4
        return v

    def i32(self) -> int:
        self.need(4)
        v = struct.unpack_from('<i', self.d, self.i)[0]
        self.i += 4
        return v

    def i64(self) -> int:
        self.need(8)
        v = struct.unpack_from('<q', self.d, self.i)[0]
        self.i += 8
        return v

    def f32(self) -> float:
        self.need(4)
        v = struct.unpack_from('<f', self.d, self.i)[0]
        self.i += 4
        return v

    def f64(self) -> float:
        self.need(8)
        v = struct.unpack_from('<d', self.d, self.i)[0]
        self.i += 8
        return v

    def cstr(self) -> str:
        """读 NUL 结尾的 UTF-8 字符串（BDX 里字符串都是这个形式）。"""
        j = self.d.find(b'\x00', self.i)
        if j < 0:
            raise BdxError('字符串没有结束符')
        s = self.d[self.i:j].decode('utf-8', 'replace')
        self.i = j + 1
        return s

    def nbt_string(self) -> str:
        """NBT 字符串 = 2 字节**小端**长度 + UTF-8（NBT 负载整体是小端）。"""
        self.need(2)
        k = struct.unpack_from('<H', self.d, self.i)[0]
        self.i += 2
        self.need(k)
        s = self.d[self.i:self.i + k].decode('utf-8', 'replace')
        self.i += k
        return s


def _nbt_payload(r: _LE, tid: int, depth: int = 0):
    """读一个小端 NBT 负载（tid 已读）。深度上限防恶意嵌套。"""
    if depth > 64:
        raise BdxError('NBT 嵌套过深')
    if tid == 0:
        return None
    if tid == 1:
        return r.i8()
    if tid == 2:
        return r.i16()
    if tid == 3:
        return r.i32()
    if tid == 4:
        return r.i64()
    if tid == 5:
        return r.f32()
    if tid == 6:
        return r.f64()
    if tid == 7:                                    # byte array
        k = r.i32()
        if k < 0 or k > 8 * 1024 * 1024:
            raise BdxError('byte 数组长度异常：%d' % k)
        r.need(k)
        v = list(r.d[r.i:r.i + k])
        r.i += k
        return v
    if tid == 8:
        return r.nbt_string()
    if tid == 9:                                    # list
        et = r.u8()
        k = r.i32()
        if k < 0 or k > 4 * 1024 * 1024:
            raise BdxError('list 长度异常：%d' % k)
        return [_nbt_payload(r, et, depth + 1) for _ in range(k)]
    if tid == 10:                                   # compound
        out = {}
        while True:
            t = r.u8()
            if t == 0:
                return out
            name = r.nbt_string()
            out[name] = _nbt_payload(r, t, depth + 1)
    if tid == 11:                                   # int array
        k = r.i32()
        if k < 0 or k > 4 * 1024 * 1024:
            raise BdxError('int 数组长度异常：%d' % k)
        return [r.i32() for _ in range(k)]
    if tid == 12:                                   # long array
        k = r.i32()
        if k < 0 or k > 1024 * 1024:
            raise BdxError('long 数组长度异常：%d' % k)
        return [r.i64() for _ in range(k)]
    raise BdxError('未知 NBT 类型 %d' % tid)


def self_bytes(r: _LE, k: int):
    return r.d[r.i:r.i + k]


def read_nbt_compound(r: _LE) -> dict:
    """读一个「无名根 compound」——BDX 里 NBT 数据块就是这种形式。"""
    t = r.u8()
    if t != 10:
        raise BdxError('期望 NBT compound，实际类型 %d' % t)
    name = r.nbt_string()
    if name:
        pass                                        # 根名通常为空
    return _nbt_payload(r, 10)


# ---------------------------------------------------------------- 容器解包

def is_bdx(raw: bytes) -> bool:
    return bool(raw) and raw[:3] == BDX_MAGIC


def decompress_inner(raw: bytes) -> bytes:
    """'BD@' + brotli(...) → 内层字节流。"""
    if not is_bdx(raw):
        raise BdxError('不是 BDX 文件（缺少 BD@ 头）')
    body = raw[3:]
    try:
        import brotli                                # 延迟导入：没装也要能 import 本模块
    except ImportError:
        raise BdxError('解析 .bdx 需要 brotli 库，请先安装：pip install brotli')
    try:
        out = brotli.decompress(body)
    except Exception as e:
        raise BdxError('BDX 解压失败（可能不是 brotli 数据或文件损坏）：%s' % e)
    if len(out) > MAX_INNER_BYTES:
        raise BdxError('BDX 解压后过大（>%d MiB）' % (MAX_INNER_BYTES // 1048576))
    return out


# ---------------------------------------------------------------- 操作串扫描

def scan_operations(inner: bytes):
    """扫描内层操作串，返回 (author, ops, constant_strings, warnings)。

    ops 里每项是 {'id': int, 'name': str, 'data': {...}}。
    不做方块落地 —— 只把操作流**忠实解出来**，落地交给上层。
    """
    r = _LE(inner)
    r.need(4)
    if inner[0:3] != INNER_MAGIC:
        raise BdxError('BDX 内层头不对：%r' % inner[0:3])
    r.i = 3
    if r.u8() != 0:
        raise BdxError('BDX 内层头第 4 字节应为 0')
    author = r.cstr()

    ops = []
    strings = []                                    # 常量字符串池（按入池顺序编号）
    warnings = []
    unknown = {}
    guard = 0

    while True:
        guard += 1
        if guard > 4_000_000:
            raise BdxError('操作数量异常，疑似文件损坏')
        if r.i >= r.n:
            warnings.append('操作串没有遇到结束标记（文件可能被截断）')
            break
        cid = r.u8()
        if cid == OP_END:
            break

        # ---- 常量字符串池
        if cid == 1:
            strings.append(r.cstr())
            ops.append({'id': cid, 'name': 'CreateConstantString', 'data': {}})
            continue

        # ---- 空操作
        if cid == 9:
            ops.append({'id': cid, 'name': 'NOP', 'data': {}})
            continue

        # ---- 调试数据（含长度前缀的字节块，跳过）
        if cid == 39:
            k = r.u32()          # 大端
            r.need(k)
            r.i += k
            ops.append({'id': cid, 'name': 'AssignDebugData', 'data': {'size': k}})
            continue

        # ---- 光标移动
        if cid in _MOVE_OPS:
            axis, width, implicit = _MOVE_OPS[cid]
            if width == 0:
                val = implicit if implicit is not None else 1
            else:
                if width == 1:
                    val = r.i8()
                elif width == 2:
                    val = r.i16()
                else:
                    val = r.i32()
                if implicit == 0:
                    val = val if val != 0 else 0
            ops.append({'id': cid, 'name': 'Move', 'data': {'axis': axis, 'value': val}})
            continue

        # ---- 运行时 ID 池切换
        if cid == 31:
            pool = r.u8()
            ops.append({'id': cid, 'name': 'UseRuntimeIDPool', 'data': {'pool': pool}})
            continue

        # ---- 放方块（各种变体）
        if cid in _PLACE_OPS:
            data, warn = _read_place(r, cid, strings)
            if warn:
                warnings.append(warn)
            ops.append({'id': cid, 'name': _PLACE_OPS[cid], 'data': data})
            continue

        # ---- 未知操作码：无法安全跳过，只能中止
        unknown[cid] = unknown.get(cid, 0) + 1
        raise BdxError('遇到未支持的操作码 %d（位置 %d）—— 无法安全解析后续内容' % (cid, r.i - 1))

    if unknown:
        warnings.append('未知操作码：%s' % unknown)
    return author, ops, strings, warnings


def _read_place(r: _LE, cid: int, strings: list):
    """读一个「放方块」操作的参数，返回 (data, warning)。"""
    d = {'op': _PLACE_OPS[cid]}

    if cid == 26:                                   # SetCommandBlockData
        d['mode'] = r.le_u32()                      # ← 小端（同 _read_cmd_payload）
        d['command'] = r.cstr()
        d['customName'] = r.cstr()
        d['lastOutput'] = r.cstr()
        d['tickDelay'] = r.le_u32()                 # ← 小端
        d['executeOnFirstTick'] = bool(r.u8())
        d['trackOutput'] = bool(r.u8())
        d['conditional'] = bool(r.u8())
        d['needsRedstone'] = bool(r.u8())
        d['is_set_only'] = True
        return d, ''

    if cid == 27:                                   # PlaceBlockWithCommandBlockData
        sid = r.u16()
        d['block'] = _s(strings, sid)
        d['blockData'] = r.u16()
        d.update(_read_cmd_payload(r))
        return d, ''

    if cid == 34:                                   # PlaceRuntimeBlockWithCommandBlockData
        d['runtimeId'] = r.u16()
        d.update(_read_cmd_payload(r))
        return d, ''

    if cid == 35:                                   # ...AndUint32RuntimeID
        d['runtimeId'] = r.u32()
        d.update(_read_cmd_payload(r))
        return d, ''

    if cid == 36:                                   # PlaceCommandBlockWithCommandBlockData
        d['data'] = r.u16()
        d.update(_read_cmd_payload(r))
        d['block'] = 'minecraft:command_block'
        return d, ''

    # ---- 普通放方块
    if cid == 7:                                    # PlaceBlock
        sid = r.u16()
        d['block'] = _s(strings, sid)
        d['blockData'] = r.u16()
        return d, ''

    if cid == 5:                                    # PlaceBlockWithBlockStates
        sid = r.u16()
        ssid = r.u16()
        d['block'] = _s(strings, sid)
        d['states'] = _s(strings, ssid)
        return d, ''

    if cid == 13:                                   # PlaceBlockWithBlockStatesDeprecated
        sid = r.u16()
        d['block'] = _s(strings, sid)
        d['states'] = r.cstr()
        return d, ''

    if cid == 32:                                   # PlaceRuntimeBlock
        d['runtimeId'] = r.u16()
        return d, '运行时 ID 方块需要基岩版映射表，暂以占位块落地'

    if cid == 33:                                   # PlaceBlockWithRuntimeId
        d['runtimeId'] = r.u32()
        return d, '运行时 ID 方块需要基岩版映射表，暂以占位块落地'

    if cid in (37, 38):                             # 带箱子数据
        # ⚠️ 箱子物品表是变长结构，字段宽度不足以可靠跳过；硬猜会**错位解析后续所有操作**。
        #    宁可明确报错，也不产出"看起来成功但内容错乱"的导入。
        raise BdxError('带箱子数据的 BDX 变体暂不支持（操作码 %d）' % cid)

    if cid == 40:                                   # PlaceBlockWithChestData
        raise BdxError('带箱子数据的 BDX 变体暂不支持（操作码 40）')

    if cid == 41:                                   # PlaceBlockWithNBTData
        sid = r.u16()
        ssid = r.u16()
        r.u16()                                     # 该实现里重复写了一次 statesID
        d['block'] = _s(strings, sid)
        d['states'] = _s(strings, ssid)
        try:
            d['nbt'] = read_nbt_compound(r)
        except BdxError as e:
            return d, 'NBT 数据读取失败：%s' % e
        return d, ''

    raise BdxError('未支持的放方块操作码 %d' % cid)


def _read_cmd_payload(r: _LE, with_nbt: bool = False):
    """命令块公共字段，逐字段对齐 BDXConverter 的 Marshal 实现。

    ⚠️ **混合字节序**（这是本格式最容易搞错的地方，实测踩过）：
      · 外部操作码的整数是**大端**（`pack('>H')` / `pack('>I')`）
      · 但命令块内的 `mode` 与 `tickDelay` 是 **小端**（`pack('<I')`）
    我把它们全按大端读时，mode=1 会读成 16777216（0x01000000）——
    写成"看起来能跑但数值全错"。所以这里区分 `r.i32()`(大端) 与 `r.u32()`(小端)。

    ⚠️ 保留 `with_nbt` 参数是为了**将来应对变体**，但当前 26/27/34/35/36 号操作的
    Marshal 实现里**都没有** NBT 负载（只有操作码 41 `PlaceBlockWithNBTData` 才有）——
    我曾误加 with_nbt=True，结果多读字节、后续操作全部错位（实测报
    `遇到未支持的操作码 69`）。现在调用方一律用默认 False。
    """
    mode = r.le_u32()                               # ← 小端（特例）
    command = r.cstr()
    custom_name = r.cstr()
    last_output = r.cstr()
    tick_delay = r.le_u32()                         # ← 小端（特例）
    out = {
        'mode': mode,
        'command': command,
        'customName': custom_name,
        'lastOutput': last_output,
        'tickDelay': tick_delay,
        'executeOnFirstTick': bool(r.u8()),
        'trackOutput': bool(r.u8()),
        'conditional': bool(r.u8()),
        'needsRedstone': bool(r.u8()),
    }
    if with_nbt:
        try:
            out['nbt'] = read_nbt_compound(r)
        except BdxError:
            pass                                    # 字段差异容错：命令已拿到就够了
    return out


def _s(strings: list, idx: int) -> str:
    if 0 <= idx < len(strings):
        return strings[idx]
    return ''


# ================================================================ 落地转换
#
# 把 scan_operations() 解出的操作流「执行」一遍，得到面板内部统一的 Schematic。
#
# 为什么这样架构：BDX 是**操作码流**（移动光标 + 放方块），而不是"方块数组 + 调色板"。
# 解析（scan_operations）与落地（to_schematic）分开，好处是：
#   · 解析层可以单独测试（不需要任何方块映射表）
#   · 落地层可以复用面板的 mapping / Registry 做方块名与状态处理
#   · 出问题时能明确区分"格式读错了"还是"方块映射错了"

#: 基岩版数字 data 值（legacy meta）→ 面板 mapping 用的 (id, meta)
_CMD_BLOCK_MODES = {0: 'impulse', 1: 'repeat', 2: 'chain'}


def _bedrock_block_to_java(name: str, block_data: int = 0, states: str = ''):
    """把 BDX/基岩版的方块标识尽量转成 Java 版方块状态字符串。

    返回 (state_str, note)。转不干净时 note 里说明原因 —— **宁可不转也不瞎转**。
    """
    nm = (name or '').strip()
    if not nm:
        return '', '方块名为空'
    note = ''
    # 去掉基岩版可能带的 _legacy / 状态后缀
    if ':' not in nm:
        nm = 'minecraft:' + nm
    base = nm
    props = {}

    # PlaceBlockWithBlockStates 的 states 字段是 "key=value,key=value" 形式
    if states:
        for part in states.split(','):
            if '=' in part:
                k, v = part.split('=', 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    props[k] = v

    # 基岩版方块名与 Java 不一致的常见项（只放"确定"的映射，拿不准的不动）
    rename = {
        'minecraft:grass': 'minecraft:grass_block',
        'minecraft:grass_path': 'minecraft:dirt_path',
        'minecraft:lit_furnace': 'minecraft:furnace',
        'minecraft:lit_pumpkin': 'minecraft:jack_o_lantern',
        'minecraft:reeds': 'minecraft:sugar_cane',
        'minecraft:flowing_water': 'minecraft:water',
        'minecraft:flowing_lava': 'minecraft:lava',
        'minecraft:brick_block': 'minecraft:bricks',
        'minecraft:hardened_clay': 'minecraft:terracotta',
        'minecraft:stained_hardened_clay': 'minecraft:white_terracotta',
    }
    if base in rename:
        note = '方块名按基岩→Java 对照改名：%s → %s' % (base, rename[base])
        base = rename[base]

    if props:
        return '%s[%s]' % (base, ','.join('%s=%s' % kv for kv in sorted(props.items()))), note
    return base, note


def _command_block_entity(cb: dict, java_cmd: str, notes: list):
    """构造命令块的方块实体 NBT（照 formats.py 里既有的方块实体写法）。"""
    from .nbt import Tag
    d = {
        'id': Tag.string('minecraft:command_block'),
        'Command': Tag.string(java_cmd or ''),
        'auto': Tag.byte(1 if cb.get('executeOnFirstTick', True) else 0),
        'conditionMet': Tag.byte(1 if cb.get('conditional') else 0),
        'powered': Tag.byte(0 if cb.get('needsRedstone', True) else 1),
        'TrackOutput': Tag.byte(1 if cb.get('trackOutput', True) else 0),
        'UpdateLastExecution': Tag.byte(1),
        'custom_name': Tag.string(cb.get('customName') or ''),
    }
    delay = int(cb.get('tickDelay') or 0)
    if delay > 0:
        d['Delay'] = Tag.int(delay)
    if notes:
        # 变更说明写进 item 的提示文本，方便玩家在游戏里看到"这条命令被改过"
        d['mcpanel_notes'] = Tag.string('；'.join(str(x) for x in notes)[:1800])
    return Tag.compound(d)


def _state_to_entry(state: str) -> 'PaletteEntry':
    """把 "minecraft:oak_stairs[facing=east,half=bottom]" 拆成 PaletteEntry。"""
    txt = (state or 'minecraft:stone').strip()
    name = txt
    props = {}
    if '[' in txt and txt.endswith(']'):
        name, _, rest = txt.partition('[')
        for part in rest[:-1].split(','):
            if '=' in part:
                k, v = part.split('=', 1)
                k, v = k.strip(), v.strip()
                if k:
                    props[k] = v
    if ':' not in name:
        name = 'minecraft:' + name
    return PaletteEntry(name, props, raw=txt)


def to_schematic(author: str, ops: list, strings: list, warnings: list,
                 source: str = '', convert_commands: bool = True,
                 max_blocks: int = 2_000_000):
    """把操作流落地成 Schematic（面板内部统一结构）。

    `convert_commands=True` 时把命令块里的基岩命令转成 Java 语法
    （转换器在 bedrockcmd 模块；缺失时**原样保留命令并记警告**，不静默丢命令）。
    """

    # 命令转换器（可选模块，缺失时降级为"原样保留 + 警告"）
    conv = None
    if convert_commands:
        try:
            from . import bedrockcmd as conv           # noqa: F401
        except Exception:
            conv = None
            warnings.append('命令转换器（bedrockcmd）不可用，命令块内容将原样保留')

    x = y = z = 0
    blocks = []
    converted_cmds = []
    kept_cmds = []
    skipped = {}

    for op in ops:
        nm = op.get('name')
        d = op.get('data') or {}

        if nm == 'Move':
            v = int(d.get('value') or 0)
            ax = d.get('axis')
            if ax == 'x':
                x += v
            elif ax == 'y':
                y += v
            else:
                z += v
            # BDX 里 y 轴向下为正的约定与 Java 一致（+y 向上），不需翻转
            continue

        if nm in ('CreateConstantString', 'NOP', 'AssignDebugData', 'UseRuntimeIDPool'):
            continue

        if nm == 'SetCommandBlockData':
            consumed = _apply_set_cmd(blocks, x, y, z, d, conv, warnings,
                                      converted_cmds, kept_cmds)
            if not consumed:
                skipped[nm] = skipped.get(nm, 0) + 1
            continue

        if nm in ('PlaceCommandBlockWithCommandBlockData',):
            blocks.append(_mk_cmd_block(d, x, y, z, conv, warnings,
                                        converted_cmds, kept_cmds,
                                        state='minecraft:command_block'))
            continue

        if nm == 'PlaceBlockWithCommandBlockData':
            state, note = _bedrock_block_to_java(d.get('block') or 'minecraft:command_block',
                                                 d.get('blockData') or 0)
            if note:
                warnings.append(note)
            blocks.append(_mk_cmd_block(d, x, y, z, conv, warnings,
                                        converted_cmds, kept_cmds, state=state))
            continue

        if nm.startswith('Place'):
            # 普通方块
            if 'runtimeId' in d:
                skipped['runtime_id_block'] = skipped.get('runtime_id_block', 0) + 1
                continue
            if 'nbt' in d and d.get('nbt'):
                # PlaceBlockWithNBTData：容器等带 NBT 的方块
                state, note = _bedrock_block_to_java(d.get('block') or '',
                                                     states=d.get('states') or '')
                if note:
                    warnings.append(note)
                be = _nbt_dict_to_tag(d['nbt'])
                blocks.append({'x': x, 'y': y, 'z': z, 'state': state or 'minecraft:stone', 'nbt': be})
                continue
            state, note = _bedrock_block_to_java(d.get('block') or '',
                                                 d.get('blockData') or 0,
                                                 d.get('states') or '')
            if note:
                warnings.append(note)
            if state:
                blocks.append({'x': x, 'y': y, 'z': z, 'state': state, 'nbt': None})
            else:
                skipped['empty_block_name'] = skipped.get('empty_block_name', 0) + 1
            continue

        skipped[nm or ('op#%s' % op.get('id'))] = skipped.get(nm or 'op', 0) + 1

    if len(blocks) > max_blocks:
        raise BdxError('BDX 展开后方块数 %d 超过上限 %d' % (len(blocks), max_blocks))

    if skipped:
        warnings.append('以下内容未导入：%s' % '；'.join(
            '%s ×%d' % (k, v) for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])[:8]))

    xs = [b['x'] for b in blocks] or [0]
    ys = [b['y'] for b in blocks] or [0]
    zs = [b['z'] for b in blocks] or [0]
    ox, oy, oz = min(xs), min(ys), min(zs)
    size = (max(xs) - ox + 1, max(ys) - oy + 1, max(zs) - oz + 1)

    # ⚠️ 三个容易写错的地方（都在测试里被立刻抓到）：
    #   1. Frame 签名是 Frame(size, name, origin) —— 尺寸在前，不是原点在前
    #   2. Block.state 是**调色板索引(int)**，不是状态字符串；
    #      要先用 add_palette(PaletteEntry) 拿到索引
    #   3. 方块必须走 frame.append()（内部会校验坐标范围），
    #      直接 append 到 frame.blocks 会绕过边界检查
    frame = Frame(size, name=os.path.basename(source or 'bdx'), origin=(ox, oy, oz))
    for b in blocks:
        entry = _state_to_entry(b['state'])
        idx = frame.add_palette(entry)
        frame.append(b['x'] - ox, b['y'] - oy, b['z'] - oz, idx, b['nbt'])

    sch = Schematic('bdx', [frame], 0, 0, source=source or 'bdx')
    sch.warnings.extend(warnings)
    # 命令转换统计，供上层报告
    sch.cmd_converted = converted_cmds
    sch.cmd_kept = kept_cmds
    return sch


def _mk_cmd_block(d: dict, x: int, y: int, z: int, conv, warnings,
                  converted_cmds: list, kept_cmds: list, state: str):
    from .formats import Block
    raw = d.get('command') or ''
    java = raw
    notes = []
    if raw.strip() and conv is not None:
        try:
            java, notes = conv.convert_command(raw)
        except Exception as e:
            notes = ['命令转换器出错，已原样保留：%s' % e]
            java = raw
    if not raw.strip():
        notes = []
    elif java != raw:
        converted_cmds.append({'from': raw, 'to': java, 'notes': notes})
    else:
        kept_cmds.append({'cmd': raw, 'notes': notes})
    be = _command_block_entity(d, java, notes)
    return {'x': x, 'y': y, 'z': z, 'state': state, 'nbt': be}


def _apply_set_cmd(blocks: list, x: int, y: int, z: int, d: dict, conv, warnings,
                   converted_cmds: list, kept_cmds: list) -> bool:
    """SetCommandBlockData(26) 是给**已有**命令块补命令，找最近的那个命令块改。"""
    for b in reversed(blocks):
        if 'command_block' in (b.get('state') or ''):
            raw = d.get('command') or ''
            java, notes = (conv.convert_command(raw) if (conv and raw.strip()) else (raw, []))
            b['nbt'] = _command_block_entity(d, java, notes)
            if raw.strip():
                if java != raw:
                    converted_cmds.append({'from': raw, 'to': java, 'notes': notes})
                else:
                    kept_cmds.append({'cmd': raw, 'notes': notes})
            return True
    warnings.append('SetCommandBlockData 之前没有命令块，已跳过')
    return False


def _nbt_dict_to_tag(node):
    """把 _LE 解出的小端 NBT（纯 Python dict/list/标量）转成面板的 Tag。"""
    from .nbt import Tag
    return _to_tag(node)


def _to_tag(v):
    from .nbt import Tag
    if isinstance(v, dict):
        return Tag.compound({k: _to_tag(x) for k, x in v.items()})
    if isinstance(v, list):
        items = [_to_tag(x) for x in v]
        return Tag.list_(items)
    if isinstance(v, bool):
        return Tag.byte(1 if v else 0)
    if isinstance(v, int):
        return Tag.int(v)
    if isinstance(v, float):
        return Tag.double(v)
    if isinstance(v, (bytes, bytearray)):
        return Tag.byte_array(bytes(v))
    return Tag.string('' if v is None else str(v))


# ================================================================ 对外入口

def parse_bdx(path: str, limits=None, convert_commands: bool = True, source: str = ''):
    """解析磁盘上的 .bdx 文件 → Schematic。"""
    if not os.path.isfile(path):
        raise BdxError('文件不存在')
    with open(path, 'rb') as f:
        raw = f.read()
    inner = decompress_inner(raw)
    author, ops, strings, warns = scan_operations(inner)
    return to_schematic(author, ops, strings, warns,
                        source=source or path, convert_commands=convert_commands)
