"""
统一视觉主题（v0.17.0）
=======================

集中管理整个桌面应用的配色、字体、ttk 控件样式与通用 UI 组件，
让所有窗口（主界面 / 文献详情 / PDF 库 / 激活窗）风格一致。

设计取向：浅色、扁平、留白充足的"现代工作台"风格
  · 全局白底，靠 1px 细线 + 字重 + 间距分层，避免多种背景色拼接产生的色差瑕疵
  · 顶部深色品牌条锚定视觉重心
  · 主色为学术蓝，语义色（成功/警告/危险/收藏）统一取自同一色板

只依赖标准库 tkinter，不引入任何第三方 UI 库。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, font as tkfont

# --------------------------------------------------------------------------
# 色板
# --------------------------------------------------------------------------
PALETTE = {
    # 基础面
    "bg": "#FFFFFF",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",      # 斑马纹 / 只读文本区
    "surface_sunken": "#F1F5F9",   # 表头 / 状态栏
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",

    # 文本
    "text": "#0F172A",
    "text_sub": "#475569",
    "text_mute": "#94A3B8",

    # 主色（学术蓝）
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "primary_press": "#1E40AF",
    "primary_soft": "#EFF6FF",
    "primary_soft2": "#DBEAFE",

    # 语义色
    "success": "#15803D",
    "success_soft": "#DCFCE7",
    "warning": "#B45309",
    "warning_soft": "#FEF3C7",
    "danger": "#DC2626",
    "danger_hover": "#B91C1C",
    "danger_soft": "#FEF2F2",
    "gold": "#D97706",

    # 顶部品牌条
    "header_bg": "#0F172A",
    "header_text": "#F8FAFC",
    "header_sub": "#94A3B8",
    "header_chip": "#1E293B",
}
P = PALETTE

_FAMILY = None


# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------
def font_family(root=None) -> str:
    """挑一个当前系统真实存在的中文友好无衬线字体。"""
    global _FAMILY
    if _FAMILY:
        return _FAMILY
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for cand in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                 "Segoe UI", "Noto Sans CJK SC", "Helvetica"):
        if cand in fams:
            _FAMILY = cand
            break
    else:
        _FAMILY = "TkDefaultFont"
    return _FAMILY


def fonts(root=None) -> dict:
    fam = font_family(root)
    mono = "Consolas"
    try:
        if mono not in set(tkfont.families(root)):
            mono = "Courier New"
    except Exception:
        pass
    return {
        "h1": (fam, 15, "bold"),
        "h2": (fam, 12, "bold"),
        "h3": (fam, 10, "bold"),
        "body": (fam, 10),
        "small": (fam, 9),
        "small_b": (fam, 9, "bold"),
        "tiny": (fam, 8),
        "mono": (mono, 10),
        "mono_small": (mono, 9),
    }


# --------------------------------------------------------------------------
# 高分屏
# --------------------------------------------------------------------------
def enable_dpi_awareness():
    """让窗口在 Windows 高分屏下不糊（必须在创建 Tk 前调用）。"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Win 8.1+
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()        # Win 7
    except Exception:
        pass


# --------------------------------------------------------------------------
# 主题应用
# --------------------------------------------------------------------------
def apply_theme(root) -> ttk.Style:
    """把统一主题应用到某个 Tk / Toplevel 的根，并返回 Style 对象。"""
    style = ttk.Style(root)
    try:
        if "clam" in style.theme_names():
            style.theme_use("clam")   # clam 是可定制程度最高的内置主题
    except Exception:
        pass

    F = fonts(root)
    fam = font_family(root)
    root.configure(bg=P["bg"])

    # 全局默认字体（影响 Menu、messagebox 等原生控件）
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                 "TkHeadingFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name).configure(family=fam, size=10)
        except Exception:
            pass

    # Combobox 下拉列表（原生 Listbox，只能靠 option 数据库着色）
    fspec = "{%s} 10" % fam
    root.option_add("*TCombobox*Listbox.background", P["surface"])
    root.option_add("*TCombobox*Listbox.foreground", P["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", P["primary_soft2"])
    root.option_add("*TCombobox*Listbox.selectForeground", P["text"])
    root.option_add("*TCombobox*Listbox.font", fspec)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    # ---- 容器 / 文本 ----
    style.configure(".", background=P["surface"], foreground=P["text"],
                    font=F["body"], borderwidth=0, focuscolor=P["primary_soft"])
    style.configure("TFrame", background=P["surface"])
    style.configure("Alt.TFrame", background=P["surface_alt"])
    style.configure("Sunken.TFrame", background=P["surface_sunken"])
    style.configure("TLabel", background=P["surface"], foreground=P["text"], font=F["body"])
    style.configure("Sub.TLabel", foreground=P["text_sub"], font=F["small"])
    style.configure("Mute.TLabel", foreground=P["text_mute"], font=F["small"])
    style.configure("H1.TLabel", font=F["h1"], foreground=P["text"])
    style.configure("H2.TLabel", font=F["h2"], foreground=P["text"])
    style.configure("H3.TLabel", font=F["h3"], foreground=P["text"])
    style.configure("Section.TLabel", font=F["small_b"], foreground=P["text_mute"])
    style.configure("Success.TLabel", foreground=P["success"], font=F["small"])
    style.configure("Danger.TLabel", foreground=P["danger"], font=F["small"])
    style.configure("Dot.TLabel", font=(font_family(root), 9))

    # ---- 按钮 ----
    style.configure(
        "TButton", background=P["surface"], foreground=P["text_sub"],
        bordercolor=P["border_strong"], lightcolor=P["surface"], darkcolor=P["surface"],
        relief="flat", borderwidth=1, padding=(12, 7), font=F["body"], anchor="center",
    )
    style.map(
        "TButton",
        background=[("disabled", P["surface_alt"]), ("pressed", P["surface_sunken"]),
                    ("active", P["surface_alt"])],
        foreground=[("disabled", P["text_mute"]), ("active", P["text"])],
        bordercolor=[("active", P["primary"]), ("pressed", P["primary"])],
        lightcolor=[("pressed", P["surface_sunken"]), ("active", P["surface_alt"])],
        darkcolor=[("pressed", P["surface_sunken"]), ("active", P["surface_alt"])],
    )

    style.configure(
        "Accent.TButton", background=P["primary"], foreground="#FFFFFF",
        bordercolor=P["primary"], lightcolor=P["primary"], darkcolor=P["primary"],
        relief="flat", borderwidth=1, padding=(14, 7), font=F["h3"],
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", P["border_strong"]), ("pressed", P["primary_press"]),
                    ("active", P["primary_hover"])],
        foreground=[("disabled", "#FFFFFF")],
        bordercolor=[("pressed", P["primary_press"]), ("active", P["primary_hover"])],
        lightcolor=[("pressed", P["primary_press"]), ("active", P["primary_hover"])],
        darkcolor=[("pressed", P["primary_press"]), ("active", P["primary_hover"])],
    )

    style.configure(
        "Soft.TButton", background=P["primary_soft"], foreground=P["primary_hover"],
        bordercolor=P["primary_soft2"], lightcolor=P["primary_soft"],
        darkcolor=P["primary_soft"], relief="flat", borderwidth=1, padding=(12, 7),
    )
    style.map(
        "Soft.TButton",
        background=[("pressed", P["primary_soft2"]), ("active", P["primary_soft2"])],
        bordercolor=[("active", P["primary"])],
        lightcolor=[("pressed", P["primary_soft2"]), ("active", P["primary_soft2"])],
        darkcolor=[("pressed", P["primary_soft2"]), ("active", P["primary_soft2"])],
    )

    style.configure(
        "Danger.TButton", background=P["surface"], foreground=P["danger"],
        bordercolor=P["border_strong"], lightcolor=P["surface"], darkcolor=P["surface"],
        relief="flat", borderwidth=1, padding=(12, 7),
    )
    style.map(
        "Danger.TButton",
        background=[("pressed", P["danger_soft"]), ("active", P["danger_soft"])],
        foreground=[("active", P["danger_hover"])],
        bordercolor=[("active", P["danger"]), ("pressed", P["danger"])],
        lightcolor=[("pressed", P["danger_soft"]), ("active", P["danger_soft"])],
        darkcolor=[("pressed", P["danger_soft"]), ("active", P["danger_soft"])],
    )

    style.configure(
        "Ghost.TButton", background=P["surface"], foreground=P["text_mute"],
        bordercolor=P["surface"], lightcolor=P["surface"], darkcolor=P["surface"],
        relief="flat", borderwidth=1, padding=(8, 4), font=F["small"],
    )
    style.map(
        "Ghost.TButton",
        background=[("pressed", P["surface_sunken"]), ("active", P["surface_alt"])],
        foreground=[("active", P["primary"])],
        bordercolor=[("active", P["border"])],
        lightcolor=[("pressed", P["surface_sunken"]), ("active", P["surface_alt"])],
        darkcolor=[("pressed", P["surface_sunken"]), ("active", P["surface_alt"])],
    )

    # ---- 输入类 ----
    style.configure(
        "TEntry", fieldbackground=P["surface"], foreground=P["text"],
        bordercolor=P["border_strong"], lightcolor=P["border_strong"],
        darkcolor=P["border_strong"], insertcolor=P["text"],
        padding=(8, 6), relief="flat", borderwidth=1,
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", P["primary"])],
        lightcolor=[("focus", P["primary"])],
        darkcolor=[("focus", P["primary"])],
        fieldbackground=[("disabled", P["surface_alt"]), ("readonly", P["surface_alt"])],
    )

    style.configure(
        "TCombobox", fieldbackground=P["surface"], background=P["surface"],
        foreground=P["text"], bordercolor=P["border_strong"],
        lightcolor=P["border_strong"], darkcolor=P["border_strong"],
        arrowcolor=P["text_sub"], padding=(8, 5), relief="flat", borderwidth=1,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", P["surface"]), ("disabled", P["surface_alt"])],
        bordercolor=[("focus", P["primary"]), ("hover", P["border_strong"])],
        lightcolor=[("focus", P["primary"])],
        darkcolor=[("focus", P["primary"])],
        arrowcolor=[("active", P["primary"])],
    )

    style.configure(
        "TSpinbox", fieldbackground=P["surface"], foreground=P["text"],
        bordercolor=P["border_strong"], lightcolor=P["border_strong"],
        darkcolor=P["border_strong"], arrowcolor=P["text_sub"],
        padding=(6, 4), relief="flat", borderwidth=1,
    )
    style.map("TSpinbox",
              bordercolor=[("focus", P["primary"])],
              lightcolor=[("focus", P["primary"])],
              darkcolor=[("focus", P["primary"])])

    # ---- 勾选 / 单选 ----
    for st in ("TCheckbutton", "TRadiobutton"):
        style.configure(st, background=P["surface"], foreground=P["text_sub"],
                        font=F["body"], focuscolor=P["surface"],
                        indicatorcolor=P["surface"], bordercolor=P["border_strong"],
                        padding=(2, 3))
        style.map(
            st,
            background=[("active", P["surface"])],
            foreground=[("active", P["text"]), ("selected", P["text"]),
                        ("disabled", P["text_mute"])],
            indicatorcolor=[("selected", P["primary"]), ("pressed", P["primary_soft2"])],
            bordercolor=[("active", P["primary"]), ("selected", P["primary"])],
        )
    style.configure("Small.TCheckbutton", font=F["small"])
    style.configure("Small.TRadiobutton", font=F["small"])

    # ---- 滚动条（细、无箭头感） ----
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar", gripcount=0,
            background=P["border_strong"], troughcolor=P["surface_alt"],
            bordercolor=P["surface_alt"], lightcolor=P["surface_alt"],
            darkcolor=P["surface_alt"], arrowcolor=P["text_mute"],
            arrowsize=12, relief="flat", borderwidth=0,
        )
        style.map(f"{orient}.TScrollbar",
                  background=[("pressed", P["text_mute"]), ("active", P["text_mute"])])

    # ---- 表格 ----
    style.configure(
        "Treeview", background=P["surface"], fieldbackground=P["surface"],
        foreground=P["text"], rowheight=30, borderwidth=0, relief="flat", font=F["body"],
    )
    style.map("Treeview",
              background=[("selected", P["primary_soft2"])],
              foreground=[("selected", P["text"])])
    style.configure(
        "Treeview.Heading", background=P["surface_sunken"], foreground=P["text_sub"],
        font=F["small_b"], relief="flat", borderwidth=0, padding=(8, 9),
    )
    style.map("Treeview.Heading",
              background=[("active", P["border"]), ("pressed", P["border"])],
              foreground=[("active", P["text"])])

    # ---- 进度条 ----
    style.configure(
        "Thin.Horizontal.TProgressbar", troughcolor=P["surface_sunken"],
        background=P["primary"], bordercolor=P["surface_sunken"],
        lightcolor=P["primary"], darkcolor=P["primary"], thickness=6, borderwidth=0,
    )

    # ---- 分栏 ----
    style.configure("TPanedwindow", background=P["border"])
    style.configure("Sash", sashthickness=6, gripcount=0)

    # ---- 分页（备用） ----
    style.configure("TNotebook", background=P["surface"], borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=P["surface_alt"], foreground=P["text_sub"],
                    padding=(16, 8), font=F["body"], borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", P["surface"])],
              foreground=[("selected", P["primary"])])

    return style


# --------------------------------------------------------------------------
# 通用组件
# --------------------------------------------------------------------------
class HeaderBar(tk.Frame):
    """顶部深色品牌条：logo 方块 + 标题 + 副标题 + 右侧版本徽标。"""

    def __init__(self, master, title, subtitle="", version="", **kw):
        super().__init__(master, bg=P["header_bg"], height=60, **kw)
        self.pack_propagate(False)
        F = fonts(master)

        left = tk.Frame(self, bg=P["header_bg"])
        left.pack(side="left", fill="y", padx=(16, 0))

        logo = tk.Label(left, text="a", bg=P["primary"], fg="#FFFFFF",
                        font=(font_family(master), 16, "bold"), width=2, height=1)
        logo.pack(side="left", pady=12)

        txt = tk.Frame(left, bg=P["header_bg"])
        txt.pack(side="left", padx=10)
        tk.Label(txt, text=title, bg=P["header_bg"], fg=P["header_text"],
                 font=(font_family(master), 13, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(txt, text=subtitle, bg=P["header_bg"], fg=P["header_sub"],
                     font=F["small"]).pack(anchor="w")

        self.right = tk.Frame(self, bg=P["header_bg"])
        self.right.pack(side="right", fill="y", padx=16)
        if version:
            tk.Label(self.right, text=version, bg=P["header_chip"], fg=P["header_sub"],
                     font=F["small"], padx=10, pady=3).pack(side="right", pady=18)


class SectionTitle(ttk.Frame):
    """侧栏 / 卡片的小节标题：图标 + 标题（大写灰字）+ 右侧可选控件。"""

    def __init__(self, master, text, icon="", **kw):
        super().__init__(master, **kw)
        head = ttk.Frame(self)
        head.pack(fill="x")
        label = f"{icon}  {text}" if icon else text
        ttk.Label(head, text=label, style="Section.TLabel").pack(side="left")
        self.extra = ttk.Frame(head)
        self.extra.pack(side="right")


def hline(master, color=None, pady=0):
    """1px 水平分隔线。"""
    f = tk.Frame(master, height=1, bg=color or P["border"])
    f.pack(fill="x", pady=pady)
    return f


def vline(master, color=None, padx=8):
    """1px 垂直分隔线（工具栏分组用）。"""
    f = tk.Frame(master, width=1, bg=color or P["border"])
    f.pack(side="left", fill="y", padx=padx, pady=6)
    return f


def bordered_box(master, **kw):
    """带 1px 边框的白色容器（tk.Frame，边框颜色可控）。"""
    return tk.Frame(master, bg=P["surface"], highlightthickness=1,
                    highlightbackground=P["border"], highlightcolor=P["border"],
                    bd=0, **kw)


def style_text(widget, mono=False, alt=False, root=None):
    """统一 Text / ScrolledText 外观（原生控件，需手动着色）。"""
    F = fonts(root)
    widget.configure(
        bg=P["surface_alt"] if alt else P["surface"],
        fg=P["text"] if not alt else P["text_sub"],
        insertbackground=P["text"],
        selectbackground=P["primary_soft2"], selectforeground=P["text"],
        relief="flat", borderwidth=0, highlightthickness=1,
        highlightbackground=P["border"], highlightcolor=P["primary"],
        font=F["mono_small"] if mono else F["body"],
        padx=10, pady=8, wrap=widget.cget("wrap"),
    )
    return widget


def style_menu(menu, root=None):
    """统一右键菜单外观。"""
    F = fonts(root)
    try:
        menu.configure(bg=P["surface"], fg=P["text"], activebackground=P["primary_soft2"],
                       activeforeground=P["text"], bd=0, relief="flat",
                       activeborderwidth=0, font=F["body"])
    except Exception:
        pass
    return menu


class StatusBar(tk.Frame):
    """底部状态栏：状态点 + 文本 + 进度条 + 右侧信息。"""

    def __init__(self, master, textvariable, **kw):
        super().__init__(master, bg=P["surface_sunken"], height=30, **kw)
        self.pack_propagate(False)
        F = fonts(master)

        tk.Frame(self, height=1, bg=P["border"]).pack(fill="x", side="top")

        inner = tk.Frame(self, bg=P["surface_sunken"])
        inner.pack(fill="both", expand=True, padx=12)

        self.dot = tk.Label(inner, text="●", bg=P["surface_sunken"], fg=P["success"],
                            font=(font_family(master), 8))
        self.dot.pack(side="left", padx=(0, 6))
        self.label = tk.Label(inner, textvariable=textvariable, bg=P["surface_sunken"],
                              fg=P["text_sub"], font=F["small"], anchor="w")
        self.label.pack(side="left")

        self.right = tk.Frame(inner, bg=P["surface_sunken"])
        self.right.pack(side="right")

    def set_kind(self, kind="info"):
        self.dot.configure(fg={
            "info": P["success"], "busy": P["primary"],
            "warn": P["warning"], "error": P["danger"],
        }.get(kind, P["success"]))
