# 版本变更日志

## v0.20.3 (飞书设置对话框「保存 / 关闭 / 测试连接」按钮被挤出可视区)

### 缺陷修复
- **【用户截图反馈】飞书设置对话框里看不到「保存 / 关闭 / 测试连接」按钮**
  - 根因：对话框原 geometry `640x520` + `resizable(False, False)`；按钮 frame `btns` 是表单 frame `f`
    的子控件（共享垂直空间），整体内容在 DPI 缩放下**超过 520**，按钮被自然挤出客户区可见范围。
  - 修复：三段式重布局——顶部 HeaderBar(side=top) / 中间表单(side=top, fill+expand) /
    底部按钮条(side=bottom，**parent 改为 self**，不再嵌套在 f 里)。这样按钮条始终贴底，
    即便表单内容很多或用户把窗口拉到最小也不会被推走。
  - 顺手把默认 geometry 改 `640x600` 并允许上下缩放（`resizable(True, True)`），
    DPI 自适应更稳。

### 兼容性 / 行为差异
- 无破坏性；按钮回调（保存/关闭/测试连接）逻辑未变。
- `_t_v019.py` 新增 6 项 widget 树断言（self 直接子 3 个、按钮条 parent=self、
  含 3 个按钮、表单内绝无按钮等），全套 **51/51 passed**。

### 升级
- 纯 UI 修复，升末位（不动快照）。装新版的「飞书设置」会重排按钮位置到窗口底部，按一下「保存」即生效。

---

## v0.20.2 (飞书密钥保存失败不再静默 + 版本号显示修复)

### 缺陷修复
- **【关键】飞书「App ID / Secret / Token / Table ID」保存后实际没写进本机配置**
  - 根因：`pdf_store.save_config()` 用 `except Exception: pass` 把写入失败**完全静默吞掉**，
    而 `FeishuSettingsDialog._save` 无论成败都显示「✓ 已保存」。一旦真实环境里保存因目录权限 /
    安全软件拦截 / 文件被锁等任意原因失败，用户就会看到「已保存」却实际没写进去，
    下次写入飞书依旧报「密钥缺失」——表现为「填写的 appid 等等没办法保存」。
  - 修复：`save_config()` 改为显式返回 `True/False`（写入前 `os.makedirs` 确保目录存在，
    失败清理临时文件并返回 `False`）；`_save` 据返回值提示——成功才显示「已保存」，
    失败显示具体路径（`%LOCALAPPDATA%\ArxivCrawler\config.json`）及「检查目录权限 / 安全软件拦截」提示。
  - 影响：升级后若点保存仍提示失败，对话框会**明确报错**而非假装成功，便于定位真实原因。
- **【次要】主程序标题栏版本号恒显 0.18.0**
  - 根因：`desktop_app.py` 把 `APP_VERSION` 硬编码成 `"0.18.0"`，版本号从不随发布更新，
    用户无法凭标题栏确认自己装的是否为新版。
  - 修复：开发期从 `VERSION` 文件读取；打包时 `build_installer.py` 像同步向导版本一样，
    在打包前把 `APP_VERSION = _read_app_version()` 替换为当前版本常量注入临时副本，
    冻结后标题栏版本号正确。

### 影响范围
- 单点修复，仅升末位（0.20.1 → 0.20.2），按约定不复制快照。
- 飞书密钥保存逻辑更健壮：失败可见、目录自动创建、临时文件不残留。

## v0.20.1 (授权私钥迁出项目树)

### 安全加固
- **【安全】授权私钥 / 签发记录不再留在项目树内**
  - 现象：`license_keys/`(含 `private_key.pem` 私钥) 一直放在 `D:\arxiv_crawler-main\` 内，
    一旦项目被 git 提交、打包成源码 zip 或随手发给别人，私钥就有随项目泄露的风险
    （尽管快照/安装包已排除它，但项目根目录本身仍不安全）。
  - 修复：把整个 `license_keys/`(私钥 + 公钥 + issued.json) 迁移到
    `C:/workbuddy_tools/ArxivCrawler_license_keys/`（项目树外专用目录、目录与文件名均带项目前缀，
    **绝不在项目树内**，不会被任何快照/源码 zip/安装包带上）。
  - 代码侧：`generate_license.py` / `license_generator.py` 改为从默认
    `C:/workbuddy_tools/ArxivCrawler_license_keys`（带项目前缀文件名）定位密钥，可用环境变量 `ARXIV_LICENSE_KEYS_DIR`
    强制覆盖到任意安全位置；旧的 in-tree 位置仅作回退。
  - 客户端 `app_license.py` 不受影响：它只内嵌公钥常量，运行时不读 `license_keys/`。
  - 安装包不受影响：生成器 exe 本就不内嵌私钥，现改为从项目树外默认位置读取。

### 影响范围
- 升级到本版后，签发激活码（CLI 或 GUI 生成器）会自动去 `C:/workbuddy_tools/ArxivCrawler_license_keys` 找私钥，**无需手动操作**。
- 密钥文件已按跨项目标准加项目前缀重命名（ArxivCrawler_private_key.pem / ArxivCrawler_public_key.pem / ArxivCrawler_issued.json），并校验「私钥派生公钥 = 文件公钥 = 客户端内嵌公钥」modulus 一致，确认密钥对未错配、激活码可正常验签。
- 若把密钥放在别处，设 `ARXIV_LICENSE_KEYS_DIR=<你的目录>` 即可；GUI 也可点「选择…」手动指定。
- 无需重建数据库；`issued.json`(防重发记录) 已随密钥一起迁移，历史签发记录保留。

## v0.20.0 (安全审计 + 抓取写库路由修复 + PMC 解析修复)

### 审计发现的 5 个问题（2 小时彻查）
1. **【关键功能】抓取结果写进错误的数据库（GUI 抓取不显示）**
   - 现象：`MultiSourceScraper` 内部 `PaperDatabase()` 默认写 CWD 的 `papers.db`，
     而 GUI 实际读写的是 `%LOCALAPPDATA%\ArxivCrawler\papers.db`。
     结果：点「抓取」跑完一切正常，但界面里一篇新论文都不出现，像没抓到。
   - 修复：`MultiSourceScraper` 新增 `db_path` 参数并下传给各 source；
     GUI 创建 scraper 时传入 `get_app_db_path()`。抓取与读取现在走同一库。
2. **【安全】PDF 下载关闭了 TLS 证书校验**
   - 现象：`pdf_store.py` 用 `aiohttp.TCPConnector(ssl=False)`，所有 PDF 下载跳过证书验证，
     存在中间人篡改风险（尤其经代理下载时）。
   - 修复：默认 `ssl=None`（按系统 CA 校验）；保留「调用方显式传 `ssl=False`」的逃生口供自签名证书场景。
3. **【安全】`run.py` 硬编码真实飞书密钥**
   - 现象：源码第 19-22 行把真实 `app_id/secret/token/table_id` 写死，会随安装包与源码 zip 一起分发。
   - 修复：全部改为空默认；密钥只从「环境变量」或「桌面 GUI 飞书设置」注入，缺失时清晰报错而非傻跑。
4. **【功能】PMC 源解析 0 结果**
   - 现象：`PmcSource._parse_xml` 用 `ET.fromstring` 解析 JATS 命名空间 XML，
     `article-id`/`article-title` 等带命名空间前缀导致 `find` 永远返回 None，每篇被 `continue` 跳过，
     PMC 永远抓到 0 篇。
   - 修复：解析前剥离命名空间（正则去掉 `{...}`），本地名即可正常匹配。
5. **【一致性】`_migrate` 列类型错误**
   - 现象：`rating`/`favorite` 在 `CREATE TABLE` 里是 `INTEGER`，但 `_migrate` 的
     `ALTER TABLE ADD COLUMN` 一律加 `TEXT`，旧库迁移后这两列类型不一致。
   - 修复：`_migrate` 按 `integer_cols = {"rating","favorite"}` 区分加 `INTEGER`/`TEXT`。

### 影响范围
- 此版本无需重建数据库；旧库经 `_migrate` 自动补齐（含正确的列类型）。
- 飞书用户需在「桌面 GUI 飞书设置」或环境变量里重新填入 4 个密钥（源码不再内置）。

## v0.19.2 (下载失败可诊断 + 明细对话框 + 表格实时标记)

### 背景
- v0.19.0 引入 12 个新论文源（Nature/Science/IEEE/ACM/Springer/Elsevier 走 Crossref 聚合、
  ChinaXiv/PLOS/bioRxiv/PMC 真抓），但其中多数期刊 PDF 链接要么是 DOI 落地页（付费墙，
  返回 HTML）、要么是启发式拼出的直链（容易 404）。结果一批论文下载失败率高，
  而旧实现把失败原因只闪一下页脚、跑完只剩「失败 N」计数，用户完全不知道哪篇、为什么失败。

### 修复 / 改进
- **下载失败明细对话框**（`desktop_app._show_download_report`）：
  批量下载完成后，凡有失败就弹出对话框，逐篇列出「来源 / 标题 / 原因 / PDF 链接」，
  并提供「重试失败项」按钮（只重下失败的那几篇），不再只藏页脚。
- **失败原因分类清晰化**（`pdf_store._reason_4xx` + 魔数校验）：
  - `404` → `PDF 链接失效(404)：<url>`（带来源 PDF 链接，便于判断是哪类源）
  - `401/403` → `需订阅/被拦截(<code>)：<url>`
  - 返回 200 却是网页（魔数非 `%PDF`）→ `返回网页而非 PDF（多为付费墙/需订阅，或限流验证页）：<url>`
  - 无直链 → `无 PDF 直链（来源 X 未提供，无法下载）`
  - 其它 4xx → `请求被拒(<code>)：<url>（来源 X）`
- **表格实时标记**：扩展 `on_progress` 回调传入 `url`，下载过程中在表格 PDF 列实时打
  `✓`（成功）/ `✗`（失败 / 付费墙 / 无链接），一眼看出哪些没下下来。
- 典型结论：34/36 失败多为 Crossref 聚合的付费墙期刊（Nature/Science/IEEE/ACM/Springer/Elsevier）
  与启发式直链源，属「该来源本来就没有免费 PDF」，并非程序崩溃；现在可逐篇看清原因。

## v0.19.1 (修复下载时 SQLite 跨线程报错)

### 修复
- **下载偶发「下载失败：SQLite objects created in a thread can only be used in that same thread」**：
  - 根因：`PdfStore._fetch_one` 在后台 worker 线程（`threading.Thread` + `asyncio.run`）
    里调用 `self.db.set_local_pdf(...)` 写库，但 `PaperDatabase` 的 SQLite 连接是 GUI 线程
    创建的，默认 `check_same_thread=True`，所以一跨线程就抛错；
  - 这条 bug 自 v0.16.0 加本地 PDF 库就一直潜伏，v0.19.0 之前只是恰好被 `pdf_url`
    缺失 / 多数论文直接走「已存在」分支而未触发；
  - 修法：`PaperDatabase.__init__` 改为 `check_same_thread=False, timeout=10`，
    并启用 `PRAGMA journal_mode=WAL / synchronous=NORMAL / busy_timeout=10000`，
    让后台写不阻塞 GUI 读、锁竞争自动重试，不再弹该错误对话框。
- 此为单点修复，仅升末位；v0.19.0 的下载加固 / 新源 / 语言区分不受影响。

## v0.19.0 (下载反爬加固 + 新增论文源 + 语言区分)

### 新增
- **PDF 下载可靠性加固（回应「下载大部分不成功」）**：
  - 重试 + 指数退避：对 429 / 5xx / 连接超时 / 魔数校验失败自动重试（默认 3 次）；
  - 状态码细分：`429` 读 `Retry-After` 退避；`401/403` 标记为「需订阅/付费墙」并直接跳过不重试；其它 4xx 不重试；
  - 魔数校验增强：返回 200 + HTML（限流页/付费墙）按非 PDF 拒收；
  - 新增 `Referer` 请求头（部分期刊站下 PDF 需要）；
  - 修复代理传参（改为 `session.get(proxy=)`，去掉无效的 `_default_proxy`）。
- **新增论文源（共 17 个平台）**：
  - 开放获取 / 可直接下 PDF：arXiv、PLOS、bioRxiv、PMC（PubMed Central）、ChinaXiv（中科院开放预印本，中文）；
  - 顶刊元数据（经 Crossref 聚合，PDF 多为 DOI 链接，下载时诚实标记「需订阅/付费墙」）：Nature、Science、IEEE、ACM、Springer、Elsevier；
  - 国内平台占位（需登录且强反爬，暂不支持自动抓取，仅占位并诚实提示）：CNKI 知网、WanFang 万方。
- **不同语言刊物区分**：
  - `Paper` 新增 `language` 字段（en / zh / ja / ko / multi）；
  - 抓取后按「来源 + 标题字符特征（CJK / 假名 / Hangul）」统一推断语言标记；
  - 表格新增「语言」列，左栏新增「语言」筛选，统计面板按语言汇总。

### 影响
- 下载失败原因现在更清晰：区分「无 PDF 链接」「需订阅/付费墙(403)」「限流(429)」「返回非 PDF」等。
- 中文论文（ChinaXiv 等）会被标记为 zh，可在左栏按语言筛选。
- 旧库自动迁移（ALTER TABLE 加 `language` 列），无需重建。

## v0.18.0 (飞书设置面板 + PDF 下载可靠性修复)

### 新增
- **GUI「飞书设置」面板**：工具栏新增「飞书设置」按钮，弹出模态对话框
  可填写 App ID / App Secret / App Token / Table ID 4 个密钥。Secret 输入框默认
  隐藏（带「显示」复选框），并提供「测试连接」按钮（实时调飞书鉴权 API
  校验 App ID/Secret 是否正确）。值保存到 `%LOCALAPPDATA%\ArxivCrawler\config.json`，
  下次启动与「写入飞书」都会自动优先读这里。优先级：GUI 配置 > 环境变量 > 默认值。

### 修复
- **飞书写入崩溃 `'dict' object has no attribute 'paper'`**：
  `run.py._build_fields()` 现同时支持 PaperRecord（CLI）与 dict（GUI）两种入参，
  GUI 路径不再触发属性错误；`_load_feishu_config()` 在 `write_to_feishu()` 每次调用
  时重新读取，保证 GUI 改完密钥后无需重启进程即可生效。
- **PDF 下载大部分失败**（实际根因，非 arxiv 论文 fallback 到 HTML 详情页被魔数
  校验拒绝）：
  1. `Paper.pdf_url` 在非 arXiv 且无 `pdf_link` 时不再返回错误的 `url`，
     改为返回 `None`——宁可让下载器跳过，也不要拿论坛/详情页硬下；
  2. `PdfStore._fetch_one` 检测到 `pdf_url` 为空时直接报告"无 PDF 链接（该来源
     未提供直链）"并跳过，不再浪费请求；
  3. 错误提示更明确，用户能看出哪几篇是"压根没 PDF 直链"。

### 影响
- arXiv 论文下载行为不变（仍走 `arxiv.org/pdf` 推导路径）。
- 非 arXiv 论文（OpenReview/CVF/ACL/部分 SemanticScholar）若没 `pdf_link`
  字段会被标为"无 PDF 链接"，不再误报"下载失败：返回的不是 PDF"。

## v0.17.0 (界面全面美化 + DPI 适配 + 2 个 bug 修复)

### 视觉（重做整个桌面 UI）
- 新增 `ui_theme.py` 统一视觉主题（学术蓝 + 灰白底 + 语义色，整套 ttk 样式 + 通用组件）
- 顶部深色品牌栏：logo 方块 + 主标题 + 副标题 + 右侧版本徽标
- 两行分组工具栏：第 1 行「抓取/刷新/示例 + 导出」；第 2 行「下载 PDF/PDF 库/取消 + 自动下载开关」
- 左侧筛选栏分组卡片化：每个小节带图标 + 大写灰字小标题 + 已选计数徽标 + 一键清除 + 带边框卡片容器 + 滚轮适配
- 表格美化：30px 行高、斑马纹背景、行级多色编码（**收藏=金、已下载=绿、已过滤=灰**）、表头独立样式
- 结果概览条：粗体计数 `共 N 篇 · 已下载 M` + 操作提示 + 全选/清除按钮
- 底部状态栏：白点带语义颜色（绿空闲/蓝进行/橙警告/红错误）+ 文字 + 进度条 + 库内计数
- 详情窗口 / PDF 库窗口 / 激活窗口全部卡片化，按钮分级（Accent 主操作 / Soft 次要 / Danger 危险 / Ghost 文字按钮）
- Windows DPI 感知（在 4K 屏不糊）
- 「PDF 库」窗口：存储概览卡片 + 操作行 + 提示脚注，所有操作按钮重新分组

### Bug 修复
- 详情窗口「PDF(本地)」按钮实际不打开本地文件（`_open("local")` 未处理 → 改为 `pdf_store.open_file`）
- PDF 库窗口启动时若点「更改目录」会 `NameError`（`change_dir` 在 `command=change_dir` 引用之后才定义）→ 移至引用之后
- `add_papers` 由 `INSERT OR REPLACE` 改 UPSERT 已随 v0.16.0 一并修

## v0.16.0 (本地 PDF 库：下载 + 软件内管理 + 删除)
- **新增"真正下载论文 PDF 到本地"的能力**（此前界面只存元数据，PDF 始终在远端，"PDF"按钮只是用浏览器打开链接）：
  - `pdf_store.py`（新建，仅依赖 aiohttp）：并发受限下载（信号量 4）、先写 `.part` 再原子改名、`%PDF` 魔数校验防限流页/HTML 误存、可中途取消、带进度回调。
  - 下载成功把文件名写回 `papers.local_pdf`，界面表格即显示 ✓。
  - 存储目录默认 `%LOCALAPPDATA%\ArxivCrawler\pdfs`，可在「📁 PDF 库」窗口更改（配置持久化到 `config.json`）。
- **软件内管理（含删除）**：
  - 工具栏「下载 PDF」（下载选中或当前筛选出的全部）、「📁 PDF 库」管理窗口、「取消下载」、勾选「抓取后自动下载」。
  - 列表新增「PDF」列（✓ / — / ↓ 下载中）；左栏新增「PDF 状态」筛选（全部/已下载/未下载）。
  - 右键菜单：下载 / 打开本地文件 / 在文件夹中定位 / **删除本地文件** / **从库删除记录**。
  - 详情弹窗「PDF(本地)」「PDF(在线)」分开，加「🗑 删除本地 PDF」。
  - 「📁 PDF 库」窗口：占用统计、打开/更改目录、扫描重建关联、清理孤立文件、删除全部本地 PDF。
- **数据层加固（顺手修了一个真 bug）**：
  - `paper.py`：新增 `local_pdf` 字段 + 兼容旧库的 `_migrate()`。
  - `add_papers` 由 `INSERT OR REPLACE` 改为 **UPSERT**——以前重复抓取同一篇会把你
    的笔记/标签/收藏/评分/本地 PDF 整行覆盖掉，现在只刷新平台侧元数据，用户字段
    全部保留（已写回归测试验证）。

## v0.15.0 (安装包拆分为「客户版 / 自用版」)
