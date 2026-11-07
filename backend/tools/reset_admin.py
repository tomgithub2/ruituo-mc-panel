# -*- coding: utf-8 -*-
"""重置账号口令（本地通道）—— 面板自带的正式入口。

为什么需要它（产品可用性缺口）：
  · 首次启动时初始口令**只在终端打印一次**，用户忘了就没法登录；
  · 网页侧的"修改口令"要求先登录，所以忘记口令时无路可走；
  · 原来唯一的补救办法是一份**开发机上的** `backend/tools/reset_password.py`，
    但它被 .gitignore 排除、**不在安装包里**，而产品文案却让用户去跑它。

这个脚本随包发布，用法（在 backend 目录，或安装后的 /opt/mc-panel/backend）：

    python tools/reset_admin.py                  # 列出账号，不做修改
    python tools/reset_admin.py admin            # 给 admin 生成随机口令并打印一次
    python tools/reset_admin.py admin --password '新口令'

行为与网页侧重置**完全一致**：同一个 `hash_password`（PBKDF2-SHA256 600k）、
同一个口令策略校验、改完 bump `token_epoch` 并清空该用户全部会话。
明文口令只打印到当前终端，不写日志、不写审计、不落盘。
"""
import argparse
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BACKEND, os.path.join(BACKEND, '.deps')]
os.chdir(BACKEND)

from app import auth as a, database as d            # noqa: E402
from app.permissions import ROLE_NAMES              # noqa: E402


def list_users() -> list:
    return d.query('SELECT id, username, role, status, last_login FROM users ORDER BY id') or []


def main() -> int:
    ap = argparse.ArgumentParser(description='重置面板账号口令（本地通道）')
    ap.add_argument('username', nargs='?', default='', help='目标用户名（省略则仅列账号）')
    ap.add_argument('--password', default='', help='指定新口令（省略则随机生成 20 位）')
    ap.add_argument('--list-only', action='store_true', help='只列账号，不做任何修改')
    args = ap.parse_args()

    d.init_db()

    rows = list_users()
    if not rows:
        print('库里还没有任何账号。启动一次面板即可自动创建默认超级管理员（admin）。')
        return 0

    print('现有账号：')
    for r in rows:
        print('  #%-3s %-20s %-12s %s%s' % (
            r['id'], r['username'], ROLE_NAMES.get(r['role'], r['role']),
            '启用' if r['status'] else '已停用',
            '' if r['last_login'] else '  （从未登录）'))

    if args.list_only or not args.username:
        if not args.list_only:
            print('\n（未指定账号，未做任何修改。示例：python tools/reset_admin.py admin）')
        return 0

    target = next((r for r in rows if r['username'] == args.username), None)
    if not target:
        print('没有名为 %s 的账号' % args.username, file=sys.stderr)
        return 2

    pwd = args.password or a.random_password(20)
    err = a.password_policy_error(pwd)
    if err:
        print('口令不符合策略：%s' % err, file=sys.stderr)
        return 3

    # 与网页侧重置一致：写新哈希 + bump token_epoch + 清空会话
    d.execute('UPDATE users SET password_hash=?, token_epoch=COALESCE(token_epoch,0)+1 WHERE id=?',
              (a.hash_password(pwd), target['id']))
    d.execute('DELETE FROM sessions WHERE uid=?', (target['id'],))
    # 若账号是被停用状态，顺带启用（否则重置了也登不进）
    if not target['status']:
        d.execute('UPDATE users SET status=1 WHERE id=?', (target['id'],))

    print()
    print('  芮拓MC面板 · 口令已重置（本地通道）')
    print('    账号: %s' % target['username'])
    print('    新口令: %s' % pwd)
    print('  * 该口令只在此处显示一次，不会写入日志或审计。')
    print('  * 该账号所有旧会话已立即失效（token_epoch +1）。')
    if not target['status']:
        print('  * 已顺带把账号置为「启用」（之前是停用状态）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
