"""
ArxivCrawler 安装向导（编译为 ArxivCrawler_Setup.exe）

- 把捆绑进来的 ArxivCrawler.exe 复制到安装目录（默认 C:\\Program Files\\ArxivCrawler）
- 创建桌面 + 开始菜单快捷方式
- 写入 Windows 卸载信息（注册表）
- 生成 uninstall.bat 供卸载使用

用 PyInstaller 以 --add-data "dist/ArxivCrawler.exe;app" 与 --uac-admin 编译。
"""
import os
import sys
import shutil
import subprocess

import tkinter as tk
from tkinter import filedialog, messagebox

APP_NAME = "ArxivCrawler"
APP_VERSION = "0.11.0"
DEFAULT_DIR = r"C:\Program Files\ArxivCrawler"


def resource_path(rel):
    """打包后从 _MEIPASS 取资源；未打包时回退到脚本同级的 dist/。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "dist", rel)


def create_shortcut(target, shortcut_path, description):
    ps = (
        "$WScript = New-Object -ComObject WScript.Shell;"
        "$s = $WScript.CreateShortcut('{shortcut}');"
        "$s.TargetPath = '{target}';"
        "$s.Description = '{desc}';"
        "$s.WorkingDirectory = '{work}';"
        "$s.Save()"
    ).format(shortcut=shortcut_path, target=target, desc=description,
             work=os.path.dirname(target))
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, check=False)
    except Exception:
        pass


def write_uninstall(target_dir):
    bat = os.path.join(target_dir, "uninstall.bat")
    reg = r"HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\ArxivCrawler"
    content = (
        "@echo off\n"
        "echo 正在卸载 ArxivCrawler ...\n"
        "taskkill /f /im ArxivCrawler.exe >nul 2>&1\n"
        'rmdir /s /q "%~dp0"\n'
        f'reg delete "{reg}" /f >nul 2>&1\n'
        'del /f "%PUBLIC%\\Desktop\\ArxivCrawler.lnk" >nul 2>&1\n'
        'del /f "%ProgramData%\\Microsoft\\Windows\\Start Menu\\Programs\\ArxivCrawler.lnk" >nul 2>&1\n'
        "echo 卸载完成。\n"
        "pause\n"
    )
    with open(bat, "w", encoding="utf-8") as f:
        f.write(content)
    return bat


def write_registry(target_dir, uninstall_bat):
    reg = r"HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\ArxivCrawler"
    icon = os.path.join(target_dir, "ArxivCrawler.exe")
    cmd = (
        f'reg add "{reg}" /f '
        f'/v DisplayName /t REG_SZ /d "{APP_NAME}" '
        f'&& reg add "{reg}" /f /v DisplayVersion /t REG_SZ /d "{APP_VERSION}" '
        f'&& reg add "{reg}" /f /v UninstallString /t REG_SZ /d "cmd /c \\"{uninstall_bat}\\"" '
        f'&& reg add "{reg}" /f /v DisplayIcon /t REG_SZ /d "\\"{icon}\\"" '
        f'&& reg add "{reg}" /f /v InstallLocation /t REG_SZ /d "\\"{target_dir}\\"" '
        f'&& reg add "{reg}" /f /v NoModify /t REG_DWORD /d 1 '
        f'&& reg add "{reg}" /f /v NoRepair /t REG_DWORD /d 1'
    )
    try:
        subprocess.run(["cmd", "/c", cmd], capture_output=True, check=False)
    except Exception:
        pass


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} 安装向导 {APP_VERSION}")
        self.geometry("520x220")
        self.resizable(False, False)
        try:
            self.style = ttk.Style(self)
            if "clam" in self.style.theme_names():
                self.style.theme_use("clam")
        except Exception:
            pass
        from tkinter import ttk
        self.ttk = ttk

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=f"将 {APP_NAME} {APP_VERSION} 安装到：").pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=8)
        self.dir_var = tk.StringVar(value=DEFAULT_DIR)
        self.entry = ttk.Entry(row, textvariable=self.dir_var)
        self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览...", command=self.browse).pack(side="left", padx=4)

        self.status = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.status, foreground="#555").pack(anchor="w", pady=4)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="安装", command=self.install).pack(side="right", padx=4)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def install(self):
        target = self.dir_var.get().strip()
        if not target:
            messagebox.showerror("错误", "请选择安装目录")
            return
        self.status.set("正在复制文件...")
        self.update()
        try:
            os.makedirs(target, exist_ok=True)
            src_exe = resource_path(os.path.join("app", "ArxivCrawler.exe"))
            if not os.path.exists(src_exe):
                # 未打包时直接找 dist
                src_exe = resource_path("ArxivCrawler.exe")
            if not os.path.exists(src_exe):
                messagebox.showerror("安装失败",
                                     f"找不到 ArxivCrawler.exe（期望位置：{src_exe}）。\n"
                                     "请先运行 build_installer.py 生成应用 exe。")
                return
            dst_exe = os.path.join(target, "ArxivCrawler.exe")
            shutil.copy(src_exe, dst_exe)

            uninstall_bat = write_uninstall(target)
            write_registry(target, uninstall_bat)

            # 快捷方式
            desktop = os.path.join(os.environ.get("PUBLIC", "C:\\Users\\Public"), "Desktop")
            startmenu = os.path.join(os.environ.get("ProgramData",
                                  "C:\\ProgramData"),
                                  "Microsoft", "Windows", "Start Menu", "Programs")
            os.makedirs(desktop, exist_ok=True)
            os.makedirs(startmenu, exist_ok=True)
            create_shortcut(dst_exe, os.path.join(desktop, f"{APP_NAME}.lnk"),
                            f"{APP_NAME} {APP_VERSION}")
            create_shortcut(dst_exe, os.path.join(startmenu, f"{APP_NAME}.lnk"),
                            f"{APP_NAME} {APP_VERSION}")

            messagebox.showinfo("安装完成",
                                f"{APP_NAME} 已安装到：\n{target}\n\n"
                                "桌面与开始菜单已创建快捷方式。\n"
                                "可在「控制面板 → 程序和功能」中卸载。")
            self.destroy()
        except PermissionError:
            messagebox.showerror("权限不足",
                                 "写入该目录被拒绝。请右键安装程序选择「以管理员身份运行」，"
                                 "或把安装目录改到不需要管理员权限的路径（如 D:\\ArxivCrawler）。")
        except Exception as e:
            messagebox.showerror("安装失败", str(e))


if __name__ == "__main__":
    Installer().mainloop()
