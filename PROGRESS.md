# PROGRESS · 芮拓MC面板（当前进度快照）

> 总入口仍是 [`HANDOFF.md`](HANDOFF.md)（项目全貌、规格索引、环境坑）。
> 本文件记录**最近一次会话真实做到哪、证据在哪、还欠什么**。
> 更新说明：上一版是"用户叫停全部并行工作时"的现场记录；本次会话把**账号与权限后端**
> 从零做完并端到端验收通过，因此整份重写，避免再拿过期状态当依据。

## 0. 本次会话做了什么（一句话）

**把「账号与权限」这条主线做完了**：四角色权限矩阵 + 实例归属与配额 + 用户管理 API +
默认账号=超级管理员（含老库迁移）+ 全部既有路由的 403 兜底 + 前端从 mock 切到真实接口。
两份验收脚本（后端 160 项、前端 DOM 24 项）**全通过**，原始输出见 `docs/evidence/`。

## 1. 本次新增/改动的文件

### 新增

| 文件 | 作用 |
|---|---|
| `backend/app/permissions.py` | **权限唯一裁定处**：角色矩阵、`require_perm` / `require_instance`、实例归属、配额逐项校验、`/api/me/permissions` 载荷 |
| `backend/app/routers/users.py` | 用户管理 API 全套（列表/建号/改角色状态配额/重置口令/强制下线/删除）+ `GET /api/me/permissions` |
| `backend/tools/accounts_selftest.py` | **规格 §8 六条验收**端到端脚本（起真服务、打真接口、含老库迁移用例） |
| `backend/tools/reset_password.py` | 本地口令恢复通道（规格 §4.5 要求的唯一后门，只走本地） |
| `frontend/dist/js/permissions.js` | 前端 `Perm` 真实实现（对接 `/api/me/permissions`，替换原 mock） |
| `tools/dev_ui_panel.py` | 在**独立数据目录**上起面板的启动器（UI 验收用，不动 `backend/data`） |
| `tools/ui-shots/dom-check.js` | 无头浏览器 **DOM 断言**（截图不可用时的前端验收手段，见 §4） |
| `docs/evidence/` | 验收原始输出（自检日志 + DOM 断言 JSON） |

### 改动（要点）

- `database.py`：`users` 增配额列、`instances` 增 `owner_id`；`ensure_column()` / `migrate()` 幂等迁移。
- `auth.py`：首启建号落成 **super_admin** + 审计 `warn` 级；**停用账号拒绝登录**（实测修掉的洞）；
  新增 `reset_password()` / `kick_user()`。
- `main.py`：挂载 `users` + `build` 两个 router（**建筑导入路由之前一直没挂上**）；路由计数递归统计
  （FastAPI 0.142 的 `_IncludedRouter` 惰性包装会把"挂载成功"误判成 0 条）。
- `config.py` / `settings.py`：`login_rate_limit`、`api_rate_limit` 可配（默认 10 / 1800，运行时读配置）。
- 既有各路由（instances / files / config / players / backups / cron / plugins / logs / settings / core / build）：
  **逐个挂上权限校验与归属过滤**；`instances` 列表按归属返回；`logs` 全局日志要 `audit.view`。
- `router.js`：**重写 resolve/render**，修掉两个真实竞态（详见 §4）。
- `pages/accounts.js`：从 mock 改为真实 `API.*` 调用；删除 `mock-accounts.js`（避免"改假数据以为生效"）。
- `tools/ui-shots/shot.js`：角色视角截图改用**真实角色账号 token**（原来靠 `MockAccounts.setMe` 演）。

## 2. 明确做到什么程度（都有原始证据）

| 项 | 证据 |
|---|---|
| ✅ **规格 §8 六条验收全通过** | `docs/evidence/selfcheck-accounts.log`：**通过 161 项，失败 0 项** |
| ✅ 默认账号=超级管理员，初始口令只在终端打印一次 | 同上 §8.1：脚本从子进程 stdout 抓到口令并用它成功登录，`/api/me/permissions` 返回 15 个权限点；全目录扫描确认明文未落盘 |
| ✅ 越权一律 403（普通用户 vs 他人实例/系统设置/用户管理/审计/面板日志） | 同上 §8.2：逐条打接口核 403；自己的实例逐条核 200；`viewer` 能读不能写 |
| ✅ 配额超限 403 + 中文原因 | 同上 §8.3：内存超配额、实例数超配额、`allow_build_import=false` 三种都验过（后者走真 multipart 上传） |
| ✅ 重置口令：旧 token 立即失效、新口令可登录、只回显一次、不落库 | 同上 §8.4 |
| ✅ 唯一超管不可降级/停用/删除，自删/自踢被拒 | 同上 §8.5 |
| ✅ 审计含谁/对谁/动作/IP，用户管理类为 `warn`，**审计里无明文口令** | 同上 §8.6 |
| ✅ **老库迁移**（角色体系之前的库升级上来仍可管） | 同上 §13：配额列与 `owner_id` 自动补齐、老 admin 提升为超管、孤儿实例划给初始超管 |
| ✅ 前端权限体系联通真实接口 | `docs/evidence/dom-check.json`：**DOM 断言 24 项全通过**，零 console 异常 |
| ✅ 本地口令恢复通道可用 | 实测：随机/指定口令都能重置并 `verify_password` 通过、弱口令被拒、明文不入库 |

**权限矩阵（规格 §2，后端逐路由兜底）**

| 角色 | 权限点 |
|---|---|
| `super_admin` | 全 15 项 |
| `admin` | 13 项（无 `user.manage`、无 `system.settings`） |
| `user` | 10 项（仅自己实例；`instance.settings_network` 除外） |
| `viewer` | 空集（**默认拒绝**；"能读"由 `can_view_instance()` 按角色放行） |

> `instance.settings_network`（改端口/绑定/settings 类）在命名权限点里只有这一个，
> 规格 §2「普通用户不可改实例设置」与 §2 权限点列表由此统一，没有新增第 16 个权限点。

## 3. 本次实测修掉的 4 个真 bug（都有复现记录）

1. **停用账号仍能登录**：登录路由只看口令哈希，没查 `status`。`resolve_token` 拦住了已有会话，
   但被停用的人当场换个新 token 又进来了 —— 停用形同虚设。现返回 403 +「账号已被停用」。
2. **`log.view` 被当成"能看所有实例"的通行证**：普通用户权限集里也有 `log.view`，
   于是能读到**他人实例**的详情/文件列表/备份列表。现改为按"归属 or 管理员 or 只读角色"判定。
3. **WebSocket 控制台没做实例级校验**：任何登录用户都能连**别人实例**的控制台看历史、下命令。
   现连接时校验归属、无 `instance.command` 的连接标记 `readonly` 并拒绝下发。
4. **`main.py` 从来没挂载 `routers/build.py`**：建筑导入的 13 条路由此前**根本不存在**
   （前端调就是 404）。现已挂载，API 路由总数 125 条。

## 4. 前端的两个真实竞态（`router.js`，DOM 验收抓到的）

> 这两个是**真 bug**，不是测试问题：换账号/首次进入时页面会停在登录页或按旧身份渲染。

1. **异步装载期间 hash 变了**：`#/accounts` →（未登录被判）`#/login` →（补上 token）回 `#/accounts`，
   而 `resolve()` 在 await 之前就把 hash 解析好了，等完再渲染时**用旧路径把新页面盖掉**。
   现在渲染时**重新取当前 hash**，并用自增序号保证只有最新一次 resolve 能画页面。
2. **布尔闸门 `_permReady` 会把路由永久卡死**：某条路径置位后没复位（401/网络抖动）→ 之后
   **再也不渲染任何页面**（白屏/停在登录页）。现在改用 Promise + 8s 超时兜底，失败也一定渲染。

## 5. 部署到本机时又挖出 11 个真 bug（"面板没法用"的根因）

> 教训：**API 通了 ≠ 面板能用**。这一轮我先用接口和 DOM 断言验的，真正"用一遍"
> （登录 → 点每个菜单 → 进实例详情 → 换角色看）才发现下面这些。

1. **控制台 WebSocket 握手直接 500**：`auth._bearer()` 对 `authorization` 直接调
   `.startswith()`，但 `get_current_user` 是 FastAPI 依赖 —— 从 WebSocket 路径直接调用它时，
   拿到的是 `Header(...)` 的**哨兵对象**而不是字符串 → `AttributeError: 'Header' object has no attribute 'startswith'`。
   修复：`_bearer` 做类型判断；新增 `_ws_bearer()` 专门从 `?token=` / 请求头取令牌；
   `try_get_user` 不再转发哨兵对象。
2. **控制台订阅队列不可哈希**：`process_manager` 里 `self.subscribers = set()`，
   而订阅队列是 `collections.deque` → `TypeError: unhashable type: 'collections.deque'`，
   握手成功后立刻崩（前端只看到 **1006**，控制台永远空白）。修复：改用 `list`。
   > 1+2 叠加 = **每个实例详情页的控制台都连不上**。
3. **非管理员点「设置」只得到一个近乎空白的页面**：`settings.js` 的 `catch` 写的是
   `C.empty('warn','读取配置失败')` —— 后端对普通用户/只读返回 **403 system.settings**，
   于是页面既不是设计过的「权限不足」空状态、也不是能用的设置页，用户看到的就是
   "框架在、内容区空了"。修复：新增 `Perm.denyIfForbidden(err, app, {perm})`，
   **403 一律画出「权限不足」页**（规格 §6 的要求），并接到 settings / instances / audit 三处。
4. **菜单里挂着点不到的入口**：`components.js` 的 NAV 里「设置」「审计日志」**没挂任何门禁**，
   普通用户/只读看得见、点进去必然 403。修复：按后端一致的门禁标注
   （设置→`flags.system_settings`、审计→`flags.view_audit`、账号→`user.manage`、新建→`instance.create`），
   整组无权限时连组标题一起不渲染。现在只读只剩「实例列表」、普通用户剩「实例列表 + 新建实例」。
5. **删掉 `mock-accounts.js` 让缓存的旧页面 404**：浏览器缓存着旧 `index.html` 时会去请求这个
   已删除的文件，404 会断掉 JS 链。修复：给旧资源名加**兼容路由**映射到 `permissions.js`。
   ⚠️ 该路由必须注册在 `app.mount('/js', ...)` **之前**，否则会被 StaticFiles 先截获（第一版就写错了顺序）。
6. **自家 CSP 把内联脚本全拦了**（`script-src 'self'`，缺 `'unsafe-inline'`）：面板是
   **无构建步骤的原生前端**，页面模板里有内联 `onclick` 与内联 `<script>`
   （实测 8 处内联事件处理器；`doctor.html` 是整段内联脚本）。被 CSP 拦掉时浏览器
   **只在 console 报 violation、页面上毫无提示** —— 表现为"点了没反应"、
   自检页永远停在"正在检查…"。修复：`script-src 'self' 'unsafe-inline' 'unsafe-eval'`
   （自托管运维面板，脚本全由本进程发出的 HTML 内联，无第三方注入面，属可接受取舍）。

7. **实例列表"静默空白"**（用户最终确认的现象：**有标题和统计数字，但列表是空的**）：
   `instances.js` 的 `render()` 先画页头与四个统计卡、**再**渲染列表。列表渲染链路上任何一处
   抛异常（卡片里的 `C.ring` / `Chart.sparkline` / 单条脏数据 …），异常会被 `load()` 的
   `.catch` 吞掉：于是**统计数据在、列表空着、页面上一个字的报错都没有**。
   修复（不管触发点是什么都能兜住）：
   - `render()` 包 try/catch，出错时把**异常原文 + 堆栈**画进列表区，并给「重试 / 用简化视图」两个按钮；
   - `safeCard()`：单张卡片失败只退化那一张，其余照常；
   - `renderPlain()`：只用基础元素列出实例的**简化视图**，不碰任何装饰性组件；
   - `load()` 末尾自检：数据拿到了、列表里却仍是"正在读取…"占位 → 自动退到简化视图。
   > 这一条是最难查的，因为**服务端日志全是 200**（接口、静态资源都正常），
   > 只有把"用户看到什么"问清楚才定位到。

8. **背景层 `#mc-bg` 把整页内容盖住了**（用户截图："顶栏在、侧栏与内容区整片空白"）：
   `bg.css` 顶部注释写的是 `z-index:-1`（待在内容背后），**代码里却是 `z-index: 0`
   且 `background: var(--bg-base)` 不透明底色**。`position: fixed` + `z-index: 0`
   会建立层叠上下文、盖在普通文档流内容之上 —— 于是只有 sticky 的顶栏（自带层叠上下文）
   正常显示，侧栏与内容区被一层不透明膜糊掉。
   修复：`z-index: -1` + `background: transparent`，底色交给 `html, body` 与 `.bg-item` 那套；
   与文件头注释的意图恢复一致。
   > 定位手段：`Page.captureScreenshot` 在不同视口下时好时坏（同一页面 17 KB 空白 / 93 KB 有内容），
   > 差异不在代码而在**绘制层叠**；`elementsFromPoint` 查不到它（`pointer-events:none` 仍参与绘制），
   > 最后靠"把候选层逐个 `display:none` 再截图比对字节数"锁定。

10. **"自动下载不对"**（创建实例自动下核心的整条链路）：

    排查时做了**前后端 URL 交叉核对**（新增 `backend/tools/route_cross_check.py`，
    把前端 95 处 API 路径与后端路由表机器比对）—— 结果是前端路径**全对**，
    问题都在别处：

    1. **进度条不显示 + 提示自相矛盾**。后端创建响应的 `download.ok` 语义是
       "已下载完成"（`ok = state=='done'`），而下载是异步的、创建那一刻必然还在
       running → `ok:false`；前端却把 `ok:false` 当成"下载没启动"，于是
       **不渲染进度条**，还打出"核心下载未开始：核心正在后台下载中（0%）"这种话。
       修：`ok` 改为"任务被接受"（恒 true 当启动成功），是否下完看 `state`；
       前端改看 `task_id` 判断启动。实测修复后进度条实时 57.3% · 7.34 MB/s。
    2. **Quilt 装不上**（`maven.quiltmc.org` 8.5MB 慢到几十 KB/s + 元数据陈旧）：
       - BMCLAPI 的 quilt-meta 只到 **0.9.1**（官方已 0.15.1，落后 6 个大版本），
         而 0.9.1 的 jar 在 maven 上**下不动**；旧代码"先成功先返回"拿到镜像的过时
         清单就选了它 → 注定失败。修：installer 清单改读 **maven 自己的
         `maven-metadata.xml`**（2 秒、34 版本、与 jar 同源）。
       - 官方版本化 loader 接口**稳定 89 秒**，解析整个拖到 ~90s；改走镜像的全量
         loader 清单（秒回），解析降到 **3.3s**。
       - sha1 只为**选中的那个版本**拉一次边车文件（34 个全拉要 4 分多钟）。
    3. **慢源/停滞源一停就整体失败**。下载循环"单次 read 45s 超时"拦不住慢速滴数据，
       一停就抛错。修：加**断点续传**（`Range` + 增量哈希延续）+ 每候选**总时长预算**
       600s。实测 Quilt 续传 0→256KB→512KB 全程**递增、0 次重来**（`MC_DL_CAND_BUDGET` 可调）。
    4. **5 个残留实例路径指向旧项目目录**（`D:\DeepSeekHarness\ops-panel\…`），
       全部启不起来（"服务端 jar 不存在"）。已批量改到当前数据目录。

    回归（`tools/ui-shots/download-all.js`，7 个核心）：**7/7 可正常自动下载**；
    解析速度 paper 1.2s / quilt 3.3s / fabric 3.3s / vanilla 2.2s。

11. **新建实例向导：版本选不上、按钮与列表重叠**（用户："只能手动填写，没法在列表里选，之后有点按钮都重合了"）：

    新增 `tools/ui-shots/create-overlap.js`（逐步扫描元素矩形，重叠面积 > 30% 即报警）后定位到**两个独立缺陷**：

    1. **滚动列表溢出、压住下方控件**。`.version-list` 只写了 `max-height: 320px`，
       而它作为 grid/flex 项目会被行高**拉伸**，`max-height` 形同虚设 ——
       30~200 个版本项直接盖在「手填版本号」输入框和上一步/下一步按钮上
       （实测重叠率 **65%~85%**，`1.21.4release ⨯ 上一步` 最严重）。
       修：改成固定 `height` + `min-height: 0`，并给 `.grid > *` 补 `min-width/min-height: 0`。
    2. **构建列表压根没渲染成可选项**。`loadBuilds()` 只把构建当文字显示
       （"已选构建：143"），用户当然"没法在列表里选"。修：渲染成可点列表（`data-build`），
       只显示最近 40 个（有的源有几百个）。
    3. 顺带修的：`.grid.cols-2` 两列会把输入框压到溢出（撑破到相邻列，交叠 14px）→
       改成 `minmax(340px, 1fr)` 自适应，撑不下自动掉单列；
       选中态在单色主题下只有 8% 透明度、**看着像没选中** → 加左侧竖条 + 更实的底色 + 字重；
       版本列表滚动位置不再被"选一次就整页重渲染"清掉；新增"当前选择"常驻提示条。

    验证（修正检测器误报后：**滚动容器里被裁掉的项目不算重叠**）：
    5 个步骤 **全部无重叠**，14 页走查 0/14 问题。

**另外加了两层"自曝"机制**（因为上述问题全都是"静默的失败"）：
- `View.showError()`（重构后唯一出处，见 §7）：页面渲染完 1.5 秒后若内容区仍为空、
  或权限接口没拿到，就把**当前页面 / 角色 / 令牌长度 / 各接口状态 / 错误原文 + 堆栈**画在内容区。
- **`/doctor.html`（浏览器打开即自检）** + `tools/panel_doctor.py`（命令行体检）：逐项检查端口、首页、
  11 个静态资源（含 Content-Type）、登录、6 个关键接口。**遇到"空白"先让用户打开
  `http://127.0.0.1:8100/doctor.html`**，一眼分清是服务问题还是浏览器问题。

## 6. 前端架构重构 + 建筑导入接通真实接口 + 单色主题与仪表盘布局（本轮）

### 6.1 为什么重构
7 个 bug 里 **5 个是"静默失败"**：错误处理散落在 4 个页面（`catch` 合计 80+ 处、
`innerHTML` 60+ 处），`instance_detail.js` 长到 1135 行。补丁式修法已经明显不划算，
所以把**出错路径**收口成一套基础设施，行为不再依赖"每个页面记得写对"。

### 6.2 新增的基础设施

| 文件 | 职责 |
|---|---|
| `js/view.js` | **页面状态与错误边界的唯一实现**：`loading/error/denied` 三种态、`describe(err)` 把 401/403/404/429/5xx/网络错误各自归一成人话与下一步、`boot(app,{run})` 包住页面初始化（同步异常也变可读错误卡）、`installGlobalHandlers()` |
| `js/roles.js` | 角色元信息唯一出处（原来散在已删除的 mock-accounts.js 与各处），后端返回的 `roles` 会合并进来 |
| `js/build-api.js` | **建筑导入真实接口客户端**（替换 `mock-build.js`），把后端形状归一成页面已渲染的字段（`status`→`state`、`progress`→`written_chunks`/`total_chunks`） |
| `js/api.js`（重写） | 错误对象统一形状：`{message,status,url,method,data,isAuth,isDenied,isNetwork}`；网络异常也归一；401 清令牌回登录页 |
| `js/permissions.js`（重写） | 只做权限；403 统一出口 `denyIfForbidden()` 转交 `View.denied()`；删掉旧的诊断 UI |
| `js/router.js`（重写） | handler 异常统一走 `View.showError`；保留两个已修的竞态对策（序号渲染 + 每次强制重取权限） |
| `css/snowolf.css` | **Snowolf 皮肤**：只覆盖 token，去掉即可回退 |

`mock-accounts.js`、`mock-build.js` **已删除**（避免"改假数据以为生效"）。
旧资源名加了兼容路由（`/js/mock-accounts.js` → `permissions.js`），缓存旧 index.html 的浏览器不会断链。

### 6.3 建筑导入：从 mock 接通到真实后端（**功能没丢，而且第一次真的能用**）

后端 12 条路由原本就齐备，但前端**一直用 MockBuild**（等于功能没打通）。现在：

- `GET /api/build/formats` → 真实格式表/上限/引擎说明（页面上限显示 **512.0 MB** 来自后端）
- `POST …/build/upload` → 真实上传（带进度）
- `POST …/build/preview` → 真实预检报告（`size/blocks_total/bbox/palette_unmapped/chunks_touched/engine_recommended/warnings`）
- `POST …/build/import` → 异步任务 + 轮询 `GET …/tasks/{id}`
- `POST …/rollback/{id}`、`POST …/tasks/{id}/cancel`、`GET …/tasks`（导入记录）

**浏览器内实测**（`tools/ui-shots/build-check.js`）：拖入真实 `sample_v3.schem` →
`POST upload` → 自动 `POST preview`，页面显示"575 B"，格式表/导入记录/引擎信息全部为真实数据，**零 console 异常**。

**后端专项验收**（新写 `backend/tools/build_import_check.py`，不依赖 54 MB 服务端 jar）：**42 项通过**，
含 7 种格式上传+预检、越界/超高/不存在 upload_id 被拒、**8 种恶意样本全部被精确拒绝**
（zip-slip / 绝对路径 / 符号链接 / zip 炸弹 / 600 成员 / 深层 NBT / 超大数组 / gzip 炸弹）且实例目录未被污染、
审计留痕（`build.import.*` 为 warn 级）、面板进程存活。
> 未覆盖：引擎 B 真写方块与回滚 sha256 —— 离线写 Anvil **只对已存在的区块有效**，
> 需要先真启动一次服务端生成世界。jar 已下载到 `backend/data/buildwork/server-1.21.4.jar`，
> 跑 `python tools/build_selftest.py` 即可补上这一段（本轮未跑完）。

### 6.4 单色主题（`css/theme-mono.css`）

数值取自 `docs/RESEARCH-snowolf.md` §2.2/§2.3（**源码实测**，不是猜的）。浏览器内实测生效值：

| 项 | 值 |
|---|---|
| 深色基底 | `--bg-base #0a0a0a` / `--surface-1 #141414`（body 实测 `rgb(10,10,10)`） |
| 品牌色 | `--accent #f5f5f5`（**单色系统：品牌色＝浅灰白，不是彩色**） |
| 文字 | `#f5f5f5 / #a3a3a3 / #525252` |
| 成功色 | `--success #a3a3a3`（**Snowolf 的 success 映射到灰，不映射绿**） |
| 圆角 | `r-sm 12 / r-md 16 / r-lg 20 / r-xl 24`（等比例大圆角） |
| 阴影 | 多层叠加 + 末尾 **1px 内描边**：实测卡片 `rgba(0,0,0,.6) 0 1px 2px, rgba(255,255,255,.05) 0 0 0 1px` |
| 动效 | 150 / 200 / 300ms，`cubic-bezier(.4,0,.2,1)` |

主题按钮改为 **跟随系统 / 暗色 / 亮色** 三态循环（`auto` 由 `prefers-color-scheme` 决定明暗）；
旧的 `darkgold` / `lightgold` 与彩色变体仍在，设置页可选。**只要删掉 `snowolf.css` 那个 `<link>` 就回到原来的金色体系**，组件代码一行不用改。

### 6.5 仪表盘布局（`#/dashboard` + `css/home.css`）

调研原文（官方文档 + 源码实测）：「**首屏 = 仪表盘（Home）**：多实例**卡片网格**…
服务器卡片显示状态、CPU、内存和冲突提示……**可拖动排序或置顶**常用服务器」；
「**顶栏**：含…**服务器下拉菜单**」；「侧栏 + 顶栏 + **服务器子导航**」。

| Snowolf 结构 | 本面板现状 |
|---|---|
| 首屏 = 仪表盘（多实例卡片网格） | ✅ **新增 `#/dashboard`，登录后默认落地页**：主机资源 4 指标（CPU/内存/磁盘/实例数）+ 实例卡片网格 |
| 卡片显示 状态 / CPU / 内存 | ✅ 运行指示环 + CPU/内存/玩家/TPS 四指标 + **CPU sparkline** + 状态徽标 |
| 卡片**可拖动排序 / 置顶** | ✅ HTML5 拖放排序 + 图钉置顶，两组分别持久化（`mc_dash_order` / `mc_dash_pins`） |
| 顶栏**服务器下拉菜单** | ✅ 顶栏快速切换器（跳该实例控制台）；实例数据到了才填充，避免空壳 |
| 侧栏 + 顶栏 + 服务器子导航 | ✅ 侧栏新增「概览 / 仪表盘」分组；顶栏＝面包屑+切换器+时钟+角色徽标+操作；实例详情 8 个子 Tab |

新增：`js/pages/dashboard.js`、`css/dashboard.css`；`components.js` 增 `fillServerSwitch()`。
**与「实例列表」的分工**：仪表盘＝扫一眼全局 + 常用置顶 + 快捷启停；实例列表＝完整字段、搜索、表格视图、批量操作。

浏览器实测：落地页 `#/dashboard`、导航 6 项、主机 4 卡、5 张实例卡（含 sparkline）；
点图钉后分组变「置顶 / 全部实例」且 `localStorage.mc_dash_pins=[1]`；
顶栏切换器 6 个选项、切到实例 2 → `#/instances/2/console`（8 个 Tab）——**零 console 异常**。

### 6.6 合规边界复核（**已核实：代码里没有对方任何东西**）

> 用户明确要求：**不能直接复制对方项目独有的业务代码、封装好的工具函数、专属逻辑。**
> 已按此逐项核实，并做了名称中性化整改。

**核实结果（`frontend/` + `backend/` 全量扫描）**

| 检查项 | 结果 |
|---|---|
| 第三方源码目录（`snowolf/`、`vendor/`、`third_party/`、`src/components/`…） | **一个都没有** |
| 对方专有组件/模块名（`GlowCard` `ServerSubNav` `DynamicIsland` `MetallicPaint` `ThickSlider` `SegmentControl` `XTermConsole` …） | **0 命中** |
| 对方专有桌面能力标识（`apply_vibrancy` / `apply_acrylic` / `Snowolf Cloud`） | **0 命中** |
| 被 clone 进来的仓库 | 非 git 仓库，不存在 |
| 代码里出现对方产品名的位置 | **已清零**（原只在注释与一个 CSS 文件名里，现全部中性化） |

**实际只吸收了两类内容，都不是代码**

1. **无著作性的通用设计参数**：颜色字面值（中性灰阶本身即 Tailwind 标准阶）、圆角/间距尺寸阶、
   缓动曲线与动效时长 —— 这些是数值事实。
2. **通用交互范式**：侧栏 + 顶栏 + 卡片网格首屏、卡片可拖动/可置顶。这类范式在 Vercel / Linear
   与各家后台里普遍存在，属风格取向，不是某产品的独创表达。

**我方实现是独立原创**：对方是 React + `src/components/*`，我们是原生 JS + `pages/*.js`；
选择器（`.inst-card` `.dash-card` `.stat` `.ring` …）与全部逻辑都是本项目原有代码，结构上无对应关系。

**已做的整改**

- 文件重命名：`css/snowolf.css` → **`css/theme-mono.css`**、`css/dashboard.css` → **`css/home.css`**
  （不把竞品名挂进本产品资产）
- 注释全部改为实现性描述（"单色基底 / 等比例大圆角 / 多层阴影 + 1px 内描边 / 成功态用灰度"），
  不再以"某产品皮肤"的名义叙述
- `docs/RESEARCH-snowolf.md` 顶部**新增《使用边界》**：明确它是事实性调研、**不是取材仓库**；
  禁止复制其源码/组件/工具函数/专有命名/素材，只允许吸收通用设计参数与通用范式

### 6.7 仪表盘实时更新（WebSocket 推送 + 原地增量更新）

**原来的问题**：仪表盘用 `setInterval(load, 5000)` **整页重绘** —— 既不够实时（5 秒），
又会打断正在进行的拖动排序、闪掉输入框焦点、把 sparkline 历史重置回一个点。

**改法（两端各一半）**

后端新增**一条全局推送通道** `GET /api/dashboard/ws`（`instances.live_router`，
前缀 `/api/dashboard` 独立于 `/api/instances/{iid}`，避免 `dashboard` 被 `{iid}` 先匹配成
"不是整数"报 422）：

- 每 **2 秒**推一帧 `{type:'live', ts, instances[], host, counts}`
- 实例范围走 `_visible_rows(user)`，**与 HTTP 列表同一口径**（超管/管理员=全部，其余=自己名下）
  —— 否则会把别人的实例状态推给普通用户；主机负载只给管理员
- 只推**纯内存可读**字段（`status/pid/running/uptime/cpu/mem_mb/players/tps/mspt`）；
  `dir_size`、`eula` 这类要访问磁盘的**故意不推**，仍由 HTTP 负责（否则每 2 秒给每个实例扫一遍目录）
- 不用"每实例一条 WS"：仪表盘可能同时看几十个实例，逐实例建连会把浏览器连接数和服务端任务数打满；
  与按实例的 `/console/ws`（要日志流）是不同用途

前端 `pages/dashboard.js` 改为**原地增量更新**：

- 首帧仍走 HTTP（`core_label` / `port` / `memory_mb` / `auto_restart` 等字段只有 HTTP 有），
  之后交给 WS 合并（`Object.assign`，保留 HTTP 独有字段）
- `patchCard()` 只改卡片上会变的那几处（状态环、徽标、四个指标、运行时长、sparkline），
  DOM 节点**不重建** → 拖动排序、焦点、历史都不受影响
- **只有"实例集合变了"（新增/删除）才整体重画**
- 断线按 `1s → 2s → 5s → 10s` 退避重连；连续失败 ≥4 次退到 **10 秒轮询兜底**，
  并在标题旁如实显示 `实时 / 重连中 / 轮询 / 离线`；页面切走即断开（`destroy`）

**实测证据**

| 检查 | 结果 |
|---|---|
| 帧间隔（9 秒窗口） | **2009 / 2012 / 2017 ms**（稳定 2 秒） |
| WS 建立 | `ws://127.0.0.1:8100/api/dashboard/ws?token=…` |
| 是否原地更新 | 给卡片打的 `data-mark="LIVE"` **全程保留**（未整页重绘） |
| sparkline | 点数累积 |
| **活体**：启动实例后不刷新页面 | `已停止` → **`运行中`**，PID 20404、`0分4秒`、内存 `618MB` → `767MB` → `713MB` |
| **活体**：停止后 | 回到 `已停止` |
| console | 零异常 |

验收脚本：`tools/ui-shots/live-check.js`（帧率与原地更新）、`tools/ui-shots/live-change.js`（活体变化）。


#### 6.7.1 顺手挖出并修掉的真 bug：**CPU 读数恒为 0.0%**

写实时推送时发现实例卡片上 CPU **永远是 `0.0%`**（内存却在正常变化）。
根因在两处叠加：

1. **`psutil` 的用法错了**。`Process.cpu_percent(interval=None)` 是**两次调用之间的增量**，
   首次调用必定返回 `0.0`。监控循环每轮都 `psutil.Process(pid)` **新建对象**，
   所以直接读**永远**是 0.0 —— 内存读数正确是因为 RSS 是瞬时值，掩盖了这个错误。
   修法：同一对象上先采样一次、隔 0.2s 再读。
   证据（`backend/tools/cpu_sample_check.py`，对一个真在满核燃烧的子进程采样）：
   旧写法 `['0.0','0.0','0.0']` vs 新写法 `['101.4','101.5','101.5']`。
2. **量纲没有归一化**。psutil 以"单核 100%"为单位，多线程 JVM 会远超 100%
   （数据库里实测到过 **3022.7%** —— 32 个 GC/worker 线程各烧一个核）。
   读数除以 `cpu_count()` 才是**全机占用**，否则界面上的百分比没有可比性。

另外精度也不够：`round(cpu, 1)` + 前端 `toFixed(1)` 会把空闲 JVM 常见的
0.05%~0.4% 显示成 `0.0%`，**看起来像"数据不动"**。现改为后端 2 位、前端 `toFixed(2)`。

修完实测（真实 vanilla 服务端，world 生成期）：
`cpu=8.05%` → `0.24%` → `0.0%` → `0.24%` —— 量级合理、**可见变化**，3022% 那种数字消失。

#### 6.7.2 流畅度：数值不再"硬跳"

推送频率从 2 秒提到 **1 秒**（`dashboard_push_ms` 可配，默认 1000；`manager.status()` 是纯内存读取，
实例数 × 每秒一次，成本很低）。但每秒直接改 `textContent` 会看着像闪，所以：

- **只在值真的变了才写 DOM**（`setVal()` 先比 `textContent`）—— 避免无意义的重排/重绘
- 值变化时打 `mc-val-updated`：上浮 2px + 淡入 220ms（`transform`/`opacity` 属合成层属性，**不触发布局**）
- 数字统一 **`tabular-nums` 等宽**，位数变化时宽度不跳、整行不抖
- 徽标/指示灯颜色变化走 `transition`，不瞬切
- sparkline **只做整块淡入**，**不给 SVG 的 `points` 加 transition** —— 那是几何属性，会触发布局重算、反而更卡
- 运行时长每秒都在变，**刻意不做动画**（否则一直闪）
- `prefers-reduced-motion: reduce` 下全部动画关闭

实测：10 秒窗口收到 **10 帧**，间隔 `1011/1018/1012/1009/1015/1018/1012/1013/1014 ms`；
卡片节点与指标值节点的自定义标记**全程保留**（`data-mark=KEEP`、`data-vmark=V0`），
证明是原地更新、没有重建 DOM；动画类按预期挂载；console 零异常。

### 6.8 视图扩充：从 2 个视图到 7 个

**原来只有两个视图**（实例列表：卡片 / 表格），而且视图逻辑是**撒在页面里的** ——
`localStorage.getItem('mc_inst_view')` 读一次、`data-v` 属性写一次、按钮 active 类手工切一次。
每加一个视图要在三处各改一遍，很容易漏（漏了就是"点了没反应"或"刷新后回到旧视图"）。

**先把视图机制收口**：新增 `js/viewmode.js` —— 把"有哪些视图 / 默认哪个 / 怎么记住 / 按钮怎么画"
收在一处，页面只负责渲染。旧键 `mc_inst_view` 由 `mc_inst_view`（`ViewMode` 内）统一管理。

**然后按两个维度铺开视图**：

| 维度 | 视图 | 干什么用 |
|---|---|---|
| 信息密度 | **卡片** | 大卡 + 状态环 + sparkline + 启停，适合少数实例 |
| | **紧凑** | 单行一个实例（CSS Grid 定列，不用 `<table>`），**一屏看清二十来个** |
| | **表格** | 完整字段：磁盘占用 / 归属 / 端口等 |
| 关注对象 | **分组** | 按运行状态归堆：**异常 → 运行中 → 启停中 → 已停止**，异常排最前；可折叠，折叠状态持久化 |
| | **趋势** | 主机 CPU / 内存曲线 + 各实例内存 TOP 叠加 + **实例状态色带**（每格一次采样） |

仪表盘 3 个视图（卡片 / 紧凑 / 趋势），实例列表 4 个（卡片 / 紧凑 / 表格 / 分组）。

**实现要点**

- 紧凑视图用 **CSS Grid 定列宽**（表头与数据行共用同一套列定义），窄屏整列隐藏也不破坏对齐；
  1180px 以下丢掉"运行时长/端口/玩家"，780px 以下只剩"名称 + 状态 + 操作"
- 分组视图**空组不渲染**（避免一屏空标题）；折叠状态存 `mc_inst_group_collapsed`
- 趋势视图的数据来自实时通道累积：主机资源 120 点（1 秒一帧 ≈ 2 分钟）、
  实例 sparkline 60 点、状态色带 120 格。**趋势视图更新时只重画图表，不重建整页**（否则图表会闪）
- `Chart.area()` 原来多系列**全用同一个颜色**（颜色只在调用方传 `color` 时才设置），
  图例分不清哪条是哪条 —— 补了内建灰阶色板循环分配
- 紧凑/趋势视图同样走 §6.7.2 的"值变了才写 DOM + 上浮淡入"流畅度方案

**实测（`tools/ui-shots/views-check.js`，2000×1100 视口）**

| 页面 | 视图 | 渲染结果 |
|---|---|---|
| 实例列表 | cards | 实例卡 5 |
| | compact | 紧凑行 5 · 表头 8 列 |
| | table | 表格 1 |
| | grouped | 分组块 1「已停止」· 组内卡片 5（**刷新后仍保持 grouped**） |
| 仪表盘 | cards | 卡片 5 |
| | compact | 紧凑行 5 · 表头 8 列 |
| | trend | 趋势图 3 · 状态色带 5 条（45 格）；曲线路径长度随时间增长（154→178→202） |

console 零异常。

### 6.9 项目官网（`/site/`）

面板自带一个自述页，随面板一起提供，不需要另外部署：**`http://127.0.0.1:8100/site/`**。

- `frontend/dist/site/index.html` + `site.css` + `site.js`，无框架、无外部依赖、无外部字体。
- **复用面板自己的设计令牌**（`/css/tokens.css` + `/css/theme-mono.css`），
  所以官网与产品是同一套视觉语言；主题三态（跟随系统 / 暗色 / 亮色）与面板一致。
- 内容：Hero（含**按真实布局复刻**的面板界面示意，非宣传图）、6 张功能卡、
  7 种核心支持表（**实测速度如实标注，Quilt 慢就写慢**）、两步命令的快速开始、
  设计系统展示（色板 / 圆角 / 阴影 / 动效 / 视图密度）、5 条 FAQ、页脚。
- 所有数字都对着仓库里的真实值写：126 条后端接口、7 种核心、2 套建筑导入引擎、161 项后端验收。
- 后端在 `main.py` 里加 `site` 到静态白名单，并补 `/site` 与 `/site/` 的首页路由 ——
  **必须注册在 `app.mount('/site', …)` 之前**（StaticFiles 会先截获，与 `/js/mock-accounts.js` 同一个坑，已踩两次）。
- 验收：`tools/ui-shots/site-check.js` —— 结构/锚点/主题切换/入场动画/资源加载/四档宽度
  （1500 / 1024 / 780 / 420）**均无横向溢出、console 零异常**。

## 7. 还欠什么（续做清单，按优先级）

1. **下载竞态**（老问题，未动）：`POST /api/instances` 的 `download` 是异步的，紧接 `start` 会报
   「服务端 jar 不存在」。应按 §HANDOFF.7.3 改成 `state/progress` 语义 + 下载中 `start` 回 409 + 进度。
2. **源收口**：Paper 必须切 `fill.papermc.io/v3`（v2 已 410）、BMCLAPI 镜像回退、Java 源 sha256 校验；
   NeoForge 可达性待复测。
3. **TPS/MSPT 真实性**：拿不到必须 `null` + `available=false`（不许回 0）。
4. **建筑导入复核**：`main.py` 现已挂载，但**没跑过** `backend/tools/build_selftest.py`，
   规格 §9 五条验收（引擎 B 读回方块、回滚后 region sha256 一致、越界/未映射/恶意样本被拒）仍待验。
5. **前端剩余 mock**：`build.js` 仍用 `MockBuild`（建筑 Tab），接口已齐、可直接切换；
   切完记得照 `dom-check.js` 的思路补 DOM 断言（**别再靠截图**，见下）。
6. **截图证据**：`docs/ui-review/` 里旧的账号/权限截图是 mock 时代的，已过期；
   而本机 Edge 无头对 1080 量级高度页面**合成不出内容**（`shot.js` 头部已记录该已知限制），
   所以前端验收请用 `tools/ui-shots/dom-check.js`（读 DOM，不依赖合成）。

## 8. 怎么复跑验证

```bash
# 后端：规格 §8 六条验收（自己起独立数据目录的临时服务，跑完自动清理）
cd backend
set PYTHONUTF8=1
python tools\accounts_selftest.py          # 期望：通过 161 项，失败 0 项，退出码 0

# 前端：DOM 断言（需先起一个面板，口令用终端打印的初始口令）
python ..\tools\dev_ui_panel.py 8123       # 另开一个终端，MC_DATA_DIR 指向临时目录
node ..\tools\ui-shots\dom-check.js http://127.0.0.1:8123 <初始口令> ..\docs\evidence\dom-check.json
                                           # 期望：DOM 断言 24 项全通过，退出码 0

# ⭐ 前端"真的能用"的走查（**新增，务必跑**）：逐页打开并抓 console 异常/4xx
node ..\tools\ui-shots\page-audit.js http://127.0.0.1:8100 <口令>
                                           # 期望：0 / 14 个页面有问题
node ..\tools\ui-shots\ws-check.js http://127.0.0.1:8100 <口令>
                                           # 期望：控制台 WS 收到 hello、无失败痕迹
node ..\tools\ui-shots\repro-login.js http://127.0.0.1:8100 <口令>
                                           # 期望：真实登录表单提交后进实例列表、无 console 异常
```

> 只跑 `dom-check.js` 是不够的：它只断言"账号页有内容"，**看不到**实例详情页的控制台
> WebSocket 崩了（§5.1 / §5.2）。`page-audit.js` 就是那次教训的产物 —— 逐页走一遍，
> 任何一页 console 有异常或请求 4xx 都会标 BAD。

跑后端自检的那个脚本会**自带独立数据目录、跑完杀进程并释放端口**；不会碰 `backend/data` 里的开发库。

## 9. 环境提醒

- 本机没有 `py` 启动器；Python 命令前置 `PYTHONUTF8=1`。
- **不要用 PowerShell 的 `Set-Content -Encoding UTF8` 写 JSON 配置**：PS 5.1 会写 BOM，
  Python 的 `json.load` 会因 BOM 解析失败而**静默回落到默认配置**
  （本次就因此把限流当成"应用有 bug"，白查了一轮）。用 `[System.IO.File]::WriteAllText(...UTF8Encoding($false))`。
- Windows 上 `select()` 不能用于管道（`WinError 10038`）；读子进程输出请用线程。
- 杀面板要用 `taskkill /F /T`：`terminate()` 在 Windows 上不一定能杀掉，会留着旧服务占端口，
  下一轮脚本就会**连到旧服务**上跑（本次踩过，测试因此在脏状态上失败）。
- 架外仍有两个**本次会话之前**就存在的残留 python 进程：`tmp/place_var.py` 与一个 8110 端口的面板。
  它们不是本轮产生的，未清理（如需清理请先确认不是你在用的）。

## 展示名改名历史（避免下次再摸不清）

| 次序 | 展示名 | logo | 仓库名 |
|---|---|---|---|
| 1 | MC 开服面板 | MC | ruituo-mc-panel（后改） |
| 2 | rt_mc 面板 | rt_mc | rt_mc_plane（GitHub 原用名） |
| 3 | 云枢面板 | 云枢 | ruituo-mc-panel |
| 4 | 锐同面板 | 锐同 | ruituo-mc-panel |
| 5 | **芮拓面板（当前）** | **芮拓** | ruituo-mc-panel（用户定：仓库先不动） |
| 6 | **芮拓MC面板（当前）** | **芮拓** | ruituo-mc-panel（用户定：仓库先不动） |

> ⚠️ 注意：**改名脚本会把这张历史表里的旧名一起替换掉**（它按文本整体替换）。
> 所以每次改名之后，这张表要**手工补一行**，否则历史会被"吃掉"。

致未来的自己：**品牌名已改动 5 次**。若用户再要改名，先把「仓库名要不要一起改」
「已发出的宣传物料要不要重做」问清，不要在展示名上单方面来回改——每改一次，
仓库名（`ruituo-mc-panel`）与展示名就更脱节一层，而仓库改名又会带来
PAT 授权失效 / 旧链接失效 / 开源平台重指向等一串副作用。

⚠️ 改名时要动的面：`frontend/dist/index.html`（title/boot-logo）、`js/components.js`（侧栏）、
`js/pages/login.js`、`site/index.html`（两处 logo + brand-txt）、`app/config.py` 默认 site_name、
`app/main.py` FastAPI title、`run.py` 横幅、`app/auth.py`、`app/mcprops.py`、`app/serverctl.py`、
`tools/reset_*.py`、`install.sh`、`panelctl*`、`selftest.py` 的标题断言、各 css/js 文件头注释，
**以及运行中实例的 `backend/data/config.json` 的 site_name + 重启面板**（否则界面不变）。
