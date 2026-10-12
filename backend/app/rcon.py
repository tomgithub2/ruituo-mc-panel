"""最小 RCON 客户端（Source RCON 协议）+ TPS/MSPT 采集。

协议：小端 length(4) + request_id(4) + type(4) + body(NUL) + 0x00
type: 3=LOGIN 2=COMMAND 0=RESPONSE_VALUE
任何失败（端口不通/认证失败/超时）都抛 RconError，由上层标注"不可用"。

TPS/MSPT（P0-3）
  · 优先 RCON：`/forge tps`、`/neoforge tps`（Forge/NeoForge）、`spark tps`、
    `tps`（Paper/Spigot 系）；
  · 退化：解析服务端日志（`TPS from last 1m, 5m, 15m:`、`Can't keep up!`、`Done (x.xxxs)!`）；
  · **拿不到就是 null**：绝不回 0、不猜数；`available=false` + 中文 `note` 说明原因。
"""
import os
import re
import socket
import struct
import threading
import time

# ---------------------------------------------------------------- 解析
_COLOR = re.compile(r'\u00a7[0-9A-FK-ORa-fk-or]')
_RE_TRIPLE = re.compile(
    r'TPS from last 1m, 5m, 15m:\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)', re.I)
_RE_TPS_HEAD = re.compile(r'TPS\s*(?:\([^)]*\))?\s*(?:from last)?\s*:?\s*(.+)', re.I)
#: 窗口标签（5s / 1m / 5m / 15m …）
_RE_WIN_LABEL = re.compile(r'\b(\d+[smhd])\b', re.I)
#: 数值（去掉末尾逗号，避免把分隔符吃进数字里）
_RE_FLOAT = re.compile(r'([0-9]+(?:\.[0-9]+)?)\s*,?')
#: 「窗口:数值」成对写法（另一种服务端变体）
_RE_WIN_PAIR = re.compile(r'(\d+[smhd])\s*:\s*([0-9.]+)', re.I)
_RE_TRIPLE2 = re.compile(
    r'TPS\s*(?:\(1m, ?5m, ?15m\)|from last)\s*:?\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)', re.I)
_RE_FORGE = re.compile(
    r'(Overall|Dim\s+\S+).*?Mean tick time:\s*([0-9.]+)\s*ms.*?Mean TPS:\s*([0-9.]+)', re.I | re.S)
_RE_FORGE_OVERALL = re.compile(r'Overall.*?Mean tick time:\s*([0-9.]+)\s*ms.*?Mean TPS:\s*([0-9.]+)',
                               re.I | re.S)
_RE_TICKTIME = re.compile(
    r'Tick(?:\s*Times?|\s*durations?)[^:]*:\s*([0-9.]+)\s*/\s*([0-9.]+)\s*/\s*([0-9.]+)', re.I)
_RE_PAPER_TICK = re.compile(r'tick times[^:]*:\s*(.+?)(?:\n|$)', re.I)
_RE_TRIPLET = re.compile(r'([0-9.]+)\s*/\s*([0-9.]+)\s*/\s*([0-9.]+)')
_RE_MSPT = re.compile(r'(?:MSPT|ms/t|ms per tick)[^0-9]{0,12}([0-9.]+)', re.I)
_RE_CANT_KEEP_UP = re.compile(
    r"Can't keep up!.*?(?:Running\s+(\d+)\s*ms behind|skipping\s+(\d+)\s+tick)", re.I | re.S)
_RE_DONE = re.compile(r'Done \(([0-9.]+)s\)!', re.I)
_RE_UNKNOWN = re.compile(r'unknown or incomplete command|unknown command|command not found|'
                         r'incorrect argument|is not a (?:valid|known) command', re.I)


def strip_mc_colors(text: str) -> str:
    """去掉 Minecraft 的 §x 颜色/格式码（Paper 的 tps/mspt 输出带颜色码）。"""
    return _COLOR.sub('', text or '')


def _parse_tps_windows(clean: str):
    """解析 TPS 输出，返回 {1m,5m,15m} 或 None。

    覆盖 Paper/Purpur 的多种写法（实测踩过坑：只兼容一种会一条都解析不出来）：
        TPS from last 1m, 5m, 15m: 20.0, 19.5, 18.0          ← 旧版：窗口是「标签列表」
        TPS from last 5s, 1m, 5m, 15m: 20.0, 20.0, 20.0, 20.0 ← 实测 Purpur 26.x
        TPS (1m, 5m, 15m): 20.0, 20.0, 20.0
        TPS: 5s: 20.0, 1m: 20.0, 5m: 20.0                     ← 成对写法

    关键点：**窗口标签和数值是分开的两组**（`5s, 1m, 5m, 15m: 20.0, 20.0, …`），
    所以不能按「窗口:数值」去配对；改为分别取出两组再按顺序对齐。
    """
    head = _RE_TPS_HEAD.search(clean)
    if not head:
        return None
    body = head.group(1)

    # ① 成对写法：窗口跟着自己的数值
    pairs = _RE_WIN_PAIR.findall(body)
    if len(pairs) >= 3:
        by = {}
        for win, val in pairs:
            try:
                by[win.lower()] = float(val)
            except ValueError:
                pass
        if '1m' in by and '5m' in by and '15m' in by:
            return {'1m': by['1m'], '5m': by['5m'], '15m': by['15m']}

    # ② 分开写法：先取全部数值，再取窗口标签，按顺序对齐
    vals = [float(x) for x in _RE_FLOAT.findall(body)]
    if len(vals) < 3:
        return None
    wins = [w.lower() for w in _RE_WIN_LABEL.findall(body)]
    # 标签数应与数值数相当（不算 section 符号等噪声）
    if len(wins) == len(vals):
        by = dict(zip(wins, vals))
        if '1m' in by and '5m' in by and '15m' in by:
            return {'1m': by['1m'], '5m': by['5m'], '15m': by['15m']}
    # ③ 兜底：取最后三个数当作 1m/5m/15m（1m,5m,15m 与 5s,1m,5m,15m 的尾部都是这三个）
    return {'1m': vals[-3], '5m': vals[-2], '15m': vals[-1]}


def parse_tps_text(text: str) -> dict:
    """从命令/日志输出里解析 TPS 与 MSPT（解析不出来就返回空，不猜）。"""
    raw = (text or '')
    clean = strip_mc_colors(raw)
    out = {'tps': None, 'mspt': None, 'note': '', 'raw': raw[:1500], 'clean': clean[:1500],
           'unknown_cmd': bool(_RE_UNKNOWN.search(clean)) and not _RE_TRIPLE.search(clean)}
    if not clean:
        return out
    m = _RE_TRIPLE.search(clean) or _RE_TRIPLE2.search(clean)
    if m:
        vals = [float(m.group(i)) for i in (1, 2, 3)]
        out['tps'] = {'1m': vals[0], '5m': vals[1], '15m': vals[2]}
    if out['tps'] is None:
        # 兜底：通用「窗口:数值」解析（覆盖 5s/1m/5m/15m 这类变体）
        w = _parse_tps_windows(clean)
        if w:
            out['tps'] = w
    if out['tps'] is None:
        m = _RE_FORGE_OVERALL.search(clean)
        if m:
            out['tps'] = {'1m': float(m.group(2)), '5m': None, '15m': None}
            out['mspt'] = {'avg': float(m.group(1)), 'p95': None}
            out['note'] = 'Forge/NeoForge 只提供整体 TPS 与平均 tick 时间，5m/15m 与 p95 无数据'
        elif _RE_FORGE.search(clean):
            m2 = _RE_FORGE.search(clean)
            out['tps'] = {'1m': float(m2.group(3)), '5m': None, '15m': None}
            out['mspt'] = {'avg': float(m2.group(2)), 'p95': None}
            out['note'] = 'Forge/NeoForge 只提供整体 TPS 与平均 tick 时间，5m/15m 与 p95 无数据'
    if out['mspt'] is None:
        # Paper/Spigot：Server tick times (avg/min/max) from last 5s, 10s, 1m: a/b/c, a/b/c, a/b/c
        m = _RE_PAPER_TICK.search(clean)
        if m:
            trips = _RE_TRIPLET.findall(m.group(1))
            if trips:
                avg, mn, mx = [float(x) for x in trips[-1]]      # 最后一组 = 1m
                out['mspt'] = {'avg': avg, 'p95': None, 'min': mn, 'max': mx,
                               'window': '1m', 'windows': '5s/10s/1m'}
        if out['mspt'] is None:
            m = _RE_TICKTIME.search(clean)
            if m:
                out['mspt'] = {'avg': float(m.group(1)), 'p95': float(m.group(2))}
            else:
                m = _RE_MSPT.search(clean)
                if m:
                    try:
                        out['mspt'] = {'avg': float(m.group(1)), 'p95': None}
                    except ValueError:
                        pass
    return out


def parse_log_tps(lines) -> dict:
    """退化路径：从日志行里找 TPS / 掉帧 / 启动耗时。"""
    joined = '\n'.join(lines or [])
    parsed = parse_tps_text(joined)
    signals = {'cant_keep_up': 0, 'lag_ms': 0, 'startup_seconds': None, 'done': False}
    for m in _RE_CANT_KEEP_UP.finditer(joined):
        signals['cant_keep_up'] += 1
        if m.group(1):
            try:
                signals['lag_ms'] = max(signals['lag_ms'], int(m.group(1)))
            except ValueError:
                pass
    m = _RE_DONE.search(joined)
    if m:
        signals['done'] = True
        try:
            signals['startup_seconds'] = float(m.group(1))
        except ValueError:
            pass
    parsed['log_signals'] = signals
    return parsed


# ---------------------------------------------------------------- 客户端
class RconError(Exception):
    pass


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = int(port)
        self.password = password or ''
        self.timeout = timeout
        self._sock = None
        self._lock = threading.Lock()
        self._rid = 0

    # ------------------------------------------------------------ 传输
    def _next_id(self) -> int:
        self._rid = (self._rid + 1) % 0x7FFFFFFF
        return self._rid

    def _send(self, rid: int, ptype: int, body: str):
        payload = struct.pack('<ii', rid, ptype) + body.encode('utf-8') + b'\x00\x00'
        self._sock.sendall(struct.pack('<i', len(payload)) + payload)

    def _recv(self) -> tuple:
        raw = self._recv_exact(4)
        (length,) = struct.unpack('<i', raw)
        if length < 10 or length > 4 * 1024 * 1024:
            raise RconError('响应长度异常')
        data = self._recv_exact(length)
        rid, ptype = struct.unpack('<ii', data[:8])
        body = data[8:-2].decode('utf-8', 'replace')
        return rid, ptype, body

    def _recv_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RconError('连接被服务端关闭')
            buf += chunk
        return buf

    # ------------------------------------------------------------ 连接
    def connect(self):
        try:
            self._sock = socket.create_connection((self.host, self.port), self.timeout)
            self._sock.settimeout(self.timeout)
        except OSError as e:
            raise RconError(f'无法连接 RCON {self.host}:{self.port}（{e}）')
        rid = self._next_id()
        self._send(rid, 3, self.password)
        try:
            rrid, _ptype, _body = self._recv()
        except RconError:
            self.close()
            raise
        except OSError as e:
            self.close()
            raise RconError(f'RCON 认证超时（{e}）')
        if rrid == -1:
            self.close()
            raise RconError('RCON 认证失败：密码不正确')
        return self

    def close(self):
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    # ------------------------------------------------------------ 命令
    def command(self, cmd: str, timeout: float = None) -> str:
        with self._lock:
            if self._sock is None:
                self.connect()
            if timeout:
                self._sock.settimeout(timeout)
            rid = self._next_id()
            self._send(rid, 2, cmd)
            out = []
            try:
                while True:
                    rrid, _ptype, body = self._recv()
                    if rrid == rid:
                        out.append(body)
                        # 分片响应：短超时探测是否还有后续，超时当作读完
                        self._sock.settimeout(0.35)
            except (RconError, OSError):
                pass
            if timeout:
                self._sock.settimeout(self.timeout)
            return ''.join(out)

    def __enter__(self):
        return self.connect()

    def __exit__(self, *a):
        self.close()


def try_command(host: str, port: int, password: str, cmd: str, timeout: float = 5.0) -> tuple:
    """返回 (ok, text)；失败时 ok=False 且 text 为失败原因。"""
    c = RconClient(host, port, password, timeout)
    try:
        c.connect()
        text = c.command(cmd)
        if not text.strip():
            return False, 'RCON 无响应内容'
        return True, text
    except RconError as e:
        return False, str(e)
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    finally:
        c.close()


def try_commands(host: str, port: int, password: str, cmds, timeout: float = 5.0) -> list:
    """在**同一条 RCON 连接**上按序执行多条命令，返回 [{cmd, ok, text, error}]。"""
    out = []
    c = RconClient(host, port, password, timeout)
    try:
        c.connect()
    except RconError as e:
        return [{'cmd': cmd, 'ok': False, 'text': '', 'error': str(e)} for cmd in cmds]
    try:
        for cmd in cmds:
            try:
                text = c.command(cmd)
                if not text.strip():
                    out.append({'cmd': cmd, 'ok': False, 'text': '',
                                'error': 'RCON 无响应内容（命令可能不存在）'})
                else:
                    out.append({'cmd': cmd, 'ok': True, 'text': text, 'error': ''})
            except Exception as e:
                out.append({'cmd': cmd, 'ok': False, 'text': '',
                            'error': f'{type(e).__name__}: {e}'})
    finally:
        c.close()
    return out


# ---------------------------------------------------------------- TPS 采集
_tps_cache = {}
_tps_lock = threading.RLock()
#: 实例 id → 最近一次「有人请求 TPS」的时间。
#: 采样器据此决定要不要主动开 RCON 连接（没有观察者就不采，见 tps_recently_watched）。
_tps_watched = {}


def _rcon_creds(row: dict, cfg: dict) -> tuple:
    cfg = cfg or {}
    port = int((row or {}).get('rcon_port') or cfg.get('rcon_port') or 25575)
    pwd = (row or {}).get('rcon_password') or cfg.get('rcon_password') or ''
    host = cfg.get('rcon_host') or '127.0.0.1'
    return host, port, pwd


def tps_commands_for(core_type: str) -> list:
    """按核心类型排好"最可能有数据"的命令顺序（RCON 下发不带前导 /）。

    Paper/Spigot/Purpur：`tps` + `mspt`；Forge/NeoForge：`forge tps` / `neoforge tps`。

    ⚠️ 为什么不再无脑把全部命令都试一遍（实测教训）：
      · Purpur 上 `forge tps` / `neoforge tps` **必然不存在**，试它们只是白开一轮往返；
      · 服务端对 RCON 命令有并发限制，试探不存在的命令会让它在控制台刷
        `failed main thread check: Cannot perform command async`（实测 6 次），
        既污染日志又没有产出；
      · `spark tps` 从"每轮都试"改成"不试"：Purpur 虽内置 spark，
        但 `tps`/`mspt` 已能给出数据，多试一条只会多一次被拒。
    """
    core = str(core_type or '').lower()
    if core in ('forge', 'neoforge'):
        return [f'{core} tps']
    return ['tps', 'mspt']


def tps_scalar(tps, window: str = '1m'):
    """把 collect_tps 返回的 tps dict/值**归一成单个浮点数**，供写进 metrics 表用。

    ⚠️ 为什么需要它：`metrics.tps` / `metrics.mspt` 是 **REAL 列**（存单个数字），
    而 collect_tps 返回的是 `{'1m': 20.0, '5m': .., '15m': ..}` 这种 dict。
    直接把 dict 塞给 sqlite3 会报
        `Error binding parameter N - probably unsupported type`
    于是**整条采样都写不进去**（实测：监控曲线长时间没有任何数据，
    且原来的 `except: pass` 把这条报错完全吞了，只能靠猜）。
    """
    if tps is None:
        return None
    if isinstance(tps, (int, float)):
        return float(tps)
    if isinstance(tps, dict):
        for k in (window, '1m', '5m', '15m'):
            v = tps.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


def mspt_scalar(mspt):
    """把 mspt dict/值归一成单个浮点数（avg 优先）。理由同 tps_scalar。"""
    if mspt is None:
        return None
    if isinstance(mspt, (int, float)):
        return float(mspt)
    if isinstance(mspt, dict):
        for k in ('avg', 'mean', 'p95'):
            v = mspt.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


#: 「单飞」锁：同一实例同一时刻只允许一次真实抓取。
#: ⚠️ 为什么必须有它：`_tps_lock` 只保护缓存字典，**不保护抓取过程** ——
#:    采样器（每 5 秒）与前端轮询的 `/status`（每 1 秒）会**同时穿透缓存**，
#:    各自新开一条 RCON 连接。服务端每接一次连接就往控制台打两行
#:    `Thread RCON Client ... started / shutting down`，日志被刷屏（用户实测反馈）。
_flight_locks = {}
_flight_guard = threading.Lock()


def _flight_lock(iid):
    with _flight_guard:
        lk = _flight_locks.get(iid)
        if lk is None:
            lk = _flight_locks[iid] = threading.Lock()
        return lk


def collect_tps_on_demand(iid, row: dict, log_lines_fn=None, cfg: dict = None,
                          wait: float = 1.2) -> dict:
    """**按需**取一次 TPS：优先走 stdin（不碰 RCON），取不到才回退 RCON。

    这是用户明确要求的方式：「TPS 不再轮询，只在我点『刷新』时取一次」。
    为什么要分两条路：
      · 面板**自己启动**的实例 → 保留了子进程 stdin（`Popen(stdin=PIPE)`），
        直接写一条 `tps` 命令、再读控制台回显即可 —— **完全不碰 RCON**；
      · 面板重启后**接管的外部实例** → 没有 stdin 通道，只能回退 RCON
        （用户选择「保留 RCON 作为兜底」）。
    两者都失败时 `available=False`，**绝不编造数值**。
    """
    result = {'ok': True, 'available': False, 'tps': None, 'mspt': None,
              'source': None, 'note': '', 'raw': '', 'commands': [],
              'checked_at': time.time(), 'log_signals': None}
    if not iid:
        result['note'] = '缺少实例 id'
        return result

    # ---- 路线 1：stdin（面板启动的实例）
    sent = False
    try:
        from .process_manager import manager
        inst = manager.get(iid)
        proc = getattr(inst, 'proc', None)
        if proc is not None and proc.poll() is None and getattr(proc, 'stdin', None):
            # 服务端核心不同，TPS 命令也不同（沿用 collect_tps 里的同一张表）
            cmds = tps_commands_for((row or {}).get('core_type'))
            for c in cmds:
                r = manager.send_command(iid, c)
                if r.get('ok'):
                    sent = True
                    result['commands'].append({'cmd': c, 'ok': True,
                                              'via': 'stdin'})
                else:
                    result['commands'].append({'cmd': c, 'ok': False,
                                              'via': 'stdin',
                                              'error': (r.get('error') or '')[:200]})
            if sent:
                # ⚠️ 取回显有两条路，**优先读日志文件**：
                #   `_pump` 从子进程 stdout 读到的每一行都会**同时**写进
                #   `<实例目录>/logs/latest.log`（`lf.write(line)`），
                #   所以直接读这个文件的新增内容最可靠 ——
                #   不依赖内存里的 `ring`（ring 是环形缓冲、内容受推送时机影响，
                #   实测它常常取不到命令回显）。
                #   若日志文件读不到，再回退内存 ring。
                fresh = []
                size0 = 0
                logpath = ''
                try:
                    wd = getattr(inst, 'workdir', '') or ''
                    if wd:
                        logpath = os.path.join(wd, 'logs', 'latest.log')
                        if os.path.isfile(logpath):
                            size0 = os.path.getsize(logpath)
                except Exception:
                    logpath, size0 = '', 0

                # 轮询最多 ~2.4 秒，等到出现 TPS 行就立刻返回（通常 <1 秒）
                deadline = time.time() + 2.4
                while True:
                    time.sleep(0.25)
                    if logpath and os.path.isfile(logpath):
                        try:
                            cur = os.path.getsize(logpath)
                            if cur > size0:
                                with open(logpath, 'rb') as f:
                                    f.seek(size0)
                                    chunk = f.read(cur - size0)
                                text = chunk.decode('utf-8', 'replace')
                                fresh = [x for x in text.split('\n') if x.strip()]
                        except Exception:
                            fresh = []
                    if fresh:
                        p = parse_log_tps(fresh)
                        if p.get('tps'):
                            break
                    if time.time() >= deadline:
                        break

                # 日志文件没取到 → 回退内存 ring
                if not fresh:
                    try:
                        tail = inst.tail(400)
                        fresh = [(x.get('line', '') if isinstance(x, dict) else str(x))
                                 for x in tail[-40:]]
                    except Exception as e:
                        result['note'] = 'stdin 读取回显失败：%s' % e
                        fresh = []

                if fresh:
                    p = parse_log_tps(fresh)
                    result['log_signals'] = p.get('log_signals')
                    if p.get('tps'):
                        result['tps'] = p['tps']
                        result['available'] = True
                        result['source'] = 'stdin'
                        result['raw'] = p.get('raw', '')
                        result['tps_note'] = p.get('note') or ''
                    if p.get('mspt') and not result['mspt']:
                        result['mspt'] = p['mspt']
                        result['mspt_source'] = 'stdin'
                    if not result['available']:
                        # 解析失败时把线索留在返回值里（不静默），方便定位
                        result['stdin_debug'] = {
                            'log_file': logpath,
                            'size_before': size0,
                            'fresh_lines': len(fresh),
                            'sample': [str(x)[:120] for x in fresh[-6:]],
                        }
                else:
                    result['stdin_debug'] = {
                        'log_file': logpath, 'size_before': size0,
                        'fresh_lines': 0, 'sample': [],
                        'hint': '命令已发送，但日志文件与内存缓冲都没读到回显',
                    }
    except Exception as e:
        result['note'] = 'stdin 路径异常：%s' % e

    if result['available']:
        return result

    # 若还给了 log_lines_fn，也用它再试一次（可能面板已从日志里拿到了）
    if log_lines_fn:
        try:
            lines = log_lines_fn()
            if lines:
                p = parse_log_tps(lines)
                if p.get('tps'):
                    result['tps'] = p['tps']
                    result['available'] = True
                    result['source'] = 'log'
                    result['raw'] = p.get('raw', '')
                    if p.get('mspt') and not result['mspt']:
                        result['mspt'] = p['mspt']
        except Exception:
            pass
    if result['available']:
        return result

    # ---- 路线 2：回退 RCON（仅"接管的外部实例"需要；用户选择保留作兜底）
    r = collect_tps(row or {}, cfg=cfg, log_lines=log_lines_fn() if log_lines_fn else None,
                    ttl=0.0)
    if r.get('available'):
        result['tps'] = r.get('tps')
        result['mspt'] = r.get('mspt') or result['mspt']
        result['available'] = True
        result['source'] = r.get('source') or 'rcon'
        # ⚠️ 不要写成 'rcon:%s' % r.get('source')：下游 source 本身已带 `rcon:` 前缀，
        #    拼出来会变成 `rcon:rcon:tps`（实测踩到，前端展示很怪）。
        if not str(result['source']).startswith('rcon'):
            result['source'] = 'rcon:' + str(result['source'])
        result['raw'] = r.get('raw') or ''
        result['commands'] = result['commands'] + (r.get('commands') or [])
    else:
        result['note'] = (r.get('note') or '') or (
            '未能取到 TPS：该实例没有 stdin 通道，且 RCON 不可用'
            if not sent else 'tps 命令已发送但没读到可用回显，且 RCON 不可用')
    return result


def mark_tps_watched(row: dict) -> None:
    """登记「刚刚有人真的在取这个实例的 TPS」。

    用途见 `tps_recently_watched()`：采样器靠它判断**该不该主动去 RCON 取 TPS**。
    设计动机（用户实测反馈）：面板会**每 25~30 秒**开一次 RCON 连接，服务端控制台
    被 `Thread RCON Client ... started / shutting down` 一直刷 —— 哪怕服务器**根本没人在看、
    没人在线**。TPS 是慢变量，没有观察者时完全没有采集的必要，
    所以改成「**有人看才采**」，空闲时 RCON 完全静默。
    """
    iid = (row or {}).get('id')
    if iid is None:
        return
    with _tps_lock:
        _tps_watched[iid] = time.time()


def tps_recently_watched(row: dict, window: float = 90.0) -> bool:
    """最近 `window` 秒内有没有人请求过该实例的 TPS。"""
    iid = (row or {}).get('id')
    if iid is None:
        return False
    with _tps_lock:
        t = _tps_watched.get(iid) or 0.0
    return (time.time() - t) < window


def collect_tps(row: dict, cfg: dict = None, log_lines=None, ttl: float = 5.0,
                timeout: float = 5.0) -> dict:
    """采集某实例的 TPS/MSPT。

    返回：{ok, available, tps:{1m,5m,15m}|None, mspt:{avg,p95}|None,
           source:'rcon:forge'|'rcon:spark'|'rcon:tps'|'log'|None, note, raw, ...}
    **拿不到就 available=false 且 tps/mspt 为 None。**
    """
    from .config import get_config
    cfg = cfg or get_config()
    row = row or {}
    iid = row.get('id')
    key = ('tps', iid)
    with _tps_lock:
        hit = _tps_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return dict(hit[1], cached=True)

    # 单飞：同一实例同一时刻只抓一次；拿到锁后再查一次缓存（可能刚被别人取完）
    with _flight_lock(iid):
        with _tps_lock:
            hit = _tps_cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return dict(hit[1], cached=True)
        return _collect_tps_uncached(row, cfg, log_lines, ttl, timeout, key)


def _collect_tps_uncached(row: dict, cfg, log_lines, ttl, timeout, key) -> dict:
    result = {'ok': True, 'available': False, 'tps': None, 'mspt': None, 'source': None,
              'mspt_source': None, 'note': '', 'raw': '', 'commands': [],
              'checked_at': time.time(), 'log_signals': None}
    host, port, pwd = _rcon_creds(row, cfg)
    rcon_errors = []
    if not int(row.get('rcon_enabled') or 0):
        rcon_errors.append('实例未启用 RCON')
    elif not pwd:
        rcon_errors.append('实例未配置 RCON 密码')

    if int(row.get('rcon_enabled') or 0) and pwd:
        results = try_commands(host, port, pwd, tps_commands_for(row.get('core_type')), timeout)
        result['commands'] = [{'cmd': r['cmd'], 'ok': r['ok'],
                               'error': (r.get('error') or '')[:200]} for r in results]
        for r in results:
            if not r.get('ok'):
                rcon_errors.append(f'{r["cmd"]}：{r["error"]}')
                continue
            p = parse_tps_text(r['text'])
            got = False
            if p.get('tps') and not result['tps']:
                result['tps'] = p['tps']
                tag = r['cmd'].strip().lstrip('/').split()[0].lower()
                if tag in ('forge', 'neoforge'):
                    tag = 'forge'
                result['source'] = f'rcon:{tag}'
                result['tps_note'] = p.get('note') or ''
                result['raw'] = p.get('raw', '')
                got = True
            if p.get('mspt') and not result['mspt']:
                result['mspt'] = p['mspt']
                result['mspt_source'] = f'rcon:{r["cmd"].strip().lstrip("/").split()[0].lower()}'
                got = True
            if got:
                result['available'] = True
                if result['tps'] and result['mspt']:
                    break
            elif p.get('unknown_cmd'):
                rcon_errors.append(f'{r["cmd"]}：该服务端没有这条命令')
            else:
                rcon_errors.append(f'{r["cmd"]}：输出里没有 TPS/MSPT 数据')

    if not result['available'] and log_lines:
        p = parse_log_tps(log_lines)
        result['log_signals'] = p.get('log_signals')
        if p.get('tps') or p.get('mspt'):
            result['tps'] = p['tps']
            result['mspt'] = p['mspt']
            result['source'] = 'log'
            result['available'] = True
            result['note'] = '数据来自服务端日志（插件/服务端自己打印的 TPS 行）'
            result['raw'] = p.get('raw', '')[-800:]
        elif p.get('log_signals') and not result.get('log_signals'):
            result['log_signals'] = p.get('log_signals')

    if result['available']:
        if not result.get('source'):
            result['source'] = result.get('mspt_source')      # 只有 mspt 时也如实标明来源
        src = result.get('source') or result.get('mspt_source') or ''
        label = {'rcon:tps': 'Paper/Spigot 的 tps 命令', 'rcon:mspt': 'Paper/Spigot 的 mspt 命令',
                 'rcon:forge': '/forge tps（Forge/NeoForge）', 'rcon:spark': 'spark 插件的 tps 命令',
                 'log': '服务端日志'}.get(src, src)
        notes = [f'来源：{label}'] if label else []
        if result.get('tps_note'):
            notes.append(result['tps_note'])
        if result['tps'] and result['tps'].get('5m') is None:
            notes.append('该源只给 1m/整体 TPS，5m/15m 无数据（如实留空，不填 0）')
        if result['mspt'] and result['mspt'].get('p95') is None:
            notes.append('该源不提供 p95（Paper 给的是 avg/min/max）')
        result['note'] = '；'.join(notes)
    else:
        sig = result.get('log_signals') or {}
        bits = []
        if rcon_errors:
            bits.append('RCON：' + '；'.join(rcon_errors[:4]))
        if sig.get('cant_keep_up'):
            bits.append(f'日志出现 {sig["cant_keep_up"]} 次 "Can\'t keep up!"'
                        + (f'（最近落后 {sig["lag_ms"]}ms）' if sig.get('lag_ms') else '')
                        + '，说明有掉帧，但日志没有给出 TPS 数值，因此不回具体 TPS')
        if sig.get('startup_seconds') is not None:
            bits.append(f'服务端启动耗时 {sig["startup_seconds"]}s')
        result['note'] = '；'.join(bits) or '该服务端未提供 TPS 数据（原版 / 未装 spark / RCON 不可用）'
    with _tps_lock:
        _tps_cache[key] = (time.time(), dict(result))
    return result


def clear_tps_cache(iid=None):
    with _tps_lock:
        if iid is None:
            _tps_cache.clear()
        else:
            _tps_cache.pop(('tps', iid), None)
