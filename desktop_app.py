"""
arXiv 论文爬虫 · 桌面版（tkinter，零额外依赖，不依赖浏览器）

功能：
- 联网抓取多平台论文（arXiv / SemanticScholar / OpenReview / CVF / ACL）
- 左侧丰富筛选：关键词、论文年龄、领域(arXiv分类)、会议/期刊、来源平台、
  阅读状态、仅看收藏、我的标签、日期范围、排序
- 中间论文表格（标题/来源/会议/领域/年龄/状态/收藏/评分）
- 双击打开文献详情：笔记、标签、阅读状态、评分、收藏、🤖 一键智能分析
  （关键词 / 主题 / 抽取式摘要 / 推荐标签，全部本地离线）
- 统计面板：来源 / 会议 / 领域 / 主题 / 年份分布
- 导出 Markdown / CSV、飞书多维表格写入

抓取与主题分析在子线程执行，避免界面卡顿。
"""
import os
import sys
import threading
import datetime
import webbrowser

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

import app_license  # 离线激活 / 设备绑定授权（v0.13.0）
import ui_theme as ui  # 统一视觉主题（v0.17.0）

from paper import PaperDatabase
from paper_sources import MultiSourceScraper, ALL_SOURCE_NAMES, DEFAULT_AI_VENUES
import litman
import pdf_store
from pdf_store import PdfStore, make_cancel_flag

try:
    import run as feishu_run  # 飞书写入（需配置环境变量 FEISHU_*）
    _HAS_FEISHU = True
except Exception:
    _HAS_FEISHU = False

def _read_app_version():
    """从 VERSION 文件读取版本号（与 installer 同步），避免硬编码导致标题栏版本失真。
    frozen（PyInstaller）时从解包目录 _MEIPASS 读取，需打包时把 VERSION 加进 datas。"""
    try:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


APP_VERSION = _read_app_version()

DEFAULT_WHITELIST = ["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"]
AGE_OPTIONS = [("全部", ""), ("最近 7 天", 7), ("最近 30 天", 30),
               ("最近 90 天", 90), ("最近 180 天", 180), ("最近 365 天", 365)]
SORT_OPTIONS = [("按日期(新→旧)", "date"), ("按论文年龄(新→旧)", "age"),
                ("按来源", "source"), ("按会议", "venue")]

# 语言标记候选（v0.19.0）：用于「不同语言刊物区分」
LANG_OPTIONS = ["en", "zh", "ja", "ko", "multi"]
LANG_DISPLAY = {"en": "EN", "zh": "中文", "ja": "日文", "ko": "韩文",
                "multi": "多语", "other": "其他", "": "EN"}


def get_app_db_path():
    """返回用户可写的数据库路径（安装到 Program Files 后也能正常读写）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    app_dir = os.path.join(base, "ArxivCrawler")
    os.makedirs(app_dir, exist_ok=True)
    return os.path.join(app_dir, "papers.db")


def _norm(s):
    return (s or "").strip()


def _status_of(paper):
    cats = paper.categories or []
    if any(c in DEFAULT_WHITELIST for c in cats):
        return "chosen"
    return "filtered"


class ScrolledCheckGroup(ttk.Frame):
    """带滚动条的复选框组（卡片式小节标题 + 计数徽标 + 一键清除）。"""

    def __init__(self, master, title, options, height=120, icon="", **kw):
        super().__init__(master, **kw)
        self.vars = {}
        self._title = title

        head = ui.SectionTitle(self, title, icon)
        head.pack(fill="x", pady=(0, 4))
        self.badge = ttk.Label(head.extra, text="", style="Mute.TLabel")
        self.badge.pack(side="left", padx=(0, 6))
        ttk.Button(head.extra, text="清除", style="Ghost.TButton",
                   command=self.clear).pack(side="left")

        box = ui.bordered_box(self)
        box.pack(fill="x")
        self.canvas = tk.Canvas(box, height=height, bd=0, highlightthickness=0,
                                bg=ui.P["surface"])
        sb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb.pack(side="right", fill="y", pady=6, padx=(0, 2))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

        self.set_options(options)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _sync(self):
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        n = sum(1 for v in self.vars.values() if v.get())
        self.badge.configure(text=(f"已选 {n}" if n else ""))

    def set_options(self, options):
        """重建选项，保留已勾选状态。"""
        keep = {k: v.get() for k, v in self.vars.items()}
        for child in self.inner.winfo_children():
            child.destroy()
        self.vars.clear()
        if not options:
            ttk.Label(self.inner, text="（暂无数据）", style="Mute.TLabel").pack(anchor="w", padx=4)
        for opt in options:
            key = _norm(opt)
            v = tk.BooleanVar(value=keep.get(key, False))
            v.trace_add("write", lambda *_: self._sync())
            ttk.Checkbutton(self.inner, text=key, variable=v,
                            style="Small.TCheckbutton").pack(anchor="w", padx=4)
            self.vars[key] = v
        self._sync()

    def clear(self):
        for v in self.vars.values():
            v.set(False)

    def selected(self):
        return [k for k, v in self.vars.items() if v.get()]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"arXiv 论文爬虫 · 文献管理  v{APP_VERSION}")
        self.geometry("1400x860")
        self.minsize(1120, 680)
        self.style = ui.apply_theme(self)

        self.db = PaperDatabase(get_app_db_path())
        self.store = PdfStore(self.db)      # 本地 PDF 库（v0.16.0）
        self.all_papers = []
        self._busy = False
        self._dl_busy = False               # 下载任务是否进行中（独立于抓取）
        self._cancel = None                 # 下载取消事件
        self._dl_set = set()                # 正在下载的 url 集合（用于表格显示 ↓）
        self.status_var = tk.StringVar(value="就绪")

        self._build_header()
        self._build_toolbar()
        self._build_statusbar()     # 先占位底部，主体再填充剩余空间
        self._build_body()
        self._build_detail_dialog()
        self.refresh_tags()
        self.refresh()

        # 右键菜单（PDF 下载 / 打开 / 定位 / 删除）
        self._ctx_url = None
        self._ctx_menu = ui.style_menu(tk.Menu(self, tearoff=0), self)
        self._ctx_menu.add_command(label="  下载 PDF", command=self.on_ctx_download)
        self._ctx_menu.add_command(label="  打开本地文件", command=self.on_ctx_open_local)
        self._ctx_menu.add_command(label="  在文件夹中定位", command=self.on_ctx_locate)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="  删除本地文件", command=self.on_ctx_delete_local)
        self._ctx_menu.add_command(label="  从库删除记录", command=self.on_ctx_delete_record)

    # ---------------- 顶部品牌栏 ----------------
    def _build_header(self):
        ui.HeaderBar(
            self,
            title="arXiv 论文爬虫",
            subtitle="多平台文献采集 · 阅读管理 · 本地 PDF 库",
            version=f"v{APP_VERSION}",
        ).pack(side="top", fill="x")

    # ---------------- 工具栏 ----------------
    def _build_toolbar(self):
        wrap = ttk.Frame(self)
        wrap.pack(side="top", fill="x")

        # 第 1 行：主操作 + 导出
        r1 = ttk.Frame(wrap)
        r1.pack(fill="x", padx=14, pady=(10, 4))
        ttk.Button(r1, text="开始抓取", style="Accent.TButton",
                   command=self.on_crawl).pack(side="left")
        ttk.Button(r1, text="刷新", command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(r1, text="示例数据", command=self.on_demo).pack(side="left", padx=(8, 0))
        ui.vline(r1)
        ttk.Button(r1, text="导出 MD", command=lambda: self.on_export("md")).pack(side="left")
        ttk.Button(r1, text="导出 CSV", command=lambda: self.on_export("csv")).pack(side="left", padx=(8, 0))
        ttk.Button(r1, text="写入飞书", command=self.on_feishu).pack(side="left", padx=(8, 0))
        ttk.Button(r1, text="飞书设置", style="Ghost.TButton",
                   command=self.open_feishu_settings).pack(side="left", padx=(8, 0))

        # 第 2 行：PDF 相关 + 自动下载开关
        r2 = ttk.Frame(wrap)
        r2.pack(fill="x", padx=14, pady=(0, 10))
        ttk.Button(r2, text="下载 PDF", style="Soft.TButton",
                   command=self.on_download_selected).pack(side="left")
        ttk.Button(r2, text="PDF 库", command=self.open_pdf_library).pack(side="left", padx=(8, 0))
        ttk.Button(r2, text="取消下载", style="Danger.TButton",
                   command=self.on_cancel_download).pack(side="left", padx=(8, 0))
        self.auto_dl_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="抓取后自动下载", variable=self.auto_dl_var,
                        style="Small.TCheckbutton").pack(side="left", padx=(16, 0))
        ttk.Label(r2, text="提示：双击行看详情 · 右键管理本地 PDF",
                  style="Mute.TLabel").pack(side="right")

        ui.hline(wrap)

    # ---------------- 底部状态栏 ----------------
    def _build_statusbar(self):
        self.statusbar = ui.StatusBar(self, self.status_var)
        self.statusbar.pack(side="bottom", fill="x")
        self.prog = ttk.Progressbar(self.statusbar.right, mode="indeterminate",
                                    length=190, style="Thin.Horizontal.TProgressbar")
        self.prog.pack(side="right", pady=11)
        self.lib_var = tk.StringVar(value="")
        tk.Label(self.statusbar.right, textvariable=self.lib_var,
                 bg=ui.P["surface_sunken"], fg=ui.P["text_mute"],
                 font=ui.fonts(self)["small"]).pack(side="right", padx=(0, 14))

    def set_status(self, text, kind="info"):
        self.status_var.set(text)
        try:
            self.statusbar.set_kind(kind)
        except Exception:
            pass

    # ---------------- 主体布局 ----------------
    def _build_body(self):
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)

        # 左：筛选侧栏（固定宽 + 右侧分隔线）
        left = ttk.Frame(body, width=308)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        head = ttk.Frame(left)
        head.pack(fill="x", padx=16, pady=(14, 8))
        ttk.Label(head, text="筛选条件", style="H2.TLabel").pack(side="left")
        ttk.Button(head, text="重置全部", style="Ghost.TButton",
                   command=self.on_reset).pack(side="right")

        cv = tk.Canvas(left, bd=0, highlightthickness=0, bg=ui.P["surface"])
        sb = ttk.Scrollbar(left, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        filt = ttk.Frame(cv)
        win = cv.create_window((0, 0), window=filt, anchor="nw")
        filt.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.bind("<Enter>", lambda e: cv.bind_all(
            "<MouseWheel>", lambda ev: cv.yview_scroll(int(-ev.delta / 120), "units")))
        cv.bind("<Leave>", lambda e: cv.unbind_all("<MouseWheel>"))
        cv.pack(side="left", fill="both", expand=True, padx=(16, 0))
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

        self._build_filters(filt)

        tk.Frame(body, width=1, bg=ui.P["border"]).pack(side="left", fill="y")

        # 右：列表 + 统计
        mid = ttk.Frame(body)
        mid.pack(side="left", fill="both", expand=True)
        self._build_stats(mid)      # 先占位底部
        self._build_list(mid)

    def _build_filters(self, parent):
        pad = {"fill": "x", "pady": (0, 14)}

        # 关键词
        sec = ttk.Frame(parent)
        sec.pack(**pad)
        ui.SectionTitle(sec, "关键词", "Q").pack(fill="x", pady=(0, 4))
        self.kw_var = tk.StringVar()
        kw = ttk.Entry(sec, textvariable=self.kw_var)
        kw.pack(fill="x")
        kw.bind("<Return>", lambda e: self.refresh())
        ttk.Label(sec, text="匹配标题与摘要，回车即筛选",
                  style="Mute.TLabel").pack(anchor="w", pady=(4, 0))

        # 论文年龄
        sec = ttk.Frame(parent)
        sec.pack(**pad)
        ui.SectionTitle(sec, "论文年龄", "T").pack(fill="x", pady=(0, 4))
        self.age_var = tk.StringVar(value="")
        ttk.Combobox(sec, textvariable=self.age_var, state="readonly",
                     values=[f"{l} ({v})" if l != "全部" else l
                             for l, v in AGE_OPTIONS]).pack(fill="x")
        self.age_var.set("全部")

        # 复选组
        self.g_cat = ScrolledCheckGroup(parent, "领域 (arXiv 分类)", [], height=108, icon="#")
        self.g_cat.pack(**pad)
        self.g_venue = ScrolledCheckGroup(parent, "会议 / 期刊", [], height=108, icon="V")
        self.g_venue.pack(**pad)
        self.g_src = ScrolledCheckGroup(parent, "来源平台", ALL_SOURCE_NAMES, height=92, icon="S")
        self.g_src.pack(**pad)
        self.g_lang = ScrolledCheckGroup(parent, "语言", LANG_OPTIONS, height=72, icon="Σ")
        self.g_lang.pack(**pad)
        self.g_tag = ScrolledCheckGroup(parent, "我的标签", [], height=92, icon="L")
        self.g_tag.pack(**pad)

        # 阅读状态 + 收藏
        sec = ttk.Frame(parent)
        sec.pack(**pad)
        ui.SectionTitle(sec, "阅读状态", "R").pack(fill="x", pady=(0, 4))
        self.read_var = tk.StringVar(value="all")
        rs = ttk.Frame(sec)
        rs.pack(anchor="w")
        for txt, val in [("全部", "all"), ("未读", "unread"), ("在读", "reading"), ("已读", "read")]:
            ttk.Radiobutton(rs, text=txt, value=val, variable=self.read_var,
                            style="Small.TRadiobutton").pack(side="left", padx=(0, 8))
        self.fav_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(sec, text="★ 只看收藏", variable=self.fav_var,
                        style="Small.TCheckbutton").pack(anchor="w", pady=(4, 0))

        # PDF 状态（v0.16.0）
        sec = ttk.Frame(parent)
        sec.pack(**pad)
        ui.SectionTitle(sec, "PDF 状态", "P").pack(fill="x", pady=(0, 4))
        self.pdf_var = tk.StringVar(value="all")
        pf = ttk.Frame(sec)
        pf.pack(anchor="w")
        for txt, val in [("全部", "all"), ("已下载", "downloaded"), ("未下载", "none")]:
            ttk.Radiobutton(pf, text=txt, value=val, variable=self.pdf_var,
                            style="Small.TRadiobutton").pack(side="left", padx=(0, 8))

        # 日期范围
        sec = ttk.Frame(parent)
        sec.pack(**pad)
        ui.SectionTitle(sec, "首次提交日期", "D").pack(fill="x", pady=(0, 4))
        dr = ttk.Frame(sec)
        dr.pack(fill="x")
        self.df_var = tk.StringVar()
        self.du_var = tk.StringVar()
        ttk.Entry(dr, textvariable=self.df_var, width=11).pack(side="left", expand=True, fill="x")
        ttk.Label(dr, text="—", style="Mute.TLabel").pack(side="left", padx=6)
        ttk.Entry(dr, textvariable=self.du_var, width=11).pack(side="left", expand=True, fill="x")
        ttk.Label(sec, text="格式 yyyy-mm-dd，留空则抓取最近 30 天",
                  style="Mute.TLabel").pack(anchor="w", pady=(4, 0))

        # 排序
        sec = ttk.Frame(parent)
        sec.pack(**pad)
        ui.SectionTitle(sec, "排序方式", "↑↓").pack(fill="x", pady=(0, 4))
        self.sort_var = tk.StringVar(value="date")
        ttk.Combobox(sec, textvariable=self.sort_var, state="readonly",
                     values=[l for l, _ in SORT_OPTIONS]).pack(fill="x")
        self.sort_var.set("按日期(新→旧)")

        # 按钮
        bf = ttk.Frame(parent)
        bf.pack(fill="x", pady=(0, 18))
        ttk.Button(bf, text="应用筛选", style="Accent.TButton",
                   command=self.refresh).pack(side="left", fill="x", expand=True)
        ttk.Button(bf, text="重置", command=self.on_reset).pack(side="left", padx=(8, 0))

    def _build_list(self, parent):
        wrap = ttk.Frame(parent)
        wrap.pack(side="top", fill="both", expand=True)

        # 结果概览条
        top = ttk.Frame(wrap)
        top.pack(side="top", fill="x", padx=16, pady=(14, 10))
        self.count_var = tk.StringVar(value="—")
        ttk.Label(top, textvariable=self.count_var, style="H2.TLabel").pack(side="left")
        self.hint_var = tk.StringVar(value="双击查看详情 · 右键管理 PDF")
        ttk.Label(top, textvariable=self.hint_var, style="Mute.TLabel").pack(side="left", padx=10)
        ttk.Button(top, text="清除选择", style="Ghost.TButton",
                   command=lambda: self.tree.selection_remove(self.tree.selection())
                   ).pack(side="right")
        ttk.Button(top, text="全选", style="Ghost.TButton",
                   command=lambda: self.tree.selection_set(self.tree.get_children())
                   ).pack(side="right", padx=6)

        # 表格
        box = ttk.Frame(wrap)
        box.pack(side="top", fill="both", expand=True, padx=16, pady=(0, 12))
        cols = ("来源", "语言", "会议", "领域", "年龄", "状态", "★", "评分", "PDF")
        self.tree = ttk.Treeview(box, columns=cols, show="tree headings", height=20)
        self.tree.heading("#0", text="  论文标题")
        self.tree.column("#0", width=440, minwidth=260, anchor="w")
        widths = {"来源": 90, "语言": 52, "会议": 108, "领域": 116, "年龄": 64,
                  "状态": 64, "★": 32, "评分": 54, "PDF": 50}
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths[c], minwidth=36,
                             anchor="center" if c not in ("来源", "会议", "领域") else "w")
        vsb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        # 行着色：斑马纹 / 已过滤淡化 / 收藏描金 / 已下载
        self.tree.tag_configure("odd", background=ui.P["surface_alt"])
        self.tree.tag_configure("even", background=ui.P["surface"])
        self.tree.tag_configure("dim", foreground=ui.P["text_mute"])
        self.tree.tag_configure("fav", foreground=ui.P["gold"])
        self.tree.tag_configure("dl", foreground=ui.P["success"])

        self.tree.bind("<Double-1>", self.on_open_detail)
        self.tree.bind("<Button-3>", self.on_tree_right_click)

    def _build_stats(self, parent):
        wrap = ttk.Frame(parent)
        wrap.pack(side="bottom", fill="x")
        ui.hline(wrap)

        head = ttk.Frame(wrap)
        head.pack(fill="x", padx=16, pady=(10, 6))
        ttk.Label(head, text="统计概览", style="H3.TLabel").pack(side="left")
        ttk.Label(head, text="来源 / 会议 / 领域 / 年份分布",
                  style="Mute.TLabel").pack(side="left", padx=8)
        ttk.Button(head, text="分析主题分布", style="Soft.TButton",
                   command=self.on_topic_stats).pack(side="right")

        self.stat_text = scrolledtext.ScrolledText(wrap, height=8, wrap="none")
        ui.style_text(self.stat_text, mono=True, alt=True, root=self)
        self.stat_text.pack(fill="x", padx=16, pady=(0, 14))

    # ---------------- 详情弹窗 ----------------
    def _build_detail_dialog(self):
        self.detail = tk.Toplevel(self)
        self.detail.title("文献详情")
        self.detail.geometry("760x780")
        self.detail.minsize(640, 560)
        self.detail.configure(bg=ui.P["surface"])
        self.detail.withdraw()
        self.detail.protocol("WM_DELETE_WINDOW", lambda: self.detail.withdraw())
        self.detail.bind("<Escape>", lambda e: self.detail.withdraw())
        self._cur_url = None

        # 底部操作条（先占位，保证窗口缩小时不被挤掉）
        actwrap = ttk.Frame(self.detail)
        actwrap.pack(side="bottom", fill="x")
        ui.hline(actwrap)
        act = ttk.Frame(actwrap)
        act.pack(fill="x", padx=18, pady=12)
        ttk.Button(act, text="关闭", command=lambda: self.detail.withdraw()).pack(side="right")
        ttk.Button(act, text="保存修改", style="Accent.TButton",
                   command=self.on_save_detail).pack(side="right", padx=8)
        ttk.Button(act, text="智能分析", style="Soft.TButton",
                   command=self.on_analyze).pack(side="right")
        ttk.Label(act, text="本地离线分析 · 关键词 / 主题 / 摘要 / 标签",
                  style="Mute.TLabel").pack(side="left")

        f = ttk.Frame(self.detail)
        f.pack(fill="both", expand=True, padx=18, pady=(16, 0))

        # 标题 + 元信息
        self.d_title = tk.StringVar()
        ttk.Label(f, textvariable=self.d_title, style="H1.TLabel",
                  wraplength=700, justify="left").pack(anchor="w")
        self.d_meta = tk.StringVar()
        ttk.Label(f, textvariable=self.d_meta, style="Sub.TLabel",
                  wraplength=700, justify="left").pack(anchor="w", pady=(6, 12))

        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Button(row, text="原文", command=lambda: self._open("url")).pack(side="left")
        ttk.Button(row, text="本地 PDF", style="Soft.TButton",
                   command=lambda: self._open("local")).pack(side="left", padx=8)
        ttk.Button(row, text="在线 PDF", command=lambda: self._open("pdf")).pack(side="left")
        ttk.Button(row, text="DOI", command=lambda: self._open("doi")).pack(side="left", padx=8)
        ttk.Button(row, text="删除本地 PDF", style="Danger.TButton",
                   command=self.on_delete_local_from_detail).pack(side="left", padx=(8, 0))

        ui.SectionTitle(f, "摘要", "¶").pack(fill="x", pady=(16, 4))
        self.d_abs = scrolledtext.ScrolledText(f, height=7, wrap="word")
        ui.style_text(self.d_abs, root=self)
        self.d_abs.pack(fill="x")

        ui.SectionTitle(f, "阅读管理", "R").pack(fill="x", pady=(16, 4))
        g = ui.bordered_box(f)
        g.pack(fill="x")
        gi = ttk.Frame(g)
        gi.pack(fill="x", padx=12, pady=12)

        g1 = ttk.Frame(gi)
        g1.pack(fill="x")
        ttk.Label(g1, text="阅读状态", style="Sub.TLabel").pack(side="left")
        self.d_status = tk.StringVar(value="unread")
        ttk.Combobox(g1, textvariable=self.d_status, state="readonly",
                     values=["unread", "reading", "read"], width=10).pack(side="left", padx=(8, 16))
        ttk.Label(g1, text="评分 0-5", style="Sub.TLabel").pack(side="left")
        self.d_rating = tk.IntVar(value=0)
        ttk.Spinbox(g1, from_=0, to=5, textvariable=self.d_rating,
                    width=5).pack(side="left", padx=(8, 16))
        self.d_fav = tk.BooleanVar(value=False)
        ttk.Checkbutton(g1, text="★ 收藏", variable=self.d_fav).pack(side="left")

        g2 = ttk.Frame(gi)
        g2.pack(fill="x", pady=(10, 0))
        ttk.Label(g2, text="我的标签", style="Sub.TLabel").pack(side="left")
        self.d_tags = tk.StringVar()
        ttk.Entry(g2, textvariable=self.d_tags).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(g2, text="逗号分隔", style="Mute.TLabel").pack(side="left")

        ttk.Label(gi, text="笔记", style="Sub.TLabel").pack(anchor="w", pady=(10, 4))
        self.d_notes = scrolledtext.ScrolledText(gi, height=4, wrap="word")
        ui.style_text(self.d_notes, root=self)
        self.d_notes.pack(fill="x")

        ui.SectionTitle(f, "智能分析", "Ai").pack(fill="x", pady=(16, 4))
        self.d_ana = scrolledtext.ScrolledText(f, height=6, wrap="word")
        ui.style_text(self.d_ana, alt=True, root=self)
        self.d_ana.pack(fill="x", pady=(0, 16))

    # ---------------- 数据刷新 ----------------
    def refresh_tags(self):
        papers = self.db.fetch_all()
        cats, venues, tags = set(), set(), set()
        for p in papers:
            for c in (p.categories or []):
                cats.add(c)
            if p.venue:
                venues.add(p.venue)
            for t in (p.user_tags or "").split(","):
                t = t.strip()
                if t:
                    tags.add(t)
        self.g_cat.set_options(sorted(cats))
        self.g_venue.set_options(sorted(venues))
        self.g_tag.set_options(sorted(tags))

    def _age_days(self, paper):
        d = paper.first_submitted_date or paper.first_announced_date
        if not d:
            return None
        return (datetime.datetime.now() - d).days

    def _apply_filters(self, papers):
        kw = self.kw_var.get().strip().lower()
        age_val = ""
        for l, v in AGE_OPTIONS:
            if self.age_var.get().startswith(l):
                age_val = v
                break
        cats = set(self.g_cat.selected())
        venues = set(self.g_venue.selected())
        sources = set(self.g_src.selected())
        langs = set(self.g_lang.selected())
        tags = set(self.g_tag.selected())
        read = self.read_var.get()
        fav = self.fav_var.get()
        df, du = self.df_var.get().strip(), self.du_var.get().strip()

        out = []
        for p in papers:
            title = (p.title or "")
            abs_ = (p.abstract_translated or p.abstract or "")
            if kw and kw not in (title + " " + abs_).lower():
                continue
            age = self._age_days(p)
            if age_val != "" and (age is None or age > int(age_val)):
                continue
            cat_ok = (not cats) or bool(set(p.categories or []) & cats)
            ven_ok = (not venues) or (p.venue in venues)
            if cats and venues:
                ok = cat_ok or ven_ok
            else:
                ok = cat_ok and ven_ok
            if not ok:
                continue
            if sources and (p.source or "arXiv") not in sources:
                continue
            if langs and (p.language or "en") not in langs:
                continue
            if tags and not (set((p.user_tags or "").split(",")) & tags):
                continue
            if read != "all" and (p.read_status or "unread") != read:
                continue
            if fav and not p.favorite:
                continue
            pdff = self.pdf_var.get()
            if pdff == "downloaded" and not p.local_pdf:
                continue
            if pdff == "none" and p.local_pdf:
                continue
            fs = p.first_submitted_date.strftime("%Y-%m-%d") if p.first_submitted_date else ""
            if df and fs and fs < df:
                continue
            if du and fs and fs > du:
                continue
            out.append(p)
        return out

    def _sort_papers(self, papers):
        key = dict(SORT_OPTIONS)[self.sort_var.get()]
        if key == "date":
            papers.sort(key=lambda p: (p.first_submitted_date or datetime.datetime.min), reverse=True)
        elif key == "age":
            papers.sort(key=lambda p: (self._age_days(p) if self._age_days(p) is not None else 1e9))
        elif key == "source":
            papers.sort(key=lambda p: p.source or "arXiv")
        elif key == "venue":
            papers.sort(key=lambda p: p.venue or "")
        return papers

    def refresh(self):
        self.all_papers = self.db.fetch_all()
        filtered = self._apply_filters(self.all_papers)
        filtered = self._sort_papers(filtered)
        self._fill_tree(filtered)
        self._fill_stats(filtered)
        dl = sum(1 for p in filtered if p.local_pdf)
        self.count_var.set(f"共 {len(filtered)} 篇" + (f" · 已下载 {dl}" if dl else ""))
        try:
            self.lib_var.set(f"库内 {len(self.all_papers)} 篇")
            if not self.all_papers:
                self.hint_var.set("文献库为空 — 点左上角『开始抓取』或『示例数据』")
            else:
                self.hint_var.set("双击查看详情 · 右键管理 PDF")
        except Exception:
            pass

    def _fill_tree(self, papers):
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(papers):
            age = self._age_days(p)
            chosen = _status_of(p) == "chosen"
            status = "● 入选" if chosen else "○ 已过滤"
            pdf_mark = "↓" if p.url in self._dl_set else ("✓" if p.local_pdf else "—")

            # 前景色优先级：收藏 > 已下载 > 已过滤淡化；背景由斑马纹提供
            tags = []
            if p.favorite:
                tags.append("fav")
            elif p.local_pdf:
                tags.append("dl")
            elif not chosen:
                tags.append("dim")
            tags.append("odd" if i % 2 else "even")

            vals = (p.source or "arXiv",
                    LANG_DISPLAY.get(p.language, (p.language or "en")),
                    p.venue or "—",
                    ",".join((p.categories or [])[:2]) or "—",
                    f"{age} 天" if age is not None else "—",
                    status,
                    "★" if p.favorite else "",
                    "★" * int(p.rating) if p.rating else "—",
                    pdf_mark)
            title = (p.title_translated or p.title or "").strip()
            self.tree.insert("", "end", iid=p.url, text="  " + title[:120],
                              values=vals, tags=tuple(tags))

    def _fill_stats(self, papers):
        by_source, by_venue, by_cat, by_year, by_lang = {}, {}, {}, {}, {}
        chosen = 0
        for p in papers:
            by_source[p.source or "arXiv"] = by_source.get(p.source or "arXiv", 0) + 1
            by_lang[p.language or "en"] = by_lang.get(p.language or "en", 0) + 1
            if p.venue:
                by_venue[p.venue] = by_venue.get(p.venue, 0) + 1
            for c in (p.categories or []):
                by_cat[c] = by_cat.get(c, 0) + 1
            if p.first_submitted_date:
                y = p.first_submitted_date.strftime("%Y")
                by_year[y] = by_year.get(y, 0) + 1
            if _status_of(p) == "chosen":
                chosen += 1
        lines = [f"论文总数: {len(papers)}   入选: {chosen}   已过滤: {len(papers) - chosen}",
                 "— 来源 —"]
        lines += [f"  {k}: {v}" for k, v in sorted(by_source.items(), key=lambda x: -x[1])]
        lines.append("— 语言 —")
        lines += [f"  {LANG_DISPLAY.get(k, k)}: {v}" for k, v in sorted(by_lang.items(), key=lambda x: -x[1])]
        lines.append("— 会议/期刊 —")
        lines += [f"  {k}: {v}" for k, v in sorted(by_venue.items(), key=lambda x: -x[1])][:15]
        lines.append("— 领域 —")
        lines += [f"  {k}: {v}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])][:15]
        lines.append("— 年份 —")
        lines += [f"  {k}: {v}" for k, v in sorted(by_year.items())]
        self.stat_text.delete("1.0", "end")
        self.stat_text.insert("1.0", "\n".join(lines))

    # ---------------- 详情操作 ----------------
    def on_open_detail(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        url = sel[0]
        p = self.db.get_by_url(url)
        if not p:
            return
        self._cur_url = url
        self.d_title.set(p.title or "")
        meta = []
        meta.append(f"作者: {p.authors or '—'}")
        meta.append(f"来源: {p.source or 'arXiv'}  会议: {p.venue or '—'}")
        meta.append(f"领域: {', '.join(p.categories or []) or '—'}")
        meta.append(f"首次提交: {p.first_submitted_date.strftime('%Y-%m-%d') if p.first_submitted_date else '—'}")
        self.d_meta.set("  |  ".join(meta))
        self.d_abs.delete("1.0", "end")
        self.d_abs.insert("1.0", (p.abstract_translated or p.abstract or ""))
        self.d_status.set(p.read_status or "unread")
        self.d_rating.set(p.rating or 0)
        self.d_fav.set(bool(p.favorite))
        self.d_tags.set(p.user_tags or "")
        self.d_notes.delete("1.0", "end")
        self.d_notes.insert("1.0", p.notes or "")
        self.d_ana.delete("1.0", "end")
        self.detail.deiconify()
        self.detail.lift()

    def on_analyze(self):
        p = self.db.get_by_url(self._cur_url)
        if not p:
            return
        r = litman.analyze(p.title or "", p.abstract or "", p.abstract_translated or "")
        txt = ["【关键词】 " + ", ".join(r["keywords"]),
               "【主题】 " + ", ".join(f"{t['topic']}({t['score']:.2f})" for t in r["topics"]),
               "【摘要】 " + r["summary"],
               "【推荐标签】 " + ", ".join(r["suggested_tags"])]
        self.d_ana.delete("1.0", "end")
        self.d_ana.insert("1.0", "\n\n".join(txt))
        self.d_tags.set(", ".join(r["suggested_tags"]))

    def on_save_detail(self):
        if not self._cur_url:
            return
        self.db.update_meta(
            self._cur_url,
            user_tags=self.d_tags.get().strip(),
            notes=self.d_notes.get("1.0", "end").strip(),
            read_status=self.d_status.get(),
            rating=int(self.d_rating.get()),
            favorite=1 if self.d_fav.get() else 0,
        )
        messagebox.showinfo("已保存", "文献元数据已保存")
        self.refresh_tags()
        self.refresh()

    # ---------------- 本地 PDF 库（v0.16.0） ----------------
    def on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._ctx_url = iid
        self._ctx_menu.tk_popup(event.x_root, event.y_root)

    def _ctx_paper(self):
        if not getattr(self, "_ctx_url", None):
            return None
        return self.db.get_by_url(self._ctx_url)

    def on_ctx_download(self):
        p = self._ctx_paper()
        if p and p.pdf_url:
            self._start_download([p])
        else:
            messagebox.showwarning("无可下载", "该论文没有可用的 PDF 链接")

    def on_ctx_open_local(self):
        p = self._ctx_paper()
        if not p:
            return
        path = self.store.local_path(p)
        if path:
            try:
                pdf_store.open_file(path)
            except Exception as e:
                messagebox.showerror("打开失败", str(e))
        else:
            messagebox.showinfo("未下载", "该论文尚未下载本地 PDF")

    def on_ctx_locate(self):
        p = self._ctx_paper()
        if not p:
            return
        path = self.store.local_path(p)
        pdf_store.open_in_explorer(path if path else self.store.directory)

    def on_ctx_delete_local(self):
        p = self._ctx_paper()
        if not p:
            return
        if not self.store.local_path(p):
            messagebox.showinfo("未下载", "该论文没有本地 PDF 文件")
            return
        if not messagebox.askyesno("删除本地文件", f"确定删除本地 PDF：{p.title[:50]}？\n（数据库记录保留）"):
            return
        n, freed = self.store.delete_local([p])
        self.status_var.set(f"已删除 {n} 个本地文件，释放 {pdf_store.human_size(freed)}")
        self.refresh()

    def on_ctx_delete_record(self):
        p = self._ctx_paper()
        if not p:
            return
        if not messagebox.askyesno("删除记录", f"从库中删除该论文记录：{p.title[:50]}？\n（如本地有 PDF 也会一并删除）"):
            return
        n, freed = self.store.delete_local([p])
        removed = self.db.delete_papers([p.url])
        self.status_var.set(f"已删除 {removed} 条记录" + (f"，释放 {pdf_store.human_size(freed)}" if freed else ""))
        self.refresh_tags()
        self.refresh()

    def on_download_selected(self, auto=False):
        if self._dl_busy:
            if not auto:
                messagebox.showinfo("进行中", "已有下载任务在执行，请稍候或点『取消下载』")
            return
        sel = self.tree.selection()
        if sel:
            papers = [self.db.get_by_url(u) for u in sel if u]
        else:
            filtered = self._apply_filters(self.all_papers)
            if not filtered:
                if not auto:
                    messagebox.showwarning("无数据", "当前没有可下载的论文")
                return
            if not auto and not messagebox.askyesno("下载全部", f"未选中论文，是否下载当前筛选出的全部 {len(filtered)} 篇 PDF？"):
                return
            papers = filtered
        papers = [p for p in papers if p and p.pdf_url]
        if not papers:
            if not auto:
                messagebox.showwarning("无可下载", "所选论文没有可用的 PDF 链接（可能缺少 pdf_link）")
            return
        self._start_download(papers)

    def _start_download(self, papers):
        self._dl_busy = True
        self._cancel = make_cancel_flag()
        self._dl_set = {p.url for p in papers}
        for p in papers:
            if self.tree.exists(p.url):
                self.tree.set(p.url, "PDF", "↓")
        self.set_status(f"开始下载 {len(papers)} 篇 PDF…", "busy")
        threading.Thread(target=self._download_worker, args=(papers, self._cancel), daemon=True).start()

    def _download_worker(self, papers, cancel):
        total = len(papers)
        self.after(0, lambda: self.prog.configure(mode="determinate", maximum=total, value=0))

        def on_progress(done, title, status, url=None):
            self.after(0, lambda: self._on_download_progress(done, total, title, status, url))

        try:
            result = self.store.download(papers, on_progress=on_progress, cancel=cancel)
            self.after(0, lambda: self.set_status(f"下载完成：{result.summary()}"))
            self.after(0, self.refresh)
            if result.failed:
                self.after(0, lambda: self._show_download_report(result, papers))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("下载失败", str(e)))
            self.after(0, lambda: self.set_status("下载失败", "error"))
        finally:
            self._dl_set = set()
            self.after(0, lambda: self.prog.configure(mode="indeterminate"))
            self.after(0, self.prog.stop)
            self._dl_busy = False

    def _on_download_progress(self, done, total, title, status, url=None):
        try:
            self.prog.configure(maximum=total, value=done)
        except Exception:
            pass
        self.status_var.set(f"下载中 ({done}/{total}) {title[:28]} … {status}")
        # 在表格 PDF 列实时标记每篇结果，让用户一眼看出哪些没下下来
        if url and self.tree.exists(url):
            if status == "完成":
                self.tree.set(url, "PDF", "✓")
            elif status.startswith("失败") or status == "付费墙" or status.startswith("跳过"):
                self.tree.set(url, "PDF", "✗")

    def _show_download_report(self, result, papers):
        """下载完成后弹出失败明细：逐篇列出 来源/标题/原因/PDF链接，并提供「重试失败项」。

        解决「报错只会在页脚显示、具体原因不明」——把 result.failed 一次性展开给用户看。
        """
        win = tk.Toplevel(self)
        win.title("下载结果 · 失败明细")
        win.geometry("880x500")
        win.minsize(680, 380)
        ui.apply_theme(win)
        win.bind("<Escape>", lambda e: win.destroy())

        sub = (f"成功 {len(result.ok)}  ·  已存在 {len(result.skipped)}  ·  "
               f"失败 {len(result.failed)}")
        ui.HeaderBar(win, title="下载结果", subtitle=sub).pack(fill="x")

        cols = ("来源", "标题", "原因", "PDF 链接")
        frm = tk.Frame(win)
        frm.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        tree = ttk.Treeview(frm, columns=cols, show="headings", height=20)
        for c, w in zip(cols, (90, 300, 220, 250)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w", stretch=False)
        sb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        by_url = {p.url: p for p in papers}
        for url, reason in result.failed:
            p = by_url.get(url)
            src = getattr(p, "source", "") or "?"
            title = (getattr(p, "title", "") or url)[:90]
            pdf = (getattr(p, "pdf_url", "") or "") if p else ""
            tree.insert("", "end", values=(src, title, reason, pdf))

        bar = tk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        failed = [p for p in papers if p.url in {u for u, _ in result.failed}]

        def _retry():
            win.destroy()
            self._start_download(failed)

        ttk.Button(bar, text="重试失败项", command=_retry).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

    def on_cancel_download(self):
        if self._cancel:
            self._cancel.set()
            self.status_var.set("正在取消下载…")

    def on_delete_local_from_detail(self):
        p = self.db.get_by_url(self._cur_url)
        if not p:
            return
        if not self.store.local_path(p):
            messagebox.showinfo("未下载", "该论文尚未下载本地 PDF")
            return
        if not messagebox.askyesno("删除本地文件", "确定删除该论文的本地 PDF 文件？\n（数据库记录保留）"):
            return
        n, freed = self.store.delete_local([p])
        self.status_var.set(f"已删除本地 PDF，释放 {pdf_store.human_size(freed)}")
        self.refresh()

    def open_pdf_library(self):
        win = tk.Toplevel(self)
        win.title("PDF 库管理")
        win.geometry("620x430")
        win.minsize(560, 400)
        win.configure(bg=ui.P["surface"])
        win.bind("<Escape>", lambda e: win.destroy())

        ui.HeaderBar(win, title="本地 PDF 库", subtitle="存储位置 · 占用统计 · 关联与清理").pack(fill="x")

        f = ttk.Frame(win)
        f.pack(fill="both", expand=True, padx=18, pady=16)

        # —— 统计卡片 ——
        ui.SectionTitle(f, "存储概览", "Σ").pack(fill="x", pady=(0, 6))
        card = ui.bordered_box(f)
        card.pack(fill="x")
        ci = ttk.Frame(card)
        ci.pack(fill="x", padx=14, pady=12)

        dir_var = tk.StringVar(value="—")
        files_var = tk.StringVar(value="—")
        orph_var = tk.StringVar(value="—")
        for lab, var in (("存储目录", dir_var), ("PDF 文件", files_var), ("孤立文件", orph_var)):
            r = ttk.Frame(ci)
            r.pack(fill="x", pady=3)
            ttk.Label(r, text=lab, style="Sub.TLabel", width=10).pack(side="left")
            ttk.Label(r, textvariable=var, wraplength=430, justify="left").pack(side="left")

        def refresh_stats():
            s = self.store.stats()
            dir_var.set(s["dir"])
            files_var.set(f"{s['files']} 个    占用 {s['human']}")
            orph_var.set(f"{s['orphans']} 个    占用 {s['orphan_human']}"
                         + ("" if s["orphans"] else "（无需清理）"))

        # —— 操作 ——
        ui.SectionTitle(f, "管理操作", "⚙").pack(fill="x", pady=(16, 6))

        def change_dir():
            d = filedialog.askdirectory(initialdir=self.store.directory, title="选择 PDF 存储目录")
            if not d:
                return
            move = messagebox.askyesno("搬运已有文件", "是否把当前目录里已有的 PDF 一并搬到新目录？")
            moved = self.store.set_directory(d, move_files=move)
            refresh_stats()
            self.set_status(f"PDF 目录已切换为 {d}" + (f"，搬运 {moved} 个文件" if moved else ""))

        def do_scan():
            r = self.store.scan()
            self.refresh()
            messagebox.showinfo("扫描完成", f"重新关联 {r['linked']} 篇，清除失效标记 {r['unlinked']} 条，目录现有 {r['files']} 个 PDF")
            refresh_stats()

        def do_clean():
            if not messagebox.askyesno("清理孤立文件", "确定删除所有数据库未认领的孤立 PDF 文件？此操作不可撤销。"):
                return
            n, freed = self.store.clean_orphans()
            messagebox.showinfo("清理完成", f"已删除 {n} 个孤立文件，释放 {pdf_store.human_size(freed)}")
            refresh_stats()

        def do_clear_local():
            if not messagebox.askyesno("删除全部本地PDF", "确定删除全部本地 PDF 文件（保留数据库记录）？此操作不可撤销。"):
                return
            n, freed = self.store.delete_local(self.db.fetch_downloaded())
            self.refresh()
            messagebox.showinfo("完成", f"已删除 {n} 个本地文件，释放 {pdf_store.human_size(freed)}")
            refresh_stats()

        # 按钮组（必须在上面各回调定义之后创建，否则 command= 引用未定义名字会报错）
        bf = ttk.Frame(f)
        bf.pack(fill="x")
        ttk.Button(bf, text="打开目录", style="Soft.TButton",
                   command=lambda: pdf_store.open_in_explorer(self.store.directory)).pack(side="left")
        ttk.Button(bf, text="更改目录…", command=change_dir).pack(side="left", padx=8)
        ttk.Button(bf, text="扫描并重建关联", command=do_scan).pack(side="left")
        ttk.Button(bf, text="清理孤立文件", style="Danger.TButton",
                   command=do_clean).pack(side="left", padx=8)
        ttk.Button(bf, text="删除全部本地 PDF…", style="Danger.TButton",
                   command=do_clear_local).pack(side="left")

        ttk.Label(f, text="· 扫描：把目录里手工拷入 / 换机后未关联的 PDF 重新认领到文献库\n"
                          "· 清理：删除数据库中已无对应记录的孤立 PDF 文件（不可撤销）",
                  style="Mute.TLabel", justify="left").pack(anchor="w", pady=(12, 0))

        refresh_stats()
        win.transient(self)
        win.grab_set()

    def _open(self, kind):
        """打开原文 / 本地 PDF / 在线 PDF / DOI。"""
        p = self.db.get_by_url(self._cur_url)
        if not p:
            return
        if kind == "local":
            path = self.store.local_path(p)
            if not path:
                messagebox.showinfo("未下载", "该论文尚未下载本地 PDF，可在列表中右键选择『下载 PDF』")
                return
            try:
                pdf_store.open_file(path)
            except Exception as e:
                messagebox.showerror("打开失败", str(e))
            return
        target = {"url": p.url, "pdf": p.pdf_url,
                  "doi": f"https://doi.org/{p.doi}" if p.doi else None}.get(kind)
        if target:
            webbrowser.open(target)
        else:
            messagebox.showinfo("无链接", "该论文没有对应的链接")

    # ---------------- 抓取 ----------------
    def on_crawl(self):
        if self._busy:
            return
        srcs = self.g_src.selected() or list(ALL_SOURCE_NAMES)
        kw = self.kw_var.get().strip()
        # v0.14.0：关键词空时显式提示，避免用户茫然卡住
        if not kw:
            ok = messagebox.askyesno(
                "未填关键词",
                "未填关键词 → 将按日期全量抓取所有勾选平台，耗时较长且可能被限流。\n\n"
                "建议填一个或多个关键词（如 diffusion、rag、mamba）以缩小范围。\n\n"
                "仍要全量抓取吗？",
            )
            if not ok:
                return
        self._busy = True
        self._crawl_done = {"n": 0, "total": len(srcs), "errors": []}
        self.prog.start()
        self.set_status(f"开始抓取（{len(srcs)} 个平台）…", "busy")
        threading.Thread(target=self._crawl_worker, args=(srcs, kw), daemon=True).start()

    def _crawl_worker(self, srcs, kw):
        try:
            # GUI 端若日期 Entry 为空，回退到"最近 30 天"（与 CLI 默认一致），
            # 避免把 None 一路传到 _as_date → strptime 崩溃。
            from datetime import timedelta
            today = datetime.date.today()
            df = self.df_var.get().strip()
            du = self.du_var.get().strip()
            if not df:
                df = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            if not du:
                du = today.strftime("%Y-%m-%d")

            # v0.14.0：实时进度反馈到 GUI（用 on_source_done 钩子）
            def on_start(name):
                self.after(0, lambda: self.status_var.set(f"⏳ 抓取 {name}…"))
            def on_done(name, n):
                self.after(0, lambda n=n, name=name: self._on_source_done(name, n))

            scraper = MultiSourceScraper(
                date_from=df,
                date_until=du,
                optional_keywords=kw or None,
                source_names=srcs,
                ai_venues=DEFAULT_AI_VENUES,
                db_path=get_app_db_path(),
                on_source_start=on_start,
                on_source_done=on_done,
            )
            scraper.fetch_update()
            errs = self._crawl_done.get("errors", [])
            if errs:
                msg = f"抓取完成（{self._crawl_done['n']}/{self._crawl_done['total']} 平台成功，"\
                      f"{len(errs)} 个失败：{', '.join(errs)}）"
            else:
                msg = f"抓取完成（{self._crawl_done['n']}/{self._crawl_done['total']} 平台成功）"
            self.after(0, lambda: self.status_var.set(msg))
            self.after(0, self.refresh_tags)
            self.after(0, self.refresh)
            if getattr(self, "auto_dl_var", None) and self.auto_dl_var.get():
                self.after(0, lambda: self.on_download_selected(auto=True))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "抓取失败",
                f"{e}\n\n常见原因：\n"
                "  · 本机未联网 / 沙箱无外网\n"
                "  · 日期范围格式错误（应为 yyyy-mm-dd）\n"
                "  · 平台 API 限流（可重试或换日期）",
            ))
            self.after(0, lambda: self.set_status("抓取失败", "error"))
        finally:
            self.after(0, self.prog.stop)
            self._busy = False

    def _on_source_done(self, name, n):
        """源完成回调（v0.14.0）：实时更新状态栏，让用户能看见进展而不是干等。"""
        if isinstance(n, BaseException):
            self._crawl_done["errors"].append(name)
        else:
            self._crawl_done["n"] += 1
        done = self._crawl_done["n"] + len(self._crawl_done["errors"])
        total = self._crawl_done["total"]
        if isinstance(n, int):
            self.status_var.set(f"✅ {name} 完成 {n} 篇（{done}/{total}）")
        else:
            self.status_var.set(f"⚠️ {name} 失败：{n}（{done}/{total}）")

    def on_demo(self):
        try:
            import webui
            n = webui._seed_demo(database_path=get_app_db_path())
            self.status_var.set(f"已加载 {n} 条示例数据")
        except Exception as e:
            messagebox.showerror("加载示例失败", str(e))
        self.refresh_tags()
        self.refresh()

    # ---------------- 导出 / 飞书 ----------------
    def on_export(self, fmt):
        filtered = self._apply_filters(self.all_papers)
        if not filtered:
            messagebox.showwarning("无数据", "当前筛选结果为空")
            return
        ext = "md" if fmt == "md" else "csv"
        path = filedialog.asksaveasfilename(defaultextension="." + ext,
                                            filetypes=[(ext.upper(), "*." + ext)])
        if not path:
            return
        if fmt == "md":
            self._export_md(filtered, path)
        else:
            self._export_csv(filtered, path)
        messagebox.showinfo("已导出", f"已导出 {len(filtered)} 篇到 {path}")

    def _export_md(self, papers, path):
        lines = ["# 论文筛选结果\n"]
        for i, p in enumerate(papers, 1):
            lines.append(f"## {i}. {p.title}")
            lines.append(f"- 作者: {p.authors or '—'}")
            lines.append(f"- 来源: {p.source or 'arXiv'} / 会议: {p.venue or '—'}")
            lines.append(f"- 领域: {', '.join(p.categories or []) or '—'}")
            lines.append(f"- 链接: {p.url}")
            if p.pdf_link:
                lines.append(f"- PDF: {p.pdf_link}")
            lines.append("")
            lines.append((p.abstract_translated or p.abstract or "")[:800])
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _export_csv(self, papers, path):
        import csv
        keys = ["title", "title_translated", "authors", "source", "venue",
                "categories", "url", "pdf_link", "doi", "first_submitted_date",
                "read_status", "rating", "favorite", "user_tags", "notes"]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for p in papers:
                row = {k: getattr(p, k, "") for k in keys}
                row["categories"] = ",".join(p.categories or [])
                w.writerow(row)

    def on_feishu(self):
        if not _HAS_FEISHU or not hasattr(feishu_run, "write_to_feishu"):
            messagebox.showwarning("未配置", "未找到飞书写入模块，请确认 run.py")
            return
        filtered = self._apply_filters(self.all_papers)
        if not filtered:
            messagebox.showwarning("无数据", "当前筛选结果为空")
            return
        records = [{
            "标题": p.title, "标题翻译": p.title_translated or "",
            "摘要": (p.abstract or "")[:2000], "摘要翻译": (p.abstract_translated or "")[:2000],
            "分类": ",".join(p.categories or []), "作者": p.authors or "",
            "链接": p.url, "首次提交": p.first_submitted_date.strftime("%Y-%m-%d") if p.first_submitted_date else "",
            "来源": p.source or "arXiv", "期刊会议": p.venue or "",
            "DOI": p.doi or "", "关键词": p.user_tags or "",
            "期刊/会议": p.venue or "",  # 兼容新 _build_fields
        } for p in filtered]
        try:
            feishu_run.write_to_feishu(records)
            messagebox.showinfo("飞书", f"已写入 {len(records)} 条到飞书多维表格")
        except Exception as e:
            messagebox.showerror("飞书写入失败",
                                 f"{e}\n（请在工具栏「飞书设置」里填 4 个密钥）")

    def open_feishu_settings(self):
        FeishuSettingsDialog(self)

    # ---------------- 主题分布 ----------------
    def on_topic_stats(self):
        if self._busy:
            return
        filtered = self._apply_filters(self.all_papers)
        self._busy = True
        self.prog.start()
        self.set_status("正在分析主题分布…", "busy")
        threading.Thread(target=self._topic_worker, args=(filtered,), daemon=True).start()

    def _topic_worker(self, papers):
        try:
            score = {}
            for p in papers:
                txt = (p.abstract_translated or p.abstract or p.title or "")
                if not txt.strip():
                    continue
                topics = litman.classify_topics(txt)
                for t, s in topics:
                    score[t] = score.get(t, 0) + s
            top = sorted(score.items(), key=lambda x: -x[1])[:15]
            lines = ["— 主题分布（智能分类） —"]
            lines += [f"  {t}: {int(s)}" for t, s in top]
            self.after(0, lambda: self.stat_text.insert("end", "\n" + "\n".join(lines)))
            self.after(0, lambda: self.status_var.set("主题分布分析完成"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("分析失败", str(e)))
        finally:
            self.after(0, self.prog.stop)
            self._busy = False

    # ---------------- 重置 ----------------
    def on_reset(self):
        self.kw_var.set("")
        self.age_var.set("全部")
        self.read_var.set("all")
        self.fav_var.set(False)
        self.df_var.set("")
        self.du_var.set("")
        self.sort_var.set("按日期(新→旧)")
        for grp in (self.g_cat, self.g_venue, self.g_src, self.g_tag):
            for v in grp.vars.values():
                v.set(False)
        self.refresh()


class FeishuSettingsDialog(tk.Toplevel):
    """配置飞书 4 个密钥的对话框（v0.18.0）。

    替代环境变量：把 4 个值存到 %LOCALAPPDATA%\\ArxivCrawler\\config.json，
    run.py 写入时优先从这里读，环境变量次之，默认值兜底。
    """

    _FIELDS = [
        ("FEISHU_APP_ID",      "App ID",        "cli_ 开头，应用的唯一标识"),
        ("FEISHU_APP_SECRET",  "App Secret",    "与 App ID 配对的应用密钥"),
        ("FEISHU_APP_TOKEN",   "App Token",     "多维表格的 app_token（链接里 bseXXX 部分）"),
        ("FEISHU_TABLE_ID",    "Table ID",      "数据表的 table_id（tblXXX 开头）"),
    ]

    def __init__(self, master):
        super().__init__(master)
        self.title("飞书设置")
        # 修复 v0.20.3：原 640x520 在 DPI 缩放下装不下 header+说明+4 行表单+hint+消息+按钮，
        # 按钮 frame 又是 f 的子控件（共享垂直空间），被挤到可视区外。改成三段式布局：
        # 顶部 header / 中间表单(expand) / 底部按钮条；按钮 parent 改为 self 始终贴底。
        self.geometry("640x600")
        self.minsize(640, 520)
        self.resizable(True, True)
        self.transient(master)
        ui.apply_theme(self)
        F = ui.fonts(self)

        # 1) 顶部 brand bar
        ui.HeaderBar(self, title="飞书多维表格 · 密钥配置",
                     subtitle="填一次即可，保存到本机配置；优先于环境变量",
                     version=f"v{APP_VERSION}").pack(side="top", fill="x")

        # 3) 底部按钮条——parent 改 self，始终贴底；与表单完全解耦，
        #    即便用户把窗口拉得很高，按钮也只在底部，不会被推走。
        btn_bar = ttk.Frame(self)
        btn_bar.pack(side="bottom", fill="x", padx=22, pady=(8, 14))
        ttk.Button(btn_bar, text="测试连接", style="Soft.TButton",
                   command=self._test).pack(side="left")
        ttk.Button(btn_bar, text="关闭", command=self.destroy, width=10).pack(side="right")
        ttk.Button(btn_bar, text="保存", style="Accent.TButton",
                   command=self._save, width=12).pack(side="right", padx=(0, 8))

        # 2) 中间表单——上下两个空白 frame 抢走剩余空间，按钮已固定在底，
        #    此处只用 pad 控制上下间距，不会再被按钮挤掉。
        f = ttk.Frame(self)
        f.pack(side="top", fill="both", expand=True, padx=22, pady=(14, 4))

        ui.SectionTitle(f, "飞书应用凭证（4 项必填）").pack(fill="x", pady=(0, 6))
        ttk.Label(f,
                  text="在飞书开放平台 https://open.feishu.cn 创建「企业自建应用」"
                       "并添加「多维表格」权限，把凭证填到下面。",
                  style="Mute.TLabel", wraplength=580, justify="left").pack(anchor="w", pady=(0, 10))

        cfg = pdf_store.load_config()
        self.vars = {}
        for key, label, hint in self._FIELDS:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=14, anchor="e").pack(side="left")
            v = tk.StringVar(value=cfg.get(key, ""))
            ent = ttk.Entry(row, textvariable=v, font=F["mono"], show="*" if "SECRET" in key else "")
            ent.pack(side="left", fill="x", expand=True, padx=(8, 6))
            # 显隐切换（仅 SECRET）
            if "SECRET" in key:
                self._show = tk.BooleanVar(value=False)
                def _toggle(ent=ent, var=self._show):
                    var.set(not var.get())
                    ent.config(show="" if var.get() else "*")
                ttk.Checkbutton(row, text="显示", style="Small.TCheckbutton",
                                variable=self._show, command=_toggle).pack(side="left")
            self.vars[key] = v
            ttk.Label(f, text=hint, style="Mute.TLabel").pack(anchor="w", padx=(126, 0))

        self.msg = tk.StringVar()
        ttk.Label(f, textvariable=self.msg, style="Success.TLabel",
                  wraplength=580, justify="left").pack(anchor="w", pady=(12, 0))

        self.grab_set()

    def _save(self):
        cfg = pdf_store.load_config()
        for k, v in self.vars.items():
            val = v.get().strip()
            if val:
                cfg[k] = val
            else:
                cfg.pop(k, None)
        if pdf_store.save_config(cfg):
            self.msg.set("✓ 已保存。下次写入飞书将使用新密钥。")
            self.after(1800, lambda: self.msg.set(""))
        else:
            # 失败：明确告知路径与可能原因，不再假装成功
            # （旧版 pdf_store.save_config 用 except:pass 吞掉所有错误，
            #  导致“点了保存却没保存”却还显示“已保存”，用户被误导）
            lp = os.environ.get("LOCALAPPDATA", "")
            self.msg.set(
                "✗ 保存失败：无法写入本机配置文件\n"
                f"{lp}\\ArxivCrawler\\config.json\n"
                "请检查该目录权限，或被安全软件 / 杀毒软件拦截。")

    def _test(self):
        try:
            import requests
            # 先把当前填的值存到 config 再测，避免默认值的误导
            self._save()
            from run import _load_feishu_config  # 同步读最新
            app_id, app_secret, _, _ = _load_feishu_config()
            r = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret}, timeout=8)
            data = r.json()
            if data.get("code") == 0:
                self.msg.set(f"✓ 鉴权成功（token 有效期 {data.get('expire')} 秒）")
            else:
                self.msg.set(f"✗ 鉴权失败：{data.get('msg')}（code={data.get('code')}）")
        except Exception as e:
            self.msg.set(f"✗ 连接失败：{e}")


class ActivationDialog(tk.Tk):
    """未激活时弹出的模态激活窗口；激活成功才放行主程序。"""

    def __init__(self):
        super().__init__()
        self.title("激活 ArxivCrawler")
        self.geometry("600x430")
        self.resizable(False, False)
        self.result = False
        ui.apply_theme(self)
        F = ui.fonts(self)

        ui.HeaderBar(self, title="激活 ArxivCrawler",
                     subtitle="首次使用需绑定本机设备", version=f"v{APP_VERSION}").pack(fill="x")

        f = ttk.Frame(self)
        f.pack(fill="both", expand=True, padx=22, pady=18)

        ui.SectionTitle(f, "第 1 步 · 本机设备请求码", "1/").pack(fill="x", pady=(0, 4))
        ttk.Label(f, text="把下面这串请求码发给授权方，用于生成与本机绑定的激活码",
                  style="Mute.TLabel").pack(anchor="w", pady=(0, 6))
        self.req_var = tk.StringVar(value=app_license.get_request_code())
        req = ttk.Entry(f, textvariable=self.req_var, state="readonly", font=F["mono"])
        req.pack(fill="x")
        ttk.Button(f, text="复制请求码", style="Soft.TButton",
                   command=self._copy_req).pack(anchor="e", pady=(6, 0))

        ui.SectionTitle(f, "第 2 步 · 输入激活码", "2/").pack(fill="x", pady=(14, 6))
        self.code_var = tk.StringVar()
        ent = ttk.Entry(f, textvariable=self.code_var, font=F["mono"])
        ent.pack(fill="x")
        ent.bind("<Return>", lambda e: self._activate())

        self.msg = tk.StringVar()
        ttk.Label(f, textvariable=self.msg, style="Danger.TLabel",
                  wraplength=520, justify="left").pack(anchor="w", pady=(10, 0))

        btns = ttk.Frame(f)
        btns.pack(side="bottom", fill="x", pady=(14, 0))
        ttk.Button(btns, text="激活", style="Accent.TButton",
                   command=self._activate, width=14).pack(side="right")
        ttk.Button(btns, text="退出", command=self._quit, width=12).pack(side="right", padx=8)


def main():
    ui.enable_dpi_awareness()   # 高分屏下不糊（必须在创建 Tk 之前）
    if not app_license.is_activated():
        dlg = ActivationDialog()
        dlg.mainloop()
        if not getattr(dlg, "result", False):
            return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
