# -*- coding: utf-8 -*-
"""一键体检：检查本机 芮拓MC面板是否真的活着、前端文件是否齐全、首页是否可渲染。

给"打开是一片空白、不知道哪一步断了"用的。不需要懂命令，双击 `体检面板.bat` 即可，
结果会弹一个窗口显示（同时写到 <仓库根>/体检报告.txt）。

用法（**源码里不写死任何凭据**）::

    python tools/panel_doctor.py                          # 只做不需要登录的检查
    python tools/panel_doctor.py --password 你的口令       # 连登录与权限接口一起查
    python tools/panel_doctor.py --base http://host:8100 --password 口令 --user admin

口令也可以走环境变量 `MC_DOCTOR_PASSWORD` / `MC_DOCTOR_USER` / `MC_DOCTOR_BASE`。
没给口令时会**跳过登录相关的第 5 步**，其余检查照常。
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

#: 仓库根目录：本文件在 <repo>/tools/ 下，往上一层就是根
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_FILE = os.path.join(ROOT, '体检报告.txt')

#: 配置优先级：命令行 > 环境变量 > 默认值
DOCTOR_BASE = os.environ.get('MC_DOCTOR_BASE') or 'http://127.0.0.1:8100'
DOCTOR_USER = os.environ.get('MC_DOCTOR_USER') or 'admin'
DOCTOR_PASSWORD = os.environ.get('MC_DOCTOR_PASSWORD') or ''


def _parse_argv(argv):
    """解析命名参数。**不再把位置参数当口令** —— 原来 `argv[1]` 是"检查地址"，
    再加一个位置参数当口令就会互相打架（实测把口令当 URL 去解析，直接报
    `getaddrinfo failed`）。这里统一用 `--flag value`。"""
    global DOCTOR_BASE, DOCTOR_USER, DOCTOR_PASSWORD
    i = 1
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else ''
        if a in ('--base', '-b') and nxt:
            DOCTOR_BASE = nxt.strip(); i += 2; continue
        if a in ('--password', '-p') and nxt:
            DOCTOR_PASSWORD = nxt.strip(); i += 2; continue
        if a in ('--user', '-u') and nxt:
            DOCTOR_USER = nxt.strip(); i += 2; continue
        if a in ('-h', '--help'):
            print(__doc__)
            sys.exit(0)
        # 兼容老用法：只给一个位置参数时，它是"检查地址"
        if not a.startswith('-') and i == 1:
            DOCTOR_BASE = a.strip(); i += 1; continue
        print(f'未知参数：{a}（用 --help 看用法）')
        sys.exit(2)


_parse_argv(sys.argv)

LINES = []


def log(s=''):
    LINES.append(s)
    print(s)


def http(url, method='GET', body=None, timeout=6, token=''):
    data = None
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace'), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace'), {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:                                   # noqa: BLE001
        return 0, f'{type(e).__name__}: {e}', {}


def main():
    base = (DOCTOR_BASE or 'http://127.0.0.1:8100').rstrip('/')
    host_port = base.split('//')[-1]
    host, _, port = host_port.partition(':')
    port = int(port or 80)

    log('=' * 62)
    log('  芮拓MC面板 · 一键体检')
    log('=' * 62)
    log(f'  检查地址: {base}')
    log()

    # 1) 端口在不在听
    s = socket.socket()
    s.settimeout(3)
    reachable = s.connect_ex((host if host not in ('localhost',) else '127.0.0.1', port)) == 0
    s.close()
    log(f'[1] 端口 {port} {"✅ 有人监听" if reachable else "❌ 没人监听（面板没在跑）"}')
    if not reachable:
        log('    → 面板没启动。启动命令（在 backend 目录）：')
        log('        set PYTHONUTF8=1')
        log('        python run.py')
        return 1

    # 2) 健康检查
    st, body, _ = http(base + '/api/health')
    log(f'[2] /api/health → {st} {body[:120] if st == 200 else body[:200]}')

    # 3) 首页
    st, html, hdrs = http(base + '/')
    csp = hdrs.get('content-security-policy', '')
    log(f'[3] 首页 → {st}，{len(html)} 字节，Content-Type={hdrs.get('content-type','?')}')
    if st == 200:
        for need in ('<div id="app">', '/js/main.js', '/js/permissions.js', '/css/tokens.css'):
            log(f'      {"✅" if need in html else "❌"} 首页包含 {need}')
        if 'mock-accounts.js' in html:
            log('      ⚠️ 首页还在引用已删除的 mock-accounts.js（说明是**旧的 index.html**，'
                '请强制刷新 Ctrl+Shift+R）')
    if csp:
        log(f'      CSP: {csp[:100]}…（若 script-src 不含 self 会拦掉 JS）')

    # 4) 关键静态资源
    log('[4] 静态资源:')
    for p in ('/js/main.js', '/js/permissions.js', '/js/router.js', '/js/components.js',
              '/js/pages/instances.js', '/js/pages/settings.js', '/js/pages/accounts.js',
              '/css/tokens.css', '/css/base.css', '/css/components.css', '/css/bg.css'):
        st, b, h = http(base + p, timeout=5)
        ct = h.get('content-type', '?')
        ok = st == 200 and ('javascript' in ct or 'css' in ct)
        log(f'      {"✅" if ok else "❌"} {p} → {st}  {ct}  {len(b)} 字节')

    # 5) 登录 + 权限接口（口令从参数/环境变量取，源码里不留凭据）
    token = ''
    if not DOCTOR_PASSWORD:
        log('[5] 登录 → 已跳过（没给口令）')
        log('      → 用法：python tools/panel_doctor.py --password <口令>')
        log('        或设环境变量 MC_DOCTOR_PASSWORD；口令忘了见 README §1')
    else:
        st, body, _ = http(base + '/api/auth/login', 'POST',
                           {'username': DOCTOR_USER, 'password': DOCTOR_PASSWORD})
        if st != 200:
            log(f'[5] 登录 → {st} {body[:160]}')
            log('      → 口令不对或被锁定（连续输错会锁 10 分钟；重启面板可立即清除）')
        else:
            token = json.loads(body).get('token', '')
            log(f'[5] 登录 → 200，拿到令牌（{len(token)} 字符）')
    if token:
        for p in ('/api/auth/me', '/api/me/permissions', '/api/instances', '/api/settings',
                  '/api/users', '/api/overview'):
            st, body, _ = http(base + p, timeout=8, token=token)
            extra = ''
            if p == '/api/me/permissions' and st == 200:
                d = json.loads(body)
                extra = f"  role={d.get('role')} 权限点={len(d.get('perms') or [])}"
            if p == '/api/instances' and st == 200:
                d = json.loads(body)
                extra = f"  实例数={len(d.get('instances') or [])} scope={d.get('scope')}"
            log(f'      {"✅" if st == 200 else "❌"} {p} → {st}{extra}')

    log()
    log('=' * 62)
    log('  结论怎么看')
    log('=' * 62)
    log('  · [1] 没人监听 → 面板没启动')
    log('  · [3] 提示 index.html 是旧的 → 浏览器缓存，Ctrl+Shift+R')
    log('  · [4] 有 ❌ → 前端文件缺失/被拦（把整份报告发出来）')
    log('  · [5] 403/401 → 账号权限或令牌问题')
    log('  · 全部 ✅ 但浏览器仍空白 → 把 体检报告.txt 整份发出来')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    rc = 0
    try:
        rc = main()
    except Exception as exc:                                 # noqa: BLE001
        log(f'体检脚本自己出错: {type(exc).__name__}: {exc}')
        rc = 2
    out = REPORT_FILE
    try:
        with open(out, 'w', encoding='utf-8') as f:
            f.write('\n'.join(LINES))
    except Exception:
        pass
    print()
    print('报告已写入: ' + out)
    sys.exit(rc)
