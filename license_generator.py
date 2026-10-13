"""
license_generator.py — 激活码生成器 GUI（v0.14.0）

零额外 Python 依赖（仅 tkinter + cryptography）。打包成单文件 Windows exe
（PyInstaller --onefile --windowed）后，可在任意 Windows 机器上运行，
无需 Python 环境。

私钥查找顺序（运行时，v0.20.1 起已迁出项目树）：
  1) 环境变量 ARXIV_LICENSE_KEYS_DIR 指定的目录（文件名用 ArxivCrawler_private_key.pem）
  2) 默认 C:/workbuddy_tools/ArxivCrawler_license_keys/ArxivCrawler_private_key.pem（授权方本机）
  3) PyInstaller 解包目录（sys._MEIPASS，可选）
  4) 当前工作目录 / exe 同目录 / 源脚本目录（含无前缀旧名兼容）
  5) 让用户点 "选择私钥" 自行指定

签发记录 ArxivCrawler_issued.json 默认写在私钥同目录
（即上面的 C:/workbuddy_tools/ArxivCrawler_license_keys/），
便于多台机器协作时共享查重；也可在界面里另存。
"""
import os
import sys
import json
import datetime
import traceback

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# 复用命令行 generate_license 的全部逻辑
import generate_license as gl


APP_TITLE = "ArxivCrawler 激活码生成器"
APP_VERSION = "0.14.0"


# --------------------------------------------------------------------------
# 私钥定位
# --------------------------------------------------------------------------
def _private_key_search_paths():
    """返回可能存在私钥的候选路径列表（按优先级）。"""
    paths = []
    # 0) 迁出项目树后的标准位置（授权方本机，优先级最高）
    env = os.environ.get("ARXIV_LICENSE_KEYS_DIR")
    if env:
        paths.append(os.path.join(env, "ArxivCrawler_private_key.pem"))
    paths.append(os.path.join(
        "C:/workbuddy_tools/ArxivCrawler_license_keys", "ArxivCrawler_private_key.pem"))
    # 1) PyInstaller 解包目录（only when frozen）
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            paths.append(os.path.join(meipass, "ArxivCrawler_private_key.pem"))
            paths.append(os.path.join(meipass, "license_keys", "private_key.pem"))
            paths.append(os.path.join(meipass, "private_key.pem"))
    # 2) 当前工作目录 / exe 同目录
    cwd = os.getcwd()
    paths.append(os.path.join(cwd, "ArxivCrawler_private_key.pem"))
    paths.append(os.path.join(cwd, "license_keys", "private_key.pem"))
    paths.append(os.path.join(cwd, "private_key.pem"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir and exe_dir != cwd:
            paths.append(os.path.join(exe_dir, "ArxivCrawler_private_key.pem"))
            paths.append(os.path.join(exe_dir, "license_keys", "private_key.pem"))
            paths.append(os.path.join(exe_dir, "private_key.pem"))
    # 3) 源脚本所在目录
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        paths.append(os.path.join(here, "ArxivCrawler_private_key.pem"))
        paths.append(os.path.join(here, "license_keys", "private_key.pem"))
    except NameError:
        pass
    # 去重
    seen, uniq = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def find_private_key() -> str:
    """找到第一个真实存在的私钥路径，否则返回空串。"""
    for p in _private_key_search_paths():
        if os.path.exists(p):
            return p
    return ""


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class GeneratorGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("760x560")
        self.minsize(640, 460)
        self.resizable(True, True)

        self._priv_path = tk.StringVar(value=find_private_key())
        self._issued_path = tk.StringVar()  # 默认 = 私钥同目录
        if self._priv_path.get():
            self._issued_path.set(os.path.join(os.path.dirname(self._priv_path.get()),
                                               "issued.json"))
        self._req_var = tk.StringVar()
        self._device_var = tk.StringVar(value="（解析后显示）")
        self._code_var = tk.StringVar()
        self._status_var = tk.StringVar(value="就绪")

        self._build()

    # -------- 布局 --------
    def _build(self):
        pad = {"padx": 8, "pady": 4}

        # 私钥行
        f1 = ttk.LabelFrame(self, text="私钥（签发凭证，绝对不要外发）")
        f1.pack(fill="x", **pad)
        e1 = ttk.Entry(f1, textvariable=self._priv_path)
        e1.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        ttk.Button(f1, text="选择…", command=self._pick_priv).pack(side="left", padx=4, pady=4)
        ttk.Button(f1, text="检查", command=self._check_priv).pack(side="left", padx=4, pady=4)

        # 签发记录行
        f1b = ttk.LabelFrame(self, text="签发记录（防重发）")
        f1b.pack(fill="x", **pad)
        e1b = ttk.Entry(f1b, textvariable=self._issued_path)
        e1b.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        ttk.Button(f1b, text="查看历史", command=self._show_history).pack(side="left", padx=4, pady=4)

        # 请求码区
        f2 = ttk.LabelFrame(self, text="客户请求码（来自客户软件【激活】对话框）")
        f2.pack(fill="x", **pad)
        er = ttk.Entry(f2, textvariable=self._req_var, font=("Consolas", 11))
        er.pack(side="left", fill="x", expand=True, padx=4, pady=4)
        ttk.Button(f2, text="解析预览", command=self._parse_only).pack(side="left", padx=4, pady=4)
        ttk.Button(f2, text="生成激活码", command=self._do_sign).pack(side="left", padx=4, pady=4)

        # 设备信息
        f3 = ttk.LabelFrame(self, text="设备信息")
        f3.pack(fill="x", **pad)
        ttk.Label(f3, textvariable=self._device_var, font=("Consolas", 10))\
            .pack(anchor="w", padx=6, pady=4)

        # 激活码输出
        f4 = ttk.LabelFrame(self, text="激活码（连字符可省略，发给客户即可）")
        f4.pack(fill="both", expand=True, **pad)
        tcode = tk.Text(f4, height=6, font=("Consolas", 11), wrap="word")
        tcode.pack(fill="both", expand=True, padx=4, pady=4)
        tcode.configure(state="disabled")
        self._code_text = tcode

        bf = ttk.Frame(f4)
        bf.pack(fill="x", padx=4, pady=2)
        ttk.Button(bf, text="复制到剪贴板", command=self._copy_code).pack(side="left", padx=2)
        ttk.Button(bf, text="保存到文件…", command=self._save_code).pack(side="left", padx=2)
        ttk.Button(bf, text="清空", command=self._clear_code).pack(side="left", padx=2)

        # 状态栏
        sb = ttk.Frame(self)
        sb.pack(fill="x", side="bottom")
        ttk.Label(sb, textvariable=self._status_var, foreground="#2c3e50")\
            .pack(side="left", padx=8, pady=2)
        ttk.Label(sb, text=f"v{APP_VERSION}  ·  私钥仅本机持有，请妥善保管",
                  foreground="#888").pack(side="right", padx=8, pady=2)

    # -------- 行为 --------
    def _pick_priv(self):
        p = filedialog.askopenfilename(
            title="选择私钥文件",
            filetypes=[("PEM 私钥", "*.pem"), ("全部", "*.*")],
        )
        if p:
            self._priv_path.set(p)
            # 自动联动 issued 路径
            cand = os.path.join(os.path.dirname(p), "issued.json")
            if not self._issued_path.get() or not os.path.exists(self._issued_path.get()):
                self._issued_path.set(cand)

    def _check_priv(self):
        p = self._priv_path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("找不到私钥", f"路径不存在：\n{p}\n\n默认应在 C:/workbuddy_tools/ArxivCrawler_license_keys/ 下；也可点“选择…”手动指定。")
            return
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            with open(p, "rb") as f:
                load_pem_private_key(f.read(), password=None)
            self._status_var.set(f"私钥 OK：{p}")
        except Exception as e:
            messagebox.showerror("私钥无效", f"无法加载私钥：\n{e}")

    def _show_history(self):
        path = self._issued_path.get().strip()
        gl.ISSUED_PATH = path or gl.ISSUED_PATH
        issued = gl._load_issued()
        if not issued:
            messagebox.showinfo("签发记录", "（空）尚未签发任何激活码。")
            return
        lines = [f"已签发 {len(issued)} 张：\n"]
        for req, info in issued.items():
            lines.append(f"  {req}")
            lines.append(f"    serial : {info.get('serial', '?')}")
            lines.append(f"    iat    : {info.get('iat', '?')}")
            lines.append(f"    device : {info.get('device_id', '?')[:16]}…")
            lines.append("")
        messagebox.showinfo("签发记录", "\n".join(lines))

    def _parse_only(self):
        """只解析请求码显示设备 ID，不签发。"""
        req = self._req_var.get().strip()
        if not req:
            messagebox.showwarning("提示", "请先粘贴客户发来的请求码")
            return
        try:
            dev = gl._dev_from_request(req)
            self._device_var.set(
                f"device_id : {dev}\n"
                f"请求码(规范化) : {gl._req_from_dev(dev)}\n"
                f"长度 : {len(dev)} 字符（应为 32 位十六进制）"
            )
            self._status_var.set("已解析（未签发）。")
        except SystemExit as e:
            messagebox.showerror("解析失败", str(e))
        except Exception as e:
            messagebox.showerror("解析失败", f"{e}\n\n{traceback.format_exc()}")

    def _do_sign(self):
        priv = self._priv_path.get().strip()
        if not priv or not os.path.exists(priv):
            messagebox.showerror("缺少私钥",
                                 f"找不到私钥：\n{priv}\n\n请先把 private_key.pem 放到正确路径，"
                                 "或点“选择…”手动指定。")
            return

        req = self._req_var.get().strip()
        if not req:
            messagebox.showwarning("提示", "请先粘贴请求码")
            return

        # 临时覆盖 gl 的路径，让本签发写到界面上对应的 issued.json
        gl.PRIVATE_KEY_PATH = priv
        gl.ISSUED_PATH = self._issued_path.get().strip() or gl.ISSUED_PATH

        try:
            grouped, dev, serial, iat = gl.sign_one(req, allow_resign=False)
        except SystemExit as e:
            messagebox.showerror("签发失败", str(e))
            self._status_var.set("签发失败（已阻止）")
            return
        except Exception as e:
            messagebox.showerror("签发失败", f"{e}\n\n{traceback.format_exc()}")
            return

        self._device_var.set(
            f"device_id : {dev}\n"
            f"serial    : {serial}\n"
            f"iat       : {iat}\n"
            f"签发记录  : {gl.ISSUED_PATH}"
        )
        self._code_text.configure(state="normal")
        self._code_text.delete("1.0", "end")
        self._code_text.insert("1.0", grouped)
        self._code_text.configure(state="disabled")
        self._status_var.set(f"已签发 serial={serial}（写入 {gl.ISSUED_PATH}）")

    def _copy_code(self):
        code = self._code_text.get("1.0", "end").strip()
        if not code:
            messagebox.showwarning("提示", "暂无可复制的激活码")
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self.update()
        self._status_var.set("已复制到剪贴板。")

    def _save_code(self):
        code = self._code_text.get("1.0", "end").strip()
        if not code:
            messagebox.showwarning("提示", "暂无可保存的激活码")
            return
        path = filedialog.asksaveasfilename(
            title="保存激活码",
            defaultextension=".txt",
            initialfile=f"activation_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt",
            filetypes=[("文本", "*.txt"), ("全部", "*.*")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"ArxivCrawler 激活码\n")
            f.write(f"生成时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"签发来源: {self._priv_path.get()}\n")
            f.write(f"设备: {self._device_var.get()}\n")
            f.write(f"激活码（粘贴时连字符可省略）:\n{code}\n")
        self._status_var.set(f"已保存：{path}")

    def _clear_code(self):
        self._code_text.configure(state="normal")
        self._code_text.delete("1.0", "end")
        self._code_text.configure(state="disabled")
        self._req_var.set("")
        self._device_var.set("（解析后显示）")
        self._status_var.set("已清空。")


def main():
    app = GeneratorGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
