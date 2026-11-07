"""认证：PBKDF2-SHA256 口令、会话令牌、登录失败锁定（与 RT面板 同口径）。"""
import hashlib
import hmac
import logging
import secrets
import string
import threading
import time

from fastapi import Header, HTTPException, Request

from . import audit as audit_mod
from .config import get_config
from .database import execute, now, query

logger = logging.getLogger('mcpanel.auth')

_lock = threading.RLock()
_fail_map = {}
FAIL_MAP_MAX = 5000
FAIL_KEY_MAX = 128

PBKDF2_ITERATIONS = 600000
PASSWORD_MIN_LENGTH = 12


def hash_password(password: str, salt: str = '') -> str:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                             salt.encode('utf-8'), PBKDF2_ITERATIONS)
    return salt + '$' + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hexhash = stored.split('$', 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                             salt.encode('utf-8'), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), hexhash)


def password_policy_error(password: str) -> str:
    """返回口令策略错误；空字符串代表符合要求。"""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f'密码至少 {PASSWORD_MIN_LENGTH} 位'
    classes = sum((any(c.islower() for c in password),
                   any(c.isupper() for c in password),
                   any(c.isdigit() for c in password),
                   any(not c.isalnum() for c in password)))
    if classes < 3:
        return '密码需包含四类字符中的至少三类：小写字母、大写字母、数字、特殊字符'
    return ''


def random_password(length: int = 20) -> str:
    """生成满足策略的随机口令（保证四类字符齐全）。"""
    pools = [string.ascii_lowercase, string.ascii_uppercase,
             string.digits, '!@#$%^&*-_=+']
    chars = [secrets.choice(p) for p in pools]
    allch = ''.join(pools)
    chars += [secrets.choice(allch) for _ in range(max(0, length - len(chars)))]
    out = list(chars)
    for i in range(len(out) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        out[i], out[j] = out[j], out[i]
    return ''.join(out)


# ---------------------------------------------------------------- 会话令牌
def create_session(uid: int, ip: str = '', ua: str = '') -> tuple:
    cfg = get_config()
    hours = min(max(int(cfg.get('session_hours', 24)), 1), 168)
    token = secrets.token_urlsafe(32)
    ts = now()
    execute('INSERT INTO sessions (token, uid, created_at, expires_at, ip, ua) '
            'VALUES (?,?,?,?,?,?)', (token, uid, ts, ts + hours * 3600, ip, ua[:200]))
    return token, ts + hours * 3600


def destroy_session(token: str):
    if token:
        execute('DELETE FROM sessions WHERE token=?', (token,))


def _purge_sessions():
    execute('DELETE FROM sessions WHERE expires_at<=?', (now(),))


def resolve_token(token: str):
    if not token:
        return None
    _purge_sessions()
    return query('SELECT s.token, s.expires_at, u.* FROM sessions s JOIN users u ON u.id=s.uid '
                 'WHERE s.token=? AND s.expires_at>? AND u.status=1', (token, now()), one=True)


# ---------------------------------------------------------------- 失败锁定
def record_fail(key: str) -> int:
    with _lock:
        key = str(key)[:FAIL_KEY_MAX]
        if len(_fail_map) >= FAIL_MAP_MAX and key not in _fail_map:
            oldest = sorted(_fail_map.items(), key=lambda kv: kv[1][1])[:FAIL_MAP_MAX // 4]
            for k, _ in oldest:
                _fail_map.pop(k, None)
        count, _ts = _fail_map.get(key, (0, 0))
        _fail_map[key] = (count + 1, time.time())
        return count + 1


def reset_fail(key: str):
    with _lock:
        _fail_map.pop(key, None)


def is_locked(key: str) -> bool:
    cfg = get_config()
    max_fails = int(cfg.get('max_login_fails', 5))
    lock_min = int(cfg.get('lock_minutes', 10))
    with _lock:
        count, ts = _fail_map.get(key, (0, 0))
    return count >= max_fails and (time.time() - ts) < lock_min * 60


# ---------------------------------------------------------------- FastAPI 依赖
def _bearer(request: Request, authorization) -> str:
    """从 Authorization 头或 `?token=` 取令牌。

    ⚠️ `authorization` **不能假设一定是 str**：`get_current_user` 是 FastAPI 依赖，
    正常走依赖注入时它是字符串；但 WebSocket（和任何直接调用）传进来的是
    `Header(...)` 的**默认哨兵对象**，对它调 `.startswith()` 会抛
    `AttributeError: 'Header' object has no attribute 'startswith'` ——
    表现成控制台 WebSocket 握手 **500**（实例详情页的实时控制台直接连不上）。
    """
    if isinstance(authorization, str) and authorization.startswith('Bearer '):
        return authorization[7:].strip()
    # WebSocket / 下载直链兼容 ?token=
    try:
        t = request.query_params.get('token') if hasattr(request, 'query_params') else ''
    except Exception:
        t = ''
    return (t or '').strip()


def _ws_bearer(ws) -> str:
    """WebSocket 取令牌：优先 `?token=`（前端 wsUrl 就是这么拼的），退化到请求头。"""
    try:
        t = (ws.query_params.get('token') or '').strip()
        if t:
            return t
    except Exception:
        pass
    try:
        auth = ws.headers.get('authorization') or ''
    except Exception:
        auth = ''
    if isinstance(auth, str) and auth.startswith('Bearer '):
        return auth[7:].strip()
    return ''


def get_current_user(request: Request, authorization: str = Header(default='')) -> dict:
    token = _bearer(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail='未登录')
    user = resolve_token(token)
    if not user:
        raise HTTPException(status_code=401, detail='登录已过期，请重新登录')
    user.pop('password_hash', None)
    return user


def try_get_user(request, authorization=''):
    """WebSocket / 直连用：不抛异常，失败返回 None。

    走 WebSocket 时 `request` 是 `WebSocket` 对象，没有 `query_params` 也有 `headers`，
    用 `_ws_bearer` 取令牌；拿不到就返回 None（调用方自己决定关连接码）。
    """
    try:
        token = _ws_bearer(request) or _bearer(request, authorization)
    except Exception:
        token = ''
    if not token:
        return None
    try:
        user = resolve_token(token)
    except Exception:
        return None
    if not user:
        return None
    user.pop('password_hash', None)
    return user


def get_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else 'unknown'
    return peer or 'unknown'


def ensure_admin_user() -> str:
    """首次启动创建**唯一的超级管理员**账号（规格 §4），随机口令。

    口令只在本地终端打印一次，不写日志、不写页面、不入库明文。
    另外兜底一处升级路径：老库里可能存在 `role='admin'` 的初始账号（角色体系之前建库的），
    若系统里**一个超管都没有**就把它提升为 `super_admin` 并记审计 —— 否则没人能管用户。

    返回初始口令（仅首次创建时非空）。
    """
    from . import permissions as perms

    if not query('SELECT id FROM users LIMIT 1', one=True):
        pwd = random_password(20)
        quo = perms.DEFAULT_QUOTA[perms.SUPER_ADMIN]
        execute('INSERT INTO users (username, password_hash, role, status, created_at, '
                'max_instances, max_memory_mb_total, max_backups, max_upload_mb, '
                'allow_build_import) VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('admin', hash_password(pwd), perms.SUPER_ADMIN, 1, now(),
                 quo['max_instances'], quo['max_memory_mb_total'], quo['max_backups'],
                 quo['max_upload_mb'], 1 if quo['allow_build_import'] else 0))
        audit_mod.audit('system', '', 'init',
                        detail='创建初始账号 admin（超级管理员）', level='warn')
        banner = [
            '',
            '=' * 68,
            '  芮拓MC面板 · 首次启动，已创建默认账号',
            '',
            '    用户名: admin',
            '    角色:   超级管理员（super_admin）',
            f'    初始口令: {pwd}',
            '',
            '  * 该口令只在此处显示一次，不会写入日志或页面，请立即保存。',
            '  * 登录后可在「设置 → 修改口令」中更换。',
            '  * 若忘记口令：在服务器上执行 `python tools/reset_admin.py admin` 重置（随包自带）。',
            '=' * 68,
            '',
        ]
        for line in banner:
            print(line, flush=True)
        return pwd

    if not query('SELECT id FROM users WHERE role=? AND status=1 LIMIT 1',
                 (perms.SUPER_ADMIN,), one=True):
        row = query('SELECT id, username FROM users ORDER BY id ASC LIMIT 1', one=True)
        if row:
            quo = perms.DEFAULT_QUOTA[perms.SUPER_ADMIN]
            execute('UPDATE users SET role=?, max_instances=?, max_memory_mb_total=?, '
                    'max_backups=?, max_upload_mb=?, allow_build_import=1 WHERE id=?',
                    (perms.SUPER_ADMIN, quo['max_instances'], quo['max_memory_mb_total'],
                     quo['max_backups'], quo['max_upload_mb'], row['id']))
            audit_mod.audit('system', '', 'init.user_upgrade',
                            detail=f'库中无超级管理员，已将账号「{row["username"]}」提升为超级管理员',
                            level='warn')
            print(f'[mcpanel] 库中无超级管理员 → 已将「{row["username"]}」提升为 super_admin',
                  flush=True)
    return ''


def reset_password(uid: int, new_password: str = '') -> str:
    """管理员重置他人口令：口令留空则服务端随机生成。

    返回**明文一次**（调用方负责只回显一次、绝不落日志/审计）。
    改完立即 bump `token_epoch` + 清空该用户全部会话（规格 §7）。
    """
    user = query('SELECT id, username FROM users WHERE id=?', (uid,), one=True)
    if not user:
        raise HTTPException(status_code=404, detail='用户不存在')
    pwd = new_password or random_password(20)
    err = password_policy_error(pwd)
    if err:
        raise HTTPException(status_code=400, detail=err)
    execute('UPDATE users SET password_hash=?, token_epoch=COALESCE(token_epoch,0)+1 WHERE id=?',
            (hash_password(pwd), uid))
    execute('DELETE FROM sessions WHERE uid=?', (uid,))
    return pwd


def kick_user(uid: int) -> None:
    """强制下线：bump `token_epoch` 并清空该用户全部会话。"""
    execute('UPDATE users SET token_epoch=COALESCE(token_epoch,0)+1 WHERE id=?', (uid,))
    execute('DELETE FROM sessions WHERE uid=?', (uid,))


def change_password(uid: int, old_password: str, new_password: str) -> None:
    user = query('SELECT * FROM users WHERE id=?', (uid,), one=True)
    if not user or not verify_password(old_password, user['password_hash']):
        raise HTTPException(status_code=400, detail='原密码不正确')
    err = password_policy_error(new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    execute('UPDATE users SET password_hash=?, token_epoch=COALESCE(token_epoch,0)+1 WHERE id=?',
            (hash_password(new_password), uid))
    execute('DELETE FROM sessions WHERE uid=?', (uid,))
