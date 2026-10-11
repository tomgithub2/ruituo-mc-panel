"""官网账号绑定：拿到更新推送所需的「机器身份三件套」。

契约（照抄官网 `芮拓面板` 侧 `ops-panel/backend/app/binding.py` 与官网 `/api/v1/bind`）：

    POST {server}/api/v1/bind   {account, password, machine_id, hw}
      → {ok, token, binding_id, ...}

跑通后把 `token` / `binding_id` / `machine_id` 存在数据目录的 `binding.json`（0600），
之后：
  · `/api/v1/update/ack` 要用这三个字段做鉴权（否则任何人凭可枚举的 machine_id
    就能把推送标记为已读，从而**静默压制**该机器的定向更新）
  · `/api/v1/update/download?push_id=` 的定向包下载也要机器令牌

⚠️ 设计取舍：本模块**只做更新所需的绑定**（不承担许可/在线状态等职责），
   所以字段刻意少而明确；绑定是可选的 —— 没绑定也能收到**全局推送**，
   只是收不到「定向推送」、也无法回报执状态。
"""
import io
import json
import os
import threading

from .config import DATA_DIR
from .hardware import machine_id

_lock = threading.RLock()
_FILE = os.path.join(DATA_DIR, 'binding.json')


def _load() -> dict:
    """读取绑定信息；文件不存在或损坏时返回空 dict（不抛异常）。"""
    try:
        if os.path.isfile(_FILE):
            with io.open(_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _FILE + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)
        try:
            os.chmod(_FILE, 0o600)
        except Exception:
            pass


def status() -> dict:
    """本机绑定状态（不含 token 明文，只报有没有、绑的谁）。"""
    d = _load()
    return {
        'bound': bool(d.get('token') and d.get('binding_id')),
        'machine_id': machine_id(),
        'binding_id': d.get('binding_id', ''),
        'account': d.get('account', ''),
        'bound_at': d.get('bound_at', 0),
    }


def server_url() -> str:
    from .config import get_config
    try:
        return str(get_config().get('account_server') or 'https://www.rt888.icu').rstrip('/')
    except Exception:
        return 'https://www.rt888.icu'


def bind(account: str, password: str) -> dict:
    """用官网账号绑定本机。成功返回 {ok, binding_id, machine_id}（不回显 token）。"""
    import httpx
    account = str(account or '').strip()
    if not account or not password:
        return {'ok': False, 'error': '请填写官网账号与密码'}
    mid = machine_id()
    payload = {'account': account, 'password': password, 'machine_id': mid,
               'hw': {'hostname': _hostname(), 'product': 'ruituo-mc-panel'}}
    try:
        r = httpx.post(server_url() + '/api/v1/bind', json=payload, timeout=25.0)
        body = r.json() if r.content else {}
    except Exception as e:
        return {'ok': False, 'error': '无法连接官网：%s' % e}
    if r.status_code != 200 or not (body or {}).get('ok', True):
        return {'ok': False, 'error': (body or {}).get('detail') or (body or {}).get('error')
                or '绑定失败（HTTP %s）' % r.status_code}
    if not body.get('token') or not body.get('binding_id'):
        return {'ok': False, 'error': '官网返回缺少 token / binding_id'}
    _save({'token': body['token'], 'binding_id': body['binding_id'],
           'machine_id': mid, 'account': account, 'bound_at': _now()})
    return {'ok': True, 'binding_id': body['binding_id'], 'machine_id': mid}


def unbind(local_only: bool = True) -> dict:
    """解除绑定。默认只清本地（不通知官网，避免误伤同账号其它机器）。"""
    import httpx
    d = _load()
    if not d.get('token'):
        _save({})
        return {'ok': True, 'note': '本来就没绑定'}
    if not local_only:
        try:
            httpx.post(server_url() + '/api/v1/unbind',
                       json={'machine_id': d.get('machine_id') or machine_id(),
                             'binding_id': d.get('binding_id', ''),
                             'token': d.get('token', '')}, timeout=20.0)
        except Exception as e:
            return {'ok': False, 'error': '通知官网失败：%s' % e}
    _save({})
    return {'ok': True}


def _hostname() -> str:
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return ''


def _now() -> float:
    import time
    return time.time()
