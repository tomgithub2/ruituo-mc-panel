"""远程推送更新的 API 与界面入口。

端点（都需要管理员权限，更新会改代码，属于高危操作）：

    GET  /api/update/check        检查更新（会做 RSA 验签，失败即拒）
    POST /api/update/apply        下载 → 校验 → 备份 → 应用
    GET  /api/update/status       进度与日志
    POST /api/update/restart      重启面板（走退出码，交由 systemd/panelctl 拉起）
    GET  /api/update/binding      本机绑定状态
    POST /api/update/bind         用官网账号绑定（拿定向推送所需的机器身份）
    POST /api/update/unbind       解除绑定

安全说明：
  · 全部要求登录 + 超级管理员（`perms.is_super`）—— 更新等于远程下发代码，不能放给普通用户
  · 清单与每个文件的哈希都在**面板侧**独立校验，官网不承担信任职责
  · 更新包**不碰** `backend/data/`（用户数据）与依赖树，见 `updater.PROTECTED_PREFIXES`
"""
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import audit as audit_mod
from .. import binding as binding_mod
from .. import updater
from ..auth import get_current_user
from .. import permissions as perms

router = APIRouter(prefix='/api/update', tags=['update'])


def _require_super(user: dict):
    """更新能力仅限超级管理员（高危：等于远程下发代码）。"""
    if not perms.is_super(user):
        raise HTTPException(status_code=403, detail='权限不足：仅超级管理员可以更新面板')
    return user


@router.get('/check')
def check(user: dict = Depends(get_current_user)):
    _require_super(user)
    return updater.check()


@router.get('/status')
def status_(user: dict = Depends(get_current_user)):
    _require_super(user)
    return {'ok': True, 'busy': updater.UPDATE_STATE['busy'],
            'log': updater.UPDATE_STATE['log'][-60:],
            'last_result': updater.UPDATE_STATE['last_result'],
            'current_version': updater.PANEL_VERSION}


@router.post('/apply')
def apply(request: Request, body: dict = None, user: dict = Depends(get_current_user)):
    _require_super(user)
    body = body or {}
    if updater.UPDATE_STATE['busy']:
        raise HTTPException(status_code=409, detail='已有更新任务在进行中，请稍候')
    # 允许直接带清单，也允许只给“版本号”→ 重新 check 一次取清单（默认后者更安全：
    # 清单始终来自官网当前响应，而不是客户端缓存的旧清单）
    info = body.get('manifest_info')
    if not info:
        info = updater.check()
        if not info.get('ok'):
            raise HTTPException(status_code=400, detail=info.get('error') or '检查更新失败')
        if not info.get('has_update'):
            raise HTTPException(status_code=409, detail='当前已是最新版本')
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'update.apply', 0, '应用远程更新 → %s' % info.get('version'))
    # 后台线程执行：应用包含下载，可能几十秒，不能阻塞请求
    threading.Thread(target=_run_apply, args=(info,), daemon=True).start()
    return {'ok': True, 'started': True, 'version': info.get('version'),
            'note': '更新在后台进行，用 GET /api/update/status 看进度'}


def _run_apply(info: dict):
    res = updater.apply(info)
    updater.UPDATE_STATE['last_result'] = res
    if res.get('ok'):
        try:
            updater.ack_push(info)
        except Exception:
            pass


@router.post('/restart')
def restart(request: Request, user: dict = Depends(get_current_user)):
    """重启面板：延迟 1 秒后退出当前进程，交由 systemd / panelctl 拉起。

    为什么不直接 `os.execv` 自己：运行中的 WebSocket 与后台线程需要正常收敛，
    退出码交给服务管理器最干净（也兼容 `--no-systemd` 下用 panelctl 的部署方式）。
    """
    _require_super(user)
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'update.restart', 0, '重启面板以应用更新')

    def _bye():
        time.sleep(1.0)
        import os
        import signal
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)                                        # noqa: SLF001

    threading.Thread(target=_bye, daemon=True).start()
    return {'ok': True, 'note': '面板将在 1 秒后重启；若用 panelctl/ systemd 部署会自动拉起，'
                                '否则请手动执行 panelctl start'}


# --------------------------------------------------------------------- 绑定

@router.get('/binding')
def binding_status(user: dict = Depends(get_current_user)):
    _require_super(user)
    return {'ok': True, **binding_mod.status(), 'server': binding_mod.server_url()}


@router.post('/bind')
def bind(request: Request, body: dict = None, user: dict = Depends(get_current_user)):
    """用官网账号绑定本机，以获得「定向推送」（需在官网后台把更新推给本机）。"""
    _require_super(user)
    body = body or {}
    r = binding_mod.bind(body.get('account', ''), body.get('password', ''))
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('error') or '绑定失败')
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'update.bind', 0, '绑定官网账号 %s' % body.get('account', ''))
    return {'ok': True, **binding_mod.status()}


@router.post('/unbind')
def unbind(request: Request, body: dict = None, user: dict = Depends(get_current_user)):
    _require_super(user)
    body = body or {}
    r = binding_mod.unbind(local_only=bool(body.get('local_only', True)))
    if not r.get('ok'):
        raise HTTPException(status_code=400, detail=r.get('error') or '解绑失败')
    audit_mod.audit(user['username'], request.client.host if request.client else '',
                    'update.unbind', 0, '解除官网账号绑定')
    return {'ok': True, **binding_mod.status()}
