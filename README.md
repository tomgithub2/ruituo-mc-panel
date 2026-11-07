# 芮拓MC面板

> 仓库：**`ruituo-mc-panel`**
> 一个**独立可跑**的 Minecraft 多服务器管理面板。后端 FastAPI + SQLite，
> 前端纯 HTML/CSS/JS（**无构建步骤**，后端直接托管 `frontend/dist/`）。

> **许可**：[AGPL-3.0-or-later](LICENSE) —— 自由软件，OSI 认可的开源协议。
> 你可以自由使用、修改、分发（含商用），但**修改后必须同样以 AGPL 开源**；
> 并且**如果把它作为网络服务提供给他人，必须向这些用户提供完整源码**（第 13 条）。

**它解决什么问题**：不用装客户端、不用游戏账号、不用在聊天窗里贴命令，
打开浏览器就能建服、开服、看实时状态、导建筑、备份回滚、分配权限。

---

## 0. 开源范围（**先读这一段**）

本仓库**只发布源码**。整个工作区约 492 MB，其中源码 **1.7 MB / 131 个文件**，
其余都是运行时数据与可再生的工作区，已在 [`.gitignore`](.gitignore) 中排除：

| 不发布 | 原因 |
|---|---|
| `backend/data/` | 含 SQLite 库（**口令哈希、会话**）、`config.json`（**RCON 密码**）、实例世界、备份归档(327 MB)、下载的核心、运行日志 |
| `backend/data/buildwork/` | 建筑导入工作区（57 MB，样本与 jar 可再生成） |
| `backend/tmp/` | 早期研究草稿（含 17.7 MB 的 `inner_server.jar`），`selftest.py` 会自行重建 |
| `tools/_mcresearch/` | 早期研究脚手架（17 MB），不属于产品 |
| `backend/.deps/` | `pip --target` 的依赖产物（22 MB），用 `requirements.txt` 重装即可 |
| `*.key` / `secret.key` | 面板的会话签名密钥 |
| `backend/tools/reset_password.py`、`dev_set_password.py`、`tools_reset_pw.py` | 本地口令管理通道，其中写有开发用口令 |
| `docs/evidence/*.png`、`backend/data-shots/` | 验收时现场生成的无头浏览器截图 |

**密钥永远不进仓库**：API Key 走 `backend/data/config.json`（已忽略），
或用环境变量；不要在源码里写死任何凭据。

> 因此：**克隆下来是能直接跑的**，但首次启动要自己装依赖并设置初始口令（见 §1）。
> 已有示例实例、示例世界、备份都不会带上 —— 这是有意的。

## 0.1 推到远程

```bash
git remote add origin https://github.com/<你的用户名>/ruituo-mc-panel.git
git push -u origin main

# Gitee
git remote add gitee https://gitee.com/<你的用户名>/ruituo-mc-panel.git
git push -u gitee main
```

凭据请用 **`gh auth login`** 或系统的凭据管理器；**不要把 Personal Access Token
写进 remote URL** —— 那样它会以明文永久留在 `.git/config` 里。

---

## 1. 快速开始

```bash
cd backend
pip install --target .deps -r requirements.txt   # 依赖装进 backend/.deps
python run.py                                    # 默认 http://127.0.0.1:8100/
```

**首次启动会在本地终端打印一次超级管理员初始口令**（不写日志、不写页面、不落盘明文）。
登录后到「设置 → 修改口令」更换。

本机开发用的口令记录在 [`HANDOFF.md`](HANDOFF.md)；忘记口令可用
`python -m tools.reset_password` 在服务器本地直接改库。

Linux 生产安装：

```bash
sudo bash install.sh                 # /opt/mc-panel + systemd 服务 mc-panel，端口 8100
sudo bash install.sh --port 8200
sudo bash install.sh --uninstall
```

> Windows 下请用 `python`（本机没有 `py`）；文本文件一律显式 `encoding="utf-8"`。

---

## 2. 项目是什么 / 不是什么

**是什么**

- 单机面板，管**本机上的多个 Minecraft 实例**（不是多机集群管理）
- 直接管理服务端**进程**与**文件**：控制台走 stdin/stdout，日志走环形缓冲 + WebSocket
- 所有能力都通过 `http://127.0.0.1:8100` 的 HTTP/WS 接口暴露，UI 只是它的一个客户端
- 数据就是一个 SQLite 库 + 实例目录，可用 `MC_DATA_DIR` 整体迁移

**不是什么**

- 不依赖任何 mod/插件才能工作（建筑导入的离线引擎连 RCON 都不需要）
- 不需要正版账号，不需要机器人（假人）进服
- 取不到的数据（比如没开 RCON 时的 TPS）就显示"不可用"，**不编数字**

---

## 3. 目录结构（每个文件干什么）

```
guanwang/mc/
├── README.md                    本文件
├── HANDOFF.md                   项目全貌 / 规格索引 / 环境坑（总入口）
├── PROGRESS.md                  最近一次会话做到哪、证据在哪、还欠什么
├── install.sh                   Linux 一键安装（/opt/mc-panel + systemd mc-panel）
│
├── backend/                     后端（FastAPI + SQLite）
│   ├── run.py                   启动入口：注入 .deps、读 data/config.json、默认 8100
│   ├── requirements.txt
│   ├── selftest.py              端到端自检（登录 / 401 / 403 / 生命周期 / 静态资源）
│   ├── .deps/                   本机依赖（pip --target 产物，**不随发布包走**）
│   ├── data/                    运行时数据（可用 MC_DATA_DIR 整体迁移）
│   │   ├── config.json          面板配置（端口 / 绑定 / 采样 / 下载源 / RCON 默认值…）
│   │   ├── mc.db                SQLite 主库（用户 / 实例 / 审计 / 指标 / 下载 / 计划任务）
│   │   ├── secret.key           会话签名密钥
│   │   ├── instances/           各实例工作目录（世界 / 插件 / 配置）
│   │   ├── backups/             实例备份 tar.gz
│   │   ├── buildwork/           建筑导入的工作区（上传 / 解包 / 样本）
│   │   ├── java/                面板下载的 Java 运行时
│   │   └── logs/                面板自身日志
│   ├── app/                     后端实现
│   │   ├── main.py              应用装配：挂路由、静态资源、CSP、限流、SPA 兜底
│   │   ├── config.py            路径与配置（DATA_DIR / INSTANCE_DIR / DEFAULTS）
│   │   ├── database.py          建表与迁移（ensure_column、实例归属与配额列）
│   │   ├── auth.py              口令哈希（PBKDF2-SHA256 600k）、令牌、ensure_admin_user
│   │   ├── permissions.py       **权限唯一出处**：四角色矩阵、require_perm / require_instance
│   │   ├── process_manager.py   实例进程生命周期、监控采样、控制台环形缓冲与广播
│   │   ├── serverctl.py         服务端目录操作（eula / java 探测 / 目录大小…）
│   │   ├── mcprops.py           server.properties 读写（保留注释 / 空行 / 未知键 / 键序）
│   │   ├── sources.py           服务端核心下载源适配（7 种 + 镜像降级 + 哈希）
│   │   ├── downloader.py        下载引擎（候选轮询 / 校验 / **断点续传** / 进度）
│   │   ├── javaruntime.py       Java 检测与从 Adoptium 下载
│   │   ├── rcon.py              RCON 客户端（取玩家列表 / 发命令 / 取 TPS）
│   │   ├── scheduler.py         计划任务（5 段 cron）后台调度
│   │   ├── audit.py             审计留痕
│   │   ├── pathguard.py         路径校验（realpath + commonpath，防穿越与符号链接逃逸）
│   │   ├── pluginsources.py     Modrinth / SpigotMC / CurseForge 插件源
│   │   ├── routers/             各功能域的 HTTP 与 WS 路由
│   │   │   ├── auth.py          登录 / 登出 / 当前用户
│   │   │   ├── instances.py     实例 CRUD、生命周期、控制台 WS、**仪表盘实时 WS**
│   │   │   ├── core.py          核心版本列表 / 构建列表 / 直链解析 / 可达性探测
│   │   │   ├── files.py         文件浏览 / 上传 / 下载 / 编辑 / 解压 / 重命名 / 删除
│   │   │   ├── config.py        server.properties 与 eula 的可视化读写
│   │   │   ├── players.py       白名单 / OP / 封禁 / 在线玩家 / 踢人
│   │   │   ├── plugins.py       插件与模组：上传 / 启停 / 删除 / 搜索安装
│   │   │   ├── backups.py       备份 / 还原 / 下载 / 删除
│   │   │   ├── cron.py          计划任务 CRUD 与执行历史
│   │   │   ├── logs.py          实例日志与应用日志
│   │   │   ├── build.py         建筑导入 12 条路由（上传 / 预检 / 导入 / 回滚 / 记录）
│   │   │   ├── users.py         用户管理 + `/api/me/permissions`
│   │   │   └── settings.py      面板设置与概览
│   │   └── build/               建筑导入引擎
│   │       ├── formats.py       格式解析（schem v1/v2/v3、schematic、nbt、litematic、zip）
│   │       ├── nbt.py           NBT 读写（含深度 / 数组长度 / 压缩炸弹防护）
│   │       ├── mapping.py       方块调色板映射与未映射统计
│   │       ├── plan.py          导入方案与预检报告（尺寸 / bbox / 区块数 / 警告）
│   │       ├── anvil.py         离线读写 Anvil region 文件（引擎 B）
│   │       ├── engines.py       两套引擎的实现与调度（插件+RCON / 离线写区块）
│   │       └── tasks.py         导入任务化（进度 / 取消 / 回滚记录）
│   └── tools/                   后端侧验收与运维脚本
│       ├── accounts_selftest.py 账号与权限规格 §8 全量验收（161 项）
│       ├── build_import_check.py 建筑导入专项验收（42 项，含 8 种恶意样本）
│       ├── build_selftest.py    建筑导入端到端（需真服务端，含引擎 B 写方块）
│       ├── build_make_sample.py 生成自检用建筑样本（含恶意样本）
│       ├── route_cross_check.py 前后端 URL 交叉核对（防"打了不存在的接口"）
│       ├── cpu_sample_check.py  验证 CPU 采样写法（首次调用必为 0 的坑）
│       ├── cpu_cross_check.py   CPU 采样与真实进程对照
│       ├── fetch_server_jar.py  下载 vanilla 服务端 jar 到自检期望位置
│       ├── reset_password.py    本地口令恢复（直接改库）
│       ├── dev_set_password.py  开发用口令设置
│       └── prep_reports.py      报告生成
│
├── frontend/dist/               前端产物，**直接被后端托管，无构建步骤**
│   ├── index.html               面板外壳（按顺序引入 css 与 js）
│   ├── doctor.html              浏览器自检页（打开即逐项体检）
│   ├── css/
│   │   ├── tokens.css           设计令牌：字号阶 / 间距 / 圆角 / 表面 / 文字 / 动效
│   │   ├── base.css             重置与基础排版
│   │   ├── components.css       组件样式（卡片 / 表格 / 徽标 / 弹窗 / 表单 / 版本列表…）
│   │   ├── theme-mono.css       单色主题皮肤（覆盖令牌：黑白灰 + 大圆角 + 内描边阴影）
│   │   ├── home.css             仪表盘布局（卡片网格 / 拖动排序 / 实时数值过渡）
│   │   ├── views.css            视图样式（紧凑列表 / 分组 / 趋势 / 状态色带）
│   │   └── bg.css               背景层（背景图 / 轮播 / 每图独立遮罩）
│   ├── js/
│   │   ├── theme-boot.js        首屏前应用主题与密度，避免闪白
│   │   ├── util.js              工具函数（转义 / 防抖 / 下载 / 时间格式化）
│   │   ├── api.js               统一请求层（错误归一：status/url/isAuth/isDenied/isNetwork）
│   │   ├── store.js             全局状态与定时器管理
│   │   ├── charts.js            SVG 图表（sparkline / 面积图 + 多系列灰阶配色）
│   │   ├── icons.js             内联 SVG 图标集
│   │   ├── view.js              **页面状态与错误边界唯一实现**（loading/error/denied/empty）
│   │   ├── viewmode.js          视图模式注册（有哪些视图 / 默认哪个 / 怎么持久化）
│   │   ├── roles.js             角色元信息
│   │   ├── permissions.js       前端权限（仅用于隐藏入口；后端才是边界）
│   │   ├── components.js        外壳布局 / 导航 / 弹窗 / 徽标 / 下载进度跟踪
│   │   ├── router.js            hash 路由（序号防竞态 + 异常兜底）
│   │   ├── build-api.js         建筑导入接口客户端（归一后端返回形状）
│   │   ├── main.js              路由注册与启动
│   │   └── pages/
│   │       ├── login.js         登录页
│   │       ├── dashboard.js     仪表盘（实时通道 + 卡片/紧凑/趋势三视图）
│   │       ├── instances.js     实例列表（卡片/紧凑/表格/分组四视图）
│   │       ├── create.js        新建实例向导（五步）
│   │       ├── instance_detail.js 实例详情 8 个 Tab
│   │       ├── settings.js      面板设置
│   │       ├── accounts.js      账号与权限管理
│   │       ├── audit.js         审计日志
│   │       └── build.js         建筑导入（上传 / 预检 / 坐标选择 / 任务 / 回滚）
│   └── site/                    **项目官网**（自述页，随面板一起提供）
│       ├── index.html           官网单页
│       ├── site.css             官网样式（复用面板设计令牌）
│       └── site.js              主题切换 / 入场动画 / 数字滚动 / 移动端菜单
│
├── tools/
│   ├── panel_doctor.py          命令行体检，结果写入体检报告
│   ├── dev_ui_panel.py          隔离数据目录的调试启动器
│   ├── ui-shots/                无头浏览器验收与诊断（见下）
│   └── _mcresearch/             早期研究用的脚手架（可忽略）
│
└── docs/
    ├── DESIGN.md                设计令牌表 / 组件清单 / 硬指标自查
    ├── API-CONTRACT.md          前后端接口契约
    ├── FEATURE-BUILDING-IMPORT.md  建筑导入规格
    ├── FEATURE-ACCOUNTS.md      账号与权限规格
    ├── RESEARCH-snowolf.md      界面调研（**顶部有使用边界说明**）
    └── evidence/                验收证据（自检日志、DOM 断言、截图）
```

### 服务控制（无 systemd 时）

包内自带 `panelctl`，适合 **systemd 用不了**的环境（例如加固过的机器把
`/etc/systemd/system` 设成了不可变 `chattr +i`）：

```bash
sudo bash /opt/mc-panel/panelctl start      # 启动（已启动则不重复启动）
sudo bash /opt/mc-panel/panelctl status     # 看进程 / 端口 / 健康检查
sudo bash /opt/mc-panel/panelctl restart
sudo bash /opt/mc-panel/panelctl log        # 跟踪日志
```

`install.sh --no-systemd` 会自动把它装好并尝试注册 crontab `@reboot` 自启。
⚠️ 若目标机的 crontab 也被加固锁了（`/var/spool/cron/crontabs` 带 `chattr +i`），
脚本会**如实报告"开机自启未注册"**，并打印可手动添加的那一行 —— 不要以为它静默成功了。

> 切用户一律用 `runuser`，不用 `sudo -u`：宝塔安全模块会**静默拦截** `sudo -u`
> （无输出、退出码 0），极难排查。

### 验收工具（`tools/ui-shots/`）

都是 Node + 无头 Edge（CDP），**读 DOM / 网络 / 控制台断言**，不靠截图判断：

| 脚本 | 验什么 |
|---|---|
| `page-audit.js` | 逐页走查：标题、文本量、console 异常、4xx |
| `role-audit.js` | 三种角色的菜单与越权页 |
| `user-path.js` | 模拟真实点击路径（不刷新页面导航 + F5） |
| `views-check.js` | 7 个视图是否都能渲染 |
| `live-check.js` | 仪表盘实时通道帧率 + 是否原地更新 |
| `live-change.js` | 活体：启停实例时页面是否自己变 |
| `download-all.js` | 7 种核心的自动下载端到端 |
| `wizard-download.js` | 创建向导的下载体验（进度条是否出现） |
| `build-check.js` | 建筑导入 Tab 是否真的接了后端 |
| `create-overlap.js` | **元素重叠检测**（步骤逐屏扫描） |
| `site-check.js` | 官网结构 / 锚点 / 主题 / 四档宽度 |
| `blank-repro.js` | "内容区空白"复现与定位 |
| `dom-check.js` / `ws-check.js` / `diag.js` 等 | 早期排查工具 |

---

## 4. 功能清单

**实例与运行**

- 生命周期：创建 / 启动 / 停止（先 stdin `stop` 优雅停，超时再 kill 进程树）/ 重启 / 强制结束；
  面板重启后靠 PID 文件 + 存活校验**识别已在运行的实例**并接管
- 崩溃检测与自动重启（指数退避，有上限）；崩溃时记录退出码 + 最后 50 行日志
- 控制台：stdout/stderr 落盘 `logs/latest.log` + 内存环形缓冲，WebSocket 实时推送，
  分级着色，命令历史
- 配置：`server.properties` 可视化编辑（**保留注释 / 空行 / 未知键 / 键顺序**）、
  `eula.txt` 一键接受、启动参数（内存 / JVM 参数 / nogui / RCON）
- 文件管理：浏览 / 上传 / 下载 / 重命名 / 删除 / 解压 / 新建目录 / 在线编辑；
  路径校验走 realpath + commonpath，穿越与符号链接逃逸一律 403
- 插件与模组：上传 / 列表 / 启用禁用（改扩展名）/ 删除；Modrinth 可搜索安装
- 玩家：白名单 / OP / 封禁 / IP 封禁（读写四个 json）、在线玩家（RCON `list` 或日志解析）、
  踢人 / 封禁 / 给 OP
- 备份与还原：tar.gz（默认排除 logs/cache/crash-reports）、保留份数轮转、
  下载 / 还原 / 删除；**还原前自动备份当前状态**
- 计划任务：5 段 cron（定时重启 / 备份 / 执行命令 / 广播）+ 执行历史
- 监控：按秒采样 CPU/内存/玩家落库，提供历史曲线；
  **TPS/MSPT 从日志或 RCON 解析，拿不到就标注"不可用"，不编数**

**服务端核心获取**

- 7 种：Vanilla（Mojang 清单）/ Paper（`fill.papermc.io/v3`）/ Purpur / Fabric / Quilt /
  Forge / NeoForge，各自解析版本列表 → 构建列表 → 下载直链
- 官方源优先、BMCLAPI 镜像自动降级、如实标注走的哪个源
- sha1 / sha256 / md5 校验 + jar 结构校验；**慢源支持断点续传**；进度带速率
- Java 运行时：检测系统 java（解析大版本）+ `JAVA_HOME` + 常见路径，
  可从 Adoptium 下载 17 / 21 / 25 到 `data/java/`

**建筑导入（无需机器人进服）**

- 格式：`.schem`（Sponge v1/v2/v3）、`.schematic`、`.nbt`、`.litematic`、含多建筑的 `.zip`
- 流程：上传 → **预检报告**（尺寸 / 方块总数 / bbox / 区块数 / 未映射方块 / 警告 / 推荐引擎）
  → 俯视小地图式坐标选择 → 任务进度与取消 → 一键回滚
- 两套引擎：**A 插件 + RCON**（实例运行中即可用，需装 WorldEdit 并开 RCON）；
  **B 离线写 Anvil 区块**（无插件依赖，必须先停实例）
- 安全：zip-slip / 绝对路径 / 符号链接 / zip 炸弹 / 成员数上限 / 深层 NBT /
  超大数组 / gzip 炸弹 —— 全部实测被拒，且不污染实例目录

**账号与权限**

- 四角色：超级管理员 / 管理员 / 普通用户 / 只读（`viewer` 权限集为空，默认拒绝）
- **普通用户只看得到自己的实例**；含归属过滤与逐用户配额
- 后端是唯一边界：每个路由挂 `require_perm` / `require_instance`；前端隐藏只是体验
- 口令 PBKDF2-SHA256 / 600000 次迭代、≥12 位且至少 3 类字符、失败锁定、会话令牌
- 禁止自我降权 / 自我停用 / 自我删除 / 删掉最后一个超管；本地口令恢复通道

**界面**

- **仪表盘**（首屏）：实时通道每秒一帧，卡片**原地更新**（不整页重绘）；
  三视图：卡片 / 紧凑 / 趋势；卡片可拖动排序、可置顶；断线退避重连，状态如实显示
- **实例列表**：四视图：卡片 / 紧凑 / 表格 / 分组（按运行状态归堆，异常排最前）
- 实例详情 8 个 Tab：控制台 / 文件 / 配置 / 玩家 / 建筑 / 备份 / 计划任务 / 设置
- 其余页面：登录、新建向导（五步）、设置、账号管理、审计日志
- 设计：单色主题皮肤（黑白灰 + 等比例大圆角 + 多层阴影带 1px 内描边，
  成功态走灰度）；主题三态（跟随系统 / 暗色 / 亮色）；信息密度两档；背景层（多图 + 轮播）
- 无障碍与体验：关键文字对比度过 WCAG AA；支持 `prefers-reduced-motion`；
  实时数值变化只上浮淡入 220ms（不触发布局）

**官网**（`frontend/dist/site/`）

- 自述页，随面板一起提供：`http://127.0.0.1:8100/site/`
- 复用面板自己的设计令牌；内容含 Hero（界面示意图按真实布局复刻）、功能、核心支持表、
  快速开始、设计系统、FAQ
- **要改官网就改这三个文件**：`site/index.html`、`site/site.css`、`site/site.js`

---

## 5. 自检与验收

```bash
# 后端规格 §8 全量验收（账号与权限，161 项；会起独立数据目录的测试面板）
cd backend && python tools/accounts_selftest.py

# 建筑导入专项（42 项，含 8 种恶意样本；不依赖 54MB 服务端 jar）
python tools/build_import_check.py

# 前后端 URL 交叉核对（防"前端打了不存在的接口"）
python tools/route_cross_check.py

# 浏览器侧验收（需面板在跑）
node ../tools/ui-shots/page-audit.js http://127.0.0.1:8100 '<口令>'
node ../tools/ui-shots/views-check.js  http://127.0.0.1:8100 '<口令>'
node ../tools/ui-shots/site-check.js   http://127.0.0.1:8100

# 体检
python -m tools.panel_doctor        # 或浏览器打开 /doctor.html
```

当前基线：**后端规格 §8 161 项 / 0 失败**；**14 页走查 0/14 有问题**。

---

## 6. 权限与安全模型（改代码前必读）

- **后端才是边界**。前端 `Perm.has()` 只用来隐藏菜单/按钮；任何写接口都必须过
  `require_perm(user, '...')` 或 `require_instance(user, iid, '...')`。
- `require_instance(user, iid)` **不带权限点 = 只要求"能看这个实例"**：
  管理员看全部、属主看自己、`viewer` 可读。写操作必须显式传权限点。
- **不要用 `log.view` 当"能看所有实例"的通行证** —— 普通用户权限集里也有它，
  会被读成越权。归属判定只能靠 `owner_id` / 管理员 / 角色。
- 停用账号要在**登录那一关**就拒（`users.status`）；只查 `resolve_token`
  的话，被停用的人换个新 token 又进来了。
- WebSocket 要自己做同一套校验（它绕过 FastAPI 依赖注入），用 `try_get_user()`
  而不是直接调依赖函数。
- 新增角色/权限概念时**先改 `permissions.py` 一处**，不要在路由里散写 if。

---

## 7. 许可

本项目采用 **[GNU AGPL-3.0-or-later](https://www.gnu.org/licenses/agpl-3.0.html)**
（GNU Affero 通用公共许可证 第 3 版或更新版本）授权。协议全文见 [`LICENSE`](LICENSE)。

```
Copyright (C) 2026  ruituo-mc-panel contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
```

**你可以**：自由使用、修改、分发，**包括商业使用**。

**你必须**：
- 分发时**附上本协议全文**并**开放对应源码**（copyleft）
- 你的修改同样以 AGPL 授权
- **如果你把它作为网络服务提供给他人**（第 13 条）——
  例如自己改一版挂到公网给人用 —— **必须向这些用户提供完整源码的获取方式**

> 第 13 条就是 AGPL 与 GPL 的唯一实质区别，也是它适合"自托管面板 / 网络服务"
> 这类项目的原因：**想拿它做闭源 SaaS 是不行的。**

**你可以**保留自己的版权，也可以为你的修改选择任何兼容的协议；
但分发时必须满足上述条件。

Copyright (C) 2026 ruituo-mc-panel contributors

---

## 8. 相关文档

- [`HANDOFF.md`](HANDOFF.md) —— 项目全貌、规格索引、环境坑（**总入口**）
- [`PROGRESS.md`](PROGRESS.md) —— 最近一次会话做到哪、证据在哪、还欠什么
- [`docs/DESIGN.md`](docs/DESIGN.md) —— 设计令牌表、组件清单
- [`docs/API-CONTRACT.md`](docs/API-CONTRACT.md) —— 前后端接口契约
- [`docs/FEATURE-BUILDING-IMPORT.md`](docs/FEATURE-BUILDING-IMPORT.md) —— 建筑导入规格
- [`docs/FEATURE-ACCOUNTS.md`](docs/FEATURE-ACCOUNTS.md) —— 账号与权限规格
- [`docs/RESEARCH-snowolf.md`](docs/RESEARCH-snowolf.md) —— 界面调研
  （**顶部有使用边界：只作事实参考，禁止复制对方源码/组件/工具函数/专有命名**）

---

## 9. 已知限制

- **Quilt 的核心下载很慢**：其元数据服务与制品仓库实测响应极慢
  （`maven.quiltmc.org` 约几十 KB/s）。面板已改为读 maven 自己的元数据并支持断点续传，
  **能下完但可能要几分钟**；追求省心建议用 Fabric。
- 当前 5 个示例实例是历史迁移过来的，`jar` 还没下载到本地，所以**启动会提示 jar 缺失**；
  到实例详情的核心设置里重新下载即可。
- 默认监听 `0.0.0.0`（暴露在局域网）。对外提供前请改掉默认口令并限制来源。
- `instance_detail.js` 与 `pages/build.js` 尚未完全迁到 `View.boot` 契约，
  内部仍是旧写法（功能正常，异常也已可见）。
