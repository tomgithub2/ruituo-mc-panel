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
        # ⚠️ **这里的字节序曾经写错，导致坐标变成几十亿的垃圾值**（2026-10-15 定位）：
        #    BDX 的移动值一律是**大端**（与 BDXConverter 的 `unpack('>h')`/`unpack('>i')` 一致），
        #    而 `_LE.i16()/i32()` 是**小端**（`'<h'`/`'<i'`）——
        #    读 `00 00 00 02` 会得到 33554432 而不是 2，累积后建筑高度算成 73 亿格，
        #    预览被 400 拒绝（用户报「好几万格高超出世界高度」，而他的文件本来是正常的）。
        #    所以这里**显式用大端**读，不用 `_LE` 的 i16/i32。
        if cid in _MOVE_OPS:
            axis, width, implicit = _MOVE_OPS[cid]
            if width == 0:
                val = implicit if implicit is not None else 1
            else:
                if width == 1:
                    val = struct.unpack_from('>b', r.d, r.i)[0]
                elif width == 2:
                    val = struct.unpack_from('>h', r.d, r.i)[0]
                else:
                    val = struct.unpack_from('>i', r.d, r.i)[0]
                r.i += width
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
#: 基岩版命令块 mode → Java 版**方块类型**。
#:
#: ⚠️ 这是 Java 与基岩的一个关键差异，我一开始漏了它（实测发现）：
#:   · **基岩**：三种命令块是**同一个方块**（`minecraft:command_block`），靠 `mode` 字段区分；
#:   · **Java** ：三种是**三个不同方块** ——
#:       mode 0 脉冲 impulse   → `minecraft:command_block`
#:       mode 1 循环 repeat    → `minecraft:repeating_command_block`
#:       mode 2 连锁 chain     → `minecraft:chain_command_block`
#:   只转命令文本、却把三种都落成 `command_block` 的话，
#:   导入后**循环/连锁命令块会全部变成脉冲块**（行为完全变了）。
_CMD_BLOCK_BY_MODE = {
    0: 'minecraft:command_block',
    1: 'minecraft:repeating_command_block',
    2: 'minecraft:chain_command_block',
}


def _cmd_block_for_mode(mode, fallback: str = 'minecraft:command_block') -> str:
    """由基岩 mode 推出 Java 版命令块的方块名（认不出就返回 fallback）。"""
    try:
        return _CMD_BLOCK_BY_MODE.get(int(mode), fallback)
    except (TypeError, ValueError):
        return fallback


def _cmd_block_entity_id(state: str) -> str:
    """由方块名推出正确的方块实体 `id`（Java 下 id 必须与方块一致）。

    三者都是命令块家族，但 id 各不相同；写错会让方块实体失效
    （命令块看起来在、但里面没有命令）。
    """
    st = (state or '').lower()
    if 'repeating_command_block' in st:
        return 'minecraft:repeating_command_block'
    if 'chain_command_block' in st:
        return 'minecraft:chain_command_block'
    return 'minecraft:command_block'


_CMD_BLOCK_MODES = {0: 'impulse', 1: 'repeat', 2: 'chain'}


def _nbt_compound(node):
    """取出 NBT 复合体的 {键: 值}（兼容"裸 dict"与"Tag 复合体"两种形态）。"""
    if node is None:
        return None
    if isinstance(node, dict):
        return node
    val = getattr(node, 'value', None)
    if isinstance(val, dict):
        return val
    return None


def _nbt_unwrap(v):
    """把 Tag 值拆成 Python 标量；不是 Tag 就原样返回。"""
    if hasattr(v, 'value') and not isinstance(v, (dict, list, bytes, bytearray)):
        return v.value
    return v


def _nbt_key(comp: dict, name: str):
    """在复合体里找键（大小写不敏感，兼容 Command / command）。"""
    if name in comp:
        return name
    low = name.lower()
    for k in comp:
        if str(k).lower() == low:
            return k
    return None


def _nbt_get_str(node, key):
    """读复合体里的字符串值（找不到返回 None）。"""
    comp = _nbt_compound(node)
    if comp is None:
        return None
    k = _nbt_key(comp, key)
    if k is None:
        return None
    v = _nbt_unwrap(comp[k])
    return v if isinstance(v, str) else (str(v) if v is not None else None)


def _nbt_set_str(node, key, value: str) -> bool:
    """就地改写复合体里的字符串值（保留原来的标量包装方式）。"""
    comp = _nbt_compound(node)
    if comp is None:
        return False
    k = _nbt_key(comp, key)
    if k is None:
        comp[key] = value
        return True
    old = comp[k]
    # 原来是 Tag 就包回 Tag，原来是裸值就直接放
    if hasattr(old, 'value') and not isinstance(old, (dict, list)):
        try:
            from .nbt import Tag
            comp[k] = Tag.string(value)
            return True
        except Exception:
            pass
    comp[k] = value
    return True


def _nbt_fix_cmd_id(node, state: str) -> None:
    """把 NBT 里的方块实体 id 改成与 java 方块类型一致的写法。

    ⚠️ 基岩/网易的 BDX 里 id 常是**旧式短名** `CommandBlock` / `RepeatingCommandBlock`
    / `ChainCommandBlock`；而 Java 版结构里用的是与方块同名的 id
    （`minecraft:command_block` 等）。不一致会让方块实体失效 ——
    方块在、但里面没有命令（用户看到的就是"命令丢了"）。
    """
    want = _cmd_block_entity_id(state)
    _nbt_set_str(node, 'id', want)


def _parse_bedrock_states(states: str) -> dict:
    r'''把**基岩版的方块状态串**解析成 Java 的 {键: 值}。

    ⚠️ 这个格式实测踩过坑，真实的 states（取自 BDX 常量字符串池）长这样：
        '["infiniburn_bit"=false]'      属性名带双引号、布尔值是裸的
        '["dirt_type"="normal"]'        字符串值也带双引号
        '["facing_direction"=5]'        数值裸写
        '[]'                            没有属性
    关键点（缺一个都会解析错）：
      · **最外层 `[]` 是整体包裹**，要去掉；不能按逗号切了就当属性名。
      · 属性名可能被双引号包着 → 要去掉引号。
      · 值也可能是 `"..."` → 去掉引号；`true`/`false`/数字则原样保留。
      · 值里若含 `=` 或 `,`（被引号包着）不能当分隔符 → 优先按引号配对切分。

    同时**兼容**另一种写法 `key=value,key=value`（不带外层方括号、不带引号），
    因为不同来源/版本的 states 可能是这种，改错了会让原本能用的文件反而坏掉。
    '''
    txt = (states or '').strip()
    if not txt or txt == '[]':
        return {}
    out = {}

    # ---- 形态 A：整体被 [ ] 包住（基岩版数组写法）
    if txt.startswith('[') and txt.endswith(']'):
        inner = txt[1:-1].strip()
        if not inner:
            return {}
        # 按"引号外"的逗号切分，避免值里的逗号被误切
        parts, buf, in_q = [], '', None
        for ch in inner:
            if ch in '"\'':
                in_q = None if in_q == ch else (ch if in_q is None else in_q)
                buf += ch
            elif ch == ',' and in_q is None:
                parts.append(buf)
                buf = ''
            else:
                buf += ch
        if buf:
            parts.append(buf)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # 找"引号外"的 =
            key, val, in_q = '', '', None
            for i, ch in enumerate(part):
                if ch in '"\'':
                    in_q = None if in_q == ch else (ch if in_q is None else in_q)
                elif ch == '=' and in_q is None:
                    key, val = part[:i], part[i + 1:]
                    break
            if not key:
                continue
            k = key.strip().strip('"\'')
            v = val.strip().strip('"\'')
            if k:
                out[k] = v
        return out

    # ---- 形态 B：key=value,key=value
    for part in txt.split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            k, v = k.strip().strip('"\''), v.strip().strip('"\'')
            if k and v:
                out[k] = v
    return out


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

    # ⚠️ states 的真实格式是**基岩版数组写法**，不是 "key=value,key=value"：
    #      ["infiniburn_bit"=false]        属性名带双引号、值也可能带
    #      ["dirt_type"="normal"]          多个属性用逗号分隔
    #      []                              没有属性
    #    旧写法按 ',' 切、再按 '=' 拆，结果属性名变成 `["infiniburn_bit"`
    #    （留着 `[` 和引号）、值变成 `false]`（留着 `]`）—— 生成的方块状态形如
    #      minecraft:bedrock[["infiniburn_bit"=false]]
    #    完全是坏的。实测用户的 BDX 全部命中这个格式（见 _parse_bedrock_states）。
    if states:
        props.update(_parse_bedrock_states(states))

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

    # ⚠️ 命令块的状态**属性名**在基岩与 Java 下不一样，必须换名：
    #    基岩 `facing_direction`(0-5) → Java `facing`(down/up/north/south/west/east)
    #    基岩 `conditional_bit`     → Java `conditional`
    #    只换名不换值的话，Java 收到的是无法识别的属性（方块状态应用失败）。
    if 'command_block' in base:
        if 'facing_direction' in props:
            props['facing'] = _cmd_block_facing(props.pop('facing_direction'))
        if 'conditional_bit' in props:
            props['conditional'] = props.pop('conditional_bit')
        # `facing=up` 是 Java 命令块的默认朝向 —— 与 _cmd_block_state 保持一致，
        # 默认值不写进状态（少一个无意义的调色板条目）。
        if props.get('facing') == 'up':
            props.pop('facing', None)

    if props:
        return '%s[%s]' % (base, ','.join('%s=%s' % kv for kv in sorted(props.items()))), note
    return base, note


def _cmd_block_state(mode, conditional=None, facing=None) -> str:
    """构造 Java 版命令块的**完整方块状态**。

    ⚠️ Java 版命令块有两个必须用**方块状态**表达、而不是 NBT 的东西：
      · `conditional` —— 「有条件」开关。Java 里它是方块状态
        （`conditional=true/false`）；基岩的 `conditional` 字段是 0/1。
        我原来把它塞进 NBT 的 `conditionMet`（那是**运行时**"上次条件是否满足"，
        根本不是配置项），等于**条件开关丢了**。
      · `facing` —— 朝向（下/上/北/南/西/东，默认 up）。
    这两个不设的话，导入的命令块在游戏里表现会和原建筑不一致。
    """
    props = []
    # ⚠️ `conditional=false` 是 Java 的**默认值**，不必写进状态：
    #    · 写 `conditional=false` 虽然也合法，但会让调色板多出无意义的条目；
    #    · 真正的默认（不写）更贴近原版结构文件。
    #    所以只在"有条件"时显式写 `conditional=true`。
    if conditional:
        props.append('conditional=true')
    if facing:
        props.append('facing=%s' % str(facing))
    base = _cmd_block_for_mode(mode)
    if not props:
        return base
    return '%s[%s]' % (base, ','.join(props))


def _cmd_block_facing(dir_value) -> str:
    """基岩的 `direction` 字段（0-5）→ Java 的 facing 值。

    基岩方块数据值惯例：0=下 1=上 2=北 3=南 4=西 5=东
    （命令块默认朝上 = 1）
    """
    table = {0: 'down', 1: 'up', 2: 'north', 3: 'south', 4: 'west', 5: 'east'}
    try:
        return table.get(int(dir_value), 'up')
    except (TypeError, ValueError):
        return 'up'


def _command_block_entity(cb: dict, java_cmd: str, notes: list,
                          state: str = 'minecraft:command_block'):
    """构造命令块的方块实体 NBT（照 formats.py 里既有的方块实体写法）。

    `state` 决定 `id` —— Java 版三种命令块的实体 id 各不相同，
    必须与方块本身一致，否则方块实体失效（方块在、但里面没有命令）。
    """
    from .nbt import Tag
    d = {
        # ⚠️ 不能写死 'minecraft:command_block'：循环/连锁命令块有各自的 id
        'id': Tag.string(_cmd_block_entity_id(state)),
        'Command': Tag.string(java_cmd or ''),
        'auto': Tag.byte(1 if cb.get('executeOnFirstTick', True) else 0),
        # ⚠️ `conditionMet` 是**运行时**字段（上次条件是否满足），不是"有条件"开关；
        #    「有条件」走方块状态 `conditional=…`（见 _cmd_block_state）。
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
                 source: str = '', cmd_convert: str = 'be2java',
                 max_blocks: int = 2_000_000):
    """把操作流落地成 Schematic（面板内部统一结构）。

    `cmd_convert` 决定命令方块里的命令怎么转（**用户可选方向**）：
      · 'be2java'（默认）基岩版 → Java 版：BDX 里存的本来就是基岩语法，导入 Java 服要转
      · 'java2be' Java 版 → 基岩版：把已是 Java 语法的命令改回基岩语法
      · 'none'    不转换，原样保留
    转换器模块缺失时**原样保留命令并记警告**，不静默丢命令。
    """
    direction = (cmd_convert or 'be2java').lower()
    if direction not in ('be2java', 'java2be', 'none'):
        direction = 'be2java'

    # 命令转换器（可选模块，缺失时降级为"原样保留 + 警告"）
    conv = None
    if direction != 'none':
        try:
            from . import bedrockcmd as conv           # noqa: F401
        except Exception:
            conv = None
            warnings.append('命令转换器（bedrockcmd）不可用，命令块内容将原样保留')

    def _convert(raw: str):
        """按方向转换一条命令，返回 (结果, 说明)。"""
        if direction == 'none' or conv is None or not (raw or '').strip():
            return raw, []
        fn = conv.convert_command if direction == 'be2java' else conv.convert_to_bedrock
        try:
            return fn(raw)
        except Exception as e:
            return raw, ['命令转换出错，已原样保留：%s' % e]

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
            consumed = _apply_set_cmd(blocks, x, y, z, d, _convert, warnings,
                                      converted_cmds, kept_cmds)
            if not consumed:
                skipped[nm] = skipped.get(nm, 0) + 1
            continue

        if nm in ('PlaceCommandBlockWithCommandBlockData',):
            # ⚠️ 方块类型必须由 **mode** 决定：基岩用同一个方块 + mode 区分脉冲/循环/连锁，
            #    而 Java 是**三个不同方块**。原来写死 command_block，
            #    导致循环/连锁命令块导入后全变成脉冲块（行为完全变了）。实测发现。
            blocks.append(_mk_cmd_block(d, x, y, z, _convert, warnings,
                                        converted_cmds, kept_cmds,
                                        state=_cmd_block_for_mode(d.get('mode'))))
            continue

        if nm == 'PlaceBlockWithCommandBlockData':
            state, note = _bedrock_block_to_java(d.get('block') or 'minecraft:command_block',
                                                 d.get('blockData') or 0)
            if note:
                warnings.append(note)
            # BDX 里若给的方块名是泛指的命令块，仍按 mode 细分到 Java 的对应方块
            if 'command_block' in (state or '') and 'repeating' not in state and 'chain' not in state:
                state = _cmd_block_for_mode(d.get('mode'), fallback=state)
            blocks.append(_mk_cmd_block(d, x, y, z, _convert, warnings,
                                        converted_cmds, kept_cmds, state=state))
            continue

        if nm.startswith('Place'):
            # 普通方块
            if 'runtimeId' in d:
                skipped['runtime_id_block'] = skipped.get('runtime_id_block', 0) + 1
                continue
            if 'nbt' in d and d.get('nbt'):
                # PlaceBlockWithNBTData(41)：带 NBT 的方块。
                #
                # ⚠️ **命令块大量走这条路**（实测用户的 BDX：1442/1554 个方块是 41），
                #    它的方块名在 `block`、状态在 `states`、命令在 NBT 的 `Command`。
                #    原来这里一律当"容器等普通带 NBT 方块"处理 → **命令一个都不转换**
                #    （cmd_converted 恒为 0，用户报"转换有问题"就是这个）。
                state, note = _bedrock_block_to_java(d.get('block') or '',
                                                     states=d.get('states') or '')
                if note:
                    warnings.append(note)
                be = _nbt_dict_to_tag(d['nbt'])
                # 若这是命令块 → 把 NBT 里的 Command 按所选方向转换
                if 'command_block' in (state or ''):
                    raw = _nbt_get_str(d.get('nbt'), 'Command')
                    if raw is not None:
                        java, notes = _convert(raw)
                        _nbt_set_str(d['nbt'], 'Command', java)
                        # ⚠️ NBT 里若写死了旧 id（如 `CommandBlock`）要跟着方块类型改
                        _nbt_fix_cmd_id(d['nbt'], state)
                        if raw.strip():
                            if java != raw:
                                converted_cmds.append({'from': raw, 'to': java,
                                                       'notes': list(notes or [])})
                            else:
                                kept_cmds.append({'cmd': raw, 'notes': list(notes or [])})
                        if notes:
                            warnings.extend(str(x) for x in notes)
                    be = _nbt_dict_to_tag(d['nbt'])
                blocks.append({'x': x, 'y': y, 'z': z,
                               'state': state or 'minecraft:stone', 'nbt': be})
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


def _mk_cmd_block(d: dict, x: int, y: int, z: int, convert, warnings,
                  converted_cmds: list, kept_cmds: list, state: str = ''):
    """造一个命令块。`convert` 是**方向感知**的转换闭包（由 to_schematic 注入）。

    ⚠️ **方块状态必须与命令块的属性对应**，不能只转命令文本：
      · 脉冲/循环/连锁 → Java 是**三个不同方块**（由 mode 决定）；
      · `conditional`（有条件）→ Java 用**方块状态** `conditional=true`，
        基岩是 0/1 字段。原来我把它塞进 NBT 的 `conditionMet`
        （那是运行时字段），等于**条件开关丢了**；
      · `facing`（朝向）→ 基岩在 `data` 低 3 位（0=下…5=东），Java 是 `facing=` 状态。
    这三项不处理，导入后的命令块在游戏里表现会和原建筑不一致。
    """
    raw = d.get('command') or ''
    java, notes = convert(raw)
    notes = list(notes or [])
    if not raw.strip():
        notes = []
    elif java != raw:
        converted_cmds.append({'from': raw, 'to': java, 'notes': notes})
    else:
        kept_cmds.append({'cmd': raw, 'notes': notes})

    # ---- 方块类型：优先用显式传入的，否则由 mode 推
    if not state:
        state = _cmd_block_for_mode(d.get('mode'))
    elif 'command_block' in state and 'repeating' not in state and 'chain' not in state:
        state = _cmd_block_for_mode(d.get('mode'), fallback=state)

    # ---- 条件 + 朝向 → 方块状态
    cond = bool(d.get('conditional'))
    # 基岩的朝向在 data / blockData 的低 3 位。
    # ⚠️ **只在数据位明确非 0 时才设 facing**：command block 的 `data=0` 很可能是
    #    "未指定/默认"，而命令块的默认朝向是**上**；若把 0 解读成 `facing=down`
    #    就会把方块**放歪**（0→down 只是"朝向编码表"里的位置，不代表这里确实要 down）。
    #    宁可少设一个状态（游戏用默认值），也不要把朝向设错。
    dir_raw = d.get('data', d.get('blockData'))
    facing = None
    if dir_raw is not None:
        try:
            dv = int(dir_raw)
            if (dv & 0x07) != 0:
                facing = _cmd_block_facing(dv & 0x07)
        except (TypeError, ValueError):
            facing = None
    final_state = _cmd_block_state(d.get('mode'), conditional=cond, facing=facing)

    be = _command_block_entity(d, java, notes, state=final_state)
    return {'x': x, 'y': y, 'z': z, 'state': final_state, 'nbt': be}


def _apply_set_cmd(blocks: list, x: int, y: int, z: int, d: dict, convert, warnings,
                   converted_cmds: list, kept_cmds: list) -> bool:
    """SetCommandBlockData(26) 是给**已有**命令块补命令，找最近的那个命令块改。"""
    for b in reversed(blocks):
        if 'command_block' in (b.get('state') or ''):
            raw = d.get('command') or ''
            java, notes = convert(raw)
            # 沿用该方块自身的 state（脉冲/循环/连锁），实体 id 要与之一致
            b['nbt'] = _command_block_entity(d, java, list(notes or []),
                                             state=b.get('state') or 'minecraft:command_block')
            if raw.strip():
                if java != raw:
                    converted_cmds.append({'from': raw, 'to': java, 'notes': list(notes or [])})
                else:
                    kept_cmds.append({'cmd': raw, 'notes': list(notes or [])})
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

def parse_bytes(raw: bytes, limits=None, source: str = '',
                cmd_convert: str = 'be2java'):
    """解析**已在内存里**的 .bdx 数据 → Schematic（供 zip 成员等场景复用）。"""
    inner = decompress_inner(raw)
    author, ops, strings, warns = scan_operations(inner)
    return to_schematic(author, ops, strings, warns,
                        source=source, cmd_convert=cmd_convert)


def parse_bdx(path: str, limits=None, source: str = '',
              cmd_convert: str = 'be2java'):
    """解析磁盘上的 .bdx 文件 → Schematic。

    `cmd_convert`: 'be2java'（默认）| 'java2be' | 'none' —— 见 to_schematic。
    """
    if not os.path.isfile(path):
        raise BdxError('文件不存在')
    with open(path, 'rb') as f:
        raw = f.read()
    return parse_bytes(raw, limits, source=source or path, cmd_convert=cmd_convert)
