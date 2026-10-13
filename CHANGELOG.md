# 版本变更日志

## v0.20.3 — 飞书设置对话框按钮修复
- 飞书设置对话框「保存 / 关闭 / 测试连接」按钮在 DPI 缩放下被挤出可视区。
- 重布局为 HeaderBar(top) + 表单(middle) + 按钮条(bottom，parent 改为 self)，按钮始终贴底。
- 默认 geometry 改 `640x600`，允许上下缩放，DPI 自适应更稳。

## v0.20.2 — 飞书密钥保存失败可见 + 版本号修复
- `save_config()` 不再静默吞异常，显式返回 `True/False`；保存失败弹出具体路径与排查提示。
- 标题栏版本号改从 `VERSION` 读取，打包时注入当前版本常量。

## v0.20.1 — 授权私钥迁出项目树
- 私钥 / 公钥 / 签发记录迁移到 `C:/workbuddy_tools/ArxivCrawler_license_keys/`（项目树外）。
- 支持 `ARXIV_LICENSE_KEYS_DIR` 环境变量覆盖；客户端只内嵌公钥，不受影响。

## v0.20.0 — 安全审计 + 写库路由修复 + PMC 解析修复
- 抓取结果写入错误数据库（GUI 不显示）→ scraper 新增 `db_path` 参数，与 GUI 共用同一库。
- PDF 下载关闭了 TLS 校验 → 改回 `ssl=None`（按系统 CA）。
- `run.py` 硬编码飞书密钥 → 改为空默认，仅从环境变量 / GUI 注入。
- PMC 源解析 0 结果 → 解析前剥离命名空间。
- `_migrate` 列类型错误 → `rating`/`favorite` 加 `INTEGER`。

## v0.19.2 — 下载失败可诊断
- 批量下载后弹失败明细对话框（来源 / 标题 / 原因 / 链接 + 重试失败项）。
- 失败原因分类：404 链接失效 / 401·403 需订阅 / 返回网页非 PDF / 无直链。
- 表格 PDF 列实时标记 ✓ / ✗。

## v0.19.1 — 修复下载时 SQLite 跨线程报错
- `PaperDatabase` 改 `check_same_thread=False` + WAL，后台写不阻塞 GUI 读。

## v0.19.0 — 下载反爬加固 + 新论文源 + 语言区分
- 重试 / 退避 / 状态码细分 / 魔数校验 / `Referer` / 代理传参修复。
- 论文源扩至 17 个（arXiv / PLOS / bioRxiv / PMC / ChinaXiv + Crossref 聚合顶刊 + 国内平台占位）。
- `Paper` 新增 `language` 字段，表格 / 筛选 / 统计支持语言。

## v0.18.0 — 飞书设置面板 + PDF 下载修复
- 新增 GUI 飞书设置面板（4 密钥 + 测试连接）。
- 修复飞书写入 `'dict' object has no attribute 'paper'`。
- 修复 `pdf_url` 空值误报下载失败。

## v0.17.0 — 界面美化 + DPI 适配 + 2 bug 修复
- 重做桌面 UI（统一主题 / 品牌栏 / 分组工具栏 / 筛选卡片 / 斑马纹表格 / 状态栏），Windows DPI 感知。
- 修复详情窗口本地 PDF 按钮、PDF 库更改目录 `NameError`。

## v0.16.0 — 本地 PDF 库
- 新增本地 PDF 下载与管理（`pdf_store.py`，并发受限 / 原子改名 / 魔数校验 / 可取消）。
- PDF 库窗口 + 右键菜单（下载 / 打开 / 定位 / 删除）。
- `add_papers` 改 UPSERT，保留本地字段。

## v0.15.0 — 安装包拆分为「客户版 / 自用版」
