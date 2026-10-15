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
#: ⚠️ `replaceitem` 也在这里：它在 **Java 1.17 就被移除**（替代品 `item replace`），
#:    而基岩的 `slot.hotbar`/`slot.weapon.mainhand` 等槽位名与 Java 的
#:    `hotbar.0`/`weapon.mainhand` **不是简单对应**（还要处理 data 值），
#:    自动转换极易转错 —— 所以**只标注、不转**（实测用户 32 条全部用了 `slot.` 与 data 值）。
#: ⚠️ `titleraw` 同理：Java 没有它，对应的是 `title ... <JSON 文本组件>`。
_BEDROCK_ONLY = ('tickingarea', 'structure', 'fog', 'camerashake', 'ride',
                 'playanimation', 'inputpermission', 'dialogue', 'camera',
                 'gametest', 'music', 'schedule', 'titleraw', 'replaceitem',
                 'testforblock', 'testforblocks', 'testforbiome',
                 'setmaxplayers', 'clearspawnpoint', 'immutableworld',
                 'toggledownfall', 'gettopsolidblock', 'querytarget',
                 'showborder', 'transfer', 'reloadconfig', 'mobevent',
                 'worldbuilder', 'scoreboardplayersreset')


def _effect_to_java(cmd: str) -> tuple:
    """基岩 `effect` → Java `effect give` / `effect clear`（语义与参数位置都不同）。

    权威语法（minecraft.wiki Commands/effect）：
      Java:    effect give <targets> <effect> [seconds] [amplifier] [hideParticles]
               effect clear [<targets>] [<effect>]
      Bedrock: effect <player: target> <effect> [seconds] [amplifier] [hideParticles]
               effect <player: target> clear [<effect>]
               effect @a haste 0     ← 基岩"清除"的省略写法（seconds=0）
    """
    notes = []
    toks = cmd.split()
    if len(toks) < 3:
        return cmd, ['effect 参数不足，未转换']
    head, target = toks[0], toks[1]
    if toks[2].lower() == 'clear':
        # 基岩 `effect <目标> clear [效果]` → Java `effect clear <目标> [效果]`
        rest = toks[3:]
        if rest:
            blk = _maybe_namespace(rest[0])
            notes.append('effect clear：参数顺序按 Java 规则重排（目标在前）')
            return 'effect clear %s %s' % (target, blk), notes
        notes.append('effect clear：参数顺序按 Java 规则重排（目标在前）')
        return 'effect clear %s' % target, notes
    effect_raw = toks[2]
    effect = _maybe_namespace(effect_raw)
    if effect != effect_raw:
        notes.append('effect 名补命名空间：%s → %s' % (effect_raw, effect))
    rest = toks[3:]
    # 基岩用 `<效果> <秒数> 0` 表示清除（seconds=0 且没有别的参数）
    if len(rest) == 1 and rest[0] == '0':
        notes.append('基岩 `effect <目标> <效果> 0` 的语义是**清除该效果**，'
                     '已转为 Java 的 `effect clear`')
        return 'effect clear %s %s' % (target, effect), notes
    out = 'effect give %s %s' % (target, effect)
    if rest:
        out += ' ' + ' '.join(rest)
    notes.append('effect：Java 需要 `give` 关键字（基岩不需要），已补上')
    return out, notes


def _testfor_to_java(cmd: str) -> tuple:
    """基岩 `testfor <选择器>` → Java `execute if entity <选择器>`。

    权威依据（minecraft.wiki Commands/testfor）：`testfor` 在 **Java 13w45a/1.13 被并入 `execute`**。

    ⚠️ 只对**选择器参数 Java 也支持**的情况转换；
    基岩专有的选择器参数（`hasitem` / `quantity` / `location` / `slot.` / `data=`）
    Java **不认识**，此时**不转**并明确告知 —— 转了会得到一条必然报错的命令。
    """
    toks = cmd.split(None, 1)
    if len(toks) < 2 or not toks[1].strip():
        return cmd, ['testfor 参数不足，未转换']
    sel = toks[1].strip()
    low = sel.lower()
    # ⚠️ 只列**Java 选择器确实不支持**的参数：
    #    `hasitem` / `quantity=` / `location=` / `data=` / `slot.` 是基岩选择器专有。
    #    ⚠️ **`m=`（游戏模式）Java 也支持，不要列进来** —— 列进去会误伤本来能转的命令。
    bedrock_only = [k for k in ('hasitem', 'quantity=', 'location=', 'data=', 'slot.')
                    if k in low]
    if bedrock_only:
        return cmd, ['testfor → Java 的 `execute if entity`，但选择器里用了 Java 不支持的'
                     '基岩专有参数（%s），**未自动转换**，需人工改写'
                     % '、'.join(sorted(set(bedrock_only)))]
    return 'execute if entity %s' % sel, [
        'testfor 在 Java 1.13 已并入 execute：转为 `execute if entity <选择器>`']


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

    # ⚠️ **已经是 Java 式 execute 的命令不能再来一遍** —— 否则会套出非法的双 run：
    #      /execute @a[scores={…}] at @s run say hi
    #        → /execute as @a[scores={…}] at @s run at @s run say hi   ❌ 语法错误
    #    判据：Java 式 execute 用 `run` 分隔子句链，基岩式**没有** `run`。
    #    只要出现独立的 ` run ` 就认为已是 Java 式（或无法安全判定）→ 原样返回。
    if re.search(r'(^|\s)run(\s|$)', rest):
        return cmd, ['已是 Java 式 execute（含 run 子句），无需转换为新版语法']
    # `as`/`at`/`positioned`/`if`/`unless` 等是 Java 的子句关键字；
    # 紧跟 execute 出现即为 Java 式（基岩式紧跟的是 @选择器）。
    if re.match(r'(as|at|positioned|align|anchored|in|facing|on|if|unless)\s', rest):
        return cmd, ['已是 Java 式 execute（子句关键字开头），无需转换']

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
    # ⚠️ 收尾压空白必须保护 JSON 参数（tellraw/titleraw 可能嵌在 run 后面）——
    #    无脑 `re.sub(r'\s+', ' ', …)` 会把文本组件里的空格与换行缩进一起压掉。
    return _squeeze_ws(new), notes


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


_RE_COORD_ONE = re.compile(r'^[~^][-+]?[0-9.]*$')


def _split_glued_coord(tok: str) -> str:
    """把粘连的坐标 token 拆成三个：`~~~`→`~ ~ ~`、`~~~-35`→`~ ~ ~-35`、`~~1~`→`~ ~1 ~`。

    ⚠️ 基岩允许把三轴坐标**写在一起**（`~~~`、`~~~-35`、`~~1~`），
    而 Java **要求空格分隔**。这个差异会让"按 token 下标取参数"的转换全部失效 ——
    实测（用户真实 BDX）：`fill ~~~-35 ~~~-35 air []` 的命名空间与方块状态都没转。

    ⚠️ 只拆"恰好由 3 个坐标分量首尾相接组成"的 token，别的一律不动：
      · 含 `=` → 选择器参数（`x=~-3`），不碰；
      · 拆出来不是 3 段 → 不是粘连坐标，不碰；
      · `~-3` 这类单轴 → 只有 1 段，不碰。
    """
    s = tok or ''
    if not s or s[0] not in '~^' or '=' in s:
        return tok
    parts, buf = [], ''
    for ch in s:
        if ch in '~^':
            if buf:
                parts.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        parts.append(buf)
    if len(parts) != 3:
        return tok
    if not all(_RE_COORD_ONE.match(x) for x in parts):
        return tok
    return ' '.join(parts)


def _normalize_coords(cmd: str) -> str:
    """把整条命令里所有粘连坐标拆开（保守：只拆"恰好三轴"的 token）。"""
    if not re.search(r'[~^]', cmd):
        return cmd
    toks = cmd.split()
    changed = False
    for i, t in enumerate(toks):
        nt = _split_glued_coord(t)
        if nt != t:
            toks[i] = nt
            changed = True
    return ' '.join(toks) if changed else cmd


def _join_like(orig: str, new_toks: list, old_toks: list) -> str:
    """按**原文的空白**把 token 拼回去，保留原间隔（不无脑用单空格）。

    ⚠️ 踩过的坑：原来直接 `' '.join(toks)` 重建整条命令 —— 对**所有**命令都生效，
    于是把 `tellraw` 里 JSON 文本组件的多空格也压成了一个空格，
    实测把 `{"text":"x   y"}` 变成 `{"text":"x y"}`，**改变了玩家看到的文本**。
    """
    if not old_toks:
        return orig
    parts = []
    pos = 0
    for i in range(len(old_toks)):
        j = orig.find(old_toks[i], pos)
        if j < 0:
            return ' '.join(new_toks)          # 对不上就退回（罕见）
        gap = orig[pos:j]
        if i == 0:
            gap = gap                         # 前导空白原样
        parts.append(gap + (new_toks[i] if i < len(new_toks) else old_toks[i]))
        pos = j + len(old_toks[i])
    parts.append(orig[pos:])                  # 尾部原样
    return ''.join(parts)


def _tokenize_keep_json(cmd: str) -> list:
    """按空白切词，但**把 JSON 参数当成一个 token**（内部的空白原样保留）。

    这样后面即使重建也不会破坏 JSON 文本组件里的空格与换行。
    """
    toks = []
    buf = ''
    in_json = 0
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if in_json:
            buf += ch
            if ch in '{[':
                in_json += 1
            elif ch in '}]':
                in_json -= 1
                if in_json == 0:
                    toks.append(buf)
                    buf = ''
            i += 1
            continue
        if ch.isspace():
            if buf:
                toks.append(buf)
                buf = ''
            i += 1
            continue
        if ch in '{[':
            if buf:
                toks.append(buf)
                buf = ''
            in_json = 1
            buf += ch
            i += 1
            continue
        buf += ch
        i += 1
    if buf:
        toks.append(buf)
    return toks


def _convert_block_name_params(cmd: str) -> tuple:
    """给 setblock/fill 等的方块参数补 minecraft: 前缀。"""
    notes = []
    if not cmd.strip():
        return cmd, notes
    # 必须区分「原 token 列表」与「输出 token 列表」，最后按原文空白拼回 ——
    # 不这么做的话会压掉 JSON 参数里的空格（实测踩到）。
    old = _tokenize_keep_json(cmd)
    if not old:
        return cmd, notes
    head = old[0].lower()
    out = list(old)
    touched = []

    def ns_at(idx):
        if 0 <= idx < len(out):
            new = _maybe_namespace(out[idx])
            if new != out[idx]:
                touched.append(out[idx])
                out[idx] = new

    if head == 'setblock' and len(out) >= 5:
        ns_at(4)
    elif head == 'fill':
        # ⚠️ `fill <起> <止> <方块> [模式]` —— 方块名在**第 7** 个 token（下标 6），
        #    而原来写的是下标 7（那是模式位）→ 补命名空间补到了 `replace` 上，
        #    真方块名反而没补。实测：`fill ~~~-35 ~~~-35 stone 0 replace`
        #    输出 `... minecraft:stone replace` 看似对，是因为有 `0` 顶着；
        #    去掉 data 值 `0` 之后就错位到 `replace` 了。
        #    这里改用「找到第一个不以 ~/^/数字开头的 token」= 方块名所在位置。
        idx = None
        for i in range(1, len(out)):
            t = out[i]
            if t and not (t[0] in '~^' or _is_number(t)):
                idx = i
                break
        if idx is not None:
            ns_at(idx)
    elif head in ('blockdata',):
        pass
    elif 'detect' in cmd:
        m = re.search(r'\bdetect\s+(~?\S*\s+~?\S*\s+~?\S*)\s+(\S+)', cmd)
        if m:
            blk = m.group(2)
            nb = _maybe_namespace(blk)
            if nb != blk:
                # 只替换那一个 token，别重建整条命令（否则会压掉 JSON 里的空格）
                out = list(old)
                for i, t in enumerate(out):
                    if t == blk:
                        out[i] = nb
                        break
                touched.append(blk)

    if touched:
        notes.append('补命名空间：%s' % '、'.join(sorted(set(touched))[:6]))
    # ⚠️ 按**原文的空白**拼回（`_join_like`），不要 `' '.join(out)` ——
    #    后者会把 JSON 参数里的多空格压掉（实测 `{"text":"x   y"}` → `{"text":"x y"}`）。
    res = _join_like(cmd, out, old)

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
    stripped = text.strip()

    # ⚠️ **前导 `/` 必须在入口处剥掉，否则整条链路都认不出命令**
    #    命令方块里存的就是 `/xxx` 这种带斜杠的形式，而下面所有子转换器
    #    （`_convert_execute` 用 `cmd[len('execute'):]`、`_convert_tellraw` 等）
    #    都假设命令**不带**前导斜杠 —— 原来只把首词 lstrip('/') 用于判断，
    #    却把带斜杠的原串传下去，导致 `execute` 永远匹配不上，
    #    返回"execute 形式无法识别（既不是旧版也不是新版）"。
    #    实测后果：用户 BDX 里 **全部带斜杠的命令一条都转不了**（kept 1068、converted 仅 41）。
    slash = stripped.startswith('/')
    if slash:
        stripped = stripped[1:].lstrip()
    if not stripped:
        return text, []

    head, _rest = _split_first_word(stripped)
    low = head.lower().lstrip('/')

    # 基岩专有命令：明确不转换
    if low in _BEDROCK_ONLY:
        return text, ['基岩版专有命令 `/%s`，Java 版没有对应命令，已原样保留' % low]

    # ⚠️ 网易《我的世界》中国版给一些命令加了 `x` 前缀变体（实测见 `xplayanimation`、`xexecute`）。
    #    它们同样是基岩专有（Java 没有），一律原样保留并标注，
    #    免得被当成未知命令而误判"无法识别"。
    if low.startswith('x') and len(low) > 1:
        return text, ['基岩版（网易）专有命令 `/%s`，Java 版没有对应命令，已原样保留' % low]

    # ① 先把**粘连坐标**拆开（`~~~-35` → `~ ~ ~-35`）——
    #    必须放在 execute 判断与所有"按 token 下标取参数"的逻辑**之前**，
    #    否则坐标粘连会让 token 数量变少、后面全部取错位置（实测）。
    out = _normalize_coords(stripped)

    # ① execute（旧版→新版 / 嵌套 / detect）
    if low == 'execute':
        out, n = _convert_execute(out)
        notes.extend(n)

    # ② tellraw 的 rawtext
    if low == 'tellraw':
        out, n = _convert_tellraw(out)
        notes.extend(n)

    # ②b effect：基岩 `effect <目标> <效果> …` → Java `effect give/clear …`
    if low == 'effect':
        out, n = _effect_to_java(out)
        notes.extend(n)

    # ②c testfor → execute if entity（Java 1.13 起已并入 execute）
    if low == 'testfor':
        out, n = _testfor_to_java(out)
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

    # 原来是 `/xxx` 就还原成 `/xxx`（命令方块里习惯带斜杠）
    if slash and not out.startswith('/'):
        out = '/' + out

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


# ================================================================ Java → 基岩（反向）
#
# ⚠️ 为什么反向比正向难：新版 Java execute 把「执行上下文」拆成了独立子命令
#    （as / at / positioned / if / unless … 后面统一接一个 run），而基岩版只有
#    `execute <选择器> <坐标> <命令>` 这一种形式。所以反向必须**把链折叠回选择器+坐标**。
#    只有能无损折叠时才转；折叠不了就原样保留并明确标注（绝不输出语义变了的命令）。

_RE_SELECTOR = re.compile(r'^@[aeprs](?:\[[^\]]*\])?$')


def convert_to_bedrock(cmd) -> tuple:
    """Java 版命令 → 基岩版命令。返回 (转换后, 说明列表)。不抛异常。"""
    if cmd is None:
        return '', []
    text = str(cmd)
    stripped = text.strip()
    if not stripped:
        return text, []

    head, _rest = _split_first_word(stripped)
    low = head.lower().lstrip('/')

    # ⚠️ 多行命令（命令方块里可以放多行，数据包风格）：
    #    任何"拆词再拼回"的转换都会**把换行抹掉、拼成一条非法命令**，
    #    语义完全变了。这类输入一律**原样返回并标注**，绝不改写。
    if '\n' in text or '\r' in text:
        return text, ['命令含多行（%d 行），逐行转换会把换行并成一条非法命令，'
                      '已原样保留，建议人工处理' % len([x for x in text.splitlines() if x.strip()])]

    # ⚠️ 与正向同理：**前导 `/` 必须在入口处剥掉**，否则 `_java_execute_to_bedrock`
    #    （用 `cmd[len('execute'):]`）认不出带斜杠的命令 —— 命令方块里存的正是 `/xxx`。
    slash = stripped.startswith('/')
    if slash:
        stripped = stripped[1:].lstrip()
    if not stripped:
        return text, []
    head, _rest = _split_first_word(stripped)
    low = head.lower().lstrip('/')

    notes = []

    if low == 'execute':
        out, notes = _java_execute_to_bedrock(stripped)
    else:
        out = stripped

    # 文本组件 → rawtext（tellraw / titleraw）
    if low in ('tellraw', 'titleraw'):
        out2, n2 = _component_to_rawtext(out, low)
        if n2:
            out = out2
            notes.extend(n2)

    # 去掉 minecraft: 前缀（基岩更常见裸名；但只对**方块/物品名位置**做，
    # 这里仅处理明显是方块参数的位置，避免误伤）
    out3, n3 = _strip_namespace_blocks(out)
    if n3:
        out = out3
        notes.extend(n3)

    # 原来是 `/xxx` 就还原成 `/xxx`
    if slash and not out.startswith('/'):
        out = '/' + out

    return out, notes


def _java_execute_to_bedrock(cmd: str) -> tuple:
    """折叠 `execute as X at @s positioned P if block B run C` → `execute X P detect B C`。"""
    notes = []
    body = cmd[len('execute'):].strip()
    as_sel = None
    pos = None
    cond = None

    # 逐个子句剥离，直到遇到 run
    while True:
        m = re.match(r'^(as|at|positioned|if|unless|in|facing|anchored|align)\b\s*(.*)$',
                     body, re.S)
        if not m:
            break
        kind, rest = m.group(1), m.group(2).strip()
        if kind == 'run':
            break
        if kind == 'as':
            tok = rest.split()[0] if rest.split() else ''
            if _RE_SELECTOR.match(tok):
                as_sel = tok
                body = rest[len(tok):].strip()
            else:
                notes.append('`as %s` 不是标准选择器，无法折叠成基岩 execute' % tok[:20])
                return cmd, notes
        elif kind == 'at':
            tok = rest.split()[0] if rest.split() else ''
            if tok == '@s':
                body = rest[len(tok):].strip()          # at @s 正好对应基岩的「执行者所在」
            else:
                notes.append('`at %s`（非 @s）在基岩里没有等价写法，已原样保留' % tok[:20])
                return cmd, notes
        elif kind == 'positioned':
            toks = rest.split()
            coords = []
            while len(coords) < 3 and toks:
                coords.append(toks.pop(0))
            pos = ' '.join(coords)
            body = ' '.join(toks).strip()
        elif kind == 'if':
            # ⚠️ 这里连踩三个坑（都实测过，写下来免得再犯）：
            #  ① **组索引错位**：`if block <x> <y> <z> <方块> run <命令>` 里
            #     坐标是 1/2/3 组、**方块是第 4 组**、命令是第 5 组。
            #  ② **`\S+` 贪婪撞 `run`**：token 数固定时不要靠正则回溯，
            #     直接按分隔符切更稳、出错也好查。
            #  ③ **`split(None, 5)` 的段数记错了**：maxsplit=N 得到的是 **N+1 段**，
            #     我按"N 段"去判断，于是死活进不去分支，报"结构不完整"。
            #     现在改成在**最后一个 ` run `** 处切分，语义直白、不依赖段数心算。
            if not rest.startswith('block '):
                notes.append('条件子句 `if %s` 在基岩里没有等价写法（只支持 `if block …`），'
                             '已原样保留' % ' '.join(rest.split()[:2]))
                return cmd, notes
            cond_part, sep, cmd_part = rest.rpartition(' run ')
            head_toks = cond_part.split()
            # `block <x> <y> <z> <方块>` → 5 段
            if not sep or len(head_toks) < 5:
                notes.append('`if %s` 结构不完整（需要 `if block <坐标x3> <方块> run <命令>`），'
                             '已原样保留' % ' '.join(rest.split()[:6]))
                return cmd, notes
            bc = ' '.join(head_toks[1:4])          # 坐标 x3
            blk = head_toks[4]                     # 方块名
            cond = (bc, blk, cmd_part)
            body = ''
            break
        elif kind == 'unless':
            notes.append('`unless` 条件在基岩里没有直接等价写法，已原样保留')
            return cmd, notes
        else:
            notes.append('子句 `%s` 无法折叠成基岩 execute，已原样保留' % kind)
            return cmd, notes

    if body.startswith('run '):
        body = body[4:].strip()

    if as_sel is None:
        notes.append('不是 `execute as <选择器> …` 开头的形式，无法转成基岩 execute，已原样保留')
        return cmd, notes

    coords = pos if pos else '~ ~ ~'

    if cond:
        bc, blk, tail = cond
        dcoords = bc
        # 基岩的 detect 语法：detect <位置> <方块> <数据值> <命令>
        # Java 的 if block 已经带了坐标，基岩的 execute 坐标与 detect 坐标可能不同，
        # 这里把 Java 的 if block 坐标直接当作 detect 坐标（语义等价），
        # 并把 execute 坐标也设成同一处 —— 这是**唯一保险**的等价写法。
        out = 'execute %s %s detect %s %s 0 %s' % (as_sel, dcoords, dcoords, blk, tail)
        notes.append('按语义折叠：execute 坐标取 if block 的坐标 %s，'
                     'detect 也查同一处（基岩 detect 的坐标与 execute 坐标是两处，'
                     '取同值最保险）' % dcoords)
        return _squeeze_ws(out), notes

    out = 'execute %s %s %s' % (as_sel, coords, body)
    return _squeeze_ws(out), notes


def _squeeze_ws(cmd: str) -> str:
    """压缩命令里的多余空白，但**绝不动 JSON 参数内部**。

    ⚠️ 踩过的坑：转换末尾用 `re.sub(r'\\s+', ' ', out)` 无脑压空白，
    把 `tellraw`/`titleraw` 的 JSON 文本组件里的空格和换行也一起压掉了 ——
    实测把 `"\\\\n    §e▏..."` 压成 `"\\\\n §e▏..."`，**改变了玩家看到的文本排版**。
    JSON 是从**第一个 `{` 或 `[`** 开始的部分；那之后原样保留。
    """
    s = cmd or ''
    cut = len(s)
    for ch in ('{', '['):
        i = s.find(ch)
        if i != -1:
            cut = min(cut, i)
    head, tail = s[:cut], s[cut:]
    head = re.sub(r'\s+', ' ', head)
    # 头部压缩后可能留下尾部空格，去掉（但不要动 tail）
    return (head.rstrip() + (' ' if head.strip() and tail and not head.endswith(' ') else '')
            + tail).strip()


def _component_to_rawtext(cmd: str, head: str) -> tuple:
    """Java 的 {"text":"..."} → 基岩的 {"rawtext":[{"text":"..."}]}。"""
    import json as _json
    brace = cmd.find('{')
    if brace < 0:
        return cmd, []
    pre, tail = cmd[:brace], cmd[brace:]
    try:
        obj = _json.loads(tail)
    except Exception:
        return cmd, []
    if not isinstance(obj, dict) or 'text' not in obj:
        return cmd, []
    raw = {'rawtext': [{'text': obj['text']}]}
    for k in ('color', 'bold', 'italic', 'underlined', 'strikethrough', 'obfuscated'):
        if k in obj:
            raw['rawtext'][0][k] = obj[k]
    return pre + _json.dumps(raw, ensure_ascii=False, separators=(',', ':')), \
        ['文本组件 {"text":…} → 基岩 rawtext 结构']


def _strip_namespace_blocks(cmd: str) -> tuple:
    """把 setblock/fill 等**方块参数位置**的 minecraft: 前缀去掉（基岩习惯裸名）。

    只动确定是方块参数的位置，绝不全局替换 —— 免得把实体/物品名也改了。
    """
    toks = cmd.split()
    if not toks:
        return cmd, []
    head = toks[0].lower()
    idx = None
    if head == 'setblock' and len(toks) >= 5:
        idx = 4
    elif head == 'fill' and len(toks) >= 8:
        idx = 7
    if idx is None:
        return cmd, []
    tok = toks[idx]
    if not tok.startswith('minecraft:'):
        return cmd, []
    bare = tok[len('minecraft:'):]
    if not re.fullmatch(r'[a-z0-9_]+', bare):
        return cmd, []
    out = list(toks)
    out[idx] = bare
    return ' '.join(out), ['去掉方块名的 minecraft: 前缀（基岩习惯裸名）：%s → %s' % (tok, bare)]


def convert_commands_direction(cmds, direction: str) -> list:
    """按方向批量转换。direction: 'be2java' | 'java2be' | 'none'。"""
    fn = {'be2java': convert_command, 'java2be': convert_to_bedrock}.get(direction)
    out = []
    for c in (cmds or []):
        if fn is None:
            out.append({'from': c, 'to': c, 'notes': [], 'changed': False})
            continue
        try:
            new, notes = fn(c)
        except Exception as e:
            new, notes = c, ['转换器内部错误，已原样保留：%s' % e]
        out.append({'from': c, 'to': new, 'notes': notes, 'changed': new != c})
    return out
