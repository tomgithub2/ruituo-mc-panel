"""极简 NBT 读写：二进制 NBT、SNBT、压缩探测、解压炸弹防护。

设计要点
  · 支持全部 13 种 tag（含 byte/short/int/long/float/double 数组）。
  · :class:`Tag` 显式携带类型名，写入时不需要再猜（列表元素类型必须一致）。
  · **安全上限**：递归深度、集合元素数、字符串长度、数组长度、总 tag 数、解压体积。
    恶意 NBT（深层嵌套 / 超大数组）会在解析前/解析中直接抛 :class:`NbtError`，
    绝不会把面板主进程打爆。
  · 压缩探测：先 gzip，再 zlib，最后按未压缩处理（顺序见 :func:`decompress`）。
"""
import gzip
import io
import struct
import zlib

# ---------------------------------------------------------------- 常量

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TYPE_NAMES = {
    TAG_END: 'end', TAG_BYTE: 'byte', TAG_SHORT: 'short', TAG_INT: 'int', TAG_LONG: 'long',
    TAG_FLOAT: 'float', TAG_DOUBLE: 'double', TAG_BYTE_ARRAY: 'bytes', TAG_STRING: 'string',
    TAG_LIST: 'list', TAG_COMPOUND: 'compound', TAG_INT_ARRAY: 'ints', TAG_LONG_ARRAY: 'longs',
}
NAME_TYPES = {v: k for k, v in TYPE_NAMES.items()}

# 安全上限（可在构造 NbtLimits 时覆盖）
MAX_DEPTH = 64
MAX_LIST_ITEMS = 1 << 22          # 4,194,304 个元素
MAX_COMPOUND_KEYS = 1 << 20       # 1,048,576 个键
MAX_STRING_BYTES = 1 << 20        # 1 MiB
MAX_ARRAY_ITEMS = 1 << 24         # 16,777,216 个元素
MAX_TAGS = 1 << 24                # 总 tag 数
MAX_RAW_BYTES = 512 * 1024 * 1024  # 解压后最多 512 MiB
MAX_INPUT_BYTES = 512 * 1024 * 1024


class NbtError(Exception):
    """NBT 解析/写入错误（含安全上限触发）。"""


class NbtLimits:
    __slots__ = ('depth', 'list_items', 'compound_keys', 'string_bytes', 'array_items',
                 'tags', 'raw_bytes', 'input_bytes')

    def __init__(self, depth=MAX_DEPTH, list_items=MAX_LIST_ITEMS, compound_keys=MAX_COMPOUND_KEYS,
                 string_bytes=MAX_STRING_BYTES, array_items=MAX_ARRAY_ITEMS, tags=MAX_TAGS,
                 raw_bytes=MAX_RAW_BYTES, input_bytes=MAX_INPUT_BYTES):
        self.depth = int(depth)
        self.list_items = int(list_items)
        self.compound_keys = int(compound_keys)
        self.string_bytes = int(string_bytes)
        self.array_items = int(array_items)
        self.tags = int(tags)
        self.raw_bytes = int(raw_bytes)
        self.input_bytes = int(input_bytes)


DEFAULT_LIMITS = NbtLimits()


class Tag:
    """一个 NBT tag。``type`` 是类型名（见 TYPE_NAMES），``value`` 是 Python 值。"""

    __slots__ = ('type', 'value')

    def __init__(self, type_, value):
        self.type = type_
        self.value = value

    # ---------------------------------------------------------- 构造糖
    @staticmethod
    def byte(v):
        return Tag('byte', int(v))

    @staticmethod
    def short(v):
        return Tag('short', int(v))

    @staticmethod
    def int(v):
        return Tag('int', int(v))

    @staticmethod
    def long(v):
        return Tag('long', int(v))

    @staticmethod
    def float(v):
        return Tag('float', float(v))

    @staticmethod
    def double(v):
        return Tag('double', float(v))

    @staticmethod
    def string(v):
        return Tag('string', str(v))

    @staticmethod
    def compound(d=None):
        return Tag('compound', dict(d or {}))

    @staticmethod
    def list_(items, elem_type=None):
        items = list(items)
        if elem_type is None:
            elem_type = items[0].type if items else 'end'
        return Tag('list', {'type': elem_type, 'items': items})

    @staticmethod
    def byte_array(data):
        return Tag('bytes', bytes(data))

    @staticmethod
    def int_array(data):
        return Tag('ints', [int(x) for x in data])

    @staticmethod
    def long_array(data):
        return Tag('longs', [int(x) for x in data])

    # ---------------------------------------------------------- 取值
    def as_int(self, default=0):
        try:
            if self.type in ('byte', 'short', 'int', 'long'):
                return int(self.value)
            if self.type in ('float', 'double'):
                return int(self.value)
        except Exception:
            pass
        return default

    def as_str(self, default=''):
        return self.value if self.type == 'string' else default

    def as_list(self, elem_type: str = None):
        if self.type == 'list':
            if elem_type is not None and self.value.get('type') != elem_type:
                return []
            return self.value['items']
        if self.type in ('bytes', 'ints', 'longs'):
            if elem_type not in (None, 'int', 'byte', 'long'):
                return []
            return self.value
        return []

    def as_compound(self):
        return self.value if self.type == 'compound' else {}

    def __repr__(self):
        return f'Tag({self.type}, {self.value!r})'


# ---------------------------------------------------------------- 便捷取值

def tag(t, default=None):
    return t if isinstance(t, Tag) else default


def cget(node, key, default=None):
    """从 compound Tag（或 dict）里取子 tag。"""
    if isinstance(node, Tag):
        if node.type != 'compound':
            return default
        return node.value.get(key, default)
    if isinstance(node, dict):
        return node.get(key, default)
    return default


def cget_int(node, key, default=0):
    t = cget(node, key)
    return t.as_int(default) if isinstance(t, Tag) else default


def cget_str(node, key, default=''):
    t = cget(node, key)
    return t.as_str(default) if isinstance(t, Tag) else default


def cget_list(node, key, elem_type: str = None):
    """取列表内容。``elem_type`` 给定时校验元素类型（'compound' 等）。"""
    t = cget(node, key)
    return t.as_list(elem_type) if isinstance(t, Tag) else []


def cget_compound(node, key):
    t = cget(node, key)
    return t.as_compound() if isinstance(t, Tag) else {}


def unwrap(t):
    """Tag → 纯 Python 值（dict/list/str/int/bytes），便于调试与 JSON 化。"""
    if not isinstance(t, Tag):
        return t
    if t.type == 'compound':
        return {k: unwrap(v) for k, v in t.value.items()}
    if t.type == 'list':
        return [unwrap(x) for x in t.value['items']]
    if t.type == 'bytes':
        return t.value
    return t.value


# ---------------------------------------------------------------- 压缩

def detect_compression(raw: bytes) -> str:
    """返回 'gzip' | 'zlib' | 'none'。"""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        return 'gzip'
    if len(raw) >= 2:
        cmf, flg = raw[0], raw[1]
        if (cmf & 0x0F) == 8 and (cmf >> 4) <= 7 and ((cmf << 8) + flg) % 31 == 0:
            return 'zlib'
    return 'none'


def decompress(raw: bytes, limits: NbtLimits = DEFAULT_LIMITS, max_bytes: int = None) -> tuple:
    """探测并解压 NBT 数据，返回 ``(data, kind)``。

    解压是**流式**的，一旦超过 ``max_bytes`` 立刻中止并抛 :class:`NbtError`，
    因此 zip/NBT 炸弹不会先把内存吃掉。
    """
    cap = int(max_bytes if max_bytes is not None else limits.raw_bytes)
    if len(raw) > limits.input_bytes:
        raise NbtError(f'输入超过上限（{len(raw)} > {limits.input_bytes} 字节）')
    kind = detect_compression(raw)
    if kind == 'gzip':
        src = io.BytesIO(raw)
        out = io.BytesIO()
        with gzip.GzipFile(fileobj=src) as gz:
            while True:
                chunk = gz.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > cap:
                    raise NbtError(f'解压后体积超过上限 {cap} 字节（疑似压缩炸弹）')
        return out.getvalue(), kind
    if kind == 'zlib':
        d = zlib.decompressobj()
        out = io.BytesIO()
        buf = raw
        while buf:
            try:
                piece = d.decompress(buf, 1 << 16)
            except zlib.error as e:
                raise NbtError(f'zlib 解压失败：{e}')
            buf = d.unconsumed_tail
            out.write(piece)
            if out.tell() > cap:
                raise NbtError(f'解压后体积超过上限 {cap} 字节（疑似压缩炸弹）')
            if not buf:
                break
            if d.eof:
                break
        return out.getvalue(), kind
    return raw, kind


def compress(data: bytes, kind: str = 'gzip') -> bytes:
    if kind == 'gzip':
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6, mtime=0) as gz:
            gz.write(data)
        return buf.getvalue()
    if kind == 'zlib':
        return zlib.compress(data, 6)
    return data


# ---------------------------------------------------------------- 二进制读取

class _Reader:
    def __init__(self, data: bytes, limits: NbtLimits):
        self.d = data
        self.i = 0
        self.limits = limits
        self.count = 0

    def need(self, n: int):
        if self.i + n > len(self.d):
            raise NbtError('NBT 数据意外结束（文件被截断或格式不对）')

    def u1(self) -> int:
        self.need(1)
        v = self.d[self.i]
        self.i += 1
        return v

    def i1(self) -> int:
        v = self.u1()
        return v - 256 if v > 127 else v

    def u2(self) -> int:
        self.need(2)
        v = struct.unpack_from('>H', self.d, self.i)[0]
        self.i += 2
        return v

    def i2(self) -> int:
        self.need(2)
        v = struct.unpack_from('>h', self.d, self.i)[0]
        self.i += 2
        return v

    def i4(self) -> int:
        self.need(4)
        v = struct.unpack_from('>i', self.d, self.i)[0]
        self.i += 4
        return v

    def i8(self) -> int:
        self.need(8)
        v = struct.unpack_from('>q', self.d, self.i)[0]
        self.i += 8
        return v

    def f4(self) -> float:
        self.need(4)
        v = struct.unpack_from('>f', self.d, self.i)[0]
        self.i += 4
        return v

    def f8(self) -> float:
        self.need(8)
        v = struct.unpack_from('>d', self.d, self.i)[0]
        self.i += 8
        return v

    def raw(self, n: int) -> bytes:
        self.need(n)
        v = self.d[self.i:self.i + n]
        self.i += n
        return v

    def name(self) -> str:
        n = self.u2()
        if n > self.limits.string_bytes:
            raise NbtError(f'tag 名称过长（{n} 字节）')
        return self.raw(n).decode('utf-8', 'replace')

    def tagged(self, depth: int, tid: int = None) -> Tag:
        """读一个「带名称的 tag」中的 payload（类型字节已由调用方给出或在此读取）。"""
        self.count += 1
        if self.count > self.limits.tags:
            raise NbtError(f'NBT tag 总数超过上限 {self.limits.tags}')
        if tid is None:
            tid = self.u1()
        return self.payload(tid, depth)

    def payload(self, tid: int, depth: int) -> Tag:
        if depth > self.limits.depth:
            raise NbtError(f'NBT 嵌套超过上限 {self.limits.depth} 层')
        lim = self.limits
        if tid == TAG_BYTE:
            return Tag('byte', self.i1())
        if tid == TAG_SHORT:
            return Tag('short', self.i2())
        if tid == TAG_INT:
            return Tag('int', self.i4())
        if tid == TAG_LONG:
            return Tag('long', self.i8())
        if tid == TAG_FLOAT:
            return Tag('float', self.f4())
        if tid == TAG_DOUBLE:
            return Tag('double', self.f8())
        if tid == TAG_BYTE_ARRAY:
            n = self.i4()
            if n < 0 or n > lim.array_items:
                raise NbtError(f'byte 数组长度非法（{n}）')
            return Tag('bytes', self.raw(n))
        if tid == TAG_STRING:
            n = self.u2()
            if n > lim.string_bytes:
                raise NbtError(f'字符串长度超过上限（{n} 字节）')
            return Tag('string', self.raw(n).decode('utf-8', 'replace'))
        if tid == TAG_LIST:
            et = self.u1()
            n = self.i4()
            if n < 0 or n > lim.list_items:
                raise NbtError(f'列表长度非法（{n}）')
            if et == TAG_END and n > 0:
                raise NbtError('列表元素类型为 TAG_End 但长度不为 0')
            items = []
            for _ in range(n):
                items.append(self.tagged(depth + 1, et))
            return Tag('list', {'type': TYPE_NAMES.get(et, 'end'), 'items': items})
        if tid == TAG_COMPOUND:
            out = {}
            while True:
                sub = self.u1()
                if sub == TAG_END:
                    break
                if len(out) >= lim.compound_keys:
                    raise NbtError(f'compound 键数超过上限 {lim.compound_keys}')
                key = self.name()
                out[key] = self.tagged(depth + 1, sub)
            return Tag('compound', out)
        if tid == TAG_INT_ARRAY:
            n = self.i4()
            if n < 0 or n > lim.array_items:
                raise NbtError(f'int 数组长度非法（{n}）')
            self.need(4 * n)
            vals = list(struct.unpack_from('>%di' % n, self.d, self.i))
            self.i += 4 * n
            return Tag('ints', vals)
        if tid == TAG_LONG_ARRAY:
            n = self.i4()
            if n < 0 or n > lim.array_items:
                raise NbtError(f'long 数组长度非法（{n}）')
            self.need(8 * n)
            vals = list(struct.unpack_from('>%dq' % n, self.d, self.i))
            self.i += 8 * n
            return Tag('longs', vals)
        raise NbtError(f'未知 tag 类型 id={tid}')


class _CompoundReader(_Reader):
    """保留别名，便于外部引用（读取逻辑完全继承自 :class:`_Reader`）。"""


def parse(data: bytes, limits: NbtLimits = DEFAULT_LIMITS) -> Tag:
    """解析未压缩的二进制 NBT，返回根 tag（通常是 ``{'': compound}`` 形态的 Tag）。"""
    r = _CompoundReader(data, limits)
    if len(data) == 0:
        raise NbtError('NBT 数据为空')
    tid = r.u1()
    if tid == TAG_END:
        raise NbtError('NBT 根 tag 为 TAG_End')
    if tid not in (TAG_COMPOUND, TAG_LIST):
        # 宽容处理：允许根是其它单一 tag（少数工具会这么写）
        return r.payload(tid, 0)
    name = r.name()          # 根 tag 名（通常为空字符串）
    node = r.payload(tid, 0)
    return _RootTag(name, node)


class _RootTag(Tag):
    """带根名称的 Tag（根名称在规格里几乎总是空串，但 SNBT 里会用到）。"""

    __slots__ = ('name',)

    def __init__(self, name, node: Tag):
        super().__init__(node.type, node.value)
        self.name = name


def parse_file(path: str, limits: NbtLimits = DEFAULT_LIMITS):
    """读文件 → 探测压缩 → 解析。返回 ``(root_tag, compression_kind)``。"""
    with open(path, 'rb') as f:
        raw = f.read(limits.input_bytes + 1)
    if len(raw) > limits.input_bytes:
        raise NbtError(f'文件超过上限（{limits.input_bytes} 字节）')
    data, kind = decompress(raw, limits)
    return parse(data, limits), kind


def parse_bytes(raw: bytes, limits: NbtLimits = DEFAULT_LIMITS):
    data, kind = decompress(raw, limits)
    return parse(data, limits), kind


# ---------------------------------------------------------------- 二进制写入

def _write_string(out: bytearray, s: str):
    b = str(s).encode('utf-8')
    if len(b) > 65535:
        raise NbtError('字符串过长，无法写入 NBT（>65535 字节）')
    out += struct.pack('>H', len(b))
    out += b


def _pack_nbt_int(fmt: str, name: str, lo: int, hi: int, v, limits) -> bytes:
    """把整数按 NBT 的定长格式打包，**越界时给明确的中文报错**。

    ⚠️ 踩过的坑（2026-10-15）：BDX 解析出错的坐标累积成 **73 亿**，
    一路传到 NBT 序列化层才崩，用户看到的是一句
        `error: 'i' format requires -2147483648 <= number <= 2147483647`
    —— 完全不知道是哪一步、哪个值出了问题。
    这里在写每个整数前**显式校验范围**，并说明"哪个字段、什么值、超出多少"。

    ⚠️ **但"超范围"不都是错误**（同日第二次踩到）：
    基岩有些字段是**无符号 32 位**（实测 `minecraft:structure_block` 的 `seed`
    会出现 3257619968、4194199552 这类值），而 Java 的 NBT int 是**有符号**的。
    同一个 32 位位模式只是解释不同 → 应**按 32 位回绕转成有符号**（保留位模式），
    而不是报错。判据：若 2^32 范围内的无符号值，回绕即成合法有符号值。
    """
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise NbtError('NBT 的 %s 需要整数，实际是 %r' % (name, v))
    # 无符号 32 位 → 有符号（同一位模式，Java 侧语义正确）
    if fmt == '>i' and i > hi and i <= 0xFFFFFFFF:
        i -= 1 << 32
    if i < lo or i > hi:
        raise NbtError(
            'NBT 的 %s 超出取值范围（%d ~ %d），实际是 %d —— '
            '通常是坐标/尺寸算错了（例如建筑文件解析异常导致数值畸形），'
            '请检查建筑文件与目标坐标' % (name, lo, hi, i))
    return struct.pack(fmt, i)


def _write_payload(out: bytearray, t: Tag, depth: int, limits: NbtLimits):
    if depth > limits.depth:
        raise NbtError(f'NBT 嵌套超过上限 {limits.depth} 层')
    ty = t.type
    v = t.value
    if ty == 'byte':
        # byte 按无符号写回（原实现 `& 0xFF` 保留）
        out += bytes((int(v) & 0xFF,))
    elif ty == 'short':
        out += _pack_nbt_int('>h', 'short（短整数）', -32768, 32767, v, limits)
    elif ty == 'int':
        out += _pack_nbt_int('>i', 'int（整数，常用于坐标/尺寸）',
                             -2147483648, 2147483647, v, limits)
    elif ty == 'long':
        i = int(v)
        if i > 0x7FFFFFFFFFFFFFFF:
            i -= 1 << 64
        out += struct.pack('>q', i)
    elif ty == 'float':
        out += struct.pack('>f', float(v))
    elif ty == 'double':
        out += struct.pack('>d', float(v))
    elif ty == 'bytes':
        out += struct.pack('>i', len(v))
        out += bytes(v)
    elif ty == 'string':
        _write_string(out, v)
    elif ty == 'list':
        et = NAME_TYPES.get(v.get('type', 'end'), TAG_END)
        items = v.get('items') or []
        out.append(et)
        out += struct.pack('>i', len(items))
        for it in items:
            _write_payload(out, it, depth + 1, limits)
    elif ty == 'ints':
        vals = list(v)
        out += struct.pack('>i', len(vals))
        if vals:
            out += struct.pack('>%di' % len(vals), *[int(x) for x in vals])
    elif ty == 'longs':
        vals = list(v)
        out += struct.pack('>i', len(vals))
        if vals:
            norm = []
            for x in vals:
                i = int(x)
                if i > 0x7FFFFFFFFFFFFFFF:
                    i -= 1 << 64
                elif i < -0x8000000000000000:
                    i += 1 << 64
                norm.append(i)
            out += struct.pack('>%dq' % len(norm), *norm)
    elif ty == 'compound':
        for k, sub in v.items():
            if not isinstance(sub, Tag):
                raise NbtError(f'compound 值不是 Tag：{k!r}')
            sid = NAME_TYPES.get(sub.type, TAG_END)
            out.append(sid)
            _write_string(out, k)
            _write_payload(out, sub, depth + 1, limits)
        out.append(TAG_END)
    else:
        raise NbtError(f'未知 tag 类型 {ty!r}')


def write(root: Tag, name: str = '') -> bytes:
    """把根 tag 写成未压缩二进制 NBT。"""
    out = bytearray()
    tid = NAME_TYPES.get(root.type, TAG_END)
    if tid == TAG_END:
        raise NbtError('根 tag 不能是 end')
    out.append(tid)
    _write_string(out, name or getattr(root, 'name', '') or '')
    _write_payload(out, root, 0, DEFAULT_LIMITS)
    return bytes(out)


def write_file(path: str, root: Tag, compression: str = 'gzip', name: str = ''):
    data = write(root, name)
    with open(path, 'wb') as f:
        f.write(compress(data, compression))
    return path


# ---------------------------------------------------------------- SNBT

_SNBT_PLAIN = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.+')


def snbt_quote(s: str) -> str:
    s = str(s)
    if s and all(c in _SNBT_PLAIN for c in s) and s[0] not in '0123456789-.+':
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def to_snbt(t: Tag, depth: int = 0) -> str:
    ty, v = t.type, t.value
    if ty == 'byte':
        return f'{int(v)}b'
    if ty == 'short':
        return f'{int(v)}s'
    if ty == 'int':
        return f'{int(v)}'
    if ty == 'long':
        i = int(v)
        if i > 0x7FFFFFFFFFFFFFFF:
            i -= 1 << 64
        return f'{i}L'
    if ty == 'float':
        return f'{float(v)}f'
    if ty == 'double':
        return f'{float(v)}d'
    if ty == 'string':
        return snbt_quote(v)
    if ty == 'bytes':
        return '[B;' + ','.join(f'{b if b < 128 else b - 256}b' for b in v) + ']'
    if ty == 'ints':
        return '[I;' + ','.join(str(int(x)) for x in v) + ']'
    if ty == 'longs':
        return '[L;' + ','.join(to_snbt(Tag('long', x)) for x in v) + ']'
    if ty == 'list':
        return '[' + ','.join(to_snbt(x, depth + 1) for x in v.get('items') or []) + ']'
    if ty == 'compound':
        return '{' + ','.join(f'{snbt_quote(k)}:{to_snbt(sub, depth + 1)}'
                              for k, sub in v.items()) + '}'
    return 'null'


# ---------------------------------------------------------------- 小工具

def new_root(children: dict = None) -> Tag:
    return Tag('compound', dict(children or {}))
