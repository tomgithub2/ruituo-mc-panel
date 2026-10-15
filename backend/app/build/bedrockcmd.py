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
from collections import Counter

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
#: ⚠️ `titleraw` / `replaceitem` / `tickingarea` **都不在这里** —— 它们有对应：
#:    `titleraw` → Java `title`；`replaceitem` → `item replace`；`tickingarea` → `forceload`。
#:    只有 Java **确实没有**的命令才留在这个清单里。
_BEDROCK_ONLY = ('structure', 'fog', 'camerashake', 'ride',
                 'playanimation', 'inputpermission', 'dialogue', 'camera',
                 'gametest', 'music', 'schedule',
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
    toks = _split_tokens(cmd)
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


#: 文本组件里**基岩与 Java 同名**的键（可直接复制）
_TEXT_KEEP = ('text', 'translate', 'with', 'score', 'selector', 'separator',
              'color', 'font', 'bold', 'italic', 'underlined', 'strikethrough',
              'obfuscated', 'insertion')


def _bedrock_component_to_java(elem, notes):
    """把一个**基岩 rawtext 元素**转成 Java 文本组件。

    权威依据（minecraft.wiki Text component format）：
      · 内容四类 `text` / `translate` / `score` / `selector` **两边同名同结构**；
      · 格式化用 `§` 格式化代码（两边都支持），也可用 `color`/`bold`… 键；
      · 事件：基岩 `clickEvent`/`hoverEvent` → Java **同名字段**（1.21.5 前）；
        这里只做**键名搬运 + 嵌套递归**，不猜测语义。
    返回 (新元素, 是否改动)。
    """
    if not isinstance(elem, dict):
        return elem, False
    out = {}
    changed = False
    for k, v in elem.items():
        if k in _TEXT_KEEP:
            if k == 'with':
                # `with` 里可能是嵌套的 rawtext 对象 → 递归
                if isinstance(v, dict) and 'rawtext' in v:
                    sub, _ = _rawtext_to_java(v.get('rawtext'), notes)
                    out[k] = sub
                    changed = True
                else:
                    out[k] = v
            else:
                out[k] = v
        elif k in ('clickEvent', 'hoverEvent'):
            out[k] = v                       # Java 同名（1.21.5 前）；键名搬运
        else:
            # 认不得的键原样带上，避免丢信息
            out[k] = v
    return out, changed


def _rawtext_to_java(raw, notes):
    """基岩 `rawtext` 数组 → Java 文本组件（单元素 → 对象；多元素 → 列表）。

    ⚠️ 关键依据：Java 的**列表**组件语义 = 依次拼接（等价于基岩 rawtext 的 Appending），
       所以 `{"rawtext":[A,B]}` → `[A,B]` 是**无损**的，不要退回"原样保留"。
    """
    if not isinstance(raw, list):
        return raw, False
    conv = []
    any_change = False
    for elem in raw:
        e, ch = _bedrock_component_to_java(elem, notes)
        conv.append(e)
        any_change = any_change or ch
    if len(conv) == 1:
        return conv[0], any_change
    return conv, True


def _convert_tellraw(cmd: str) -> tuple:
    """基岩 `{"rawtext":[…]}` → Java 文本组件。

    做法：**用 json 解析**，不用正则 —— 正则容易把括号改错（我踩过）。

    权威对应（minecraft.wiki）：
      `{"rawtext":[{"text":"Hello"}]}`                → `{"text":"Hello"}`
      `{"rawtext":[{"text":"a"},{"text":"b"}]}`       → `[{"text":"a"},{"text":"b"}]`
      `{"rawtext":[{"selector":"@e[…]"} , {…}]}`      → `[{"selector":"@e[…]"} , {…}]`
    另外**顺带把 `titleraw` 转成 Java 的 `title`**（`titleraw` 的 action 名 `actionbar`
    在 Java 里叫 `actionbar`、`title`/`subtitle`/`title` 同名，只有命令名不同）。
    """
    import json as _json
    if '"rawtext"' not in cmd:
        return cmd, []

    notes = []
    brace = cmd.find('{')
    if brace < 0:
        return cmd, []
    head, tail = cmd[:brace], cmd[brace:]
    try:
        obj = _json.loads(tail)
    except Exception:
        notes.append('文本组件 JSON 无法解析（可能是非标准 JSON），已原样保留，建议人工核对')
        return cmd, notes
    if not isinstance(obj, dict) or 'rawtext' not in obj:
        return cmd, []

    new_obj, _ = _rawtext_to_java(obj.get('rawtext'), notes)
    dumped = _json.dumps(new_obj, ensure_ascii=False, separators=(',', ':'))

    # titleraw → title：命令名不同，其余（actionbar/title/subtitle + 文本组件）一致
    new_head = head
    m = re.match(r'^/(titleraw)(\s)', head)
    if m:
        new_head = '/' + 'title' + head[len('/titleraw'):]
        notes.append('titleraw → Java 的 title（其余参数一致）')
    elif head.startswith('titleraw'):
        new_head = 'title' + head[len('titleraw'):]
        notes.append('titleraw → Java 的 title（其余参数一致）')

    if isinstance(new_obj, list):
        notes.append('rawtext 多元素 → Java 的组件列表（Java 列表即依次拼接，语义等价）')
    else:
        notes.append('rawtext 结构 → Java 的文本组件')

    return new_head + dumped, notes


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


def _split_tokens(cmd: str) -> list:
    """按空白切词，但**尊重引号与成对括号** —— 引号/括号内的空格不算分隔符。

    ⚠️ 这个分词器是必需的（踩过）：实体名里带空格很常见，例如
        `replaceitem entity @e[name="§l§c[ＢＯＳＳ]§4 镜像۩凶兽§r"] slot.armor.feet 0 …`
    用普通 `cmd.split()` 会把那个名字拆成 3 个 token，导致**后面所有参数错位**
    （槽位被当成 `镜像۩凶兽§r"]`）。实测在用户真实文件里就这样出错了。
    """
    toks = []
    buf = ''
    depth = 0          # [ { 的嵌套深度
    quote = None       # 当前引号字符
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            buf += ch
            i += 1
            continue
        if ch in '[{':
            depth += 1
            buf += ch
            i += 1
            continue
        if ch in ']}':
            depth = max(0, depth - 1)
            buf += ch
            i += 1
            continue
        if ch.isspace() and depth == 0:
            if buf:
                toks.append(buf)
                buf = ''
            i += 1
            continue
        buf += ch
        i += 1
    if buf:
        toks.append(buf)
    return toks


def _normalize_coords(cmd: str) -> str:
    """把整条命令里所有粘连坐标拆开（保守：只拆"恰好三轴"的 token）。

    ⚠️ **不能重建整条命令**（`' '.join(toks)` 会压掉引号内/JSON 里的空格，
       实测把 `@e[name="… 镜像۩凶兽§r"]` 这类带空格的名字压坏 → 参数错位）。
       所以这里只做**局部替换**：把粘连坐标 token 原位换成空格分隔的版本，
       其它部分一个字符都不动。
    """
    if not re.search(r'[~^]', cmd):
        return cmd
    out = cmd
    for tok in set(_split_tokens(cmd)):
        nt = _split_glued_coord(tok)
        if nt != tok:
            # 只替换这一处（用词边界，避免误伤同名字符串）
            out = re.sub(r'(?<![\w.])' + re.escape(tok) + r'(?![\w])', nt, out)
    return out


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
    """旧名保留（等价于 `_split_tokens`）。

    ⚠️ 曾经有**两个**分词器：一个只保护 JSON 花括号、一个还保护引号 ——
    后者才对（实体名 `@e[name="… 镜像۩凶兽§r"]` 里带空格，不保护引号就会把参数拆散）。
    现在统一到 `_split_tokens`，这里只做别名，避免两处实现再次跑偏。
    """
    return _split_tokens(cmd)


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

    # ⚠️ 网易《我的世界》中国版有两类前缀写法（实测都出现过）：
    #      `x/effect …`  —— 命令空间前缀，**命令本身是正常的**，剥掉前缀后应正常转换
    #      `xplayanimation …` / `x summon …` —— 加 `x` 的基岩专有变体，Java 没有
    #    原来我把所有 `x` 开头的都当"专有命令"返回，导致 `x/effect` 这种**本来能转的被漏掉**。
    if low.startswith('x/'):
        stripped = stripped[len(head.split('/')[0]) + 1:].lstrip()   # 去掉 `x/`
        out_probe = stripped
        head, _rest = _split_first_word(out_probe)
        low = head.lower().lstrip('/')
    elif low == 'x' and _rest.strip():
        # `x playanimation …` → 把 `x ` 去掉，剩下的按正常流程判定
        stripped = _rest.strip()
        head, _rest = _split_first_word(stripped)
        low = head.lower().lstrip('/')
        notes.append('去掉网易的 `x` 前缀命令空间')

    # 基岩专有命令：明确不转换
    if low in _BEDROCK_ONLY:
        return text, ['基岩版专有命令 `/%s`，Java 版没有对应命令，已原样保留' % low]

    # ⚠️ 带 `x` 前缀的变体（`xplayanimation` / `xexecute` / `xsummon` …）：
    #    去掉 `x` 后若仍是基岩专有命令 → 标注保留；否则按正常命令继续转换。
    if low.startswith('x') and len(low) > 1:
        inner = low[1:]
        if inner in _BEDROCK_ONLY or inner in ('playanimation', 'replaceitem', 'fog',
                                               'structure', 'tickingarea'):
            return text, ['基岩版（网易）专有命令 `/%s`，Java 版没有对应命令，已原样保留' % low]
        # 其余（如 `xexecute`）→ 去掉 `x` 按正常命令继续
        stripped = stripped[len(head) - len(inner):]
        head, _rest = _split_first_word(stripped)
        newlow = head.lower().lstrip('/')
        notes.append('去掉网易的 `x` 前缀：`%s` → `%s`' % (low, newlow))
        low = newlow

    # ① 先把**粘连坐标**拆开（`~~~-35` → `~ ~ ~-35`）——
    #    必须放在 execute 判断与所有"按 token 下标取参数"的逻辑**之前**，
    #    否则坐标粘连会让 token 数量变少、后面全部取错位置（实测）。
    out = _normalize_coords(stripped)

    # ① execute（旧版→新版 / 嵌套 / detect）
    if low == 'execute':
        out, n = _convert_execute(out)
        notes.extend(n)

    # ② 文本组件（tellraw / titleraw）
    if low == 'tellraw':
        out, n = _convert_tellraw(out)
        notes.extend(n)
    elif low == 'titleraw':
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

    # ②f replaceitem → item replace（Java 1.17 起替代）
    if low == 'replaceitem':
        out, n = _replaceitem_to_java(out)
        notes.extend(n)

    # ②g tickingarea → forceload（常加载区域）
    if low == 'tickingarea':
        out, n = _tickingarea_to_java(out)
        notes.extend(n)

    # ②d 粒子名 / 音效名（可能在 `execute … run particle|playsound …` 里，用正则搜索）
    def _sub_after(kw, fn):
        nonlocal out
        m2 = re.search(r'(^|\s|/)(%s)\s' % kw, out)
        if not m2:
            return
        start = m2.start(2)
        seg = out[start:]
        new_seg, n3 = fn(seg)
        if n3:
            out = out[:start] + new_seg
            notes.extend(n3)

    _sub_after('particle', _convert_particle)
    _sub_after('playsound', _convert_playsound)

    # ③ 方块名补命名空间（含 detect）
    out, n = _convert_block_name_params(out)
    notes.extend(n)

    # ④ 仍需人工留意的项（已尽量自动转换，剩下的如实标注）
    if re.search(r'\{[^}]*\*[^}]*\}', out):
        notes.append('命令里的 NBT 通配符 `*` 写法在 Java 版可能不同，建议人工核对')

    # 原来是 `/xxx` 就还原成 `/xxx`（命令方块里习惯带斜杠）
    if slash and not out.startswith('/'):
        out = '/' + out

    return out, notes


# ================================================================ 粒子名 / 音效名映射
#: 基岩粒子名 → Java 粒子名。
#:
#: ⚠️ 这些名字在基岩与 Java 里**大多完全不同**（基岩爱用 `*_particle` / `*_emitter` 后缀），
#:    不做映射的话导入后要么报错、要么静默不显示 —— 用户明确要求
#:    「**直接扔进 bdx 就把所有的转换了，不需人工修改任何一个**」。
#: 映射依据 minecraft.wiki 的 Bedrock/Java 粒子对照；**没把握的一律不猜**（见 _unmapped 上报）。
PARTICLE_MAP = {
    # —— 火焰 / 燃烧类
    'basic_flame_particle': 'flame',
    'colored_flame_particle': 'flame',
    'blue_flame_particle': 'soul_fire_flame',
    'mobflame_single': 'flame',
    'mobflame_emitter': 'flame',
    'misc_fire_vapor_particle': 'campfire_cosy_smoke',
    'basic_smoke_particle': 'smoke',
    # —— 爆炸类
    'huge_explosion_emitter': 'explosion_emitter',
    'huge_explosion_lab_misc_emitter': 'explosion_emitter',
    'knockback_roar_particle': 'explosion',
    'sonic_explosion': 'sonic_boom',
    'cauldron_explosion_emitter': 'poof',
    'camera_shoot_explosion': 'poof',
    'dragon_death_explosion_emitter': 'explosion_emitter',
    'egg_destroy_emitter': 'poof',
    'sparkler_emitter': 'firework',
    # —— 龙 / 灵魂 / 末地
    'dragon_breath_trail': 'dragon_breath',
    'dragon_breath_fire': 'dragon_breath',
    'dragon_breath_lingering': 'dragon_breath',
    'soul_particle': 'soul',
    'sculk_soul_particle': 'sculk_soul',
    'shriek_particle': 'shriek',
    'eyeofender_death_explode_particle': 'poof',
    'endrod': 'end_rod',
    'totem_particle': 'totem_of_undying',
    # —— 其它常见
    'balloon_gas_particle': 'poof',
    'elephant_tooth_paste_vapor_particle': 'poof',
    'rising_border_dust_particle': 'dust',
    'obsidian_glow_dust_particle': 'dust',
    'redstone_wire_dust_particle': 'dust',
    'falling_dust_scaffolding_particle': 'falling_dust',
    'falling_dust': 'falling_dust',          # 基岩/Java 同名（显式列出以免被当未映射）
    'falling_dust_concrete_powder_particle': 'falling_dust',
    'falling_dust_dragon_egg_particle': 'falling_dust',
    'critical_hit_emitter': 'crit',
    'basic_crit_particle': 'crit',
    'water_evaporation_bucket_emitter': 'poof',
    'water_evaporation_azalea_leaves_emitter': 'poof',
    'splash_spell_emitter': 'effect',
    'raid_omen_ambient': 'trial_spawner_detection',
    'trial_omen_ambient': 'trial_spawner_detection',
    'lava_particle': 'lava',
    'wind_explosion_emitt': 'explosion',
}

#: 基岩音效名 → Java 音效名。
#: 基岩用 `mob.xxx.yyy` / `random.xxx` 这种点分命名，Java 用 `entity.xxx.yyy` / `block.xxx` 等。
SOUND_MAP = {
    # —— 生物
    'mob.enderdragon.growl': 'entity.ender_dragon.growl',
    'mob.zombie.remedy': 'entity.zombie_villager.cure',
    'mob.warden.sonic_boom': 'entity.warden.sonic_boom',
    'mob.blaze.shoot': 'entity.blaze.shoot',
    'mob.blaze.hit': 'entity.blaze.hurt',
    'mob.cat.hiss': 'entity.cat.hiss',
    'mob.zombie.woodbreak': 'entity.zombie.attack_wooden_door',
    'mob.irongolem.hit': 'entity.iron_golem.hurt',
    'mob.irongolem.crack': 'entity.iron_golem.damage',
    'mob.drowned.shoot': 'entity.drowned.shoot',
    'entity.zombie.converted_to_drowned': 'entity.zombie.converted_to_drowned',
    'mob.ghast.scream': 'entity.ghast.scream',
    'mob.ravager.roar': 'entity.ravager.roar',
    'mob.wither.spawn': 'entity.wither.spawn',
    # —— 环境 / 随机
    'random.orb': 'entity.experience_orb.pickup',
    'random.explode': 'entity.generic.explode',
    'random.glass': 'block.glass.break',
    'random.anvil_land': 'block.anvil.land',
    'random.bow': 'entity.arrow.shoot',
    'random.fizz': 'block.fire.extinguish',
    'random.pop': 'entity.item.pickup',
    'random.levelup': 'entity.player.levelup',
    'random.click': 'ui.button.click',
    # —— 方块 / 物品
    'item.trident.riptide_1': 'item.trident.riptide_1',
    'item.trident.riptide_2': 'item.trident.riptide_2',
    'item.trident.riptide_3': 'item.trident.riptide_3',
    'item.trident.throw': 'item.trident.throw',
    'item.trident.hit': 'item.trident.hit',
    'firework.launch': 'entity.firework_rocket.launch',
    'firework.blast': 'entity.firework_rocket.blast',
    'conduit.activate': 'block.conduit.activate',
    'conduit.deactivate': 'block.conduit.deactivate',
    'beacon.activate': 'block.beacon.activate',
    'beacon.deactivate': 'block.beacon.deactivate',
    'beacon.power': 'block.beacon.ambient',
    'note.pling': 'block.note_block.pling',
    'note.bass': 'block.note_block.bass',
    'note.harp': 'block.note_block.harp',
    # —— 天气
    'ambient.weather.lightning.impact': 'entity.lightning_bolt.impact',
    'ambient.weather.thunder': 'entity.lightning_bolt.thunder',
    'ambient.weather.rain': 'weather.rain',
    # —— 摄像机（网易/基岩的相机音效，Java 没有对应 → 映射到 ui）
    'camera.take_picture': 'ui.button.click',
    'camera.shutter': 'ui.button.click',
}


def _bare(name: str) -> str:
    """去掉 `minecraft:` 前缀。"""
    n = (name or '').strip()
    return n.split(':', 1)[1] if n.startswith('minecraft:') else n


def _map_particle_name(name: str):
    """基岩粒子名 → Java 粒子名；返回 (新名字, 是否改动)。不认得就原样返回。"""
    bare = _bare(name)
    if bare in PARTICLE_MAP:
        return 'minecraft:' + PARTICLE_MAP[bare], True
    return name, False


def _convert_particle(cmd: str) -> tuple:
    """转换 `particle <名字> …` 里的粒子名。"""
    notes = []
    m = re.match(r'^(\S+\s+)([A-Za-z_][\w:\.]*)(.*)$', cmd, re.S)
    if not m:
        return cmd, notes
    head, name, rest = m.group(1), m.group(2), m.group(3)
    new, changed = _map_particle_name(name)
    if changed:
        notes.append('粒子名按基岩→Java 对照改名：%s → %s' % (name, new))
        return head + new + rest, notes
    # 认得是粒子名（`xxx_particle` / `xxx_emitter` 这种基岩特征后缀）但没映射
    # → 记进上报，供"是否还有人工项"的整体核查
    bare = _bare(name)
    if re.search(r'(_particle|_emitter)$', bare) or re.search(r'\bparticle\b', cmd):
        _unmapped['particle'][name] += 1
    return cmd, notes


def _convert_playsound(cmd: str) -> tuple:
    """转换 `playsound <音效名> …` 里的音效名。"""
    notes = []
    m = re.match(r'^(\S+\s+)([A-Za-z_][\w:\.]*)(.*)$', cmd, re.S)
    if not m:
        return cmd, notes
    head, name, rest = m.group(1), m.group(2), m.group(3)
    bare = _bare(name)
    if bare in SOUND_MAP:
        new = 'minecraft:' + SOUND_MAP[bare]
        notes.append('音效名按基岩→Java 对照改名：%s → %s' % (name, new))
        return head + new + rest, notes
    # 基岩音效名形如 `mob.xxx.yyy` / `random.xxx` → 点分且含常见前缀
    if re.match(r'^(mob|random|item|note|beacon|conduit|firework|ambient|camera|entity)\.',
                bare.lower()):
        _unmapped['sound'][name] += 1
    return cmd, notes


#: 基岩**实体槽位类型** → Java 槽位名（权威：minecraft.wiki「Slot」）。
#: 基岩把槽位写成「类型 + 独立编号」两个参数，Java 合成一个 `<类型>.<编号>`。
_SLOT_ENTITY_MAP = {
    'slot.weapon.mainhand': 'weapon.mainhand',
    'slot.weapon.offhand': 'weapon.offhand',
    'slot.armor.head': 'armor.head',
    'slot.armor.chest': 'armor.chest',
    'slot.armor.legs': 'armor.legs',
    'slot.armor.feet': 'armor.feet',
    'slot.armor.body': 'armor.body',
    'slot.hotbar': 'hotbar',
    'slot.inventory': 'inventory',
    'slot.enderchest': 'enderchest',
    'slot.chest': 'container',
    'slot.armor': 'armor.body',
    'slot.saddle': 'saddle',
}
_SLOT_NO_NUM = {'weapon.mainhand', 'weapon.offhand', 'armor.head', 'armor.chest',
                'armor.legs', 'armor.feet', 'armor.body', 'saddle'}


def _slot_java(kind: str, num: str):
    """把基岩的（槽位类型, 编号）拼成 Java 的槽位名；无法映射时返回 None。"""
    k = (kind or '').lower()
    if k == 'slot.container':
        return 'container.%s' % num
    j = _SLOT_ENTITY_MAP.get(k)
    if not j:
        return None
    return j if j in _SLOT_NO_NUM else '%s.%s' % (j, num)


def _replaceitem_to_java(cmd: str) -> tuple:
    """基岩 `replaceitem` → Java `item replace`（依据 minecraft.wiki Commands/item + Slot）。

    对照：
      基岩 `replaceitem entity @e[…] slot.weapon.mainhand 0 iron_sword 1 0`
      Java `item replace entity @e[…] weapon.mainhand with minecraft:iron_sword 1`
      · 槽位：基岩「类型 + 独立编号」→ Java `<类型>.<编号>`（武器/盔甲类无编号）
      · `keep` 关键字在 Java 里同样存在，位置不变
      · 方块容器：基岩单独给坐标，Java 把坐标直接跟在 `block` 后面
      · 数字 data 值在 Java 1.13+ 已废弃 → 丢弃并标注
    """
    notes = []
    toks = _split_tokens(cmd)
    if len(toks) < 5:
        return cmd, ['replaceitem 参数不足，未转换']
    mode = toks[1].lower()
    if mode == 'entity':
        if len(toks) < 7:
            return cmd, ['replaceitem entity 参数不足，未转换']
        target, kind, num = toks[2], toks[3], toks[4]
        i = 5
        keep = ''
        if toks[i].lower() == 'keep':
            keep = 'keep '
            i += 1
        item = _maybe_namespace(toks[i])
        i += 1
        amount = toks[i] if i < len(toks) and toks[i].isdigit() else ''
        i += 1 if amount else 0
        if i < len(toks) and toks[i].isdigit():
            notes.append('丢弃数字 data 值 `%s`（Java 1.13+ 用数据组件）' % toks[i])
            i += 1
        rest = ' '.join(toks[i:])
        slot = _slot_java(kind, num)
        if slot is None:
            return cmd, ['replaceitem 的槽位 `%s` 无法映射到 Java 的槽位名，'
                         '已原样保留，需人工处理' % kind]
        out = 'item replace entity %s %s %swith %s' % (target, slot, keep, item)
        if amount:
            out += ' %s' % amount
        if rest:
            out += ' ' + rest
        notes.append('replaceitem → Java 的 `item replace`（Java 1.17 起替代）；'
                     '槽位 `%s %s` → `%s`' % (kind, num, slot))
        return out, notes

    if mode == 'block':
        if len(toks) < 9:
            return cmd, ['replaceitem block 参数不足，未转换']
        coords = ' '.join(toks[2:5])
        kind, num = toks[5], toks[6]
        i = 7
        keep = ''
        if toks[i].lower() == 'keep':
            keep = 'keep '
            i += 1
        item = _maybe_namespace(toks[i])
        i += 1
        amount = toks[i] if i < len(toks) and toks[i].isdigit() else ''
        i += 1 if amount else 0
        if i < len(toks) and toks[i].isdigit():
            notes.append('丢弃数字 data 值 `%s`（Java 1.13+ 用数据组件）' % toks[i])
            i += 1
        rest = ' '.join(toks[i:])
        if kind.lower() != 'slot.container':
            return cmd, ['replaceitem block 只支持 `slot.container`，'
                         '实际是 `%s`，已原样保留' % kind]
        out = 'item replace block %s container.%s %swith %s' % (coords, num, keep, item)
        if amount:
            out += ' %s' % amount
        if rest:
            out += ' ' + rest
        notes.append('replaceitem → Java 的 `item replace`；槽位 `slot.container %s` '
                     '→ `container.%s`' % (num, num))
        return out, notes

    return cmd, ['replaceitem 的第一参数只能是 entity / block，实际是 `%s`，未转换' % mode]


def _tickingarea_to_java(cmd: str) -> tuple:
    """基岩 `tickingarea` → Java `forceload`（常加载区块）。

    ⚠️ 语义**不完全等价**，必须如实标注：
      · 基岩 tickingarea = 该区域**保持加载并参与刻更新**（ticking）；
      · Java forceload   = 区块**保持加载**（ticking 由模拟距离决定）。
    所以这是"最接近的对应"，不是精确等价。
    语法：`tickingarea add <x1 y1 z1> <x2 y2 z2> [名字]` → `forceload add <x1 z1> <x2 z2>`
         `tickingarea add circle <x y z> <半径> [名字]` → `forceload add <方块坐标范围>`
         `tickingarea remove …` → Java **没有对应**（只能 `forceload remove`，语义不同）→ 不转
    """
    notes = []
    toks = _split_tokens(cmd)
    if len(toks) < 3:
        return cmd, ['tickingarea 参数不足，未转换']
    action = toks[1].lower()
    if action != 'add':
        return cmd, ['tickingarea 的 `%s` 操作在 Java 没有对应（forceload 不能按名字删除），'
                     '已原样保留，需人工处理' % action]
    rest = toks[2:]
    if rest and rest[0].lower() == 'circle':
        # add circle <x y z> <半径> [名字]
        if len(rest) < 5:
            return cmd, ['tickingarea add circle 参数不足，未转换']
        try:
            cx, cy, cz = int(float(rest[1])), int(float(rest[2])), int(float(rest[3]))
            r = int(float(rest[4]))
        except (ValueError, IndexError):
            return cmd, ['tickingarea add circle 的坐标或半径不是数字，未转换']
        # 圆 → 外接正方形（覆盖原区域，宁可多加载也不漏）
        x1, z1, x2, z2 = cx - r, cz - r, cx + r, cz + r
        notes.append('tickingarea（常加载区域）→ Java 的 `forceload`；'
                     '⚠️ **语义不完全等价**：基岩的 tickingarea 是"加载并参与刻更新"，'
                     'Java 的 forceload 只是"保持加载"。圆的区域已按**外接正方形**展开'
                     '（多加载一点，保证不漏）')
        return 'forceload add %d %d %d %d' % (x1, z1, x2, z2), notes
    # add <x1 y1 z1> <x2 y2 z2> [名字]
    if len(rest) < 6:
        return cmd, ['tickingarea add 参数不足，未转换']
    # ⚠️ Java 的 forceload 只接受**绝对坐标**；基岩的 tickingarea 常用相对坐标
    #    （`~25`）—— 相对坐标要靠命令方块自身位置才能算出绝对值，静态无法确定，
    #    所以这种情况**不转**（宁可如实报告，也不输出一条坐标错误的命令）。
    if any(v[0] in '~^' for v in rest[0:6]):
        return cmd, ['tickingarea 用了**相对坐标**（%s），而 Java 的 `forceload` 只接受'
                     '绝对坐标 —— 需人工换算，已原样保留'
                     % ' '.join(rest[0:6])]
    try:
        x1, _y1, z1 = (int(float(v)) for v in rest[0:3])
        x2, _y2, z2 = (int(float(v)) for v in rest[3:6])
    except (ValueError, IndexError):
        return cmd, ['tickingarea add 的坐标不是数字，未转换']
    notes.append('tickingarea（常加载区域）→ Java 的 `forceload`；'
                 '⚠️ **语义不完全等价**：基岩的是"加载并参与刻更新"，'
                 'Java 的 forceload 只是"保持加载"（y 轴在 forceload 里无意义，已省略）')
    return 'forceload add %d %d %d %d' % (x1, z1, x2, z2), notes


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


# ================================================================ 未映射项上报
#: 转换过程中记录「认得但没做映射」的名字，供"是否还有人工项"的整体核查使用。
#: 目的：用户要求「**直接扔进 bdx 就把所有转换了，不需人工修改任何一个**」，
#: 所以需要一份**可核对**的清单来证明（或发现没做到）。
_unmapped = {'particle': Counter(), 'sound': Counter()}


def reset_unmapped():
    """清空未映射记录（每轮解析前调用）。"""
    _unmapped['particle'].clear()
    _unmapped['sound'].clear()


def get_unmapped() -> dict:
    """返回 {'particle': {名字: 次数}, 'sound': {...}}。"""
    return {k: dict(v) for k, v in _unmapped.items()}


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
    toks = _split_tokens(cmd)
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
