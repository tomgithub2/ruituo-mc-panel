"""芮拓MC开服面板 FastAPI 应用入口。

**完全独立**：不引用、不依赖同仓任何其它面板（RT面板 / ops-panel）的代码、配置、
数据库或服务；同仓 ops-panel 仅作为"观感与形态"的只读参考。
"""
import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, DATA_DIR, PANEL_VERSION, get_config
from .database import init_db
from .auth import ensure_admin_user, resolve_token

logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
                    datefmt='%H:%M:%S')
logger = logging.getLogger('mcpanel')

_DOCS_ON = os.environ.get('MC_DOCS') == '1'
app = FastAPI(title='芮拓MC开服面板', version=PANEL_VERSION,
              docs_url='/api/docs' if _DOCS_ON else None,
              openapi_url='/api/openapi.json' if _DOCS_ON else None,
              redoc_url=None)

_started_at = time.time()

# 免鉴权白名单（登录、健康检查、登录页所需的静态资源）
_PUBLIC = ('/api/auth/login', '/api/health')


# ---------------------------------------------------------------- 安全头
@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    # ⚠️ `script-src` 必须带 `'unsafe-inline'`：面板是**无构建步骤的原生前端**，
    # index.html / doctor.html 以及各页面模板里都有内联 `onclick`、`onerror`、内联 `<script>`。
    # 只写 `'self'` 会把这些**静默拦掉**（浏览器只在 console 报 CSP violation，
    # 页面上看不出任何提示 —— 实测 doctor.html 就是整段内联脚本不执行、
    # 一直停在"正在检查…"）。这是自托管运维面板，脚本全部由本进程自己发出的 HTML 内联，
    # 没有第三方注入面，所以放开 inline 脚本是可接受的取舍。
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; "
        "connect-src 'self' ws: wss:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'")
    return response


# ---------------------------------------------------------------- 全局限流
import collections            # noqa: E402
import threading              # noqa: E402

_rate_lock = threading.Lock()
_rate_store = collections.defaultdict(list)


def _rate_ok(key: str, limit: int, window: float) -> bool:
    with _rate_lock:
        nowt = time.time()
        bucket = [t for t in _rate_store[key] if nowt - t < window]
        _rate_store[key] = bucket
        if len(_rate_store) > 5000:
            for k in [k for k, v in _rate_store.items() if not v]:
                _rate_store.pop(k, None)
        if len(bucket) >= limit:
            return False
        bucket.append(nowt)
        return True


@app.middleware('http')
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else 'unknown'
    path = request.url.path
    if path == '/api/auth/login':
        # 登录限流可配（默认 10 次/60s 防爆破）；端到端验收脚本需要跑很多次登录，
        # 会在自己的 config.json 里调高它 —— 所以这里必须**运行时读配置**，
        # 不能在模块导入时固化。
        try:
            limit = int(get_config().get('login_rate_limit', 10))
        except Exception:
            limit = 10
        limit = max(1, min(limit, 10000))
        if not _rate_ok(f'login:{ip}', limit, 60):
            return JSONResponse(status_code=429,
                                content={'detail': '请求过于频繁，请稍后再试', 'code': 429})
    # 健康检查/版本探测必须豁免限流：它们会被前端轮询、被面板自身的存活检查打，
    # 也被压测与自检脚本高频调用；把它们计入配额会把正常运维流量误判为攻击。
    if path in ('/api/health', '/api/version', '/api/ping'):
        return await call_next(request)
    if path.startswith('/api/'):
        # 全局 API 限流同样可配（默认 1800 次/60s）；端到端验收脚本会调高它，
        # 所以必须运行时读配置，让面板在真实浏览器/脚本高频访问下不会被自己限流。
        try:
            api_limit = int(get_config().get('api_rate_limit', 1800))
        except Exception:
            api_limit = 1800
        api_limit = max(60, min(api_limit, 1000000))
        if not _rate_ok(f'api:{ip}', api_limit, 60):
            return JSONResponse(status_code=429, content={'detail': '请求过于频繁，请稍后再试', 'code': 429})
    return await call_next(request)


@app.middleware('http')
async def body_size_limit(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH'):
        ctype = request.headers.get('content-type', '')
        if 'multipart/form-data' not in ctype:
            length = request.headers.get('content-length')
            try:
                if length and int(length) > 30 * 1024 * 1024:
                    return JSONResponse(status_code=413, content={'detail': '请求体过大', 'code': 413})
            except ValueError:
                pass
    return await call_next(request)


def _extract_token(request: Request) -> str:
    auth = request.headers.get('authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return (request.query_params.get('token') or '').strip()


@app.middleware('http')
async def auth_gate(request: Request, call_next):
    """统一鉴权闸门：**所有 /api/** 未带有效令牌一律 401。

    各路由自身也有依赖校验，这里是兜底 —— 保证任何新加接口默认就是"关着的"，
    不会因为忘记声明 Depends(get_current_user) 而裸奔。
    """
    path = request.url.path
    if path.startswith('/api/') and not path.startswith(_PUBLIC):
        if not resolve_token(_extract_token(request)):
            return JSONResponse(status_code=401, content={'detail': '未登录', 'code': 401})
    return await call_next(request)


# ---------------------------------------------------------------- 异常处理
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    body = {'detail': exc.detail, 'code': exc.status_code}
    code = getattr(exc, 'code', '')
    if code:
        body['perm'] = code
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={'detail': '请求参数校验失败', 'code': 422})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.error('unhandled request error: %s: %s', type(exc).__name__, exc)
    return JSONResponse(status_code=500, content={'detail': '服务器内部错误', 'code': 500})


# ---------------------------------------------------------------- 路由注册
from .routers import (auth as auth_router, backups, build as build_router,  # noqa: E402
                      config as config_router, core, cron, files, instances,
                      logs as logs_router, players, plugins, settings, update as update_router,
                      users)

for mod in (auth_router, settings, core, instances, config_router, files, players,
            backups, cron, plugins, logs_router, build_router, users, update_router):
    app.include_router(mod.router)

# 仪表盘实时通道（`/api/dashboard/ws`）：独立前缀，不落在 `/api/instances/{iid}` 命名空间里
app.include_router(instances.live_router)


def _api_route_paths(routes=None) -> list:
    """递归取全部 API 路由路径。

    FastAPI 0.142 起 `include_router()` 往 `app.routes` 里塞的是**惰性包装**
    （`_IncludedRouter`，不直接暴露 `path`），所以要顺着 `original_router.routes`
    往下走一层 —— 否则"路由数为 0"会误判成挂载失败。
    """
    out = []
    for r in (app.routes if routes is None else routes):
        p = getattr(r, 'path', None)
        if p:
            out.append(p)
        sub = getattr(r, 'original_router', None)
        if sub is not None:
            out.extend(_api_route_paths(getattr(sub, 'routes', []) or []))
    return [p for p in out if p.startswith('/api')]


_ROUTE_PATHS = _api_route_paths()
logger.info('已注册 API 路由 %d 条', len(_ROUTE_PATHS))


# ---------------------------------------------------------------- 启动 / 关闭
@app.on_event('startup')
def on_startup():
    init_db()
    ensure_admin_user()
    # 老库升级上来的实例可能没有归属 → 交给初始超管，避免"谁都看不见"
    from .permissions import assign_orphans
    n = assign_orphans()
    if n:
        logger.info('已把 %d 个无归属实例划给初始超级管理员', n)
    from .routers.logs import install_log_handler, app_log
    install_log_handler()
    app_log('面板启动，数据目录 %s' % DATA_DIR)
    from .process_manager import manager
    manager.bootstrap()
    from .scheduler import scheduler
    scheduler.start()
    cfg = get_config()
    logger.info('数据目录: %s', DATA_DIR)
    logger.info('面板地址: http://%s:%s/', cfg.get('bind_host'), cfg.get('port'))
    # 清理过期的临时下载分片
    try:
        from .config import TMP_DIR
        for fn in os.listdir(TMP_DIR):
            p = os.path.join(TMP_DIR, fn)
            if os.path.isfile(p) and time.time() - os.path.getmtime(p) > 86400:
                os.remove(p)
    except Exception:
        pass


@app.on_event('shutdown')
def on_shutdown():
    from .scheduler import scheduler
    scheduler.stop()
    from .process_manager import manager
    manager.stop_monitor()
    logger.info('面板已关闭（运行中的 MC 实例继续运行，面板重启后可重新接管）')


# ---------------------------------------------------------------- 静态前端
_DIST = os.path.normpath(os.path.join(BASE_DIR, '..', 'frontend', 'dist'))

if os.path.isdir(_DIST):
    # 旧资源名兼容：`mock-accounts.js` 已在权限体系上线后删除（它的 MockAccounts/Perm
    # 被真实实现取代）。但浏览器可能缓存着**旧的 index.html**，而它 `<script>` 里还
    # 引用着这个文件 —— 直接 404 会让那一页的 JS 链断掉（表现为卡在启动画面）。
    # 把旧名字映射到新文件，旧页面也能正常跑；新 index.html 已不引用它，不会重复加载。
    #
    # ⚠️ 必须注册在 `app.mount('/js', ...)` **之前**：StaticFiles 挂载会先截获 /js/**，
    # 放在挂载之后就永远轮不到（第一版就踩了这个，测出来仍是 404）。
    @app.get('/js/mock-accounts.js')
    async def _legacy_mock_accounts():
        return FileResponse(os.path.join(_DIST, 'js', 'permissions.js'),
                            media_type='application/javascript; charset=utf-8',
                            headers={'Cache-Control': 'no-cache, must-revalidate'})

    # `/site/` 与 `/site` 直接给官网首页。
    # ⚠️ 必须注册在下面的 `app.mount('/site', ...)` **之前** —— StaticFiles 挂载会先
    # 截获 /site/**，注册在它后面就永远轮不到（和 /js/mock-accounts.js 那次同一个坑）。
    @app.get('/site')
    @app.get('/site/')
    async def _site_index():
        return FileResponse(os.path.join(_DIST, 'site', 'index.html'), media_type='text/html')

    for sub in ('js', 'css', 'img', 'vendor', 'site'):
        subdir = os.path.join(_DIST, sub)
        if os.path.isdir(subdir):
            app.mount(f'/{sub}', StaticFiles(directory=subdir), name=sub)

    @app.get('/{full_path:path}')
    async def spa(full_path: str):
        # 未匹配的 /api/** 必须回 JSON 404，绝不能被 SPA 兜底吞成 200 HTML。
        # 否则接口名打错也“看起来成功”（前端拿 HTML 去 JSON.parse 才炸），
        # 自检脚本也会把空响应当成“接口返回空”，排查成本极高。
        if full_path == 'api' or full_path.startswith('api/'):
            return JSONResponse({'detail': '接口不存在', 'code': 404}, status_code=404)

        target = os.path.normpath(os.path.join(_DIST, full_path))
        try:
            inside_dist = os.path.commonpath(
                [os.path.realpath(_DIST), os.path.realpath(target)]) == os.path.realpath(_DIST)
        except ValueError:
            inside_dist = False
        if full_path and os.path.isfile(target) and inside_dist:
            # HTML 与 JS 一律 no-cache：面板是"自托管运维工具"，改动后必须立刻生效，
            # 缓存住旧 index.html 会引发"文件删了、页面还去请求"这类难查的问题。
            no_cache = full_path == '' or full_path.endswith(('.html', '.js', '.css'))
            resp = FileResponse(target)
            if no_cache:
                resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
            return resp
        return FileResponse(os.path.join(_DIST, 'index.html'))
