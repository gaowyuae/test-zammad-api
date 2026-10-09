# Zammad 接口自动化测试

面向求职展示的 Python 接口自动化项目，针对本地 Zammad `7.2.0-0011` 实现 **73 条可追溯功能用例**：P0/P1共39条、P2共24条、P3共10条。覆盖工单创建和流转、消息附件、用户角色权限、模板文本宏及搜索。使用 pytest、Requests、PyYAML、Psycopg 3 和 Allure，将请求、业务断言、只读数据库核查及资源处理串成一次可核验执行。

**最新完整回归（2026-10-06）：72 passed、1 failed，73条全部执行，0错误、0跳过。** JUnit用时891.137秒（约14分51秒），进程退出码1。唯一失败TC_037仍为251字符标题返回201且截断为250字符；保留原预期与真实失败，没有修改产品、放宽断言或重跑变绿。

本轮56项资源已清理、14项确认未创建，TC_145新建角色7按批准策略停用、清空权限和组关联、非默认且无人使用后保留，整轮状态`clean_with_retained`；保留不等于删除。历史角色5/6及新增7的后置只读检查通过；长期9张工单／13篇文章前后检查通过，映射未变。4263个附件引用完整，凭据扫描0命中，临时数据库密码已撤除。

M25执行摘要（本地资料，不随仓库上传） · 73条编号结果（本地资料，不随仓库上传） · TC_037差异（本地资料，不随仓库上传）。完整原始结果位于本机`artifacts/m25-full-20261006-193520/`，由Git忽略，未上传或公开发布。

M23/M24新增搜索10条先定向全部通过（JUnit118.421秒），随后纳入上述同版本完整回归，不能把两轮相加为83条覆盖。672项离线检查通过，Ruff及本轮文件格式检查通过；unit不计业务用例。**M23/M24/M25待用户评审，内部通过不代表用户已验收。** Jenkins继续延期，P4性能／P5并发10条候选未实现。

历史定向结果和授权见开发进度（本地资料，不随仓库上传）。历史39条完整回归见M14摘要（本地资料，不随仓库上传），P2与基线见M21/M22摘要（本地资料，不随仓库上传）；这些结果各自保留版本与范围，不用于拼接本轮通过数量。

## 从哪里看

| 想了解什么 | 入口 |
|---|---|
| 5～8 分钟演示、面试讲解 | 项目演示说明（本地资料，不随仓库上传） |
| 需求、用例矩阵、设计与验收标准 | 实施方案 v1.2（本地资料，不随仓库上传）；P2已实现137～148、159～162、171～174、187～190；P3已实现109～118；P4～P5未实施 |
| 原始用例来源 | [Zammad 工单创建测试用例.xlsx](docs/Zammad_工单创建测试用例.xlsx)；Excel 不参与运行时驱动 |
| 历史执行、失败修正、授权与验收记录 | 唯一开发进度文件（本地资料，不随仓库上传） |
| 看一条完整正向用例 | [TC_023](tests/functional/test_ticket_create.py) → [工单生命周期](fixtures/ticket.py) |
| 看权限与副作用断言 | [更新权限用例](tests/functional/test_ticket_update_access.py)、[消息用例](tests/functional/test_ticket_messages.py) |
| 看附件与作者保护 | [TC_092～096](tests/functional/test_ticket_article_checks.py) |

历史执行时的产物路径为本机 `artifacts/`，目录由 Git 忽略；源码副本不自带这些结果，能否查看取决于本机是否仍保留原文件。可先看 TC_037 历史差异摘要（本地资料，不随仓库上传） 和 M13 十条定向回归索引（本地资料，不随仓库上传）。后者为 **10 passed、29 deselected**，不能当作同版本 39 条全量通过；全部历史路径与逐轮结果见进度文件。

## 业务范围与架构

| 范围 | 编号 | 数量 | 主要核验 |
|---|---|---:|---|
| 工单创建 | TC_023、TC_025～037 | 14 | 必填项、标题边界、身份与客户关联、失败无残留 |
| 工单处理与分派 | TC_058～072 | 15 | 负责人、转组、认证授权、非法更新回滚、挂起时间 |
| 消息、附件与备注 | TC_087～096 | 10 | 实际作者、公开／内部可见性、附件字节与访问控制、正文及他人备注保护 |
| 用户基础 | TC_137～140 | 4 | 普通客户角色、原密码与资料不被重复创建覆盖、局部更新、不存在读取 |
| 用户权限 | TC_141～144 | 4 | 自身与客服提权保护、精确组授权、撤权后新会话访问保护 |
| 角色管理 | TC_145～148 | 4 | 精确权限、名称必填、大小写重名和客户越权；明确获准保留而非删除 |
| 工单模板 | TC_159～162 | 4 | 创建配置不生成工单、空格名称拒绝、原ID局部更新、普通客服不能删除 |
| 文本模块 | TC_171～174 | 4 | 详情读取、note250／251边界、客户不可读取内部正文 |
| 宏配置及原子性 | TC_187～190 | 4 | 动作与适用组、原ID更新、管理权限、一次跨组批量拒绝且两单不变 |
| 列表与搜索 | TC_109～118 | 10 | 精确集合、分页至空页、客户隔离、查询无副作用和不存在详情 |
| P0 试点子集 | TC_023、025、033、058、072 | 5 | 上述 39 条中的子集，不另加覆盖数量 |

当前仓库保留73条业务功能用例，不包含 `tests/unit/` 框架自测。上文离线检查数量属于开发阶段的历史记录，不能在当前仓库中直接复现。Jenkins实际接入尚未完成。

| 层 | 位置 | 职责 |
|---|---|---|
| 场景与数据 | `tests/functional/`、`data/cases/` | 明确 TC 编号、输入和预期；保留字段缺失、空串、null 的差别 |
| 前置与生命周期 | `conftest.py`、`fixtures/` | 动态核对身份、字典及权限；独立造数据、登记和清理 |
| 业务接口与 HTTP | `api/`、`clients/` | 按业务对象封装；显式身份的独立Session，单次请求，保留原始响应供断言 |
| 公共断言与数据库 | `assertions/`、`db/` | API 回读、前后状态及参数化只读查询；数据库不造数据、不清理业务行 |
| 运行控制与证据 | `support/`、`reporting/`、`tools/` | 共享环境锁、创建意图、残留清单、脱敏附件、源码及配置摘要 |

请求不自动重试、不跟随重定向，不继承 Cookie、netrc 或系统代理。4xx／5xx 交由具体用例判断，网络异常另行记录；写入结果未知时不重发。维护身份 M 负责前置和清理，不能替代被测用户执行权限场景。

## 本地复现准备

使用独立测试实例，运行期间暂停人工及其他测试写入。当前环境入口为 `http://localhost:18080`，PostgreSQL 为 `localhost:15432/zammad_production`。这些是本机映射，换环境时必须核对版本、端口、身份和业务字典，不照抄历史对象 ID。测试会创建、更新和删除本轮临时工单与临时用户，不是只读巡检。

### 1. 项目专用 Python

Windows x64，CPython 3.13.13。项目使用 F 盘专用解释器和独立 venv，不修改系统 PATH、Launcher 或 `.py` 关联。

| 用途 | 路径 |
|---|---|
| 项目 | `F:\Python\test-Zammad` |
| 专用基础解释器 | `F:\Python\test-Zammad\.runtime\Python-3.13.13\python.exe` |
| 项目命令解释器 | `F:\Python\test-Zammad\.venv\Scripts\python.exe` |
| 工作区外共享状态 | `F:\Python\test-Zammad-state` |

本机环境已准备。其他机器需先从 [Python 3.13.13 官方目录](https://www.python.org/ftp/python/3.13.13/) 获取 `python-3.13.13-amd64.zip`，核对 [Windows 分发清单](https://www.python.org/ftp/python/3.13.13/windows-3.13.13.json)，解压到基础解释器目录（不要使用嵌入式包、覆盖已有解释器或移动旧 venv）。本机使用包的 SHA-256 为 `5f066036aebfaa3aa9aa4d52f852022f24641e7b5bc52f2bf8bb7acbe7bab958`。新建 venv 后安装锁定依赖：

```powershell
Set-Location F:\Python\test-Zammad
& .\.runtime\Python-3.13.13\python.exe -I -X utf8 -m venv .venv --prompt test-Zammad
& .\.venv\Scripts\python.exe -I -X utf8 -m pip --isolated install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt
& .\.venv\Scripts\python.exe -X utf8 -m pip check
```

无需激活 venv 或修改 PowerShell 执行策略；日常命令显式用项目解释器和 `-X utf8`。HTML 报告另需 Allure CLI 与 Java；当前本机记录为 Allure 2.38.1、Java 21，Python 的 `allure-pytest` 插件不包含 CLI。

### 2. 七份非敏感配置

全量运行需要以下七份本地文件。已有文件保持不覆盖：

```powershell
if (-not (Test-Path config/local.yaml)) { Copy-Item config/local.example.yaml config/local.yaml }
if (-not (Test-Path config/run.local.yaml)) { Copy-Item config/run.example.yaml config/run.local.yaml }
if (-not (Test-Path config/db.local.yaml)) { Copy-Item config/db.example.yaml config/db.local.yaml }
if (-not (Test-Path config/creation.local.yaml)) { Copy-Item config/creation.example.yaml config/creation.local.yaml }
if (-not (Test-Path config/assignment.local.yaml)) { Copy-Item config/assignment.example.yaml config/assignment.local.yaml }
if (-not (Test-Path config/update_access.local.yaml)) { Copy-Item config/update_access.example.yaml config/update_access.local.yaml }
if (-not (Test-Path config/search.local.yaml)) { Copy-Item config/search.example.yaml config/search.local.yaml }
```

| 配置 | 要填写的内容 |
|---|---|
| `local.yaml` | 环境代号、实例根地址、连接／读取超时；地址不加 `/api/v1` |
| `run.local.yaml` | 工作区外共享状态目录、A1／M／C1 登录名、G1 名称、A1／M Token 变量名 |
| `db.local.yaml` | 独立只读账号、数据库连接、密码变量名及查询超时 |
| `creation.local.yaml` | G2、A12／C2／AD 映射、C1／AD Token 变量名 |
| `assignment.local.yaml` | A2／A12、G2、系统未分派登录名、A12／A2 Token 变量名 |
| `update_access.local.yaml` | AD／C2／AR／A2、G2、系统未分派登录名及对应 Token 变量名 |
| `search.local.yaml` | GB、BA1／BA2／BC1／BC2 登录名及四个 Token 变量名；实际基线 ID 在共享映射保存 |

配置不保存密码、Token 或历史 ID；实际 ID 在前置中动态取得。相同身份在不同配置中的映射必须一致。本地与未来 CI 必须共用同一 `env_id`、`state_dir`，运行账号能读写状态目录；不能改环境名或状态目录绕过占用／残留。

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m tools.check_config --config config/local.yaml
```

该命令只离线校验环境配置，不验证另外六份配置或实际连接；全部业务前置由运行入口检查。缺少前置应报错，不记为通过或跳过。

### 3. 专用身份与十二枚 Token

先由环境维护人员准备专用账号和活跃测试组 G1／G2。固定账号与固定Token由维护者准备，fixture不会自动建立或修复这些身份，也不更改全局认证。M16/M17的临时U按具体场景通过API创建、签发一次性Token并清理，临时授权结束立即恢复。账号使用测试姓名和虚构邮箱，G1／G2 不绑定真实邮件渠道；创建／更新请求携带抑制通知头。

| 身份 | 账号及组权限 | Token 范围 | 环境变量 |
|---|---|---|---|
| A1 | 纯 Agent，仅 G1 full | `ticket.agent` | `ZAMMAD_A1_TOKEN` |
| M | 独立 Admin＋Agent，G1／G2／GB full | `admin`＋`ticket.agent` | `ZAMMAD_M_TOKEN` |
| A2 | 纯 Agent，仅 G1 full | `ticket.agent` | `ZAMMAD_A2_TOKEN` |
| A12 | 纯 Agent，G1／G2 full | `ticket.agent` | `ZAMMAD_A12_TOKEN` |
| AR | 纯 Agent，仅 G1 read，无继承写权限 | `ticket.agent` | `ZAMMAD_AR_TOKEN` |
| AD | 纯 Admin，不含 Agent／Customer | `admin` | `ZAMMAD_AD_TOKEN` |
| C1 | 纯 Customer，无组织共享 | `ticket.customer` | `ZAMMAD_C1_TOKEN` |
| C2 | 纯 Customer，无组织共享 | `ticket.customer` | `ZAMMAD_C2_TOKEN` |
| BA1／BA2 | 纯 Agent，仅 GB full | `ticket.agent` | `ZAMMAD_BA1_TOKEN`／`ZAMMAD_BA2_TOKEN` |
| BC1／BC2 | 纯 Customer，无组织 | `ticket.customer` | `ZAMMAD_BC1_TOKEN`／`ZAMMAD_BC2_TOKEN` |

角色和 Token 范围分别核对；M 的 Token 仅有 `admin.user` 不满足清理所需权限。前置合并直接和角色继承的组授权，拒绝配置错误造成的假权限结果。系统未分派对象按登录名 `-` 查找并确认 inactive、无角色，不能替换成普通账号。涉及分派／备注场景要求 `import_mode=false`，框架不会更改全局设置。

首次在新环境运行完整73条前，还需由维护者准备名称与 `data/cases/user_permissions.yaml` 中 `token_role_name` 一致的专用空角色（当前为 `AUTO_M08_C1_TOKEN_TEMP`）：停用、权限与组为空、`default_at_signup=false`、无人关联；不写死历史ID。核实本项目临时用户的密码认证和API Token管理路径可用。前置只查找与验证，不自动创建该角色、修改系统角色或开启全局认证；条件不满足应阻塞并先处理环境准备。

在同一个 **PowerShell 7** 窗口隐藏输入十二枚 Token 和一项数据库密码；`-MaskInput` 不适用于旧版 Windows PowerShell：

```powershell
$env:ZAMMAD_A1_TOKEN = Read-Host 'A1 Token' -MaskInput
$env:ZAMMAD_M_TOKEN = Read-Host 'M Token' -MaskInput
$env:ZAMMAD_A2_TOKEN = Read-Host 'A2 Token' -MaskInput
$env:ZAMMAD_A12_TOKEN = Read-Host 'A12 Token' -MaskInput
$env:ZAMMAD_AR_TOKEN = Read-Host 'AR Token' -MaskInput
$env:ZAMMAD_AD_TOKEN = Read-Host 'AD Token' -MaskInput
$env:ZAMMAD_C1_TOKEN = Read-Host 'C1 Token' -MaskInput
$env:ZAMMAD_C2_TOKEN = Read-Host 'C2 Token' -MaskInput
$env:ZAMMAD_BA1_TOKEN = Read-Host 'BA1 Token' -MaskInput
$env:ZAMMAD_BA2_TOKEN = Read-Host 'BA2 Token' -MaskInput
$env:ZAMMAD_BC1_TOKEN = Read-Host 'BC1 Token' -MaskInput
$env:ZAMMAD_BC2_TOKEN = Read-Host 'BC2 Token' -MaskInput
$env:ZAMMAD_DB_PASSWORD = Read-Host '只读数据库临时密码' -MaskInput
```

变量只对该窗口及子进程有效。不要打印变量、在命令参数中写明文或把凭据放入 YAML、聊天、Git、日志和报告。项目 Token 仅在已批准期限和本项目范围内复用，首版完成或用户要求时撤销；数据库临时密码每轮结束撤除。退出前清除当前窗口的凭据变量，服务端撤销／撤除由维护人员核实；仅删除环境变量不等于撤销 Token。

### 4. 数据库最小只读授权

维护人员一次性准备独立 `test_zammad_ro` 账号，授予数据库 CONNECT、`public` schema USAGE 及下表列级 SELECT。不要使用应用账号或超级用户，不授予 schema CREATE 或目标表／列写权限。已有同名角色先核查，不由测试入口重建或覆盖。SQL 设计及历史批准记录见实施方案和进度文件；当前查询以 [db/queries.py](db/queries.py) 为准。

| 表 | 当前已实现范围需要 SELECT 的列 |
|---|---|
| `tickets` | `id, number, title, group_id, owner_id, customer_id, state_id, priority_id, article_count, created_by_id, updated_by_id, updated_at, pending_time` |
| `ticket_articles` | `id, ticket_id, type_id, sender_id, subject, body, content_type, internal, created_by_id, updated_by_id, updated_at` |
| `ticket_article_types` | `name` |
| `stores` | `id, store_object_id, o_id, size, filename` |
| `store_objects` | `id, name` |
| `users` | `id, login, firstname, lastname, email, active, organization_id, created_by_id, updated_by_id, created_at, updated_at` |
| `roles` | `id, name, active, default_at_signup` |
| `roles_users` | `user_id, role_id` |
| `groups_users` | `user_id, group_id, access` |
| `roles_groups` | `role_id, group_id, access` |
| `permissions_roles` | `role_id, permission_id` |
| `permissions` | `id, name, active` |
| `templates` | `id, name, options, active, created_by_id` |
| `text_modules` | `id, name, keywords, content, note, active, created_by_id` |
| `groups_text_modules` | `text_module_id, group_id` |
| `macros` | `id, name, perform, active, ux_flow_next_up, created_by_id` |
| `groups_macros` | `macro_id, group_id` |
| `organizations` | `id, shared, active` |
| `organizations_users` | `user_id, organization_id` |

每用例独立连接，短只读事务；默认连接 5 秒、语句 10 秒、锁等待 3 秒。前置拒绝管理权限、schema CREATE 和目标对象写权限，并探测需要的列。`pending_time` 明确按 UTC 解释；附件仅读取指定文章的关联元数据，不读取文件存储正文。数据库仅用于证据核对，所有业务准备和删除通过 API 完成。

## 执行与报告

pytest 默认选择 `functional`，请始终明确选择范围。收集不会发起业务前置；离线检查不需要业务凭据：

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m ruff check .
& .\.venv\Scripts\python.exe -X utf8 -m ruff format --check .
& .\.venv\Scripts\python.exe -X utf8 -m pytest tests/functional -m functional --collect-only -q
```

确认以上准备完成后，选择一次真实执行。输出目录必须不存在；下面用时间戳区分每轮。完整回归前清除遗留 `PYTEST_ADDOPTS`，避免以前的 `-k` 筛选造成漏跑：

```powershell
Remove-Item Env:\PYTEST_ADDOPTS -ErrorAction SilentlyContinue
$runDir = 'artifacts/functional-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output $runDir
```

`functional` 当前执行全部 73 条（P0/P1共39条、P2共24条、P3共10条）；后续新增模块会扩展此集合。每次包含TC_145的执行正常增加1条获准保留角色，见下文M18说明。如只需原P0五条，使用另一个新目录：

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite pilot --output artifacts/pilot-local-001
```

pilot 业务前置仅需要环境、run、db 配置和 A1／M Token、DB 密码；不会读取其他场景身份。统一入口仍尝试记录七份配置的文件摘要，缺失的非选中配置记null，不加载其凭据。需要直接 pytest 调试时可显式选 `-m functional` 或 `-m pilot`，例如：

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m pytest tests/functional -m functional --alluredir artifacts/direct-local-001/allure-results --junitxml artifacts/direct-local-001/junit.xml
```

直接 pytest 默认使用上述七份本地配置，与统一入口共用环境锁、前置和清理，但不额外生成统一入口的 `run-metadata.json`。真实执行默认 `--tb=short`；缺 Allure 输出、启用 `--showlocals`／`--full-trace` 或改为会展示参数的长回溯都会被拒绝，避免失败输出暴露凭据。当前不支持多个业务测试进程并行执行；普通业务断言失败继续收集后续结果，未解决残留则阻止后续业务请求。

统一入口输出 `junit.xml`、`allure-results/` 和 `run-metadata.json`。元数据记录源码指纹、七份配置摘要及可用的 Git 信息，不保存配置原文。共享目录中的原始运行清单是资源状态依据；验收时保存清单副本并单独核验附件引用、清理、凭据扫描和生命周期，不能把这些人工核验产物当作入口自动生成。

使用已安装的 Allure CLI 生成并打开本轮报告；本机可将 `allure` 替换为 `& F:\ruanjiancunfang\allure-2.38.1\bin\allure.bat`：

```powershell
allure generate "$runDir/allure-results" --output "$runDir/allure-report"
allure open "$runDir/allure-report"
```

生成 HTML 成功不等于用例通过。报告阅读顺序：TC 编号与状态 → 被测请求及原始脱敏响应 → 授权回读／DB → 清理。取证在请求发生时保存，非 JSON 内容只记录字节数和 SHA-256；断言仍使用原始数据。单附件 1 MiB、整轮 50 MiB，缺证或容量不足必须报错，不静默丢弃。

## 已知差异与恢复

- **TC_037：** 原预期为 251 个 A 被明确拒绝；历史实际返回 201 并保存为 250 个 A。保留失败、不跳过、不 xfail、不改原 Excel 或产品制造通过；意外创建对象仍走完整清理。`clean` 只代表资源处理完成。
- **删除后附件 URL：** 当前版本已删除工单的附件 GET 曾返回 500（`NilClassPolicy`），不能将 500 当作删除成功。工单和文章通过 API 删除／回读核对，附件删除后以只读数据库关联为空核验；不声称共享底层文件已物理销毁。
- **已接受的环境例外：** 临时角色 `AUTO_M08_C1_TOKEN_TEMP`（历史 ID 4）因部署没有角色 DELETE 路由而保留，已停用、清空权限、关闭默认注册、无人关联。停用不是删除；未通过 SQL 清理或修改系统 Customer 角色。它不是业务残留自动清理对象。
- **M18角色保留：** 本轮新建且归属明确的角色经API停用、清权限与组关联、关闭默认注册，并确认无人关联后，资源状态记 `retained`、整轮状态记 `clean_with_retained`。角色仍在库中；该状态不代表业务断言通过。后续业务准备前只读复核历次登记的保留角色，发现漂移则阻塞，不自动修复。负向意外新建也按此规则处置且保留测试失败。

创建前先保存意图到 `F:/Python/test-Zammad-state/<env_id>/run-<RUN>.json`。M 删除前核对 RUN、创建者、客户、允许的组及初始文章作者；长度边界场景不能依赖标题标记。写入超时、未知 ID、清理失败或损坏清单会留下阻塞，不能自动重发或直接删清单。

人工恢复按顺序执行：

1. 确认测试进程和服务端请求已结束，结合清单及报告定位 RUN、工单和文章 ID。
2. 通过应用或授权 API 核实归属，处理本轮临时对象；未知写入不能仅凭搜索暂时为空判定已清理。
3. 保留工单／文章不可读取和必要数据库无残留证据，附件按上述关联核对；不通过 SQL 删除业务数据。
4. 在共享目录 `recovery.log` 记录处理人、时间、RUN、对象 ID 和结论；全部核实后仅将原合法清单顶层 `status` 改为 `recovered`，保留资源记录。损坏清单先恢复信息，不直接删除。
5. 下一轮重新核查残留并获取锁。锁文件存在不等于被占用，不删除 `environment.lock` 绕过检查。

## 交付边界

当前是单实例、单进程的接口自动化作品，P2已实现24条；P3已实现10条搜索用例，P4～P5尚未实施，未建设通用企业测试平台。`Jenkinsfile` 是早期P0配置模板，只有当时的配置／凭据接入，**不代表当前73条CI已接入**。Jenkins实际接入按用户安排延期，冻结同一提交的本地一轮＋CI两轮验收未完成。

本仓库上传项目源码、配置示例及原始测试用例Excel；开发进度、实施方案、历史报告和执行证据不随仓库上传。文中历史结果对应当时的源码指纹，不代表当前Git提交已经重新执行验证。后续CI需独立Windows工作区和venv、七份配置、十二枚Token与只读DB凭据、共享锁及真实报告验证。


## M16 用户场景复现

复用 `creation.local.yaml` 的 AD 登录名及 `ZAMMAD_AD_TOKEN`，维护身份 M 负责清理；A1/M仍为统一前置所需。新增只读列已列在进度文件（本地资料，不随仓库上传）的M16授权记录中，换环境须由维护者按最小范围准备，不改用管理员连接运行用例。

TC_138仅用本条随机临时客户的原密码，通过独立 Basic Auth `GET /api/v1/users/me` 在重复创建前后验证，凭据不保存、不签发Token、不改全局开关。清理确认ID、RUN派生登录名及创建人，DELETE后同时核对API404、用户和角色关联不存在。登录名中的数字可逆映射为字母，避免CTI误识别；报告仍记录原RUN和TC。

在已注入本项目凭据的同一PowerShell窗口中，可执行本轮八条：

```powershell
$previousPytestOptions = $env:PYTEST_ADDOPTS
try {
    $env:PYTEST_ADDOPTS = '-k "test_tc_137 or test_tc_138 or test_tc_139 or test_tc_140 or test_tc_023 or test_tc_025 or test_tc_033 or test_tc_072"'
    & .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output artifacts/m16-local-001
} finally {
    $env:PYTEST_ADDOPTS = $previousPytestOptions
}
```

输出目录须不存在。该命令会真实准备及清理专用业务数据；它只选择8条，不能作为43条全量通过的证据。完整报告位于本机 `artifacts/m16-live-20261005-230718/allure-report/`；使用 `allure open` 查看。


## M17 临时用户权限场景复现

复用A1/M/AD与G1/G2配置；本轮11条定向回归还需已有C2/AR Token。读取11列新增权限见上表；不读取数据库密码列、Token表或设备详情。TC141/143/144各创建独立U，仅用U的内存密码签发、列举、撤销自身一次性Token，业务请求用U的Token。141签发和撤销期间复用 `AUTO_M08_C1_TOKEN_TEMP`，结束即恢复纯Customer及角色停用、空权限、无人关联；142仅由A1请求，不签发U Token。

143/144通过API清理工单、撤销Token、AD清空U组后，**仅设备清理**使用M现有Token加 `From=本条U` 调用自身设备GET/DELETE，随后M删除U。该方式已经用户明确批准，正式权限断言不使用代执行。当前版本的普通U认证会排队异步设备日志，因此不能用U Basic删除设备后直接宣称无残留。测试不修改系统角色或全局认证配置，不SQL删除。

首轮143清理422已保留在 `artifacts/m17-live-20261005-235658/`；用户24通过获准API恢复，原错误未改成通过。当前不存在通用自动恢复入口。

```powershell
$previousPytestOptions = $env:PYTEST_ADDOPTS
try {
    $env:PYTEST_ADDOPTS = '-k "test_tc_141 or test_tc_142 or test_tc_143 or test_tc_144 or test_tc_137 or test_tc_138 or test_tc_025 or test_tc_034 or test_tc_066 or test_tc_067 or test_tc_091"'
    & .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output artifacts/m17-local-001
} finally {
    $env:PYTEST_ADDOPTS = $previousPytestOptions
}
```

先按前文在本机注入已批准凭据，输出目录须不存在。只运行新增4条时，把筛选改为141～144即可；仍需A1/M/AD及DB凭据。不得把Token或临时密码粘贴到聊天、配置或报告。最新完整报告：`artifacts/m17-live-20261006-001443/allure-report/`，使用 `allure open` 查看。

## M18 角色场景复现

准备已有A1、M、AD、C1项目Token及独立只读数据库密码，仍从本机环境变量注入。沿用 `creation.local.yaml` 和 `run.local.yaml` 的登录名配置；数据库增加的唯一新列是 `roles.default_at_signup`。权限ID由逻辑名称映射，Excel旧ID不作为固定输入。TC_147、148原用例未传active，框架也不补齐。

当前版本没有角色DELETE路由。每次TC_145会增加一条新角色，结束时保留原RUN名称并停用、清空权限和组、关闭默认注册，确认无人关联；这是用户批准的保留例外。不要用已有角色PUT替代创建测试，也不要SQL删除、擅自解除其他用户关联。后续运行若发现保留对象不再隔离，需要人工核查后处理；仅删除或改写清单不能证明恢复完成。

```powershell
$previousPytestOptions = $env:PYTEST_ADDOPTS
try {
    $env:PYTEST_ADDOPTS = '-k "test_tc_145 or test_tc_146 or test_tc_147 or test_tc_148"'
    & .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output artifacts/m18-local-001
} finally {
    $env:PYTEST_ADDOPTS = $previousPytestOptions
}
```

本轮真实交付两轮结果：首轮7条包含034/141/142回归；代码评审修复后最终复验4条角色。原始目录分别为 `artifacts/m18-live-20261006-165053/`、`artifacts/m18-live-20261006-165422/`，最终报告可用 `allure open artifacts/m18-live-20261006-165422/allure-report` 查看。两轮正常创建的角色5、6仍存在，已通过独立下一轮只读保留状态检查；系统角色及历史角色4保持原状态。该轮未进行51条全量回归；后续M19/M20结果见下节，Jenkins仍未启动。

## M19/M20 模板与文本模块复现

使用已有A1、M、AD、C1项目Token及独立只读数据库密码，从本机环境变量注入；C1仅在TC_174需要。复用现有环境、run及creation配置，不新增账号或全局认证设置。上表新增的模板、文本及组关联14列须由维护者按批准范围授予SELECT，测试不自动扩权。

每条由AD创建独立模板或文本，M核实本轮ID、名称及创建人后API删除，再用GET404和DB无对象／关联确认。原有模板不作为更新或删除目标。TC_160名称精确保留一个空格；TC_161只提交options。模板options在数据库中是YAML文本，按安全加载器解码后比较完整`ticket.title`配置。文本note的250／251字符保持精确；正文比较可见内容，允许服务端正常HTML包装，拒绝响应同时检查部分content及分段HTML泄露。

```powershell
$previousPytestOptions = $env:PYTEST_ADDOPTS
try {
    $env:PYTEST_ADDOPTS = '-k "test_tc_159 or test_tc_160 or test_tc_161 or test_tc_162 or test_tc_171 or test_tc_172 or test_tc_173 or test_tc_174 or test_tc_023 or test_tc_137"'
    & .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output artifacts/m19-m20-local-001
} finally {
    $env:PYTEST_ADDOPTS = $previousPytestOptions
}
```

输出目录须不存在。若只检查8条新用例，去掉筛选中的023和137。当前真实证据目录 `artifacts/m19-m20-live-20261006-172130/`，完整页面用 `allure open artifacts/m19-m20-live-20261006-172130/allure-report` 查看。不能将本轮10条通过视为59条全量通过；历史TC_037预期差异仍保留，Jenkins继续延期。


## M21 宏配置与跨组原子性复现

使用已有 A1、M、AD、A12 的项目 Token 和只读数据库密码，通过本机环境变量注入；身份映射复用 creation、assignment、run 配置。新增最小 SELECT 范围为 macros(id,name,perform,active,ux_flow_next_up,created_by_id) 和 groups_macros(macro_id,group_id)，测试入口不自动授权。

TC_187～190 分别核对创建、仅更新 perform、普通客服不能创建以及一次批量跨组执行整体拒绝。TC_190 由 M 准备 G1/G2 两张独立工单，AD 创建仅适用 G1 的宏，A12 一次提交两个工单 ID；422 必须明确指向宏组限制和 G2 阻塞对象，两张工单及文章均保持。原有宏不作为变更或清理目标。

```powershell
$previousPytestOptions = $env:PYTEST_ADDOPTS
try {
    $env:PYTEST_ADDOPTS = '-k "test_tc_187 or test_tc_188 or test_tc_189 or test_tc_190 or test_tc_061 or test_tc_068 or test_tc_072 or test_tc_159 or test_tc_162 or test_tc_023 or test_tc_033"'
    & .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output artifacts/m21-local-001
} finally {
    $env:PYTEST_ADDOPTS = $previousPytestOptions
}
```

只运行四条新增用例时保留187～190筛选即可。定向回归覆盖共用配置、跨组、创建和回滚路径；不代表全部业务用例回归。

## M22 搜索长期基线

这是后续 P3 用例的前置能力，**不新增业务 TC**。定义见 `data/baseline/search.yaml`；非敏感配置从 `config/search.example.yaml` 复制为被 Git 忽略的 `config/search.local.yaml`。四个独立身份 BA1/BA2/BC1/BC2 各自通过 ZAMMAD_BA1_TOKEN、ZAMMAD_BA2_TOKEN、ZAMMAD_BC1_TOKEN、ZAMMAD_BC2_TOKEN 注入凭据，同时需要 M 和数据库密码。

GB、四身份和 M 的 GB full 权限由维护者按批准范围通过业务 API 准备，初始化入口只准备工单和文章，不自动创建账号、扩大权限或签发 Token。BA 两身份仅 GB full；BC 两身份无组织关系。新增组织读取列为 organizations(id,shared,active)、organizations_users(user_id,organization_id)。

显式初始化只在已批准、GB 空且尚无映射时执行一次：

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m tools.init_search_baseline --mode init --output artifacts/search-init-local-001
```

初始化创建方案定义的9张工单、13篇文章，逐项核对 API／数据库关系和编号索引可见性后，原子保存共享状态目录下的 `zammad-local/search-baseline.json`，以长期保留状态登记。包含4张关闭工单、BC1的6张、BA1负责的新建2张；B02含2篇公开和3篇内部文章。实际ID来自服务端，定义和配置不写历史ID。

日常只读校验（默认也是 check）：

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m tools.init_search_baseline --mode check --output artifacts/search-check-local-001
```

两个入口与功能运行共用同一个环境锁，输出目录必须全新。check 不新增、修复或重建数据；缺失或漂移时返回失败。初始化半成品只清理能证明本轮归属且写入已结束的对象；结果未知、映射已发布但清单提交失败等情况保留定位信息并停止，由维护者核查后恢复，不能只删除清单绕过阻塞。已完成的长期基线不会由普通功能用例清理。

独立入口输出脱敏 JSON 证据、运行摘要和源码／配置指纹，不将初始化伪装成 Allure 业务测试。M23/M24的正式搜索断言复用该基线；准备阶段可等待索引，正式断言不轮询。不进行性能或并发验证。

本轮完整结果：宏定向回归 `artifacts/m21-live-20261006-180943/`，基线初始化 `artifacts/m22-init-20261006-180803/`，只读复核 `artifacts/m22-check-20261006-180902/`。Allure 使用 `allure open artifacts/m21-live-20261006-180943/allure-report` 查看。当前环境已建立基线，应使用 check；再次 init 会拒绝覆盖，不要删除映射后重建。

## M23／M24 搜索与 M25 完整功能运行

完整73条运行要求上述七份配置、十二枚固定Token、只读数据库凭据，以及已完成初始化的9工单13文章基线。缺失或漂移会阻塞，日常入口不创建、重建或自动修复基线。`--search-config` 可显式指定搜索身份配置，实际映射固定从共享状态目录加载；批次前后在同一环境锁内校验，报告保留映射和定义摘要。只选旧模块或pilot时不要求搜索身份。

只验证新增10条（不代表完整回归）：

```powershell
try {
    $env:PYTEST_ADDOPTS = '-k "test_tc_109 or test_tc_110 or test_tc_111 or test_tc_112 or test_tc_113 or test_tc_114 or test_tc_115 or test_tc_116 or test_tc_117 or test_tc_118"'
    & .\.venv\Scripts\python.exe -X utf8 -m tools.run --output ("artifacts/search-" + (Get-Date -Format yyyyMMdd-HHmmss))
} finally {
    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
}
```

完整回归使用同一入口，不保留筛选：

```powershell
Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe -X utf8 -m tools.run --suite functional --output ("artifacts/full-" + (Get-Date -Format yyyyMMdd-HHmmss))
```

该完整运行会准备和清理专用测试数据，并按批准策略保留TC_145产生的一条停用空角色。业务失败不自动重跑；清理或环境异常停止后续业务，必须保留原失败。

M25当前完整报告：

```powershell
allure open artifacts/m25-full-20261006-193520/allure-report
```

报告中保留TC_037失败；源码指纹为`20a84ab171ed9d0ea665f9b90ded0d5c5ff3a09ba68abe46f76d9a10ef0c68d4`，不是Git提交。生成报告不执行测试；再次运行会形成新RUN和新数据，不能替换本轮证据。
