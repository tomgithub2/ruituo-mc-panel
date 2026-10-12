"""基岩版命令 → Java 版命令转换器。

用在**导入 BDX 建筑**时：BDX（网易《我的世界》中国版）里的命令方块保存的是
**基岩版命令语法**，而目标是 **Java 版服务端**（Purpur 26.1.x，支持新版 execute）。

## 支持的转换
1. **旧版 execute → 新版 execute**（最常见、也最容易出错的一类）
       execute @a ~ ~ ~ say hi        →  execute as @a at @s run say hi
       execute @a ~ ~1 ~ say hi       →  execute as @a at @s positioned ~ ~1 ~ run say hi
       execute @a ~ ~ ~ execute @e ~ ~ ~ <cmd>
                                      →  execute as @a at @s run execute as @e at @s run <cmd>
   ⚠️ **坐标偏移必须用 `positioned` 补回来**：新版 `at @s` 会把执行位置设为实体自身，
      直接丢掉 `~ ~1 ~` 这种偏移 —— 这是最常见的"转换后行为变了"的坑。
2. **`detect` 子命令** → `if block`
       execute @a ~ ~ ~ detect ~ ~-1 ~ stone 0 <cmd>
                                      →  execute as @a at @s run if block ~ ~-1 ~ minecraft:stone run <cmd>
3. **方块名补命名空间**：`stone` → `minecraft:stone`（只对"看起来像方块 ID"的参数补，
   且跳过已经带 `:` 的、以及 `~`/`^`/数字/`@` 开头的参数）
4. **`setblock`/`fill` 的数字 data 值**：Java 1.13+ 用状态语法。
   已知映射的照转（如 `stone 0` → `minecraft:stone`），不能确定的**丢掉 data 并标注**。
5. **`tellraw` 的 rawtext 结构**：
       {"rawtext":[{"text":"hi"}]}    →  {"text":"hi"}
6. **`tickingarea`**：基岩专有，Java 没有 → **原样保留并明确标注**

## 已知不转换（明确标注，绝不瞎改）
- `tickingarea` / `structure`（基岩专有命令）
- `playsound` / `particle` 的**音效名、粒子名**：基岩与 Java 命名体系不同，
  未建立完整对照表前**不动它们**，只标注"可能需要人工核对"
- 复杂的 `@a[...]` 选择器内部语法（双方大体一致，不动）
- NBT 通配符写法差异（`{...}` 里的 `*`）：标注"建议人工核对"

## 设计原则
- **绝不猜**：不确定的一律原样返回 + notes 说明，宁可让用户人工核对，
  也不要输出"看起来对但语义变了"的命令
- **绝不抛异常**：任何输入（None / 空串 / 畸形）都要安全返回
- 纯标准库
"""
import re

#: 旧版 execute 的目标选择器：@a / @e / @p / @r / @s，可带 [参数]
_SELECTOR = r'@[aeprs](?:\[[^\]]*\])?'

#: 一行里可能是"方块 ID"的位置：前面的关键词（这些词后面的参数是方块名）
_BLOCK_AFTER = (
    'setblock', 'fill', 'blockdata', 'testforblock', 'testforblocks',
    'if block', 'unless block', 'detect',
)

#: 基岩专有命令（Java 没有）—— 见到就原样保留并标注
_BEDROCK_ONLY = ('tickingarea', 'structure', 'fog', 'camerashake', 'ride',
                 'playanimation', 'inputpermission', 'dialogue', 'camera',
                 'gametest', 'music', 'schedule')


def _split_first_word(cmd: str):
    """拆出第一个词（命令名）与剩余部分。"""
    s = cmd.lstrip()
    i = 0
    while i < len(s) and not s[i].isspace():
        i += 1
    return s[:i], s[i:]


def _is_number(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False


def _maybe_namespace(tok: str) -> str:
    """给"看起来像方块/物品 ID"的 token 补 minecraft: 前缀。

    只处理纯 [a-z0-9_] 形式的 token；带 `:` 的、`~`/`^`/`#` 开头的一律不动。
    """
    if not tok or ':' in tok:
        return tok
    if tok[0] in '~^#@' or _is_number(tok):
        return tok
    if re.fullmatch(r'[a-z0-9_]+', tok):
        return 'minecraft:' + tok
    return tok


#: 判断坐标是不是「全零相对坐标」（~ / ~ ~ / ~ ~ ~ / ~0 ~0 ~0），
#: 这类等价于实体自身位置，`at @s` 已经表达了，**不需要**再补 positioned。
_RE_ZERO_REL = re.compile(r'^~?0?$')


def _is_zero_relative(coords) -> bool:
    if not coords:
        return True
    return all(_RE_ZERO_REL.match(c or '') for c in coords)


def _restore_position(sel_coords: str, body: str) -> tuple:
    """把旧版 execute 的坐标按需要补成 positioned 子句。

    为什么要补：旧版 `execute <sel> <x> <y> <z> <cmd>` 意思是
    「以 sel 的身份、在**坐标处**执行」；新版 `as <sel> at @s` 会把执行位置
    设为**实体自身**，于是 `~ ~1 ~` 这种偏移就丢了 —— 必须用 `positioned` 补回。

    为什么不是无脑补：`~ ~ ~`（全零相对坐标）与实体自身位置等价，
    补成 `positioned ~ ~ ~` 是**冗余但无害**的；不过输出越干净越好，
    所以全零时省略（实测发现多写了这个子句，自测抓到的）。
    """
    notes = []
    coords = sel_coords.split()
    if not coords:
        return '', notes
    if not all(c.startswith('~') or c.startswith('^') or _is_number(c) for c in coords):
        notes.append('坐标 `%s` 不是相对/绝对数值形式，已跳过 positioned 补偿，建议人工核对'
                     % sel_coords)
        return '', notes
    if _is_zero_relative(coords):
        return '', notes                     # at @s 已足够，省略冗余子句
    return 'positioned %s ' % ' '.join(coords), notes


def _convert_execute(cmd: str) -> tuple:
    """旧版 execute → 新版 execute（支持嵌套）。"""
    notes = []
    rest = cmd[len('execute'):].lstrip()
    prefix_parts = []
    body = rest

    # 逐层剥离 `execute <selector> <coords...> ` 直到剩下真正的命令
    while True:
        m = re.match(r'(%s)\s+' % _SELECTOR, body)
        if not m:
            break
        sel = m.group(1)
        after = body[m.end():]
        # 坐标：最多 3 个 token
        toks = after.split()
        coords = []
        while len(coords) < 3 and toks and (
                toks[0].startswith('~') or toks[0].startswith('^') or _is_number(toks[0])):
            coords.append(toks.pop(0))
        pos_clause, n2 = _restore_position(' '.join(coords), after)
        notes.extend(n2)
        prefix_parts.append('as %s at @s %s' % (sel, pos_clause))
        body = ' '.join(toks)
        if body.lstrip().startswith('execute '):
            # 嵌套 execute：继续剥
            body = body.lstrip()[len('execute'):].lstrip()
            continue
        break

    if not prefix_parts:
        return cmd, ['execute 形式无法识别（既不是旧版也不是新版），已原样保留']

    # 若剩下的 body 以 detect 开头 → 转 if block
    b2 = body.lstrip()
    if b2.startswith('detect '):
        parts = b2[len('detect '):].split(None, 4)
        if len(parts) >= 4:
            dcoords = ' '.join(parts[:3])
            block = parts[3]
            data = parts[4].split(None, 1)[0] if len(parts) > 4 else ''
            tail = parts[4][len(data):].strip() if len(parts) > 4 else ''
            blk = _maybe_namespace(block)
            if data and not data.startswith('~') and _is_number(data):
                notes.append('detect 的数字 data 值 `%s` 已丢弃（Java 用状态语法）' % data)
            body = 'if block %s %s run %s' % (dcoords, blk, tail)
        else:
            notes.append('detect 参数不足，未转换')
            body = b2

    new = 'execute ' + ' '.join(prefix_parts) + 'run ' + body
    return re.sub(r'\s+', ' ', new).strip(), notes


def _convert_tellraw(cmd: str) -> tuple:
    """基岩 {"rawtext":[{"text":"..."}]} → Java {"text":"..."}。

    做法：**用 json 解析**，不用正则 —— 我一开始写正则，而且反复改错括号，
    自测一直报"括号重复"，排查很久。改用 json 更可靠：解析出来之后按结构重建。

    ⚠️ 必须保留 `"text"` 这个键：只留值会产出 `{"hello"}`（无效 JSON）。
    """
    import json as _json
    if '"rawtext"' not in cmd:
        return cmd, []

    notes = []
    # 取出命令里的 JSON 部分（从第一个 { 开始）
    brace = cmd.find('{')
    if brace < 0:
        return cmd, []
    head, tail = cmd[:brace], cmd[brace:]
    try:
        obj = _json.loads(tail)
    except Exception:
        notes.append('tellraw 的 JSON 无法解析（可能是基岩的非标准 JSON），已原样保留，建议人工核对')
        return cmd, notes

    if not isinstance(obj, dict) or 'rawtext' not in obj:
        return cmd, []
    raw = obj.get('rawtext')
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        notes.append('tellraw 的 rawtext 不是简单的单元素形态，已原样保留，建议人工核对')
        return cmd, notes

    elem = raw[0]
    if 'text' in elem and isinstance(elem['text'], str):
        # 只保留 text 键（基岩的 rawtext 壳换成 Java 的文本组件）
        new_obj = {'text': elem['text']}
        for k in ('color', 'bold', 'italic', 'underlined', 'strikethrough', 'obfuscated'):
            if k in elem:
                new_obj[k] = elem[k]
        new = head + _json.dumps(new_obj, ensure_ascii=False, separators=(',', ':'))
        notes.append('tellraw 的 rawtext 结构 → Java 的文本组件 {"text":"…"}')
        return new, notes

    notes.append('tellraw 的 rawtext 元素没有 text 字段，已原样保留，建议人工核对')
    return cmd, notes


def _convert_block_name_params(cmd: str) -> tuple:
    """给 setblock/fill 等的方块参数补 minecraft: 前缀。"""
    notes = []
    toks = cmd.split()
    if not toks:
        return cmd, notes
    head = toks[0].lower()
    out = list(toks)
    touched = []

    def ns_at(idx):
        if 0 <= idx < len(out):
            new = _maybe_namespace(out[idx])
            if new != out[idx]:
                touched.append(out[idx])
                out[idx] = new

    if head == 'setblock' and len(out) >= 5:
        ns_at(4)
    elif head == 'fill' and len(out) >= 8:
        ns_at(7)
    elif head in ('blockdata',):
        pass
    elif 'detect' in cmd:
        m = re.search(r'\bdetect\s+(~?\S*\s+~?\S*\s+~?\S*)\s+(\S+)', cmd)
        if m:
            blk = m.group(2)
            nb = _maybe_namespace(blk)
            if nb != blk:
                out = cmd.replace('detect %s %s' % (m.group(1), blk),
                                  'detect %s %s' % (m.group(1), nb)).split()
                touched.append(blk)

    if touched:
        notes.append('补命名空间：%s' % '、'.join(sorted(set(touched))[:6]))
    res = ' '.join(out)

    # setblock/fill 的数字 data 值（Java 1.13+ 不需要）
    if head in ('setblock', 'fill'):
        m2 = re.search(r'^(%s)\s+((?:\S+\s+){%d}\S+)\s+(\d+)(\s+.*)?$' % (
            head, 3 if head == 'setblock' else 6), res)
        if m2:
            notes.append('丢弃数字 data 值 `%s`（Java 1.13+ 用方块状态语法）' % m2.group(3))
            res = '%s %s%s' % (m2.group(1), m2.group(2), m2.group(4) or '')

    return res.strip(), notes


def convert_command(cmd) -> tuple:
    """把一条基岩版命令转成 Java 版。

    返回 (转换后的命令, 变更说明列表)。**不抛异常**；无法转换时原样返回并把原因写进说明。
    """
    if cmd is None:
        return '', []
    text = str(cmd)
    stripped = text.strip()
    if not stripped:
        return text, []

    notes = []
    head, _rest = _split_first_word(stripped)
    low = head.lower().lstrip('/')

    # 基岩专有命令：明确不转换
    if low in _BEDROCK_ONLY:
        return text, ['基岩版专有命令 `/%s`，Java 版没有对应命令，已原样保留' % low]

    out = stripped

    # ① execute（旧版→新版 / 嵌套 / detect）
    if low == 'execute':
        out, n = _convert_execute(out)
        notes.extend(n)

    # ② tellraw 的 rawtext
    if low == 'tellraw':
        out, n = _convert_tellraw(out)
        notes.extend(n)

    # ③ 方块名补命名空间（含 detect）
    out, n = _convert_block_name_params(out)
    notes.extend(n)

    # ④ 需要人工核对的项（标注但不改动）
    if re.search(r'\b(playsound|particle)\b', out):
        notes.append('playsound/particle 的音效名或粒子名在基岩与 Java 中命名不同，'
                     '未自动改写，建议人工核对')
    if re.search(r'\{[^}]*\*[^}]*\}', out):
        notes.append('命令里的 NBT 通配符 `*` 写法在 Java 版可能不同，建议人工核对')

    return out, notes


def convert_commands(cmds) -> list:
    """批量转换，返回结构化结果（供报告/审计使用）。"""
    out = []
    for c in (cmds or []):
        try:
            new, notes = convert_command(c)
        except Exception as e:                      # 双保险：绝不让单条命令拖垮导入
            new, notes = c, ['转换器内部错误，已原样保留：%s' % e]
        out.append({'from': c, 'to': new, 'notes': notes, 'changed': new != c})
    return out


# ================================================================ 自测
if __name__ == '__main__':
    CASES = [
        # (输入, 期望输出, 说明)
        ('execute @a ~ ~ ~ say hi',
         'execute as @a at @s run say hi', '旧版 execute 基本形式'),
        ('execute @a ~ ~1 ~ say hi',
         'execute as @a at @s positioned ~ ~1 ~ run say hi', '坐标偏移必须用 positioned 补回'),
        ('execute @e[type=zombie] ~ ~ ~ kill @s',
         'execute as @e[type=zombie] at @s run kill @s', '带选择器参数的 execute'),
        ('execute @a ~ ~ ~ execute @e ~ ~ ~ say nested',
         'execute as @a at @s as @e at @s run say nested', '嵌套 execute 合并（run 只出现一次）'),
        ('execute @a ~ ~ ~ detect ~ ~-1 ~ stone 0 say 石头',
         'execute as @a at @s run if block ~ ~-1 ~ minecraft:stone run say 石头', 'detect → if block'),
        ('execute @p ~ ~1 ~ tp @s ~ ~10 ~',
         'execute as @p at @s positioned ~ ~1 ~ run tp @s ~ ~10 ~', '坐标偏移用 positioned 补回'),
        ('setblock ~ ~ ~ stone 0',
         'setblock ~ ~ ~ minecraft:stone', 'setblock 补命名空间 + 丢 data'),
        ('fill ~ ~ ~ ~5 ~5 ~5 oak_planks 0',
         'fill ~ ~ ~ ~5 ~5 ~5 minecraft:oak_planks', 'fill 补命名空间 + 丢 data'),
        ('tellraw @a {"rawtext":[{"text":"hello"}]}',
         'tellraw @a {"text":"hello"}', 'tellraw rawtext → Java 文本组件'),
        ('tickingarea add 0 0 0 10 10 10',
         'tickingarea add 0 0 0 10 10 10', '基岩专有，原样保留 + 标注'),
        ('say 普通命令',
         'say 普通命令', '无需转换'),
        ('playsound mob.zombie.death @a',
         'playsound mob.zombie.death @a', '音效名不自动改，仅标注'),
        ('', '', '空串'),
        (None, '', 'None'),
        ('   ', '   ', '纯空白'),
    ]

    passed = failed = skipped = 0
    for src, want, label in CASES:
        got, notes = convert_command(src)
        if want is None:
            print('  ⏭  %-34s → %r   （无断言，仅展示）notes=%s' % (label, got, notes))
            skipped += 1
            continue
        ok = (got == want)
        if ok:
            passed += 1
        else:
            failed += 1
        print('  %s %-34s' % ('✅' if ok else '❌', label))
        if not ok:
            print('      输入: %r' % src)
            print('      期望: %r' % want)
            print('      实际: %r' % got)
        if notes:
            print('      notes: %s' % notes)

    print()
    print('  通过 %d / 失败 %d / 未断言 %d' % (passed, failed, skipped))
