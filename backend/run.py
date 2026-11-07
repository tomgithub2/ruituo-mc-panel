"""芮拓MC面板 启动入口（完全独立，不依赖同仓任何其它面板）。

开发环境依赖装在 backend/.deps（sys.path 注入）；
目标机器部署时依赖装在系统/虚拟环境中，requirements.txt 由安装脚本处理。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))     # backend/
sys.path.insert(0, BASE_DIR)
DEPS_DIR = os.path.join(BASE_DIR, '.deps')
if os.path.isdir(DEPS_DIR):
    sys.path.insert(0, DEPS_DIR)

# 本机默认编码可能是 GBK，强制 UTF-8 输出，避免中文日志乱码
os.environ.setdefault('PYTHONUTF8', '1')
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import uvicorn  # noqa: E402


def main():
    from app.config import get_config
    cfg = get_config()
    host = cfg.get('bind_host', '127.0.0.1')
    port = int(cfg.get('port', 8100))
    print('[*] 芮拓MC面板 启动: http://%s:%s/' % (host if host != '0.0.0.0' else '127.0.0.1', port))
    uvicorn.run('app.main:app', host=host, port=port, log_level='info', ws='websockets')


if __name__ == '__main__':
    main()
