"""机器标识：稳定、唯一、格式与官网约定一致（16 位大写十六进制）。

为什么用「随机生成 + 持久化」而不是采集硬件指纹：
  · 官网侧只要求 `machine_id` 是 **16 位十六进制**（`/api/v1/update/check` 会校验长度，
    非法就忽略定向推送），并不要求它由硬件推导；
  · 采集网卡/主板序列号在容器、虚拟机、多网卡环境下**不稳定**，换个环境 ID 就变了 ——
    而对面板来说，ID 一变 = 定向更新收不到、绑定失效，代价比"随机一次"大得多；
  · 持久化在数据目录（跟随面板数据走），配合 0600 权限，语义清晰：**这台面板的机器身份**。

⚠️ 与芮拓面板的差异：那边用硬件指纹。若将来要做「硬件更换即自动失效」的策略，
   这里的数据目录本身就是判断依据（换机器 = 数据目录不跟着走 = ID 自然不同）。
"""
import os
import secrets
import threading

from .config import DATA_DIR

_lock = threading.RLock()
_FILE = os.path.join(DATA_DIR, 'machine_id')

_cache = {'value': None}


def machine_id() -> str:
    """返回本机的 16 位大写十六进制机器号（首次调用时生成并落盘）。"""
    with _lock:
        if _cache['value']:
            return _cache['value']
        val = ''
        try:
            if os.path.isfile(_FILE):
                with open(_FILE, 'r', encoding='utf-8') as f:
                    val = f.read().strip().upper()
        except Exception:
            val = ''
        if len(val) != 16 or any(c not in '0123456789ABCDEF' for c in val):
            val = secrets.token_hex(8).upper()          # 8 字节 → 16 位十六进制
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(_FILE, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(val + '\n')
                os.chmod(_FILE, 0o600)
            except Exception:
                pass
        _cache['value'] = val
        return val


def hw_report() -> dict:
    """机器概况（绑定时上报给官网，便于后台识别是哪台机器）。"""
    import platform
    import socket
    return {
        'machine_id': machine_id(),
        'hostname': socket.gethostname(),
        'platform': platform.platform(),
        'python': platform.python_version(),
    }
