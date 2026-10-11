"""实例生命周期管理：进程组、日志环形缓冲、WebSocket 广播、崩溃自动重启。

设计要点
  · 每个实例一个 Popen（Windows 用 CREATE_NEW_PROCESS_GROUP，POSIX 用 setsid），
    停止时先写 stdin `stop` 优雅退出，超时再 kill **整个进程树**（MC 服务端会 fork/子进程）。
  · stdout/stderr 由独立线程读取 → 内存环形缓冲 + 落盘 logs/latest.log。
  · 面板重启后靠 data/instances/<id>/*.pid + psutil 存活校验识别"已在运行的实例"。
  · 崩溃（非 0 退出且非用户主动停止）时记录退出码 + 最后 50 行日志，
    auto_restart 打开则指数退避重启，超过上限提示并停止。
"""
import codecs
import logging
import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import psutil

from .config import get_config, instance_dir

logger = logging.getLogger('mcpanel.monitor')
from .database import execute, now, query
from . import audit as audit_mod

IS_WIN = sys.platform == 'win32'

# 实例状态
ST_STOPPED = 'stopped'
ST_STARTING = 'starting'
ST_RUNNING = 'running'
ST_STOPPING = 'stopping'
ST_CRASHED = 'crashed'

_ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')
_TIME_RE = re.compile(r'^\[(\d{2}:\d{2}:\d{2})\]')
_DONE_RE = re.compile(r'Done \([0-9.]+s\)! For help')
_PLAYERS_RE = re.compile(r'There are (\d+) of a max of (\d+) players online')
_TPS_RE = re.compile(r'TPS from last 1m, 5m, 15m:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)', re.I)
_MSPT_RE = re.compile(r'(?:MSPT|ms/t).*?:?\s*([0-9.]+)', re.I)
_LOGIN_RE = re.compile(r'\]: ([A-Za-z0-9_]{1,16})\[/?[\d.:]+\] logged in')
_LOGOUT_RE = re.compile(r'\]: ([A-Za-z0-9_]{1,16}) lost connection')


def classify(line: str) -> str:
    """按内容判断日志级别（用于前端颜色分级）。"""
    low = line.lower()
    if 'error' in low or 'exception' in low or 'severe' in low or '\tat ' in line:
        return 'error'
    if 'warn' in low or 'could not' in low or 'failed' in low:
        return 'warn'
    return 'info'


class Instance:
    """单实例运行时对象（进程 + 日志 + 指标缓存）。"""

    def __init__(self, inst_id: int):
        self.id = inst_id
        self.proc = None
        self.status = ST_STOPPED
        self.pid = 0
        self.started_at = 0.0
        self.lock = threading.RLock()
        self.ring = collections.deque(maxlen=int(get_config().get('log_ring_lines', 2000)))
        # ⚠️ 必须是 list，不能是 set：订阅队列是 `collections.deque`，而 deque **不可哈希**，
        # `set.add(deque)` 会抛 `TypeError: unhashable type: 'collections.deque'`。
        # 那会让控制台 WebSocket 在握手成功后立刻崩掉（前端看到 1006，控制台永远空白）。
        self.subscribers = []             # websocket 队列（元素不可哈希，故用 list）
        self._sub_lock = threading.Lock()
        self.stop_requested = False
        self.players = 0
        self.max_players = 0
        self.tps = None
        self.mspt = None
        self.last_line_ts = 0.0
        self.server_done = False
        self.restart_attempts = 0
        self._last_cpu = None
        self.cpu = 0.0
        self.mem_mb = 0.0
        self.online_names = set()

    # ------------------------------------------------------------ 基础
    def db(self) -> dict:
        return query('SELECT * FROM instances WHERE id=?', (self.id,), one=True) or {}

    @property
    def workdir(self) -> str:
        return instance_dir(self.db())

    @property
    def pid_file(self) -> str:
        return os.path.join(self.workdir, '.mcpanel.pid')

    # ------------------------------------------------------------ 环形缓冲
    def push(self, line: str, stream: str = 'stdout'):
        line = _ANSI.sub('', line.rstrip('\r\n'))
        if not line.strip():
            return
        rec = {'ts': time.time(), 'line': line, 'level': classify(line), 'stream': stream}
        self.ring.append(rec)
        self.last_line_ts = rec['ts']
        self._parse(rec)
        self.broadcast(rec)

    def tail(self, n: int = 200) -> list:
        items = list(self.ring)
        return items[-n:] if n > 0 else items

    def _parse(self, rec: dict):
        line = rec['line']
        if _DONE_RE.search(line):
            self.server_done = True
        m = _PLAYERS_RE.search(line)
        if m:
            self.players = int(m.group(1))
            self.max_players = int(m.group(2))
        m = _TPS_RE.search(line)
        if m:
            self.tps = float(m.group(1))
        m = _MSPT_RE.search(line)
        if m:
            try:
                self.mspt = float(m.group(1))
            except ValueError:
                pass
        m = _LOGIN_RE.search(line)
        if m:
            self.online_names.add(m.group(1))
            self.players = max(self.players, len(self.online_names))
        m = _LOGOUT_RE.search(line)
        if m:
            self.online_names.discard(m.group(1))

    # ------------------------------------------------------------ 订阅广播
    def subscribe(self) -> 'collections.deque':
        q = collections.deque(maxlen=500)
        with self._sub_lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            if q in self.subscribers:        # 先判存在，避免 ValueError
                self.subscribers.remove(q)

    def broadcast(self, rec: dict):
        with self._sub_lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.append(rec)
                except Exception:
                    dead.append(q)
            for q in dead:
                if q in self.subscribers:
                    self.subscribers.remove(q)


class Manager:
    def __init__(self):
        self.instances = {}
        self._lock = threading.RLock()
        self._monitor_thread = None
        self._running = False

    # ------------------------------------------------------------ 装载
    def get(self, inst_id: int) -> Instance:
        with self._lock:
            inst = self.instances.get(int(inst_id))
            if inst is None:
                inst = Instance(int(inst_id))
                self.instances[int(inst_id)] = inst
            return inst

    def bootstrap(self):
        """面板启动：校验 PID 文件，识别"已在运行的实例"。"""
        init_db_instances = query('SELECT * FROM instances')
        for row in init_db_instances:
            inst = self.get(row['id'])
            alive, pid = self._check_pid_file(row)
            if alive:
                inst.pid = pid
                inst.status = ST_RUNNING
                inst.started_at = row.get('last_start_at') or time.time()
                inst.push('[面板] 检测到该实例已在运行（PID %d），已接管日志与状态。' % pid)
                # 重启日志跟随线程
                threading.Thread(target=self._attach_log_tail, args=(inst, pid), daemon=True).start()
                execute('UPDATE instances SET status=?, pid=? WHERE id=?', (ST_RUNNING, pid, row['id']))
            else:
                if row['status'] in (ST_RUNNING, ST_STARTING, ST_STOPPING):
                    execute('UPDATE instances SET status=?, pid=0 WHERE id=?', (ST_STOPPED, row['id']))
                inst.status = ST_STOPPED
            if not os.path.isdir(instance_dir(row)):
                os.makedirs(instance_dir(row), exist_ok=True)
        self.start_monitor()

    def _check_pid_file(self, row: dict) -> tuple:
        """返回 (是否存活, pid)。"""
        pf = os.path.join(instance_dir(row), '.mcpanel.pid')
        try:
            with open(pf, 'r', encoding='utf-8') as f:
                pid = int(f.read().strip())
        except Exception:
            return False, 0
        try:
            p = psutil.Process(pid)
            cmd = ' '.join(p.cmdline() or [])
        except Exception:
            return False, 0
        # 校验确实是 java 进程，避免 PID 复用误判
        if 'java' not in cmd.lower():
            return False, 0
        return True, pid

    def _attach_log_tail(self, inst: Instance, pid: int):
        """接管已运行进程：只能读日志文件增量（管道已被原面板进程持有）。"""
        log_path = os.path.join(inst.workdir, 'logs', 'latest.log')
        if not os.path.isfile(log_path):
            return
        try:
            size = os.path.getsize(log_path)
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(max(0, size - 64 * 1024))
                for line in f:
                    inst.push(line, 'tail')
        except Exception:
            pass

    # ------------------------------------------------------------ 启动
    def build_command(self, row: dict) -> list:
        java = row.get('java_path') or get_config().get('default_java') or 'java'
        cmd = [java, f"-Xmx{int(row.get('memory_mb') or 2048)}M",
               f"-Xms{max(512, int(row.get('memory_mb') or 2048) // 2)}M"]
        extra = (row.get('extra_jvm_args') or '').strip()
        if not extra:
            extra = (get_config().get('default_jvm_args') or '').strip()
        if extra:
            cmd += [a for a in extra.split() if a]
        cmd += ['-jar', os.path.basename(row.get('jar_path') or 'server.jar')]
        extra_srv = (row.get('extra_server_args') or '').strip()
        if extra_srv:
            cmd += [a for a in extra_srv.split() if a]
        if int(row.get('nogui') or 0):
            cmd.append('nogui')
        return cmd

    def _spawn_diag(self, cmd: list, workdir: str, env: dict, exc: BaseException) -> dict:
        """子进程创建失败时，把**全部上下文**收集起来（返回摘要 + 落盘全文）。

        为什么需要：`subprocess` 失败时抛的异常可能是**空消息**的
        （`SubprocessError('')`，errno/err_msg 都被丢掉），光看它一点线索都没有。
        这里把 errno、路径可执行性、权限、ulimit、cwd、uid 全部记下来。
        """
        import errno as _errno
        import resource as _resource
        import traceback as _tb
        info = {
            'exc_type': type(exc).__name__,
            'exc_repr': repr(exc),
            'exc_str': str(exc),
            'errno': getattr(exc, 'errno', None),
            'errno_name': None,
            'filename': getattr(exc, 'filename', None),
            'cmd': cmd,
            'cwd': workdir,
            'cwd_exists': os.path.isdir(workdir),
            'cwd_writable': os.access(workdir, os.W_OK) if os.path.isdir(workdir) else None,
            'exe': cmd[0] if cmd else None,
            'exe_exists': os.path.isfile(cmd[0]) if cmd else None,
            'exe_executable': os.access(cmd[0], os.X_OK) if cmd and os.path.isfile(cmd[0]) else None,
            'uid': os.getuid() if hasattr(os, 'getuid') else None,
            'euid': os.geteuid() if hasattr(os, 'geteuid') else None,
            'pid': os.getpid(),
            'open_fds': None,
            'no_new_privs': None,
            'ulimit_nofile': None,
        }
        try:
            info['ulimit_nofile'] = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            info['open_fds'] = len(os.listdir(f'/proc/{os.getpid()}/fd'))
        except Exception:                                     # noqa: BLE001
            pass
        try:
            with open(f'/proc/{os.getpid()}/status', encoding='utf-8') as f:
                for ln in f:
                    if ln.startswith(('NoNewPrivs:', 'Seccomp:', 'CapEff:')):
                        k, _, v = ln.partition(':')
                        info[k.strip().lower()] = v.strip()
        except Exception:                                     # noqa: BLE001
            pass
        if info['errno'] is not None:
            try:
                info['errno_name'] = _errno.errorcode.get(info['errno'], '?')
            except Exception:                                 # noqa: BLE001
                pass
        # 全文（含 traceback）落到 data 目录，便于事后看
        try:
            from .config import DATA_DIR                       # 局部 import，避免循环
            path = os.path.join(DATA_DIR, 'spawn_fail.log')
            with open(path, 'a', encoding='utf-8') as f:
                f.write('=' * 70 + '\n')
                f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
                for k, v in info.items():
                    f.write(f'{k}: {v}\n')
                f.write('--- traceback ---\n')
                f.write(_tb.format_exc())
                f.write('\n')
            info['diag_file'] = path
        except Exception:                                     # noqa: BLE001
            pass
        try:
            print('[实例启动失败诊断] %s' % {k: info[k] for k in
                                            ('exc_type', 'errno', 'errno_name',
                                             'exe_exists', 'exe_executable',
                                             'cwd_writable', 'open_fds')},
                  flush=True)
        except Exception:                                     # noqa: BLE001
            pass
        return info

    def start(self, inst_id: int, by: str = 'panel') -> dict:
        inst = self.get(inst_id)
        with inst.lock:
            row = inst.db()
            if not row:
                return {'ok': False, 'error': '实例不存在'}
            if inst.proc and inst.proc.poll() is None:
                return {'ok': False, 'error': '实例已在运行'}
            workdir = instance_dir(row)
            os.makedirs(workdir, exist_ok=True)
            os.makedirs(os.path.join(workdir, 'logs'), exist_ok=True)
            jar = row.get('jar_path') or ''
            if not jar:
                return {'ok': False, 'error': '未设置服务端 jar'}
            jar_abs = jar if os.path.isabs(jar) else os.path.join(workdir, os.path.basename(jar))
            if not os.path.isfile(jar_abs):
                return {'ok': False, 'error': f'服务端 jar 不存在: {jar_abs}'}
            cmd = self.build_command(row)
            env = dict(os.environ)
            env['PYTHONUTF8'] = '1'
            log_dir = os.path.join(workdir, 'logs')
            latest = os.path.join(log_dir, 'latest.log')
            try:
                if os.path.isfile(latest) and os.path.getsize(latest) > 0:
                    stamp = time.strftime('%Y%m%d-%H%M%S')
                    shutil.move(latest, os.path.join(log_dir, f'previous-{stamp}.log'))
            except Exception:
                pass
            flags = 0
            if IS_WIN:
                flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | \
                        getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            else:
                flags = 0
            try:
                kw = {}
                if not IS_WIN:
                    kw['start_new_session'] = True
                proc = subprocess.Popen(
                    cmd, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, env=env, bufsize=0,
                    creationflags=flags if IS_WIN else 0, **kw)
            except FileNotFoundError:
                return {'ok': False, 'error': f'找不到 Java 可执行文件：{cmd[0]}（请在设置中指定 java 路径）'}
            except Exception as e:
                # ⚠️ 子进程创建失败时，异常信息经常**是空的**（实测遇到过
                #    `SubprocessError('')`：type 名称在、消息和 errno 全空，
                #    只靠它根本没法排查）。这里把**全部上下文**落盘，
                #    下次失败就能直接看到 errno / 路径 / 权限到底差在哪。
                diag = self._spawn_diag(cmd, workdir, env, e)
                return {'ok': False, 'error': f'启动失败：{type(e).__name__}: {e}',
                        'diag': diag, 'log': 'backend/data/spawn_fail.log'}

            inst.proc = proc
            inst.pid = proc.pid
            inst.status = ST_STARTING
            inst.started_at = time.time()
            inst.stop_requested = False
            inst.server_done = False
            inst.restart_attempts = 0
            inst.ring.clear()
            try:
                with open(inst.pid_file, 'w', encoding='utf-8') as f:
                    f.write(str(proc.pid))
            except Exception:
                pass
            execute('UPDATE instances SET status=?, pid=?, last_start_at=?, exit_code=NULL '
                    'WHERE id=?', (ST_STARTING, proc.pid, inst.started_at, inst.id))
            threading.Thread(target=self._pump, args=(inst, proc), daemon=True).start()
            threading.Thread(target=self._watch, args=(inst, proc), daemon=True).start()
            inst.push(f'[面板] 启动命令：{" ".join(cmd)}')
            inst.push(f'[面板] 工作目录：{workdir}')
            audit_mod.audit(by, '', 'instance.start', inst.id,
                            f'启动实例 {row["name"]}（PID {proc.pid}）')
            return {'ok': True, 'pid': proc.pid}

    # ------------------------------------------------------------ 输出泵
    def _pump(self, inst: Instance, proc):
        log_path = os.path.join(inst.workdir, 'logs', 'latest.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        dec = codecs.getincrementaldecoder('utf-8')('replace')
        buf = ''
        try:
            with open(log_path, 'a', encoding='utf-8', errors='replace', buffering=1) as lf:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += dec.decode(chunk)
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        lf.write(line + '\n')
                        inst.push(line, 'stdout')
        except Exception as e:
            inst.push(f'[面板] 日志读取结束: {type(e).__name__}: {e}')
        finally:
            if buf.strip():
                inst.push(buf, 'stdout')

    # ------------------------------------------------------------ 退出监视
    def _watch(self, inst: Instance, proc):
        code = proc.wait()
        with inst.lock:
            inst.proc = None
            try:
                if os.path.isfile(inst.pid_file):
                    os.remove(inst.pid_file)
            except Exception:
                pass
            user_stopped = inst.stop_requested
            inst.status = ST_STOPPED if (code == 0 or user_stopped) else ST_CRASHED
            inst.server_done = False
            execute('UPDATE instances SET status=?, pid=0, exit_code=?, last_stop_at=? WHERE id=?',
                    (inst.status, code, time.time(), inst.id))
            inst.push(f'[面板] 进程已退出，退出码 {code}' +
                      ('（面板主动停止）' if user_stopped else ''))
        if user_stopped or code == 0:
            return
        self._handle_crash(inst, code)

    def _handle_crash(self, inst: Instance, code: int):
        row = inst.db()
        tail = '\n'.join(r['line'] for r in inst.tail(50))
        execute('INSERT INTO crash_logs (instance_id, ts, exit_code, tail, action) VALUES (?,?,?,?,?)',
                (inst.id, now(), code, tail, 'detected'))
        audit_mod.audit('system', '', 'instance.crash', inst.id,
                        f'实例 {row.get("name")} 异常退出（退出码 {code}）', level='error')
        inst.push(f'[面板] 检测到异常退出（退出码 {code}）', 'panel')
        if not int(row.get('auto_restart') or 0):
            return
        cfg = get_config()
        limit = int(cfg.get('auto_restart_limit', 3))
        attempt = int(row.get('restart_count') or 0) + 1
        if attempt > limit:
            inst.push(f'[面板] 自动重启已达上限（{limit} 次），已停止重试。请检查日志后手动启动。', 'panel')
            execute('UPDATE crash_logs SET action=? WHERE id=(SELECT MAX(id) FROM crash_logs WHERE instance_id=?)',
                    ('restart_giveup', inst.id))
            audit_mod.audit('system', '', 'instance.auto_restart_giveup', inst.id,
                            f'自动重启超过上限 {limit} 次，已放弃', level='error')
            return
        delay = min(60, 5 * (2 ** (attempt - 1)))
        execute('UPDATE instances SET restart_count=? WHERE id=?', (attempt, inst.id))
        inst.push(f'[面板] 将在 {delay} 秒后自动重启（第 {attempt}/{limit} 次尝试）', 'panel')
        audit_mod.audit('system', '', 'instance.auto_restart', inst.id,
                        f'崩溃自动重启第 {attempt}/{limit} 次，延迟 {delay}s')

        def _later():
            time.sleep(delay)
            row2 = inst.db()
            if row2 and row2['status'] in (ST_STOPPED, ST_CRASHED) and int(row2.get('auto_restart') or 0):
                self.start(inst.id, by='auto-restart')
        threading.Thread(target=_later, daemon=True).start()

    # ------------------------------------------------------------ 停止
    def stop(self, inst_id: int, by: str = 'panel', timeout: int = 30) -> dict:
        inst = self.get(inst_id)
        row = inst.db()
        proc = inst.proc
        if not proc or proc.poll() is not None:
            # 可能是面板重启后接管的实例：按 PID 文件停
            alive, pid = self._check_pid_file(row)
            if not alive:
                inst.status = ST_STOPPED
                execute('UPDATE instances SET status=?, pid=0 WHERE id=?', (ST_STOPPED, inst.id))
                return {'ok': True, 'note': '实例本就未运行'}
            return self._stop_external(inst, pid, by, timeout)
        with inst.lock:
            inst.stop_requested = True
            inst.status = ST_STOPPING
            execute('UPDATE instances SET status=? WHERE id=?', (ST_STOPPING, inst.id))
        try:
            self.send_command(inst_id, 'stop')
            inst.push('[面板] 已发送 stop 命令，等待优雅退出…', 'panel')
        except Exception as e:
            inst.push(f'[面板] 发送 stop 失败：{e}', 'panel')
        try:
            proc.wait(timeout=timeout)
            return {'ok': True, 'graceful': True}
        except subprocess.TimeoutExpired:
            inst.push(f'[面板] {timeout} 秒内未退出，强制结束进程树', 'panel')
            self.kill(inst_id, by=by)
            return {'ok': True, 'graceful': False}

    def _stop_external(self, inst: Instance, pid: int, by: str, timeout: int) -> dict:
        """面板重启后接管的外部进程：无 stdin，只能尝试 RCON stop 再 kill。"""
        row = inst.db()
        cfg = get_config()
        port = int(row.get('rcon_port') or cfg.get('rcon_port') or 25575)
        pwd = row.get('rcon_password') or cfg.get('rcon_password') or ''
        if int(row.get('rcon_enabled') or 0) and pwd:
            from .rcon import try_command
            ok, _ = try_command('127.0.0.1', port, pwd, 'stop')
            if ok:
                inst.push('[面板] 已通过 RCON 发送 stop，等待优雅退出…', 'panel')
                deadline = time.time() + timeout
                while time.time() < deadline:
                    if not psutil.pid_exists(pid):
                        break
                    time.sleep(1)
        self._kill_pid(pid)
        execute('UPDATE instances SET status=?, pid=0, last_stop_at=? WHERE id=?',
                (ST_STOPPED, time.time(), inst.id))
        inst.status = ST_STOPPED
        inst.pid = 0
        inst.push('[面板] 外部进程已结束', 'panel')
        audit_mod.audit(by, '', 'instance.stop', inst.id, '停止接管的外部实例')
        return {'ok': True, 'graceful': False, 'note': '接管实例已强制结束'}

    def _kill_pid(self, pid: int):
        try:
            p = psutil.Process(pid)
            for child in p.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            p.kill()
        except Exception:
            pass

    def kill(self, inst_id: int, by: str = 'panel') -> dict:
        inst = self.get(inst_id)
        proc = inst.proc
        inst.stop_requested = True
        if proc and proc.poll() is None:
            pid = proc.pid
            if IS_WIN:
                try:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                                   capture_output=True, timeout=15)
                except Exception:
                    pass
            self._kill_pid(pid)
            inst.push(f'[面板] 已强制结束进程 {pid}', 'panel')
        else:
            row = inst.db()
            alive, pid = self._check_pid_file(row)
            if alive:
                self._kill_pid(pid)
                inst.push(f'[面板] 已强制结束接管进程 {pid}', 'panel')
        execute('UPDATE instances SET status=?, pid=0, last_stop_at=? WHERE id=?',
                (ST_STOPPED, time.time(), inst.id))
        inst.status = ST_STOPPED
        try:
            if os.path.isfile(inst.pid_file):
                os.remove(inst.pid_file)
        except Exception:
            pass
        audit_mod.audit(by, '', 'instance.kill', inst.id, '强制结束实例进程')
        return {'ok': True}

    def restart(self, inst_id: int, by: str = 'panel') -> dict:
        self.stop(inst_id, by=by)
        time.sleep(1.5)
        return self.start(inst_id, by=by)

    # ------------------------------------------------------------ stdin
    def send_command(self, inst_id: int, cmd: str) -> dict:
        inst = self.get(inst_id)
        proc = inst.proc
        if not proc or proc.poll() is not None:
            return {'ok': False, 'error': '实例未运行'}
        if not proc.stdin:
            return {'ok': False, 'error': '实例未运行（无 stdin 通道）'}
        try:
            proc.stdin.write((cmd.rstrip('\n') + '\n').encode('utf-8'))
            proc.stdin.flush()
        except Exception as e:
            return {'ok': False, 'error': f'写入失败：{e}'}
        inst.push(f'[控制台] > {cmd.strip()}', 'input')
        return {'ok': True}

    # ------------------------------------------------------------ 状态
    def status(self, inst_id: int) -> dict:
        inst = self.get(inst_id)
        row = inst.db()
        pid = inst.pid
        proc_alive = bool(inst.proc and inst.proc.poll() is None)
        if not proc_alive:
            alive, ppid = self._check_pid_file(row)
            if alive:
                pid = ppid
                proc_alive = True
                inst.pid = ppid
        if inst.status == ST_RUNNING or proc_alive:
            st = ST_RUNNING if not inst.stop_requested else inst.status
        else:
            st = inst.status if inst.status in (ST_CRASHED, ST_STOPPED) else ST_STOPPED
        ref = None
        if pid:
            try:
                ref = psutil.Process(pid)
            except Exception:
                ref = None
        uptime = int(time.time() - inst.started_at) if (proc_alive and inst.started_at) else 0
        return {
            'id': inst.id,
            'status': ST_RUNNING if proc_alive else st,
            'pid': pid,
            'running': proc_alive,
            'uptime': uptime,
            # cpu 保留 2 位：空闲 JVM 常见 0.05%~0.4%，只留 1 位会被显示成 0.0%，
            # 看起来像"数据不动"。内存 1 位足够（单位是 MB）。
            'cpu': round(inst.cpu, 2),
            'mem_mb': round(inst.mem_mb, 1),
            'players': inst.players,
            'max_players': inst.max_players,
            'tps': inst.tps,
            'mspt': inst.mspt,
            'tps_available': inst.tps is not None,
            'online_names': sorted(inst.online_names),
        }

    # ------------------------------------------------------------ 监控采样
    def start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitor(self):
        self._running = False

    def _monitor_loop(self):
        cfg = get_config()
        interval = max(1, int(cfg.get('sample_interval', 5)))
        last_write = 0.0
        while self._running:
            try:
                rows = query('SELECT * FROM instances')
                for row in rows:
                    inst = self.get(row['id'])
                    st = self.status(inst.id)
                    pid = st['pid']
                    if pid:
                        try:
                            p = psutil.Process(pid)
                            # ⚠️ `cpu_percent(interval=None)` 是**两次调用之间的增量**，
                            # 首次调用必定返回 0.0。这里每次循环都新建 Process 对象，
                            # 所以直接读会**永远**是 0.0（内存却是对的，因为 RSS 是瞬时值）。
                            # 正确做法：同一个对象上先采样一次、隔一小段再读，才有真实百分比。
                            p.cpu_percent(interval=None)
                            time.sleep(0.2)
                            with p.oneshot():
                                cpu_raw = p.cpu_percent(interval=None)
                                mem_mb = p.memory_info().rss / 1024 / 1024
                                for child in p.children(recursive=True):
                                    try:
                                        mem_mb += child.memory_info().rss / 1024 / 1024
                                        cpu_raw += child.cpu_percent(interval=None)
                                    except Exception:
                                        pass
                            # psutil 以"单核 100%"为单位，多线程 JVM 能远超 100%（实测见过
                            # 3022%，那是 32 个 GC/worker 线程各烧一个核）。除以核数才是
                            # **全机占用**，否则仪表盘上的百分比没有可比性。
                            inst.cpu = round(cpu_raw / max(1, psutil.cpu_count() or 1), 2)
                            inst.mem_mb = mem_mb
                        except Exception:
                            pass
                if time.time() - last_write >= interval:
                    last_write = time.time()
                    for row in rows:
                        inst = self.get(row['id'])
                        st = self.status(inst.id)
                        if not st['running'] and st['status'] == ST_STOPPED:
                            continue
                        # ⚠️ 内存里的 tps 只来自「解析服务端日志」，而**有的服务端根本不把
                        #    TPS 写进日志**（实测 Purpur 26.x 就不写）—— 于是指标表里的
                        #    tps/mspt 永远是 NULL，监控曲线一直是空的「TPS 不可用」，
                        #    **哪怕面板已经能通过 RCON 取到真实数据**。
                        #    这里回退到 RCON 取一次（collect_tps 自带 5 秒缓存，
                        #    采样间隔通常 ≥5 秒，不会打爆服务端）；取不到仍写 NULL，
                        #    **绝不编造数值**。
                        tps_v, mspt_v = st['tps'], st['mspt']
                        if tps_v is None and mspt_v is None:
                            try:
                                from .rcon import collect_tps
                                t = collect_tps(row)
                                if t.get('available'):
                                    tps_v = t.get('tps') or tps_v
                                    mspt_v = t.get('mspt') or mspt_v
                            except Exception as e:
                                logger.warning('实例 %s RCON 取 TPS 失败：%s', inst.id, e)
                        # ⚠️ metrics.tps/mspt 是 REAL 列（单个数），而上面拿到的是
                        #    {'1m':..,'5m':..,'15m':..} 这种 dict —— 直接传给 sqlite3 会
                        #    报 `Error binding parameter N - probably unsupported type`，
                        #    **整条采样都写不进去**（实测：曲线长时间空白）。必须归一成标量。
                        from .rcon import tps_scalar, mspt_scalar
                        tps_v = tps_scalar(tps_v)
                        mspt_v = mspt_scalar(mspt_v)
                        execute('INSERT INTO metrics (instance_id, ts, cpu, mem_mb, players, tps, mspt) '
                                'VALUES (?,?,?,?,?,?,?)',
                                (inst.id, now(), st['cpu'], st['mem_mb'], st['players'],
                                 tps_v, mspt_v))
                    execute('DELETE FROM metrics WHERE ts < ?', (now() - 7 * 86400,))
            except Exception as e:
                # ⚠️ 这里原来是裸 `pass`：采样一旦出错就**完全静默** —— 现象是
                #    「监控曲线没有 TPS / 指标不再增长」，但日志里一个字都没有，
                #    只能靠猜（实测为此排查了很久）。改成记日志，让失败可见。
                logger.warning('监控采样失败：%s', e)
            time.sleep(1.0)

    # ------------------------------------------------------------ 销毁
    def forget(self, inst_id: int):
        with self._lock:
            inst = self.instances.pop(int(inst_id), None)
        if inst and inst.proc and inst.proc.poll() is None:
            self.kill(inst_id)


manager = Manager()


def parse_uptime(seconds: int) -> str:
    seconds = int(seconds or 0)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f'{d}天{h}小时{m}分'
    if h:
        return f'{h}小时{m}分'
    return f'{m}分{s}秒'
