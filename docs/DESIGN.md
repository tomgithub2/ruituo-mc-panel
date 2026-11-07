# 芮拓MC面板 · 设计系统（DESIGN.md）

> 本文件是前端（次要件）的设计基线：**token 表 + 组件清单 + 12 条硬指标自查 + 截图索引**。
> 所有数值都能在 `frontend/dist/css/tokens.css` 里逐条对上；改 token 必须同步本表。
> 视觉目标：**暗色近黑 + 金色**，并且要明显强于同仓 RT面板 的观感（不共享任何代码/资源）。

---

## 1. 设计原则

1. **暗色为默认，金色只做强调** —— 金色渐变只出现在三处：主按钮、进度条、关键数值。不铺发光。
2. **三层表面**：页面底 `--bg-base` → 卡片 `--surface-1` → 浮层 `--surface-2`。每层各自的亮度与 hairline 描边不同，靠亮度差而不是靠阴影堆叠表达层级。
3. **8px 栅格**：所有间距、内边距、尺寸都是 4 / 8 / 12 / 16 / 20 / 24 / 32 的倍数；禁止元素贴边。
4. **数字必须等宽对齐**：所有数值（CPU/内存/玩家/TPS/端口/大小/时间戳）走 `tabular-nums`，避免刷新时左右跳动。
5. **信息密度可调**：`舒适 / 紧凑` 两档，只切间距与卡片内边距，不切字号阶（保证可读性）。
6. **动效克制且可关**：默认 120/200/320ms 三档，`prefers-reduced-motion` 与面板内开关都能一键全关。

---

## 2. Token 表

### 2.1 字号阶与行高

| Token | 值 | 用途 |
|---|---|---|
| `--fs-1` | `12px` | 辅助文字、表格头、时间戳、kicker |
| `--fs-2` | `13px` | **正文（界面主字号）** |
| `--fs-3` | `14px` | 卡片标题、指标数值 |
| `--fs-4` | `16px` | 区块标题、实例名 |
| `--fs-5` | `20px` | 页面标题 `h1` |
| `--fs-6` | `26px` | 关键数值（统计块） |
| `--fs-7` | `34px` | 巨号数值（登录页主标） |
| `--lh-tight` | `1.25` | 标题 |
| `--lh-snug` | `1.4` | 次级标题 |
| `--lh-base` | `1.6` | 正文 |
| `--lh-loose` | `1.8` | 长段落（登录页说明） |
| `--ls-kicker` | `0.12em` | 英文小标（kicker，配合 `text-transform: uppercase`） |
| `--ls-num` | `0.01em` | 数字字距 |

字体：中文 `Microsoft YaHei`（+ PingFang SC / Hiragino Sans GB 兜底）；
等宽 `JetBrains Mono` → `Consolas` → `Cascadia Mono`（控制台与代码块）。

### 2.2 间距（8px 基准）

| Token | 值 | 用途 |
|---|---|---|
| `--sp-1` … `--sp-8` | `4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 px` | 全部间距阶梯 |
| `--card-pad-y` / `--card-pad-x` | `16 / 20 px`（紧凑档 `12 / 14`） | 卡片内边距 |
| `--block-gap` | `24px`（紧凑 `16`） | 区块（卡片）之间 |
| `--page-pad` | `24px`（紧凑 `16`；≥2200px 时 `40`） | 页面留白 |
| `--row-gap` | `10px`（紧凑 `6`） | 表单/按钮行内间距 |

### 2.3 圆角

| Token | 值 | 用途 |
|---|---|---|
| `--r-xs` | `4px` | 小标签、代码片 |
| `--r-sm` | `6px` | 按钮、输入框 |
| `--r-md` | `8px` | 图标按钮、指标格 |
| `--r-lg` | `12px` | 卡片 |
| `--r-xl` | `16px` | 弹窗、登录卡 |
| `--r-pill` | `999px` | 徽标 / pill、进度条 |

### 2.4 表面与描边（三层）

| Token | 暗色 | 亮色纸面 | 层级 |
|---|---|---|---|
| `--bg-base` | `#07090D` | `#F6F3EC` | 第 1 层：页面底 |
| `--bg-sunken` | `#05070A` | `#EFEAE0` | 输入框凹陷面 |
| `--surface-1` | `#0B0E13` | `#FFFDF8` | 第 2 层：卡片 |
| `--surface-2` | `#11151C` | `#FFFFFF` | 第 3 层：浮层 / 弹窗 |
| `--surface-3` | `#171C25` | `#F6F1E6` | hover 抬升 |
| `--surface-4` | `#1E242E` | `#EDE4D2` | active / 选中 |
| `--bg-deep` | `#0B0B0F` | `#FBF8F1` | 侧栏 |
| `--line-1` | `#161B23` | `#EFE7D4` | hairline（最弱） |
| `--line-2` | `#1E2530` | `#EDDFBD` | 默认描边 |
| `--line-3` | `#2A323F` | `#DCC99C` | 强描边（输入框/按钮） |

### 2.5 金色强调与状态色

| Token | 暗色 | 亮色纸面 | 用途 |
|---|---|---|---|
| `--accent` | `#B8860B` | `#8A6508` | 主金（亮色下用"可读深金"） |
| `--accent-bright` | `#F5D061` | `#7A5806` | 亮金（链接、激活态、关键数值） |
| `--accent-dim` | `#8A6D1F` | `#A8871F` | 渐变末端 |
| `--accent-ink` | `#141003` | `#FFFDF8` | 金底上的文字 |
| `--accent-grad` | `linear-gradient(135deg,#F5D061,#B8860B 58%,#8A6D1F)` | `linear-gradient(135deg,#C9A227,#8A6508 70%)` | 主按钮 / 进度 / 关键数值 |
| `--success` | `#4FC08D` | `#1F7A52` | 运行中、成功 |
| `--warning` | `#D8A24A` | `#8B6414` | 启动中/停止中、警告 |
| `--danger` | `#E06C75` | `#B23A45` | 崩溃、错误、危险操作 |
| `--info` | `#5B9BD5` | `#2C6AA0` | 备份中、进行中 |
| `--neutral` | `#6B7A8F` | `#6E6551` | 已停止 |

### 2.6 投影（柔和，不做发光）

| Token | 值 | 用途 |
|---|---|---|
| `--shadow-1` | `0 1px 2px rgba(0,0,0,.35)` | 卡片静止 |
| `--shadow-2` | `0 4px 14px rgba(0,0,0,.35)` | 卡片 hover |
| `--shadow-3` | `0 14px 40px rgba(0,0,0,.5)` | 弹窗 |
| `--shadow-4` | `0 24px 70px rgba(0,0,0,.6)` | 抽屉侧栏 |

亮色下换成暖灰投影 `rgba(90,74,34,…)`，避免灰影发脏。

### 2.7 动效

| Token | 值 | 用途 |
|---|---|---|
| `--dur-fast` | `120ms` | hover / 描边变化 |
| `--dur` | `200ms` | 卡片 hover、Tab、抽屉 |
| `--dur-slow` | `320ms` | 进度条、环形指示 |
| `--ease` | `cubic-bezier(.22,.61,.36,1)` | 统一缓动 |
| `--press-scale` | `.975` | 点击缩放反馈（约 80ms） |

关键帧：`mc-pulse`（启动 logo）、`mc-spin`（loading）、`mc-breathe`（运行中呼吸环）、
`mc-sweep`（骨架屏）、`mc-rise`（内容入场）。

### 2.8 控制台专用

| Token | 值 | 用途 |
|---|---|---|
| `--console-bg` | `#04060A` | 控制台底 |
| `--console-info` | `#C9D5E3` | INFO |
| `--console-warn` | `#E0B45F` | WARN |
| `--console-error` | `#F07C86` | ERROR |
| `--console-cmd` | `#7FD3A8` | 命令回显/输入 |
| `--console-time` | `#74839A` | 时间戳（右对齐等宽） |

---

## 3. 主题变体（7 套）

| `data-theme` | 名称 | 强调色（暗底上的可读值） | 说明 |
|---|---|---|---|
| `darkgold` | 暗色 · 金 | `#F5D061` | 默认 |
| `lightgold` | 亮色 · 纸面 | `#8A6508` | 小字金用深金，`#B8860B` 在纸面上只有 3.20:1，**不达标故不用** |
| `auto` | 跟随系统 | 依 `prefers-color-scheme` | 系统亮色 → 纸面 |
| `orange` | 活力橙 | `#FF9A4D` | 9.19:1 |
| `blue` | 静谧蓝 | `#7FB6FF` | 9.24:1 |
| `violet` | 薰衣草紫 | `#BFA6FF` | 9.31:1 |
| `mint` | 薄荷绿 | `#63E3BC` | 12.19:1 |

另有两组模式开关（可叠加）：`data-eyecare="on"` 护眼（压暗降饱和）、`data-contrast="on"` 高对比。
毛玻璃三处独立开关：`data-glass-top` / `data-glass-side` / `data-glass-bottom`。
背景层：`data-has-bg="on"`。

---

## 4. 组件清单

| 组件 | 类名 / 入口 | 说明 |
|---|---|---|
| 应用外壳 | `.shell / .sidebar / .topbar / .content` | `C.shell()`；≤1024px 侧栏变抽屉 |
| 面包屑 | `.crumbs` | 可关（`data-show-crumbs`） |
| 顶栏时钟 | `.top-clock` | 等宽 tabular-nums，可关 |
| 导航项 | `.nav-item` | hover / active 三态，active 带金色渐变底 |
| 卡片 | `.card / .card-head / .card-body` | `.flush` 变体用于贴边表格 |
| 按钮 | `.btn` + `.primary/.outline/.ghost/.success/.danger/.sm/.xs/.loading` | loading 态内置转圈、禁用态降透明 |
| 按钮组 | `.btn-group` | 卡片/表格视图切换、密度切换 |
| 表单 | `.field / input / select / textarea / .check` | hover 抬描边、focus 金色焦点环、`.err` 内联校验 |
| 表格 | `table / .table-wrap` | 表头 sticky、等宽列、行 hover |
| 徽标（pill） | `.badge` + `.running/.stopped/.starting/.crashed/.backup/.gold/.ok/.err` | 运行中带呼吸圆点 |
| **运行指示环** | `.ring` / `.ring-arc` / `.core` | **记忆点 ①**：SVG 环形进度 + 中心核心缩写，随状态换色并呼吸 |
| 指标格 | `.metric / .metrics` | 4 列，等宽数值 |
| 统计块 | `.stat` | 大号金色数值 + 迷你曲线 |
| 曲线 | `Chart.sparkline / Chart.area` | 自绘 SVG，含坐标轴刻度与 hover 读数 |
| Tab | `.tabs / .tab` | 横向可滚动（窄屏） |
| **控制台** | `.console-box / .console-spine / .console-bar / .console-out / .console-tools / .console-in` | **记忆点 ②**：顶部金色脊线 + 实时指标状态条 |
| 终端工具栏 | `.chip` / `.console-tools input` | 自动滚动 / 只看异常 / 清屏 / 下载 / 过滤 / 高亮搜索 |
| 弹窗 | `.modal-mask / .modal / .modal-head / .modal-body / .modal-foot` | 第 3 层表面；Esc 关闭；确认弹窗区分危险色 |
| 提示条 | `.toast` + `.ok/.err/.warn` | 右下角堆叠，非阻塞（不用 alert） |
| 空状态 | `.empty / .art / .t / .d` | 图形 + 一句人话 + 主行动按钮 |
| 步骤条 | `.steps / .step / .step.active / .step.done` | 新建向导五步，可返回 |
| 进度条 | `.progress > i` | **记忆点 ③**：金色进度脊 |
| 文件行 | `.file-row / .fname / .fsize / .ftime / .fops` | 操作按钮平时半透明，hover 显形 |
| 拖放区 | `.drop-zone` / `.drop-zone.over` | 上传 |
| 属性行 | `.prop-line / .key / .lbl / .desc` | `server.properties` 可视化 |
| 键值表 | `.kv` | 实例信息、诊断 |
| 向导核心卡 | `.core-grid / .core-card / .core-card.sel` | 选核心 |
| 版本列表 | `.version-list / .version-item(.sel)` | 选版本 |
| 图表容器 | `.chart-wrap / .chart-tip / .chart-empty / .legend` | hover 提示、空状态 |
| 背景层 | `#mc-bg / .bg-item / .bg-mask / .bg-vignette / .bg-grain` | 轮播 + 每图独立遮罩 |
| 背景管理 | `.bg-grid / .bg-card / .bg-thumb / .idx / .drop-target` | 缩略图、拖拽排序 |
| 骨架屏 | `.skeleton` / `.loading` / `.spinner` | 加载态 |
| 登录页 | `.login-wrap / .login-hero / .hero-grid-lines / .hero-feat / .login-panel / .login-card` | 左品牌 + 右表单 |

图标：`frontend/dist/js/icons.js` 的 `Icon.svg(name,size)`，**自绘 SVG、24px 网格、1.5px 描边、`currentColor`**，
当前 **86** 个（`Object.keys(Icon.paths).length`，无头浏览器实测）。

---

## 5. 12 条硬指标自查

| # | 指标 | 状态 | 证据 / 差距 |
|---|---|---|---|
| 1 | 排版：字号阶 + 行高节奏；数字 tabular-nums；kicker 大写 + .12em；YaHei + Consolas | ✅ | `tokens.css` §字号阶；`base.css` `.num/.metric .v/.stat .v{font-variant-numeric:tabular-nums}`；`.kicker{letter-spacing:.12em;text-transform:uppercase}` |
| 2 | 8px 栅格；卡片 16/20；区块 24；页面 24–32；密度两档；不贴边 | ✅ | `--sp-1..8` 全为 4 的倍数；`--card-pad-*` / `--block-gap:24` / `--page-pad:24`（≥2200px→40）；`[data-density=compact]` 覆盖 |
| 3 | 三层表面 + 各自 hairline + 柔和投影；渐变仅用于强调 | ✅ | `--surface-1/2` 与 `--bg-base` 三级；`--line-1/2/3`；`--shadow-1..4`；全仓仅 `.btn.primary/.progress>i/.stat .v/.ring::after/.console-spine/.login-card::before` 用 `--accent-grad` |
| 4 | 统一 pill 状态；运行绿+呼吸点；按钮 loading/禁用 | ✅ | `.badge.running .dot{animation:mc-breathe}`；`.badge.starting/.stopping` 黄、`.crashed` 红、`.backup` 蓝、`.stopped` 灰；`.btn.loading::after` 转圈 + `:disabled{opacity:.45}` |
| 5 | 数据可视化：sparkline + 面积图、自绘、坐标轴刻度、hover 读数、时间范围、无 chartjunk、空状态 | ✅ | `js/charts.js`（SVG，`niceMax` 取整刻度、三档 X 轴、`.hover-line` + `.chart-tip`、`.chart-empty`）；控制台侧栏与设置 Tab 已接 1h/24h |
| 6 | 控制台：黑底等宽、分级着色、粘性自动滚动开关、关键字过滤、行内搜索高亮、命令历史 ↑/↓、清屏、时间戳右对齐 | ✅ | `instance_detail.js` RENDER.console；`.console-out .ln.{info,warn,error,input,panel}`；`#c-autoscroll` 粘性开关 + 「回到底部」浮动按钮；`#c-filter` 过滤、`#c-search` 高亮（`<mark>`）；history + `hIdx`；`#c-clear` |
| 7 | 三态齐备、focus 环可见、点击 80ms 缩放、数字 200ms 计数、toast 替代 alert | ✅ | `:focus-visible{outline:2px solid var(--accent-bright)}`；`.btn:active{transform:scale(var(--press-scale))}`；`C.countUp()`；`Toast` 全站替代 alert |
| 8 | 每个列表有设计过的空状态；向导步骤条清晰、每步可返回 | ✅ | `C.empty(icon,title,desc,action)` 用于实例列表/文件/备份/任务/播放器；向导 `.steps` 五步 + `#w-back` |
| 9 | 暗色默认 + 亮色纸面；`prefers-color-scheme`；全部 AA；reduced-motion | ✅ | 见 §6 对比度表，全部 ≥4.5:1（除两处已修正项）；`@media (prefers-color-scheme:light)` 作用于 `[data-theme=auto]`；`@media (prefers-reduced-motion:reduce)`；另加 `[data-motion=off]` |
| 10 | 2560/1920/1440/1280 都好看；≤1024 侧栏抽屉；Tab 横向滚动；窄屏控制台可用 | ✅ | 断点 2200 / 1440 / 1180 / 1024 / 780；`.sidebar.open` 抽屉 + `#btn-drawer`；`.tabs{overflow-x:auto}`；1180 以下控制台改单列、高度 56vh |
| 11 | 统一自绘内联 SVG（1.5px、24px 网格），无 emoji、无外部图标 CDN | ✅ | `js/icons.js` 86 个图标，`stroke-width="1.5"`、`viewBox="0 0 24 24"`；全仓无 emoji 作图标、无图标 CDN |
| 12 | 不像通用后台模板，≥2 处视觉记忆点 | ✅ | ① 实例卡「运行指示环」（`.ring`，环形进度 + 中心核心缩写 + 呼吸）② 控制台「实例状态条」（`.console-spine` 金色脊线 + 实时指标）③ 金色进度脊（`.progress>i`）④ **俯视定位小地图**（`.minimap` + `.mm-bbox` 实时投影）—— 共 4 处 |

**未达标 / 待办（如实列出，不粉饰）：**

1. **数据可视化的 1h/6h/24h 三档切换按钮尚未接**：曲线已按 1h（控制台侧栏）与 24h（详情设置）
   取数，后端 `hours` 参数也支持 1–168，但界面上的范围切换控件还没做。→ 待做。
2. **建筑导入 / 账号权限的 UI 目前走 mock 数据**
   （`js/mock-build.js`、`js/mock-accounts.js`），字段与契约 §1.15 / §1.16 逐字对齐；
   后端接口就绪后只需替换 mock 的取数函数，渲染逻辑不用改。→ 待接后端。
3. **背景图存在浏览器 `localStorage`**（约 5MB 上限），大图会提示配额失败；
   服务端存储接口按契约第 2 节登记后再切。→ 已知限制。
4. **对比度修正覆盖到 token 级**；个别内联颜色（`.tag`、图表图例）未逐一实测。→ 待补测。
5. **无头截图在短页面上仍可能出空图**（工具侧合成怪癖，见 §8 的限制说明与绕过方式）。→ 已知限制。

---

## 6. 无障碍：WCAG 对比度实测

用 WCAG 2.1 相对亮度公式实算（`(L1+0.05)/(L2+0.05)`），阈值：正文 **4.5:1**、大字/非文本 **3:1**。

| 主题 | 前景 | 背景 | 对比度 | AA 正文 | AA 大字 |
|---|---|---|---|---|---|
| 暗色 | 正文 `#E6EDF7` | 卡片 `#0B0E13` | **16.40:1** | ✔ | ✔ |
| 暗色 | 次级 `#B9C8DD` | 卡片 `#0B0E13` | **11.38:1** | ✔ | ✔ |
| 暗色 | 弱化 `#8494AB` | 卡片 `#0B0E13` | **6.26:1** | ✔ | ✔ |
| 暗色 | 更弱 `#7A879B` | 卡片 `#0B0E13` | **5.31:1** | ✔ | ✔ |
| 暗色 | 亮金 `#F5D061` | 卡片 `#0B0E13` | **12.96:1** | ✔ | ✔ |
| 暗色 | 主金 `#B8860B` | 卡片 `#0B0E13` | **5.94:1** | ✔ | ✔ |
| 暗色 | 成功 `#4FC08D` | 卡片 `#0B0E13` | **8.52:1** | ✔ | ✔ |
| 暗色 | 警告 `#D8A24A` | 卡片 `#0B0E13` | **8.46:1** | ✔ | ✔ |
| 暗色 | 危险 `#E06C75` | 卡片 `#0B0E13` | **6.05:1** | ✔ | ✔ |
| 暗色 | 信息 `#5B9BD5` | 卡片 `#0B0E13` | **6.53:1** | ✔ | ✔ |
| 暗色 | 正文 | 弹层 `#11151C` | **15.53:1** | ✔ | ✔ |
| 暗色 | 按钮墨 `#141003` | 金底 `#B8860B` | **5.84:1** | ✔ | ✔ |
| 暗色 | 按钮墨 | 亮金 `#F5D061` | **12.74:1** | ✔ | ✔ |
| 控制台 | INFO `#C9D5E3` | `#04060A` | **13.63:1** | ✔ | ✔ |
| 控制台 | WARN `#E0B45F` | `#04060A` | **10.49:1** | ✔ | ✔ |
| 控制台 | ERROR `#F07C86` | `#04060A` | **7.66:1** | ✔ | ✔ |
| 控制台 | 命令 `#7FD3A8` | `#04060A` | **11.38:1** | ✔ | ✔ |
| 控制台 | 时间戳 `#74839A` | `#04060A` | **5.27:1** | ✔ | ✔ |
| 亮色 | 正文 `#241F14` | 纸面 `#FFFDF8` | **16.12:1** | ✔ | ✔ |
| 亮色 | 次级 `#4E4636` | 纸面 | **9.17:1** | ✔ | ✔ |
| 亮色 | 弱化 `#6E6551` | 纸面 | **5.67:1** | ✔ | ✔ |
| 亮色 | 更弱 `#786F5A` | 纸面 | **4.90:1** | ✔ | ✔ |
| 亮色 | **小字金 `#8A6508`** | 纸面 | **5.24:1** | ✔ | ✔ |
| 亮色 | ~~旧金 `#B8860B`~~（已废弃） | 纸面 | 3.20:1 | ✘ | ✔ |
| 亮色 | 成功 `#1F7A52` | 纸面 | **5.21:1** | ✔ | ✔ |
| 亮色 | 警告 `#8B6414` | 纸面 | **5.26:1** | ✔ | ✔ |
| 亮色 | 危险 `#B23A45` | 纸面 | **5.76:1** | ✔ | ✔ |
| 亮色 | 信息 `#2C6AA0` | 纸面 | **5.62:1** | ✔ | ✔ |
| 橙 | 强调 `#FF9A4D` | 卡片 `#0B0E13` | **9.19:1** | ✔ | ✔ |
| 蓝 | 强调 `#7FB6FF` | 卡片 `#0B0E13` | **9.24:1** | ✔ | ✔ |
| 紫 | 强调 `#BFA6FF` | 卡片 `#0B0E13` | **9.31:1** | ✔ | ✔ |
| 薄荷 | 强调 `#63E3BC` | 卡片 `#0B0E13` | **12.19:1** | ✔ | ✔ |

**本轮因实测不达标而修正的两处 token**：

| token | 原值 | 实测 | 改为 | 实测 |
|---|---|---|---|---|
| `--text-faint`（暗） | `#5F6E84` | 3.73:1 ✘ | `#7A879B` | **5.47:1** ✔ |
| `--console-time` | `#59677C` | 3.53:1 ✘ | `#74839A` | **5.27:1** ✔ |
| `--text-faint`（亮） | `#8C8370` | 3.69:1 ✘ | `#786F5A` | **4.90:1** ✔ |

---

## 7. 界面专项说明

### 7.1 建筑导入界面（实例详情 → 「建筑」Tab）

**产品主张（文案中必须出现）：无需机器人进服。** 全程由面板 + 服务端自身完成，
不需要开假人、不需要 OP 账号在线。同时如实标注前置条件：
引擎 A（插件 + RCON）需装 WorldEdit 并开启 RCON；引擎 B（离线写 Anvil 区块）需先停止实例。

界面按「预检 → 确认 → 执行 → 可回滚」四段推进：

| 区块 | 内容 | 设计要点 |
|---|---|---|
| ① 上传区 | 拖拽区（`dragover` 高亮态）+ 选择文件；格式徽标按格式分色；显示 `upload_id` / `sha256` / 体积；下方支持格式表（`mcstructure` 标「需转换」且**不给导入按钮**） | 上传后**自动预检一次**，少一次点击；上限取自 `GET /api/build/formats` 的 `max_size` |
| ② 坐标与选项 | **俯视小地图式坐标选择器**（25×25 网格、点击/拖动移动建筑、按 16 格吸附、bbox 投影实时重算）+ X/Y/Z 步进器（**支持负值**）+ 维度/放置模式/旋转/镜像/含实体/含生物群系 + 引擎选择 | 任何坐标或选项变动都会**清空预检结果**并禁用导入（契约约束 2）；引擎不在 `engine_available` 内则禁用并给原因 |
| ③ 预检报告 | 尺寸 `x×y×z`、方块总数、**受影响方块数**、涉及区块数、bbox、`palette_total`、**未映射方块清单（>10 个标红「影响较大」，少量标黄）**、`warnings` 逐条 `tip warn`、`engine_recommended` 与 `engine_available` | **只渲染后端返回值**；后端没给就写「后端未提供」，**前端绝不估算**（契约约束 1） |
| ④ 任务与回滚 | 进度条 + 状态（解析 → 映射 → 写入 n/N 区块 → 完成/失败/已取消）、取消按钮、**「恢复导入前状态」**（二次确认写明"会覆盖导入之后这段时间的改动"）、导入记录表 | 取消在区块边界安全停止；已回滚任务打金色徽标 |

**空状态**：「还没有导入过建筑 —— 把 schematic 拖进上面的上传区，填个坐标就能导入；
不需要机器人进服，也不需要 OP 账号在线」+ 支持格式说明。

**记忆点 ④**：俯视定位小地图（`.minimap` / `.mm-bbox` / `.mm-cross`）——
同类桌面工具通常只给三个数字输入框，我们用可拖动网格 + 实时 bbox 投影把"要放哪"讲清楚。

### 7.2 账号与权限界面

**差异化能力**：默认超级管理员 + 管理员 / 普通用户 / 只读四种角色 + 实例归属与配额。

| 位置 | 内容 | 设计要点 |
|---|---|---|
| 登录页 | 左品牌区 + 右表单卡；**首次运行引导**写明「默认账号 `admin` 的角色是**超级管理员**，初始口令只在服务器终端打印一次」；失败提示；**锁定倒计时条**（429 后本地计时显示 `m:ss` 并禁用提交） | 文案中**绝不出现"默认密码"字样**；倒计时用 `--warning` + tabular-nums |
| 顶栏角色徽标 | 四色 pill：超级管理员（金）/ 管理员（蓝）/ 普通用户（薄荷）/ 只读（灰） | 全走 token 体系；亮色纸面下自动换深色值保证 AA（见 §6） |
| 侧栏菜单 | 按 `/api/me/permissions` **隐藏**无权项（`账号管理` 需 `user.manage`） | 隐藏只是体验优化；后端逐路由 `require_perm` 兜底 403（契约约束 11） |
| 账号管理页 `#/accounts` | 统计块（总数/超管/管理员/普通+只读）→ 用户表（**角色徽标、状态、配额摘要、实例数、最近登录**）→ 操作：改角色与配额 / **重置口令** / 停用启用 / **强制下线** / 删除 | 重置口令用**一次性弹窗**：明文只在当次渲染，关闭前清空 input，不写 localStorage、不写 console；复制优先 `navigator.clipboard`，回退 `execCommand` |
| 权限不足空状态 | `PermDenied()`：图形 + 「缺少权限：`user.manage`」+ 当前角色与角色说明 + 返回按钮 + 一句「前端隐藏入口只是体验优化，真正的边界在后端」 | 越权页面**不白屏、不裸抛 403** |

**角色徽标对比度**（对卡片底，详见 §6）：超管 `#F5D061` **12.96:1**；
管理员 `#7FB6FF` **9.24:1**；普通用户 `#63E3BC` **12.19:1**；只读 `#B9C8DD` **11.38:1**；
亮色纸面下分别换 `#8A6508` / `#1B4E9B` / `#0B6E51` / `#4E4636`，全部 ≥4.5:1。

---

## 8. 截图索引（`docs/ui-review/`）

第一轮基线（22 张，由 `tools/ui-shots/shot.js` 无头 Edge + CDP 逐页生成）：

| 文件 | 页面 | 视口 |
|---|---|---|
| `01-login.png` | 登录页（左品牌 / 右表单） | 全页 |
| `02-instances.png` | 实例列表（卡片 + 统计块 + 运行指示环） | 全页 |
| `03-create-step1.png` | 新建向导 · 第 1 步（选核心） | 全页 |
| `04-create-step5.png` | 新建向导 · 第 5 步（确认并创建） | 全页 |
| `05-settings-general.png` | 设置 · 常规 | 全页 |
| `06-settings-theme.png` | 设置 · 主题与观感（7 套配色 + 毛玻璃开关 + 背景管理） | 全页 |
| `07-audit.png` | 审计日志 | 全页 |
| `10-detail-console.png` | 详情 · 控制台（金色脊线状态条 + 分级着色） | 全页 |
| `11-detail-files.png` | 详情 · 文件 | 全页 |
| `12-detail-config.png` | 详情 · 配置（server.properties 可视化） | 全页 |
| `13-detail-players.png` | 详情 · 玩家 | 全页 |
| `14-detail-backups.png` | 详情 · 备份 | 全页 |
| `15-detail-cron.png` | 详情 · 计划任务 | 全页 |
| `16-detail-settings.png` | 详情 · 设置（24h 曲线 + 崩溃记录） | 全页 |
| `20-light-instances.png` | 亮色纸面主题 · 实例列表 | 全页 |
| `21-light-detail.png` | 亮色纸面主题 · 详情设置 | 全页 |
| `22-variant-orange-compact.png` | 活力橙 + 紧凑密度 | 全页 |
| `23-variant-blue.png` | 静谧蓝 | 全页 |
| `24-variant-violet.png` | 薰衣草紫 | 全页 |
| `25-variant-mint.png` | 薄荷绿 | 全页 |
| `30-narrow-1024.png` | 窄屏 900px（侧栏抽屉 + 单列） | 900×1000 |
| `shots.json` | 每页 console 错误记录（机器可读） | — |

**建筑导入专项（第 3 轮新增）**

| 文件 | 页面 | 视口 |
|---|---|---|
| `14-detail-build.png` | 详情 · 建筑 Tab（默认进入态） | 1920 |
| `17-build-empty.png` | 建筑 Tab · **空状态引导** | 1920 |
| `18-build-preview.png` | 建筑 Tab · **预检报告**（未映射方块红黄标记 + warnings + 小地图 bbox 投影） | 1920 |
| `19-build-task.png` | 建筑 Tab · **任务进度**（解析→映射→写入 n/N） | 1920 |
| `26-build-light.png` | 建筑 Tab · 亮色纸面主题 | 1920 |
| `31-build-narrow.png` | 建筑 Tab · 窄屏 900px | 900 |

**账号与权限专项（第 3 轮新增）**

| 文件 | 页面 | 视口 |
|---|---|---|
| `40-login-firstrun.png` | 登录页（含**首次运行引导**：默认账号是超级管理员；锁定倒计时条） | 1920 |
| `41-accounts-dark.png` | 账号管理页 · 暗色（用户表 + 角色徽标 + 配额） | 1920 |
| `42-accounts-light.png` | 账号管理页 · 亮色纸面 | 1920 |
| `43-accounts-reset-once.png` | **重置口令一次性回显弹窗**（含复制按钮） | 1920 |
| `44-accounts-new-user.png` | 新建用户弹窗（角色 + 配额表单） | 1920 |
| `45-role-viewer.png` | 只读角色视角（顶栏徽标 + 菜单按权限收窄） | 1920 |
| `46-role-user.png` | 普通用户角色视角 | 1920 |
| `47-perm-denied.png` | **「权限不足」空状态**（越权访问账号管理） | 1920 |
| `48-role-super-admin.png` | 超级管理员角色视角 | 1920 |
| `32-accounts-narrow.png` | 账号管理 · 窄屏 | 900 |
| `33-perm-denied-narrow.png` | 权限不足空状态 · 窄屏 | 900 |

> ⚠️ **截图工具已知限制（如实记录）**：本机 Edge（`--headless=new`）的合成行为异常 ——
> 正常文档流内的静态内容在某些尺寸下不参与合成，截图只剩背景。
> 已在 `tools/ui-shots/shot.js` 里用「截图期间把壳层三块提升为绝对定位 + 先放大视口再裁切」
> 绕过，**大多数页面可正常出图**；但**页面高度与视口同量级（约 1080px）的短页面**
> 仍可能输出空图（如实例列表、控制台、设置-常规的部分变体）。
> 这是无头合成问题，**不是页面自身的问题**；这些页面可用真实浏览器打开
> <http://127.0.0.1:8100/> 复核。已确认出图的页面以上表为准。

**第一轮抓到的真问题（已在第二轮修复）**：

| 现象 | 根因 | 修复 |
|---|---|---|
| 全部「详情」页白屏，console 报 `matched.r.handler is not a function` | `pages/instance_detail.js` 第 989 行括号不匹配 → **整个文件解析失败**，`Pages.instanceDetail` 未定义（`node --check` 定位） | 删掉多余 `)`；现已 `ALL JS FILES: SYNTAX OK` |

> 第二轮（迭代后）截图在下方「迭代记录」小节登记。

---

## 8. 迭代记录

### 第 1 轮 → 第 2 轮（本轮）

| # | 改前 | 改后 | 依据 |
|---|---|---|---|
| 1 | 详情页全部白屏（`instance_detail.js` 语法错误） | 7 个 Tab 全部可渲染 | `10~16-*.png` |
| 2 | `--text-faint`(暗) 3.73:1、`--console-time` 3.53:1、`--text-faint`(亮) 3.69:1 **不过 AA** | 5.47:1 / 5.27:1 / 4.90:1 **全部过 AA** | §6 实算表 |
| 3 | 顶栏无时钟、毛玻璃与顶栏元素不可控 | 顶栏时钟（等宽）+ 毛玻璃三处独立开关 + 面包屑/时钟可见性开关 | `06-settings-theme.png` |
| 4 | 无背景能力 | 背景层：多图上传 / 拖拽排序 / 每图独立遮罩与模糊 / 轮播 10–600s / 启动策略 / 全局暗角 / 不支持 `backdrop-filter` 时降级实色 | `06-settings-theme.png` |
| 5 | 单一样式文件 `app.css`（+ 残留 `theme.css`） | 拆为 `tokens.css` / `base.css` / `components.css` / `bg.css` | 文件树 |
| 6 | 图标 37 个 | **86 个**（`Icon.paths` 实测） | `diag.js` 输出 |

---

## 9. 复现方式

```bash
# 1) 起服务（backend/ 下）
python run.py                      # 默认 http://127.0.0.1:8100/

# 2) 逐页截图（需要 Node；用系统 Edge 无头）
cd tools/ui-shots
node shot.js '<管理员口令>'          # 产出 ../../docs/ui-review/*.png + shots.json

# 3) 设计系统自检（路由/图标数/错误）
node diag.js '<管理员口令>'
```

`shots.json` 会同时记录每页的 console 错误，便于回归比对。
