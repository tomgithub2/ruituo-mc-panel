"""芮拓MC开服面板 全局配置（JSON 持久化 + 目录布局）。

与 RT面板 对齐：backend/data/config.json 保存面板级配置，
MC_DATA_DIR 环境变量可整体迁移数据目录。
"""
import json
import os
import secrets
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
# 数据目录：支持 MC_DATA_DIR 环境变量迁移（默认 backend/data）
DATA_DIR = os.environ.get('MC_DATA_DIR') or os.path.join(BASE_DIR, 'data')
INSTANCE_DIR = os.path.join(DATA_DIR, 'instances')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
JAVA_DIR = os.path.join(DATA_DIR, 'java')
TMP_DIR = os.path.join(DATA_DIR, 'tmp')
DOWNLOADS_DIR = os.path.join(DATA_DIR, 'downloads')

for _d in (DATA_DIR, INSTANCE_DIR, LOG_DIR, BACKUP_DIR, JAVA_DIR, TMP_DIR, DOWNLOADS_DIR):
    os.makedirs(_d, exist_ok=True)
    try:
        os.chmod(_d, 0o700)
    except Exception:
        pass

CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
SECRET_FILE = os.path.join(DATA_DIR, 'secret.key')

_lock = threading.RLock()

PANEL_VERSION = '0.2.0'

DEFAULTS = {
    'port': 8100,
    'bind_host': '127.0.0.1',
    'site_name': '芮拓MC开服面板',
    'session_hours': 24,
    'max_login_fails': 5,
    'lock_minutes': 10,
    'login_rate_limit': 10,        # 登录接口限流（次/60秒/IP），防爆破
    'api_rate_limit': 1800,        # 全局 API 限流（次/60秒/IP）
    'sample_interval': 5,          # 监控采样秒
    'dashboard_push_ms': 1000,     # 仪表盘实时推送间隔（毫秒）；纯内存读取，1 秒很轻
    'log_ring_lines': 2000,        # 内存环形缓冲行数
    'download_workers': 2,         # 并发下载上限（不要吃满全机）
    'default_java': '',            # 默认 java 可执行文件（空=自动检测）
    'default_jvm_args': '-XX:+UseG1GC -XX:MaxGCPauseMillis=200',
    'default_memory_mb': 2048,
    'backup_keep': 5,              # 每实例备份保留份数
    'mirror_prefix': '',           # 下载镜像加速前缀（可选，如 https://bmclapi2.bangbang93.com）
    'rcon_enabled': False,
    'rcon_host': '127.0.0.1',
    'rcon_port': 25575,
    'rcon_password': '',
    'curseforge_api_key': '',
    'auto_restart_limit': 3,       # 崩溃自动重启次数上限
    'theme': 'darkgold',
}


def get_config() -> dict:
    with _lock:
        cfg = dict(DEFAULTS)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg.update(json.load(f))
            except Exception:
                pass
        return cfg


def save_config(updates: dict) -> dict:
    with _lock:
        cfg = get_config()
        cfg.update({k: v for k, v in updates.items() if k in DEFAULTS})
        tmp = CONFIG_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
        return cfg


def get_panel_secret() -> str:
    """面板会话签名密钥（0600）。"""
    with _lock:
        if not os.path.exists(SECRET_FILE):
            fd = os.open(SECRET_FILE, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(secrets.token_hex(32))
            try:
                os.chmod(SECRET_FILE, 0o600)
            except Exception:
                pass
        with open(SECRET_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()


def instance_dir(inst: dict) -> str:
    """实例工作目录：库里的 dir 优先，否则按 data/instances/<id>_<safe_name>。"""
    d = (inst or {}).get('dir')
    if d:
        return os.path.realpath(d)
    iid = inst.get('id')
    name = ''.join(c for c in str(inst.get('name') or 'instance')
                   if c.isalnum() or c in '-_')[:40] or 'instance'
    return os.path.realpath(os.path.join(INSTANCE_DIR, f'{iid}_{name}'))
