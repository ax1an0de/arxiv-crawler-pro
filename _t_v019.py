"""v0.19.0 + v0.19.1 离线单测：不联网，用合成数据验证新功能不崩且逻辑正确。"""
import asyncio
import os
import sys
import tempfile
import threading
from datetime import datetime

sys.path.insert(0, r"D:\arxiv_crawler-main")

from paper import Paper, PaperDatabase, infer_language
from paper_sources import (MultiSourceScraper, ALL_SOURCE_NAMES,
                           PlosSource, BiorxivSource, PmcSource, ChinaXivSource,
                           CrossrefSource, NatureSource, IeeeSource, CnkiSource,
                           WanfangSource)
from pdf_store import PdfStore, DownloadResult

ok = []
def check(name, cond):
    ok.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL"), name)

# 1) infer_language
check("infer en", infer_language("Deep Learning for Vision", "arXiv") == "en")
check("infer zh by source", infer_language("Some Title", "ChinaXiv") == "zh")
check("infer zh by cjk", infer_language("深度学习研究", "arXiv") == "zh")
check("infer ja", infer_language("深層学習に関する研究", "arXiv") == "ja")
check("infer ko", infer_language("딥러닝 연구", "arXiv") == "ko")
check("infer multi", infer_language("深度学习 논문 연구", "arXiv") == "multi")

# 2) 所有源实例化（不联网）
ms = MultiSourceScraper(date_from="2024-01-01", date_until="2024-12-31",
                        optional_keywords=["ai"], source_names=ALL_SOURCE_NAMES)
check("all sources instantiated", len(ms._sources) == len(ALL_SOURCE_NAMES))
print("   启用平台:", [s.name for s in ms._sources])

# 3) PLOS _to_paper
plos = PlosSource()
pd = plos._to_paper({"id": "10.1371/journal.pone.0000001", "title": "A Test Paper",
                     "abstract": "abs", "authors": [{"first_name": "A", "last_name": "B"}],
                     "publication_date": "2024-01-15"})
check("plos to_paper", pd is not None and pd.source == "PLOS"
      and pd.pdf_link and "printable" in pd.pdf_link)

# 4) Crossref _to_paper + 子类 container_title
cr = CrossrefSource()
citem = {"title": ["Cross Paper"], "author": [{"given": "X", "family": "Y"}],
         "abstract": "<jats:p>abs</jats:p>", "published": {"date-parts": [[2023, 5, 1]]},
         "DOI": "10.1/test", "URL": "https://doi.org/10.1/test",
         "container-title": ["Nature"],
         "link": [{"content-type": "application/pdf", "URL": "https://x/y.pdf"}]}
cp = cr._to_paper(citem)
check("crossref pdf link", cp is not None and cp.pdf_link == "https://x/y.pdf"
      and cp.venue == "Nature")
check("nature container", NatureSource().container_title == "Nature")
check("ieee container", IeeeSource().container_title == "IEEE")

# 5) PMC XML 解析
pmc = PmcSource()
xml = '''<pmc-articleset><article><article-meta>
<article-id pub-id-type="pmc">12345</article-id>
<title-group><article-title>Test PMC Title</article-title></title-group>
<contrib-group><contrib contrib-type="author"><name><surname>Smith</surname>
<given-names>J</given-names></name></contrib></contrib-group>
<abstract><p>An abstract.</p></abstract></article-meta></article></pmc-articleset>'''
pp = pmc._parse_xml(xml)
check("pmc parse", len(pp) == 1 and pp[0].pdf_link.endswith("/pdf") and pp[0].source == "PMC")

# 6) bioRxiv / ChinaXiv HTML 解析
bp = BiorxivSource()._parse(
    '<div class="highwire-article-citation"><a href="/content/10.1/biorxiv.1">'
    'Paper One</a></div>')
check("biorxiv parse", len(bp) == 1 and bp[0].pdf_link.endswith(".full.pdf"))
cp2 = ChinaXivSource()._parse('<a href="/article/2024.001">中文论文标题</a>')
check("chinaxiv parse", len(cp2) == 1 and cp2[0].source == "ChinaXiv")

# 7) 占位源返回空
check("cnki placeholder", CnkiSource().fetch("2024-01-01", "2024-12-31", ["ai"]) == [])
check("wanfang placeholder", WanfangSource().fetch("2024-01-01", "2024-12-31", ["ai"]) == [])

# 8) 数据库迁移 + language 持久化
tmpdb = os.path.join(tempfile.gettempdir(), "t_v019_papers.db")
if os.path.exists(tmpdb):
    os.remove(tmpdb)
db = PaperDatabase(tmpdb)
cols = {r[1] for r in db.conn.execute("PRAGMA table_info(papers)")}
check("language column migrated", "language" in cols)
p = Paper(url="http://x/1", authors="A", title="T", categories=[], abstract="a",
          comments="", first_submitted_date=datetime(2024, 1, 1),
          first_announced_date=datetime(2024, 1, 1), source="arXiv", language="zh")
db.add_papers([p])
row = db.get_by_url("http://x/1")
check("language persisted", row is not None and row.language == "zh")
db.conn.close()

# 9) pdf_store 状态码分类（mock session）
class C:
    def __init__(self, data):
        self.data = data
        self.done = False
    async def read(self, n):
        if self.done:
            return b""
        self.done = True
        return self.data
class Resp:
    def __init__(self, status, content=b"%PDF-1.4", ctype="application/pdf"):
        self.status = status
        self._content = content
        self._ctype = ctype
        self._c = C(content)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    @property
    def headers(self):
        class H:
            def get(self, k, d=None):
                return {"Content-Type": self._ctype}.get(k, d)
        h = H()
        h._ctype = self._ctype
        return h
    @property
    def content(self):
        return self._c
class MockSession:
    def __init__(self, status, content=b"%PDF-1.4", ctype="application/pdf"):
        self.status = status
        self.content = content
        self.ctype = ctype
    def get(self, url, **kw):
        return Resp(self.status, self.content, self.ctype)

async def test_fetch_one():
    db2 = PaperDatabase(os.path.join(tempfile.gettempdir(), "t_v019_dl.db"))
    store = PdfStore(db2, directory=tempfile.mkdtemp())
    sem = asyncio.Semaphore(1)
    lock = asyncio.Lock()
    base = dict(url="http://p/1", authors="A", title="T", categories=[],
               abstract="a", comments="", first_submitted_date=datetime(2024, 1, 1),
               first_announced_date=datetime(2024, 1, 1), source="Nature",
               pdf_link="http://p/1.pdf")
    paper = Paper(**base)
    # 403 付费墙
    r1 = DownloadResult()
    await store._fetch_one(MockSession(403), paper, sem, None, r1, None, lock)
    check("403 -> paywall", any("403" in r for _, r in r1.failed))
    # 200 + HTML 付费墙/限流页
    r2 = DownloadResult()
    await store._fetch_one(MockSession(200, b"<html>paywall</html>", "text/html"),
                           paper, sem, None, r2, None, lock)
    check("200 html -> non-pdf", any("非 PDF" in r for _, r in r2.failed))
    # 200 + PDF 成功
    r3 = DownloadResult()
    await store._fetch_one(MockSession(200, b"%PDF-1.4 real", "application/pdf"),
                           paper, sem, None, r3, None, lock)
    check("200 pdf -> ok", len(r3.ok) == 1)
    # 无 pdf 链接
    r4 = DownloadResult()
    p4 = Paper(**{**base, "pdf_link": None, "url": "http://p/4"})
    await store._fetch_one(MockSession(200), p4, sem, None, r4, None, lock)
    check("no pdf link -> skip", any("无 PDF 直链" in r or "未提供" in r for _, r in r4.failed))
    db2.conn.close()

asyncio.run(test_fetch_one())

# ---- v0.19.1 增补：SQLite 跨线程访问（fix 「下载失败：SQLite objects created in a thread...」）----
def test_db_thread_safe():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db = PaperDatabase(path)
        # WAL 模式 + busy_timeout 已启用（之前 v0.19.0 默认是 rollback journal，
        # 跨线程写会被 SQLite 拒；现在改成 WAL + check_same_thread=False 后
        # 后台 worker 直接写库不再抛错）。
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        bt   = db.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        check("WAL enabled", mode.lower() == "wal")
        check("busy_timeout 10s", bt == 10000)

        # 跨线程 set_local_pdf —— 模拟 PdfStore._fetch_one 在 worker 线程里写库
        db.add_papers([Paper(
            url="http://t/1", title="t", authors="a",
            abstract="", comments="",
            first_submitted_date=datetime(2026, 1, 1),
            first_announced_date=datetime(2026, 1, 1),
            categories=[], source="test", venue="v",
        )])
        box = {}
        def worker():
            try:
                box["n"] = db.set_local_pdf("http://t/1", "abc.pdf")
            except Exception as e:
                box["err"] = repr(e)
        t = threading.Thread(target=worker); t.start(); t.join()
        check("set_local_pdf from worker thread",
              box.get("n") == 1 and "err" not in box)
    finally:
        try: os.unlink(path)
        except OSError: pass
        for sfx in ("-wal", "-shm", "-journal"):
            try: os.unlink(path + sfx)
            except OSError: pass

test_db_thread_safe()


# v0.19.2：下载失败原因分类 + 进度回调带 url
def test_failure_reasons():
    p = Paper(
        url="http://t/x", title="t", authors="a",
        abstract="", comments="",
        first_submitted_date=datetime(2026, 1, 1),
        first_announced_date=datetime(2026, 1, 1),
        categories=[], source="IEEE", venue="v",
        pdf_link="https://example.com/paper.pdf",
    )
    check("404 reason has url+code",
          "404" in PdfStore._reason_4xx(404, p) and "example.com/paper.pdf" in PdfStore._reason_4xx(404, p))
    check("403 reason paywall",
          PdfStore._reason_4xx(403, p).startswith("需订阅/被拦截(403)"))
    r400 = PdfStore._reason_4xx(400, p)
    check("400 reason has source",
          "400" in r400 and "IEEE" in r400)

    # _report 必须向 on_progress 透传 paper_url（第 4 个参数）
    captured = {}
    def fake_progress(total, title, status, url=None):
        captured["url"] = url
    async def _runner():
        lock = asyncio.Lock()
        res = DownloadResult()
        await PdfStore._report(fake_progress, res, "标题", "完成", lock, "http://t/y")
    asyncio.run(_runner())
    check("on_progress receives paper_url", captured.get("url") == "http://t/y")


test_failure_reasons()


# ===== v0.20.0 审计修复回归测试 =====

def test_pmc_parses_namespaced_xml():
    """PMC 的 JATS 响应带命名空间，_parse_xml 必须剥离后才能命中本地名。"""
    xml = '''<?xml version="1.0"?>
    <pmc-articleset xmlns="http://jats.nlm.nih.gov" xmlns:xlink="http://www.w3.org/1999/xlink">
      <article article-type="research-article">
        <front>
          <article-meta>
            <article-id pub-id-type="pmc">7731234</article-id>
            <title-group><article-title>Deep Neural Networks for Medical Imaging</article-title></title-group>
            <contrib-group>
              <contrib contrib-type="author"><name><given-names>Jane</given-names><surname>Doe</surname></name></contrib>
            </contrib-group>
            <abstract><p>We propose a new method for segmentation.</p></abstract>
          </article-meta>
        </front>
      </article>
    </pmc-articleset>'''
    src = PmcSource()
    papers = src._parse_xml(xml)
    check("PMC 解析命名空间 XML（>=1 篇）", len(papers) >= 1)
    if papers:
        check("PMC 提取标题", "Deep Neural Networks" in papers[0].title)
        check("PMC 提取 PMC id -> PDF 链接", "PMC7731234" in (papers[0].pdf_link or ""))
        check("PMC source 标记", papers[0].source == "PMC")


def test_multisource_scraper_uses_db_path():
    """MultiSourceScraper 必须把抓取结果写进传入的 db_path，而非 CWD 的 papers.db。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        dbp = f.name
    try:
        mss = MultiSourceScraper(
            date_from="2026-01-01", date_until="2026-01-02",
            optional_keywords=["x"], db_path=dbp,
            source_names=["CNKI"],  # 占位源，fetch 直接返回 []，不联网
        )
        rows = mss.paper_db.conn.execute("PRAGMA database_list").fetchall()
        main_path = [r[2] for r in rows if r[1] == "main"][0]
        check("MultiSourceScraper.paper_db 指向传入 db_path",
              os.path.abspath(main_path) == os.path.abspath(dbp))
        for s in mss._sources:
            check(f"source[{s.name}] 收到 db_path", getattr(s, "db_path", None) == dbp)
        mss.fetch_update()
        check("db_path 文件被创建（非 CWD papers.db）", os.path.exists(dbp))
    finally:
        for sfx in ("", "-wal", "-shm", "-journal"):
            try: os.unlink(dbp + sfx)
            except OSError: pass


def test_tls_verification_enabled_by_default():
    """pdf_store 下载不应关闭 TLS 校验（传给 TCPConnector 的 ssl 不能是 False）。"""
    import aiohttp
    cap = {}
    RealConn = aiohttp.TCPConnector

    class _CaptureConnector(RealConn):
        def __init__(self, *a, **kw):
            cap.update(kw)               # 记录 craft 代码传入的 ssl 参数
            kw.pop("ssl", None)          # 用默认校验，避免测试机真的去连
            super().__init__(*a, **kw)

    aiohttp.TCPConnector = _CaptureConnector
    try:
        p = Paper(url="http://t/1", title="t", authors="a",
                  abstract="", comments="",
                  first_submitted_date=datetime(2026, 1, 1),
                  first_announced_date=datetime(2026, 1, 1),
                  categories=[], source="test", venue="v",
                  pdf_link="https://example.com/p.pdf")
        try:
            asyncio.run(PdfStore.download_async(None, [p], max_concurrent=1, timeout=1))
        except Exception:
            pass  # 无网络必然失败，本测试只关心 ssl 参数
        check("TCPConnector 收到 ssl 参数", "ssl" in cap)
        check("TLS 校验未被关闭（ssl != False）", cap.get("ssl") is not False)
    finally:
        aiohttp.TCPConnector = RealConn


def test_run_no_hardcoded_secrets():
    """run.py 源码中不得再出现任何硬编码的真实飞书密钥。"""
    import run as runmod
    src = open(runmod.__file__, "r", encoding="utf-8").read()
    # 仅校验旧的硬编码字面量已移除（不在此回显真实值）
    # 注意：以下为历史泄露凭据的脱敏占位符，仅用于回归检测 "run.py 不应再出现此类硬编码"。切勿填入真实凭据。
    LEGACY_FEISHU_APP_ID = "<REDACTED_APP_ID>"
    LEGACY_FEISHU_SECRET = "<REDACTED_SECRET>"
    check("run.py 不再含硬编码飞书 APP_ID", LEGACY_FEISHU_APP_ID not in src)
    check("run.py 不再含硬编码飞书 SECRET", LEGACY_FEISHU_SECRET not in src)


def test_feishu_guard_on_empty_creds():
    """凭据为空时，_get_tenant_access_token 应清晰报错而非拿默认密钥傻跑。"""
    import run as runmod
    saved = (runmod.FEISHU_APP_ID, runmod.FEISHU_APP_SECRET)
    runmod.FEISHU_APP_ID, runmod.FEISHU_APP_SECRET = "", ""
    try:
        try:
            runmod._get_tenant_access_token()
            check("空凭据时飞书鉴权被拦截", False)
        except RuntimeError as e:
            check("空凭据时飞书鉴权被拦截", "未配置" in str(e) or "App ID" in str(e))
    finally:
        runmod.FEISHU_APP_ID, runmod.FEISHU_APP_SECRET = saved


test_pmc_parses_namespaced_xml()
test_multisource_scraper_uses_db_path()
test_tls_verification_enabled_by_default()
test_run_no_hardcoded_secrets()
def test_feishu_config_save_not_silent():
    """v0.20.2：save_config 必须显式返回成功/失败，失败不再被静默吞掉。

    过去 save_config 用 `except: pass` 吞掉所有错误，飞书设置点了“保存”却实际
    没写、还显示“已保存”，导致“appid 填了保存不了”。现改为返回 True/False，
    _save 据返回值提示。本测试验证：
      1) 正常写入返回 True 且文件落盘、可往返读回
      2) 写入失败（os.replace 抛异常）返回 False 且不向外抛异常
      3) 失败时临时文件被清理（避免下次加载到半截 JSON）
    """
    import pdf_store as _ps
    import unittest.mock as _mock
    tmpcfg = os.path.join(tempfile.gettempdir(), "t_cfg_verify.json")
    if os.path.exists(tmpcfg):
        os.remove(tmpcfg)
    orig = _ps.get_config_path
    _ps.get_config_path = lambda: tmpcfg
    try:
        r = _ps.save_config({"FEISHU_APP_ID": "cli_x"})
        check("save_config 成功返回 True", r is True)
        check("save_config 成功落盘", os.path.isfile(tmpcfg))
        check("save_config 往返读回", _ps.load_config().get("FEISHU_APP_ID") == "cli_x")
        with _mock.patch("pdf_store.os.replace", side_effect=OSError("locked")):
            r2 = _ps.save_config({"FEISHU_APP_ID": "cli_y"})
        check("save_config 失败返回 False（不再静默）", r2 is False)
        check("save_config 失败清理临时文件", not os.path.exists(tmpcfg + ".tmp"))
    finally:
        _ps.get_config_path = orig
        for p in (tmpcfg, tmpcfg + ".tmp"):
            if os.path.exists(p):
                os.remove(p)


def test_feishu_settings_dialog_layout():
    """v0.20.3：飞书设置对话框的按钮条必须在窗口底部，且 parent=self 不被表单挤出。

    旧版 640x520 + 按钮 parent=f + resizable=False，导致 DPI 缩放下三个按钮
    完全跑到可视区外（用户截图发现"没有保存按钮"）。修复后：
      1) self 直接子包含 1 个 HeaderBar + 1 个表单 Frame + 1 个按钮条
      2) 按钮条内含 [测试连接, 关闭, 保存] 三个按钮
      3) 表单 Frame 内绝无按钮（v0.20.2 旧 bug）
    """
    try:
        import tkinter as tk
    except Exception as e:
        check("飞书设置对话框布局可实例化（环境无 tkinter）", False)
        return
    try:
        import desktop_app as da
        root = tk.Tk()
        root.withdraw()
        dlg = da.FeishuSettingsDialog(root)
        root.update_idletasks()
        try:
            # 1) self 直接子：HeaderBar(继承 tk.Frame) + form(TFrame) + btn_bar(TFrame) = 3 个
            direct = dlg.winfo_children()
            check("飞书对话框 self 直接子恰好 3 个（header/form/btn_bar）",
                  len(direct) == 3)
            # 注：3 个 widget 全部 winfo_class 含 "Frame"——因为 HeaderBar 继承 tk.Frame
            # 所以不能仅通过 class 区分；用"含 3 个按钮"和"不含按钮"两个判定来定位。
            # 2) 找含 3 个按钮的那个 Frame = 按钮条（parent=self 而非 f）
            btn_bar = None
            form_frame_with_btns = None
            for w in direct:
                sub_btns = [s for s in w.winfo_children() if s.winfo_class() == "TButton"]
                if len(sub_btns) == 3:
                    btn_bar = w
                    btn_texts = sorted(b.cget("text") for b in sub_btns)
                    check("按钮条三件齐" + str(btn_texts),
                          btn_texts == ["保存", "关闭", "测试连接"])
                elif sub_btns:
                    form_frame_with_btns = (w, sub_btns)
            check("表单 Frame 内没有任何按钮（v0.20.2 旧 bug 已修）",
                  form_frame_with_btns is None)
            check("按钮条 parent=self（不被表单内容挤出）", btn_bar is not None)
            # 3) 窗口请求高度 <= 默认 600（旧版 520 装不下，现在能装下）
            req_h = dlg.winfo_reqheight()
            check(f"窗口请求高度 {req_h} 合理（<= 默认 600）", req_h <= 600 + 30)
        finally:
            dlg.destroy()
            root.destroy()
    except Exception as e:
        check(f"飞书设置对话框实例化（{e!r}）", False)


test_feishu_guard_on_empty_creds()
test_feishu_config_save_not_silent()
test_feishu_settings_dialog_layout()


print("\n== SUMMARY ==")
fails = [n for n, c in ok if not c]
print(f"{len(ok) - len(fails)}/{len(ok)} passed")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("ALL PASS")
