# -*- coding: utf-8 -*-
"""设计规范检查器（design lint）
================================================================
把四条设计规则变成可执行检查，改样式后跑一次就知道有没有破规：

  1. 中性色、不用渐变、不发光
  2. 统一设计变量（颜色令牌只在 css/system.css 里定义）
  3. 一套线性图标（不允许实心图标 / 第二种图标语言）
  4. 同一列元素左边缘对齐

用法：
  python tools/design_lint.py            # 检查 frontend/dist
  python tools/design_lint.py --root X   # 指定根目录

退出码 0 = 通过，1 = 有违规（逐条列出文件:行号）。
"""
import argparse
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

#: 颜色令牌只允许在这个文件里定义
TOKEN_FILE = 'system.css'

#: 规范要求 system.css 必须声明的令牌（少一个就算「变量没统一」）
REQUIRED_TOKENS = [
    '--bg-base', '--bg-sunken', '--surface-1', '--surface-2', '--surface-3',
    '--surface-4', '--bg-deep', '--line-1', '--line-2', '--line-3',
    '--text-primary', '--text-secondary', '--text-muted', '--text-faint',
    '--accent', '--accent-bright', '--accent-ink', '--accent-grad',
    '--success', '--warning', '--danger', '--info',
]

#: 规范要求 system.css 必须写到的左对齐规则（选择器片段）
REQUIRED_ALIGN = [
    ('table th', 'text-align: left'),
    ('table td', 'text-align: left'),
    ('.metric', 'text-align: left'),
    ('.stat', 'text-align: left'),
    ('.field', 'text-align: left'),
]

CSS_BAN = [
    (r'\b(?:linear|radial|conic)-gradient\s*\(', '禁止渐变'),
    (r"backdrop-filter\s*:\s*(?!\s*none\b)[^;}]*", "禁止毛玻璃"),
    (r'filter\s*:\s*blur\s*\(', '禁止模糊'),
    (r'filter\s*:\s*drop-shadow\s*\(', '禁止投影发光'),
    (r'box-shadow\s*:[^;]*var\(--accent-glow\)', '禁止彩色外发光'),
    (r'background\s*:\s*var\(--accent-grad\)[^;]*;', '禁止渐变令牌当底色'),
]

JS_BAN = [
    (r'fill\s*=\s*["\']currentColor["\']', '禁止实心图标'),
    (r'fill\s*=\s*["\']#', '禁止实心图标'),
]


def strip_comments(text):
    """去掉 CSS/JS 注释，避免说明文字被当成代码。"""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'(?m)^\s*//.*$', '', text)
    return text


def scan(root):
    violations = []
    warnings = []
    css_dir = os.path.join(root, 'css')
    js_dir = os.path.join(root, 'js')

    if not os.path.isdir(css_dir):
        return None, ['找不到 %s' % css_dir]

    # ---------------- CSS ----------------
    css_files = sorted(f for f in os.listdir(css_dir) if f.endswith('.css'))
    for fn in css_files:
        p = os.path.join(css_dir, fn)
        raw = io.open(p, encoding='utf-8', errors='replace').read()
        body = strip_comments(raw)
        lines = body.split('\n')
        for i, ln in enumerate(lines, 1):
            for pat, why in CSS_BAN:
                if re.search(pat, ln, re.I):
                    violations.append('%s:%d  %s → %s' % (fn, i, why, ln.strip()[:80]))
        # 令牌只允许在 TOKEN_FILE 定义
        if fn != TOKEN_FILE:
            for i, ln in enumerate(lines, 1):
                m = re.match(r'\s*(--[a-z0-9-]+)\s*:', ln)
                if m and m.group(1) in REQUIRED_TOKENS:
                    warnings.append('%s:%d  旧调色板仍定义 %s（被 system.css 覆盖）'
                                    % (fn, i, m.group(1)))

    # ---------------- system.css 必查项 ----------------
    sp = os.path.join(css_dir, TOKEN_FILE)
    if not os.path.isfile(sp):
        violations.append('缺少 %s（唯一设计系统文件）' % TOKEN_FILE)
    else:
        s = strip_comments(io.open(sp, encoding='utf-8', errors='replace').read())
        for tok in REQUIRED_TOKENS:
            if not re.search(re.escape(tok) + r'\s*:', s):
                violations.append('%s 缺少令牌 %s' % (TOKEN_FILE, tok))
        for sel, decl in REQUIRED_ALIGN:
            # 选择器与声明需同时出现在文件里（宽松匹配，容忍分组选择器）
            if sel not in s:
                violations.append('%s 缺少左对齐规则：%s' % (TOKEN_FILE, sel))
        if 'text-align: left' not in s:
            violations.append('%s 没有任何 text-align: left' % TOKEN_FILE)

    # ---------------- JS 图标 ----------------
    icons = os.path.join(js_dir, 'icons.js')
    if os.path.isfile(icons):
        raw = io.open(icons, encoding='utf-8', errors='replace').read()
        body = strip_comments(raw)
        for i, ln in enumerate(body.split('\n'), 1):
            for pat, why in JS_BAN:
                if re.search(pat, ln, re.I):
                    violations.append('js/icons.js:%d  %s → %s' % (i, why, ln.strip()[:80]))
        # 只允许一个渲染函数体：svg() 存在且 filled 是转发
        if 'function svg(' not in body:
            violations.append('js/icons.js 缺少 svg() 线性渲染函数')
        if re.search(r'function filled\([^)]*\)\s*\{(?!\s*return svg\()', body):
            warnings.append('js/icons.js 的 filled() 不是转发到 svg()，可能引入第二种图标语言')

    return violations, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'dist'))
    a = ap.parse_args()

    print('设计规范检查：%s' % a.root)
    violations, warnings = scan(a.root)
    if violations is None:
        print('  ✗ ' + '; '.join(warnings))
        return 1

    for w in warnings:
        print('  · 提示：' + w)

    if violations:
        print('\n  ✗ %d 处违规：' % len(violations))
        for v in violations:
            print('    - ' + v)
        print('\n  规则：中性色 / 无渐变无发光 / 变量只在 system.css / 一套线性图标 / 左对齐')
        return 1

    print('  ✓ 通过：无渐变、无发光模糊、图标单一线性、令牌集中、左对齐规则齐全')
    return 0


if __name__ == '__main__':
    sys.exit(main())
