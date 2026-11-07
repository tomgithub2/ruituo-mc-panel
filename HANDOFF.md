# HANDOFF · 芮拓MC面板（我的世界服务器管理面板）

> 本文是这个项目的**总入口/交接说明**。任何人（或另一个 AI 对话）看完这一份，就知道：
> 它是什么、现在做到哪、代码在哪、怎么跑、有哪些规格与结论、还欠什么、哪些坑别踩。
>
> 最后更新：会话交接时（见文件时间戳）。**与 `ops-panel`（RT面板）是两个独立产品/网站，零耦合。**
>
> **停止时刻的现场快照见 [`PROGRESS.md`](PROGRESS.md)**：所有并行工作已停止、进程已清空，
> 里面逐项记录了"已有什么产物 / 验证到什么程度 / 还缺什么 / 怎么恢复工作"。

---

## 1. 这是什么

**独立的 Minecraft（我的世界）开服 / 服务器管理面板**，形态是**自托管网页应用**（对比：Snowolf 是 Tauri 桌面应用）。

- 目标：一键开服 + 可视化运维 + 多实例管理；**功能上对齐 Snowolf，并做它没有的**。
- 用户口径：**「Snowolf 有的功能你也要有，他没有的你也要有」**；界面**要比现有 RT面板 更好看**。
- 与 RT面板 的关系：**两个独立产品、两个网站**。安装 RT面板 **不会**、也不允许默认安装本面板；
  反过来也一样。**不共享数据库、不互相引用、卸载互不影响**（可选互操作默认全关）。
- 差异化方向（调研结论）：**网页/远程/多端**（Snowolf 官方自述"不是网页控制面板"）、**多用户+角色+配额**（Snowolf 无）、
  **TPS/MSPT 采集与低 TPS 诊断**、**整合包一键装**、**崩溃根因分析**、**建筑一键导入（无需机器人进服）**。

## 2. 目录结构（统一文件架）

```
guanwang/mc/
├─ backend/                     # FastAPI + SQLite，独立可跑
│  ├─ app/
│  │  ├─ main.py                # 应用装配、鉴权闸门、限流、静态托管、SPA 兜底（核心，主线负责）
│  │  ├─ permissions.py         # **角色/权限/归属/配额 的唯一裁定处**（require_perm / require_instance）
│  │  ├─ auth.py  audit.py  pathguard.py    # 鉴权 / 审计 / 路径安全（核心）
│  │  ├─ database.py  config.py             # SQLite schema、幂等迁移与配置（核心）
│  │  ├─ process_manager.py                 # 进程组、日志环形缓冲、WS 广播、崩溃重启、监控采样（核心）
│  │  ├─ sources.py  downloader.py  javaruntime.py   # 核心/Java 下载源与运行时（核心）
│  │  ├─ serverctl.py  mcprops.py  rcon.py  scheduler.py  pluginsources.py
│  │  ├─ build/                 # 建筑导入（新）：schematic / anvil / mapping / plan / tasks / engines
│  │  └─ routers/               # auth core instances files config players backups cron plugins logs settings build users
│  ├─ tools/                    # dev_set_password.py / reset_password.py / build_make_sample.py / build_selftest.py
│  │                            # accounts_selftest.py（规格 §8 六条验收，160 项断言）
│  ├─ data/                     # 运行期数据：mc.db、instances/、java/、logs/（不入库）
│  ├─ .deps/                    # 依赖安装目录（pip --target）
│  ├─ requirements.txt  run.py  selftest.py
├─ frontend/dist/               # 纯 HTML/CSS/JS，无构建步骤
│  ├─ css/tokens.css base.css components.css   # 设计令牌/基础/组件（次要件线负责）
│  ├─ js/{icons,util,api,store,charts,components,router,main,theme-boot}.js
│  ├─ js/permissions.js         # 前端权限工具（对接 /api/me/permissions，**已替换原 mock**）
│  └─ js/pages/{login,instances,create,instance_detail,settings,audit,build,accounts}.js
├─ docs/                        # 全部规格、调研、设计与契约（见 §4）
│  └─ evidence/                 # 验收原始输出（自检日志 / DOM 断言 JSON）
├─ tools/                       # ui-shots/shot.js（无头截图）、ui-shots/dom-check.js（DOM 断言）、
│                               # dev_ui_panel.py（独立数据目录起面板，供 UI 验收）
└─ HANDOFF.md                   # 本文件
```

## 3. 怎么跑

```bash
cd backend
pip install --target .deps -r requirements.txt      # 首次
PYTHONUTF8=1 python run.py                          # 起在 http://127.0.0.1:8100/
```

- 配置文件：`backend/data/config.json`（端口默认 **8100**）
- **首次启动会生成超级管理员初始口令，并且只在本地终端打印一次**（不写日志、不进网页）
- 本机调试时，可用 `backend/tools/dev_set_password.py` 把口令设为已知值
  （该脚本与 `reset_password.py` **不随开源仓库发布**，见 `.gitignore`）。
  > 改密后 `token_epoch` 会 bump，所有旧会话立即失效；跑 `selftest.py` 会改口令，注意别弄丢。
- **忘了口令就用本地通道**（规格 §4.5，唯一的恢复路径，不开网页后门）：
  `cd backend` → `python tools\reset_password.py admin`（或 `--role super_admin`）→ 随机新口令只打印一次
- **体检**：`python tools\panel_doctor.py --password <口令>`（不给口令则跳过登录相关检查）
- **验收怎么跑**（详见 [`PROGRESS.md`](PROGRESS.md) §6）：
  `python tools\accounts_selftest.py`（账号权限，160 项断言，自带独立数据目录）
  / `node tools\ui-shots\dom-check.js <base> <口令>`（前端 DOM 断言，24 项）
- 独立安装（Linux，与 RT面板 零耦合）：`install.sh` → `/opt/mc-panel` + systemd `mc-panel`

## 4. 规格与调研（都在 `docs/`，**这些是接手的核心资料**）

| 文件 | 内容 |
|---|---|
| `FEATURE-BUILDING-IMPORT.md` | **建筑导入（无需机器人进服）** 完整规格：支持格式、两套引擎、预检字段、坐标与选项、任务与回滚、安全红线、验收标准 |
| `FEATURE-ACCOUNTS.md` | **账号与权限**规格：认证现状、四角色矩阵（默认账号=超级管理员）、实例归属与配额、首次运行、用户管理 API、前端约束、验收标准 |
| `API-CONTRACT.md` | 前后端接口契约（页面 → 接口 → 字段），含"前端 10 条假设" |
| `RESEARCH-snowolf.md` | **竞品调研**：Snowolf 是什么（Tauri 桌面面板）、界面风格（黑白灰单色+大圆角、毛玻璃、灵动岛）、**约 120 项功能点 / 230 个 Tauri 命令证据**、竞品矩阵、交付形态、独立站点所需能力、**「比 RT面板 更好看」的 17 组 token 规范** |
| `RESEARCH-schematic-formats.md` | **建筑导入技术调研**（1230 行）：四种格式解析规格、Anvil 离线写入全部细节、`/place template` 实测、WorldEdit 不可用的证据、安全阈值 |
| `DESIGN.md` | 设计系统：token 表、组件清单、12 条设计指标自查、截图索引 |
| `ui-review/*.png` | 无头浏览器逐页截图（暗/亮/窄屏、多套配色变体）；⚠️ 账号/权限相关截图是 mock 时代的，已过期 |
| `evidence/selfcheck-accounts.log` | **账号与权限 · 规格 §8 六条验收原始输出**（160 项通过 / 0 失败） |
| `evidence/dom-check.json` | **前端权限体系 DOM 断言原始输出**（24 项全通过、零 console 异常） |

## 5. 已实现 / 已验证（有原始证据）

- ✅ **真 Vanilla 1.21.4 生命周期跑通**：建实例 → 下载 54.2 MB `server-1.21.4.jar` → 启动（`pid` 存活、占 ~1 GB）
  → `/api/instances/{id}/log` 返回真服务端日志 → 优雅停止（`graceful:true`）→ **java 进程 0 个、exit_code=0**
- ✅ **控制台链路**：stdout/stderr 捕获 + 等级分类（`info/warn/error`）+ 环形缓冲 + WebSocket 推送
- ✅ **鉴权**：PBKDF2-SHA256 600k、≥12 位 3 类、失败锁定、`token_epoch` 会话失效、登录审计
- ✅ **安全加固（主线亲自改）**：`pathguard` 加 `normcase` 归一（Windows 大小写不敏感绕过）；
  `main.py` 修掉"未匹配 `/api/**` 被 SPA 兜底成 200 HTML"（现回 JSON 404）；
  健康检查/版本探测**豁免限流**、API 上限 600→1800 次/60s（登录仍 10 次/60s 防爆破）
- ✅ **账号与权限体系（本次会话完成，规格 §8 六条验收全过）**：
  `permissions.py` 四角色矩阵 + `require_perm` / `require_instance` + 实例归属与配额；
  `routers/users.py` 全套用户管理；默认账号落成 **super_admin**（含老库迁移）；
  既有 **全部路由逐个挂上 403 兜底**；前端 `permissions.js` + 账号页切到真实接口。
  证据：`docs/evidence/selfcheck-accounts.log`（**160 项通过 / 0 失败**）、
  `docs/evidence/dom-check.json`（前端 **DOM 断言 24 项全过**）。
  本地口令恢复通道：`backend/tools/reset_password.py`
- ✅ **建筑导入核心**：`app/build/anvil.py`、`engines.py`、`routers/build.py`、`tools/build_selftest.py`（31.8 KB）；
  已在真服务端跑 `/place template` 测试并生成真实区块文件。⚠️ **`routers/build.py` 此前一直没挂进 `main.py`
  （13 条路由根本不存在），本次已挂载**；但规格 §9 的五条验收仍未复跑（见 PROGRESS §5.4）
- ✅ **前端**：设计令牌 + 组件 + 多套配色变体 + 建筑 Tab 页面（`js/pages/build.js`，**仍用 MockBuild**）

## 6. 关键实测结论（**不要再走弯路**）

0. **权限体系怎么写才不出洞**（本次实测踩过，新增路由前必读）：
   - **后端才是边界**：前端 `Perm.has()` 只用隐藏菜单/按钮，**任何写接口都必须过
     `require_perm(user, '...')` 或 `require_instance(user, iid, '...')`**。漏写一次就是漏洞。
   - `require_instance(user, iid)` **不带权限点 = 只要求"能看这个实例"**：管理员看全部、
     属主看自己、`viewer` 可读（规格 §2 只读档含控制台/日志）。写操作必须显式传权限点。
   - **别用 `log.view` 当"能看所有实例"的通行证**：普通用户权限集里也有它，会被读成越权
     （本次实测确实让普通用户读到了他人实例的文件与备份列表）。归属判定只能靠 `owner_id` / 管理员 / viewer 角色。
   - 停用账号要在**登录那一道**就拒（`users.status`）：只管 `resolve_token` 的话，
     被停用的人当场换个新 token 又进来了。
   - WebSocket（控制台）也要做同一套校验：它绕过 FastAPI 依赖注入，最容易漏。
     而且**别把 FastAPI 依赖函数当普通函数直接调用** —— `get_current_user(request, authorization)`
     直调时 `authorization` 拿到的是 `Header(...)` 哨兵对象而不是字符串，
     对它有 `.startswith()` 会当场抛 `AttributeError`（表现为 WS 握手 500）。用 `try_get_user()`。
   - 新加一个"角色/权限"概念时，**先改 `permissions.py` 一处**，不要在路由里散写 if。
   - **菜单入口必须与后端门禁一致**：`components.js` 的 NAV 项要标 `perm`（权限点）或
     `flag`（`/api/me/permissions` 的派生布尔）。漏标就会出现"菜单点得到、进去 403"的死循环 ——
     普通用户点「设置/审计日志」正是本次用户报的"点菜单就空白"。
   - **403 不能让页面留在"读取失败/空白"**：页面级取数被拒时用
     `Perm.denyIfForbidden(e, app, {perm:'...'})` 画出设计过的「权限不足」空状态（规格 §6 要求）。
   - **静默失败是最大的坑**：`Perm.showProblem()` / `Perm.auditPage()` 会在页面渲染完仍为空时，
     把当前页面/角色/令牌长度/接口状态/错误原文画到内容区。前端改动别绕过这套自检。
   - **判空仓/订阅队列这类容器别用 `set`**：`collections.deque` 不可哈希，
     `set.add(deque)` 直接抛 `TypeError`（本次让**每个实例的控制台 WS 握手后立刻崩**，前端只看到 1006）。
   - **静态资源用 `app.mount` 挂载时，任何"旧路径兼容/重定向"路由都必须注册在 mount 之前**，
     放在之后就永远轮不到（本次给已删除的 `mock-accounts.js` 加兼容路由，第一版就写错了顺序）。
   - 验收别只看 API：`tools/ui-shots/page-audit.js`（逐页抓 console 异常 + 4xx）、
     `role-audit.js`（三种角色各走一遍）、`ws-check.js`（控制台 WS 真连）都跑一遍才算"能用"。
1. **`/place template` 可用（零插件、零机器人、运行中即可）** —— 建筑导入「引擎 A」就用它：
   - 结构放 `<server>/<world>/generated/minecraft/structures/<name>.nbt`（**复数 structures**，gzip 的 NBT）
   - **模板只在启动时扫描** → 需重启一次实例（或先探活，报 `There is no template` 就重启并续做）
   - RCON：`forceload add …` → `place template <name> <x> <y> <z>` → `forceload remove …`（**不要带前导 `/`**）
   - `<pos>` = **最小角**；**无条件覆盖**已有方块（"只填空气"要用 `structure_void` 预填充）；**实体不落、方块实体会落**；
     `rotation`/`mirror` 是枚举名；**`strict` 仅 1.21.5+，低版本报错**
   - 实测证据：0 玩家时 `Loaded template "minecraft:test1" at 100, 65, -200`，读回 `(100,65,-200)=stone`、`(100,66,-200)=gold_block`
2. **「WorldEdit/FAWE + RCON」不可行（已实测否掉）**：控制台未注册 `//` 命令（`Unknown or incomplete command`），
   网传的 `commands.allow-commands-in-console` 在 7.4.x 已不存在；机制上 `//paste` 要求 actor 是 `Locatable`。**别再试。**
3. **Paper `api.papermc.io/v2` 已 sunset（410 Gone）** → 必须用 **`fill.papermc.io/v3`**（Snowolf 1.7 硬编码 v2，其 Paper 下载当前应是坏的）；国内加 **BMCLAPI** 镜像回退。
4. **离线写 Anvil 必须停服**（运行中 `.mca` 被独占锁，内存区块保存会覆盖你的写入；"只停单维度"无可靠做法）。
   细节：区块 NBT 键是 **`xPos/yPos/zPos`**、`Status` 必须 `minecraft:full`；`bits=max(4,ceil(log2(n)))`、不跨 long、
   **LongArray 有符号**；region 用 **floorDiv**；**载荷 >1 MiB 走 `.mcc` 且类型字节高位 `0x80`**。
5. **格式要点**：`.schem` **v3 多一层 `Schematic` 包裹**；`BlockData` 是 **LEB128 varint 流（不是定宽位打包）**；索引 **YZX**；必须 gzip。
6. **注册表**：`java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar --reports` → `generated/reports/blocks.json`；
   ⚠️ `registries.json` 里**没有 `worldgen/biome`**（生物群系清单拿不到，如实标注）。

## 7. 还欠什么（TODO，接手可从这里继续）

> 详细进度、证据位置与复跑命令见 [`PROGRESS.md`](PROGRESS.md)。

**✅ 已完成（原第 1、2 项）**
1. ~~账号与权限核心~~ —— 已完成：`database.py` 迁移（`instances.owner_id` + 配额列）、`require_perm` 四角色矩阵、
   `routers/users.py` 全套、默认账号落成超级管理员、列表按归属过滤、审计扩项；
   **规格 §8 六条验收全过**（160 项断言，见 `docs/evidence/selfcheck-accounts.log`）。
2. ~~`main.py` 统一挂载新 router~~ —— 已完成：`routers/build.py`（此前**根本没挂载**）+ `routers/users.py`
   都已挂上，API 路由共 125 条。

**未完成**
3. **下载竞态**：`POST /api/instances` 返回 `download.ok=true` 但下载是异步的 → 紧接 `start` 报"服务端 jar 不存在"；
   应改为 `state/progress` 语义，下载中 `start` 回 **409 + 进度**。
4. **源收口**：Paper 切 `fill.papermc.io/v3` + BMCLAPI 镜像回退 + Java 源 sha256 校验 + TPS/MSPT 真实性
   （拿不到必须 `null` + `available=false`）。
5. **建筑导入复核**：路由已挂载，但**规格 §9 五条验收未复跑**（引擎 B 读回方块、回滚后 region sha256 一致、
   越界/未映射/恶意样本被拒）。
6. **前端收尾**：`build.js` 的 `MockBuild` 换成真实接口（接口已齐）；
   契约补全、14 个图标、`install.sh` + `README.md` + 互操作说明、`selftest.py` 鉴权 bug、`DESIGN.md` 补两节。
   ⚠️ **前端验收请用 DOM 断言而不是截图**：本机 Edge 无头对 1080 量级高度页面合成不出内容，
   用 `node tools/ui-shots/dom-check.js <base> <口令>`（本次前端的两个真实竞态就是它抓到的）。

## 8. 协作与文件边界

> 下面这套边界是**多线并行时**的约定（当时有多条子代理线同时在写）。现在是单线推进，
> 不再强制，但"改别人的段落前先说清楚"这条仍然有效。

- **`backend/app/main.py`、`permissions.py`、`pathguard.py`、`auth.py`、`database.py`、`process_manager.py`
  是核心**，改它们等于改全局行为（尤其 `permissions.py`：那是权限的唯一裁定处）。
- 建筑导入相关：`app/build/**` + `app/routers/build.py` + `backend/tools/build_*`。
- 源与生命周期：`sources.py`、`downloader.py`、`javaruntime.py`、`rcon.py`、`scheduler.py`、`serverctl.py`
  与 instances 路由的启停段。
- 前端 / 文档 / 安装脚本；**规格文档（`FEATURE-*.md`）是需求来源，改它等于改需求**，先确认再动。

## 9. 本机环境坑（踩过，照做省时间）

- **没有 `py` 启动器**（用 `python`）；Python 命令前置 `PYTHONUTF8=1`（默认可能是 GBK）
- **绝不要用 PowerShell 写文本文件**（会按 GB2312 破坏中文、写 BOM）——用编辑工具或 Python `io.open(encoding='utf-8')`
- ⚠️ **`Set-Content -Encoding UTF8` 在 PS 5.1 下会写 BOM**，Python 的 `json.load` 会因 BOM 解析失败并
  **静默回落到默认配置**（本次因此把限流异常当成应用 bug 查了一轮）。写 JSON 配置用
  `[System.IO.File]::WriteAllText($p, $json, (New-Object System.Text.UTF8Encoding($false)))`。
- ⚠️ **Windows 上杀面板必须 `taskkill /F /T /PID`**：`terminate()` 不一定杀得掉，会留旧服务占端口，
  下一轮脚本就**连到旧服务**上跑（本次踩过：测试跑在角色已被改坏的旧库上，报错完全看不懂）。
- ⚠️ **Windows 的 `select()` 不能用于管道**（`WinError 10038`）；读子进程输出用线程。
- ⚠️ **无头截图在本机不可信**：Edge `--headless=new` 对 1080 量级高度页面合成不出内容（只剩背景，
  约 8.5 KB 的纯色 PNG）。前端验收改用 `tools/ui-shots/dom-check.js`（读 DOM）。别拿空图当"验过了"。
- **没有系统 ffmpeg/ffprobe**（与本项目基本无关；需要时用 `imageio-ffmpeg` 自带的静态包）
- 本机有 **Java 21.0.8**（跑真服务端没问题）；服务端 1 个实例约吃 1 GB 内存，**测完务必停掉，别留 java 进程**
- 抓取：加速器把 `github.com` / `raw.githubusercontent.com` 劫持到 127.0.0.1（`web_fetch` 会被 SSRF 拦），
  用 shell 的 `curl.exe -k -sSL --ssl-no-revoke`；`api.github.com`、Modrinth、BMCLAPI、`fill.papermc.io/v3`、Adoptium 实测可达
- 建筑导入的验证脚手架（别人写的，可复用）：`D:\DeepSeekHarness\_mcresearch\`
  （`rcon_run.py`、`anvil_read.py`/`anvil_blocks.py`、`mkstruct.py`/`mkschem.py`、`reports_probe.py`、`cmd_tree*.py`）
  —— 已随本项目一同收进 `tools/_mcresearch/`（见 §2）
