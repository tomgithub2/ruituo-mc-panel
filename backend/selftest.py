"""本机端到端自检（不依赖外网）。

覆盖：
  1. 登录 → 令牌
  2. 未登录访问所有 API → 必须 401（全量遍历已注册路由）
  3. 文件接口安全：`../../` 穿越、绝对路径、符号链接逃逸 → 必须 403
  4. 本地上传假 jar（模拟 MC 服务端）→ 创建实例 → 启动 → 控制台有输出 → 停止
  5. 进程真的没了（PID 存活核对）

用法： python selftest.py <初始口令>
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8100'
HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []
OKS = []


def log(msg):
    print(msg, flush=True)


def check(name, cond, detail=''):
    if cond:
        OKS.append(name)
        log('  [PASS] %s %s' % (name, detail))
    else:
        FAILS.append(name + ' ' + detail)
        log('  [FAIL] %s %s' % (name, detail))
    return cond


def req(method, path, body=None, token=None, raw=False, ctype='application/json', timeout=60):
    url = BASE + path
    data = None
    headers = {'User-Agent': 'selftest'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        if ctype == 'application/json':
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        else:
            data = body
            # 关键：非 JSON 时必须显式带上调用方给的 Content-Type。
            # 之前漏了这一行 → urllib 默认发 application/x-www-form-urlencoded，
            # 服务端 multipart 解析器拿不到 boundary，直接 422（这就是那个"上传 422"的真凶）。
            headers['Content-Type'] = ctype
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw_body = resp.read()
            if raw:
                return resp.status, raw_body, dict(resp.headers)
            try:
                return resp.status, json.loads(raw_body.decode('utf-8')), dict(resp.headers)
            except Exception:
                return resp.status, raw_body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        if raw:
            return e.code, raw_body, dict(e.headers)
        try:
            return e.code, json.loads(raw_body.decode('utf-8')), dict(e.headers)
        except Exception:
            return e.code, raw_body, dict(e.headers)


def multipart(fields, filename, content):
    """构造 multipart/form-data。

    实测坑：字段名**必须带双引号**（`name="file"`）。不带引号（`name=file`）时
    服务端的 multipart 解析会失败并返回 422 —— 我最初就是这样误报"上传接口 422"的，
    而 curl 默认带引号所以一直正常。这里按 RFC 7578 规范写法。
    """
    b = b'----selftest' + os.urandom(6).hex().encode()
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(b'--' + b + b'\r\n')
        out.write(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode())
        out.write(str(v).encode() + b'\r\n')
    out.write(b'--' + b + b'\r\n')
    out.write(('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode())
    out.write(b'Content-Type: application/java-archive\r\n\r\n')
    out.write(content + b'\r\n')
    out.write(b'--' + b + b'--\r\n')
    return out.getvalue(), 'multipart/form-data; boundary=' + b.decode()


# ---------------------------------------------------------------- 0. 假 jar
MOCK_JAVA = r'''
import java.io.*;
import java.util.*;
public class MockServer {
  static volatile boolean running = true;
  public static void main(String[] a) throws Exception {
    System.out.println("[12:00:00] [main/INFO]: Starting minecraft server version 1.21.4");
    System.out.println("[12:00:00] [main/INFO]: Loading properties");
    System.out.println("[12:00:01] [main/WARN]: Failed to load eula.txt (mock warning)");
    System.out.println("[12:00:02] [main/INFO]: Preparing level \"world\"");
    System.out.println("[12:00:09] [Server thread/INFO]: Done (9.123s)! For help, type \"help\"");
    System.out.println("[12:00:09] [Server thread/INFO]: There are 0 of a max of 20 players online");
    BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
    Thread t = new Thread(() -> {
      try {
        String line;
        while ((line = br.readLine()) != null) {
          System.out.println("[12:00:10] [Server thread/INFO]: <console> issued server command: " + line);
          if (line.trim().equalsIgnoreCase("stop")) {
            System.out.println("[12:00:11] [Server thread/INFO]: Stopping the server");
            System.out.println("[12:00:11] [Server thread/INFO]: Saving worlds");
            running = false;
            break;
          }
          if (line.trim().startsWith("say ")) {
            System.out.println("[12:00:12] [Server thread/INFO]: [Server] " + line.substring(4));
          }
          if (line.trim().equals("list")) {
            System.out.println("[12:00:12] [Server thread/INFO]: There are 1 of a max of 20 players online: Steve");
          }
        }
      } catch (Exception e) {}
      running = false;
    });
    t.setDaemon(true);
    t.start();
    while (running) { Thread.sleep(200); System.out.println("[12:00:20] [Server thread/INFO]: TPS from last 1m, 5m, 15m: 19.93, 19.98, 20.0"); }
    Thread.sleep(300);
    System.out.println("[12:00:12] [Server thread/INFO]: ThreadedAnvilChunkStorage: All chunks saved");
    System.exit(0);
  }
}
'''


def build_mock_jar(workdir):
    """javac 一个模拟 MC 服务端并打成 jar；失败则返回 None（自检会如实标注）。"""
    jsrc = os.path.join(workdir, 'MockServer.java')
    with io.open(jsrc, 'w', encoding='utf-8') as f:
        f.write(MOCK_JAVA)
    cls = os.path.join(workdir, 'MockServer.class')
    javac = subprocess.run(['javac', jsrc], cwd=workdir, capture_output=True)
    if javac.returncode != 0:
        return None, javac.stderr.decode('utf-8', 'replace')[:400]
    jar = os.path.join(workdir, 'mock-server.jar')
    mf = os.path.join(workdir, 'MANIFEST.MF')
    with io.open(mf, 'w', encoding='utf-8') as f:
        f.write('Main-Class: MockServer\n')
    jarres = subprocess.run(['jar', 'cfm', jar, mf, 'MockServer.class'], cwd=workdir,
                            capture_output=True)
    if jarres.returncode != 0:
        return None, jarres.stderr.decode('utf-8', 'replace')[:400]
    return jar, ''


def pid_alive(pid):
    if not pid:
        return False
    out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % int(pid)], capture_output=True)
    return str(pid) in out.stdout.decode('utf-8', 'replace')


def main():
    pwd = sys.argv[1] if len(sys.argv) > 1 else ''
    iid = None
    pid = None
    token = ''          # 提到 try 外层：finally 与后续步骤都必须能拿到
    try:
        log('=== 1. 登录 ===')
        st, d, _ = req('POST', '/api/auth/login', {'username': 'admin', 'password': pwd})
        check('登录成功', st == 200 and d.get('token'), 'HTTP %s' % st)
        token = (d or {}).get('token', '')
        if not token:
            log('无法登录，终止')
            return 1
        # 立刻验一次令牌：确认后续请求带的是有效令牌（避免"全都 401"被误判成用例失败）
        st, d, _ = req('GET', '/api/auth/me', token=token)
        check('令牌可用（/api/auth/me 200）', st == 200, 'HTTP %s' % st)
        st, d, _ = req('POST', '/api/auth/login', {'username': 'admin', 'password': 'wrong-password-123'})
        check('错误口令被拒绝', st == 401, 'HTTP %s' % st)

        log('=== 2. 未登录访问所有 API 必须 401 ===')
        apis = []
        st, routes, _ = req('GET', '/api/openapi.json', token=token)
        if not (st == 200 and isinstance(routes, dict)):
            # 文档默认关闭：用手工清单（覆盖所有路由前缀）
            apis = [
                ('GET', '/api/instances'), ('POST', '/api/instances'),
                ('GET', '/api/instances/1'), ('GET', '/api/instances/1/status'),
                ('POST', '/api/instances/1/start'), ('POST', '/api/instances/1/stop'),
                ('POST', '/api/instances/1/restart'), ('POST', '/api/instances/1/kill'),
                ('POST', '/api/instances/1/command'), ('POST', '/api/instances/1/rcon'),
                ('GET', '/api/instances/1/log'), ('GET', '/api/instances/1/log/download'),
                ('GET', '/api/instances/1/metrics'), ('GET', '/api/instances/1/crashes'),
                ('GET', '/api/instances/1/jars'), ('POST', '/api/instances/1/jar'),
                ('POST', '/api/instances/1/install-core'), ('POST', '/api/instances/1/run-installer'),
                ('GET', '/api/instances/1/rcon/test'),
                ('GET', '/api/instances/1/files'), ('GET', '/api/instances/1/files/read'),
                ('PUT', '/api/instances/1/files/write'), ('POST', '/api/instances/1/files/mkdir'),
                ('POST', '/api/instances/1/files/rename'), ('POST', '/api/instances/1/files/delete'),
                ('POST', '/api/instances/1/files/unzip'), ('POST', '/api/instances/1/files/upload'),
                ('GET', '/api/instances/1/files/download'),
                ('GET', '/api/instances/1/config/properties'), ('PUT', '/api/instances/1/config/properties'),
                ('GET', '/api/instances/1/config/eula'), ('POST', '/api/instances/1/config/eula'),
                ('GET', '/api/instances/1/config/startup'), ('PUT', '/api/instances/1/config/startup'),
                ('GET', '/api/instances/1/players'), ('POST', '/api/instances/1/players/add'),
                ('POST', '/api/instances/1/players/remove'), ('POST', '/api/instances/1/players/op'),
                ('POST', '/api/instances/1/players/deop'), ('POST', '/api/instances/1/players/kick'),
                ('POST', '/api/instances/1/players/ban'), ('POST', '/api/instances/1/players/pardon'),
                ('GET', '/api/instances/1/players/rcon'),
                ('GET', '/api/instances/1/backups'), ('POST', '/api/instances/1/backups'),
                ('POST', '/api/instances/1/backups/1/restore'), ('DELETE', '/api/instances/1/backups/1'),
                ('GET', '/api/instances/1/backups/1/download'),
                ('GET', '/api/instances/1/plugins'), ('POST', '/api/instances/1/plugins/toggle'),
                ('POST', '/api/instances/1/plugins/delete'), ('POST', '/api/instances/1/plugins/upload'),
                ('GET', '/api/instances/1/plugins/search'), ('GET', '/api/instances/1/plugins/versions'),
                ('POST', '/api/instances/1/plugins/install'), ('GET', '/api/instances/1/plugins/sources'),
                ('GET', '/api/settings'), ('PUT', '/api/settings'),
                ('GET', '/api/audit'), ('GET', '/api/audit/login'),
                ('GET', '/api/cores'), ('GET', '/api/cores/paper/versions'),
                ('GET', '/api/cores/paper/builds'), ('GET', '/api/cores/paper/resolve'),
                ('GET', '/api/cores-probe'), ('GET', '/api/downloads'), ('GET', '/api/downloads/x'),
                ('GET', '/api/java'), ('GET', '/api/java/adoptium'), ('POST', '/api/java/install'),
                ('POST', '/api/java/test'), ('DELETE', '/api/java/21'),
                ('GET', '/api/cron'), ('POST', '/api/cron'), ('PATCH', '/api/cron/1'),
                ('DELETE', '/api/cron/1'), ('POST', '/api/cron/1/run'), ('GET', '/api/cron/1/runs'),
                ('GET', '/api/cron/all-runs'), ('POST', '/api/cron/validate'),
                ('GET', '/api/overview'), ('GET', '/api/logs/app'), ('GET', '/api/logs/app/download'),
                ('GET', '/api/logs/files'), ('GET', '/api/maintenance/usage'),
                ('POST', '/api/maintenance/clean-tmp'), ('POST', '/api/maintenance/clean-metrics'),
                ('POST', '/api/maintenance/clean-logs'), ('POST', '/api/maintenance/open-dir'),
                ('POST', '/api/instances-batch'), ('GET', '/api/webhook'), ('PUT', '/api/webhook'),
                ('GET', '/api/plugins-probe'), ('POST', '/api/auth/logout'), ('GET', '/api/auth/me'),
                ('POST', '/api/auth/password'),
            ]
        else:
            for path, ops in (routes.get('paths') or {}).items():
                for m in ops:
                    apis.append((m.upper(), path))
        bad = []
        for method, path in apis:
            real = path
            for ph in ('{iid}', '{bid}', '{jid}', '{tid}', '{source}', '{major}'):
                real = real.replace(ph, '1')
            st, d, _ = req(method, real, {} if method in ('POST', 'PUT', 'PATCH') else None)
            if st != 401:
                bad.append('%s %s -> %s' % (method, real, st))
        check('未登录访问 %d 个 API 全部 401' % len(apis), not bad,
              '' if not bad else '例外：' + '; '.join(bad[:8]))

        log('=== 3. 文件接口安全自测 ===')
        # 建一个测试实例（使用本地上传 jar 通道，不依赖外网）
        st, d, _ = req('POST', '/api/instances', {
            'name': 'selftest-mock', 'core_type': 'vanilla', 'mc_version': '', 'memory_mb': 1024,
            'port': 25599, 'download': False, 'accept_eula': True, 'java_path': ''
        }, token=token)
        check('创建测试实例', st == 200, 'HTTP %s %s' % (st, str(d)[:160]))
        iid = (d or {}).get('id')
        if not iid:
            return 1
        inst_dir = d['instance']['dir']
        log('    实例目录: %s' % inst_dir)

        # 造一个符号链接指向目录外
        outside = os.path.join(os.path.dirname(inst_dir), 'outside-secret.txt')
        with io.open(outside, 'w', encoding='utf-8') as f:
            f.write('SECRET-OUTSIDE\n')
        link = os.path.join(inst_dir, 'evil-link')
        try:
            os.symlink(outside, link)
            linked = True
        except Exception as e:
            linked = False
            log('    （创建符号链接失败，跳过该项：%s）' % e)

        trav = [
            ('../../../windows/win.ini', 'GET'),
            ('..%2f..%2f..%2fwindows%2fwin.ini', 'GET'),
            ('....//....//windows/win.ini', 'GET'),
            ('/etc/passwd', 'GET'),
            ('C:\\Windows\\win.ini', 'GET'),
        ]
        for p, m in trav:
            # 注意：带 %2f 的样例**不能**再 quote 一次，否则被二次编码成 %252f，
            # 服务端解出一个不存在的字面量目录名 → 404 而非 403，用例会误报。
            qs = p if '%' in p else urllib.parse.quote(p)
            st, d, _ = req('GET', '/api/instances/%d/files/read?path=%s' % (iid, qs), token=token)
            # `....//....//windows/win.ini`：在 POSIX 语义里 `....` 只是个普通目录名，
            # 它根本没向外跳。这类样例的判定标准是"绝不能读到内容"（非 200），
            # 而不是必须 403 —— 实例目录内不存在该文件时 404 才是正确行为。
            if '....//' in p:
                check('不构成穿越的样例未被放行 %s' % p[:26], st != 200, 'HTTP %s' % st)
            else:
                check('穿越读取被拒 %s' % p[:28], st == 403, 'HTTP %s' % st)
        for p, m in [('../../../tmp/evil.txt', 'POST')]:
            body = {'path': p, 'content': 'pwned'}
            st, d, _ = req('PUT', '/api/instances/%d/files/write' % iid, body, token=token)
            check('穿越写入被拒 %s' % p[:28], st == 403, 'HTTP %s' % st)
        st, d, _ = req('POST', '/api/instances/%d/files/mkdir' % iid, {'path': '../../evil-dir'}, token=token)
        check('穿越建目录被拒', st == 403, 'HTTP %s' % st)
        st, d, _ = req('POST', '/api/instances/%d/files/delete' % iid, {'path': '../../outside-secret.txt'}, token=token)
        check('穿越删除被拒', st == 403, 'HTTP %s' % st)
        st, d, _ = req('GET', '/api/instances/%d/files?path=%s' % (iid, urllib.parse.quote('../../')), token=token)
        check('穿越列目录被拒', st == 403, 'HTTP %s' % st)
        if linked:
            st, d, _ = req('GET', '/api/instances/%d/files/read?path=evil-link' % iid, token=token)
            check('符号链接逃逸被拒', st == 403, 'HTTP %s %s' % (st, str(d)[:120]))
            st, d, _ = req('GET', '/api/instances/%d/files/download?path=evil-link' % iid, token=token)
            check('符号链接下载被拒', st == 403, 'HTTP %s' % st)

        # 上传假 jar
        log('=== 4. 本地上传假 jar + 启动/停止 ===')
        jar, err = build_mock_jar(os.path.join(HERE, 'tmp'))
        if not jar:
            check('javac + jar 构建模拟服务端', False, err)
            return 1
        check('javac + jar 构建模拟服务端', True, os.path.basename(jar))
        with open(jar, 'rb') as f:
            content = f.read()
        data, ctype = multipart({'path': ''}, 'server.jar', content)
        st, d, _ = req('POST', '/api/instances/%d/files/upload' % iid, data, token=token, ctype=ctype)
        check('上传 server.jar', st == 200, 'HTTP %s %s' % (st, str(d)[:120]))
        st, d, _ = req('POST', '/api/instances/%d/jar' % iid,
                       {'jar_path': os.path.join(inst_dir, 'server.jar')}, token=token)
        check('指定启动 jar', st == 200, 'HTTP %s' % st)

        st, d, _ = req('GET', '/api/instances/%d' % iid, token=token)
        check('创建后实例可读', st == 200, 'HTTP %s %s' % (st, str(d)[:120]))

        st, d, _ = req('POST', '/api/instances/%d/start' % iid, {}, token=token)
        check('启动实例', st == 200 and d.get('pid'), 'HTTP %s %s' % (st, str(d)[:160]))
        pid = (d or {}).get('pid')
        time.sleep(6)
        alive = pid_alive(pid) if pid else False
        check('启动后 PID 存活', alive, 'PID=%s' % pid)

        st, d, _ = req('GET', '/api/instances/%d/log?lines=200' % iid, token=token)
        lines = [l['line'] for l in (d.get('lines') or [])]
        joined = '\n'.join(lines)
        check('控制台有服务端输出', 'Starting minecraft server version' in joined,
              '共 %d 行' % len(lines))
        check('Doom 完成标记被识别', any('Done (' in l for l in lines),
              next((l for l in lines if 'Done (' in l), ''))
        check('TPS 从日志解析到', (d.get('status') or {}).get('tps') is not None,
              'tps=%s' % (d.get('status') or {}).get('tps'))
        logfile = os.path.join(inst_dir, 'logs', 'latest.log')
        check('日志已落盘 logs/latest.log', os.path.isfile(logfile) and os.path.getsize(logfile) > 200,
              '%s bytes' % (os.path.getsize(logfile) if os.path.isfile(logfile) else 0))

        # 下发一条命令，确认真的进 stdin 并被服务端回显
        st, d, _ = req('POST', '/api/instances/%d/command' % iid, {'command': 'say selftest-hello'}, token=token)
        check('下发控制台命令', st == 200, 'HTTP %s' % st)
        time.sleep(2)
        st, d, _ = req('GET', '/api/instances/%d/log?lines=100' % iid, token=token)
        joined = '\n'.join(l['line'] for l in (d.get('lines') or []))
        check('服务端执行了命令并回显', 'selftest-hello' in joined,
              next((l for l in joined.split('\n') if 'selftest-hello' in l), ''))

        log('=== 5. 停止 → 进程必须真的没了 ===')
        st, d, _ = req('POST', '/api/instances/%d/stop' % iid, {}, token=token)
        check('优雅停止请求成功', st == 200, 'HTTP %s %s' % (st, str(d)[:160]))
        check('走的是 stdin stop 优雅路径', (d or {}).get('graceful') is True, str(d)[:120])
        time.sleep(2)
        check('停止后 PID 已不存在', not pid_alive(pid), 'PID=%s' % pid)
        st, d, _ = req('GET', '/api/instances/%d/status' % iid, token=token)
        check('状态回到 stopped', (d or {}).get('status') == 'stopped', str(d)[:160])

        # 备份 + 删除实例
        st, d, _ = req('POST', '/api/instances/%d/backups' % iid, {'note': 'selftest'}, token=token)
        check('创建备份 tar.gz', st == 200 and d.get('size', 0) > 0, str(d)[:140])
        st, d, _ = req('GET', '/api/instances/%d/backups' % iid, token=token)
        check('备份列表可读', st == 200 and len(d.get('backups') or []) >= 1, '')

        st, d, _ = req('GET', '/api/audit?limit=5', token=token)
        check('审计日志有记录', st == 200 and len(d.get('logs') or []) >= 1,
              'total=%s' % d.get('total'))

        log('=== 6. 前端静态资源 ===')
        st, html, hdrs = req('GET', '/', raw=True)
        text = html.decode('utf-8', 'replace')
        check('GET / 返回 HTML 200', st == 200, 'HTTP %s' % st)
        check('HTML 含关键元素 (#app / 面板标题)', ('id="app"' in text) and ('芮拓MC面板' in text), '')
        check('CSP 响应头存在', bool(hdrs.get('content-security-policy')), '')
        for asset in ['/css/tokens.css', '/css/base.css', '/css/components.css', '/css/bg.css', '/js/main.js', '/js/icons.js',
                      '/js/charts.js', '/js/pages/instance_detail.js', '/js/pages/settings.js']:
            st, body, h = req('GET', asset, raw=True)
            check('静态资源 200 %s' % asset, st == 200 and len(body) > 100, 'HTTP %s %d bytes' % (st, len(body)))
        st, body, _ = req('GET', '/api/instances/1/files/download?path=../../windows/win.ini', token=token)
        check('下载接口穿越被拒', st == 403, 'HTTP %s' % st)

    finally:
        if iid:
            try:
                req('POST', '/api/instances/%d/kill' % iid, {}, token=token)
            except Exception:
                pass
            try:
                req('DELETE', '/api/instances/%d?purge=true' % iid, token=token)
                log('（已清理测试实例 #%s）' % iid)
            except Exception:
                pass

    log('')
    log('================ 自检结果 ================')
    log('通过 %d 项，失败 %d 项' % (len(OKS), len(FAILS)))
    for f in FAILS:
        log('  FAILED: ' + f)
    return 0 if not FAILS else 2


if __name__ == '__main__':
    os.makedirs(os.path.join(HERE, 'tmp'), exist_ok=True)
    sys.exit(main())
