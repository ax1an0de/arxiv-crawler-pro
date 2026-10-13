#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_app.py — tkinter install wizard for arxiv_crawler's single-file installer.

This is the project-specific copy of the windows-installer-single-exe skill's
wizard template. Only the four constants below differ from the skill default;
keep APP_VERSION in sync with the project VERSION file (build_installer.py
patches it automatically before each build).

Bundled into the payload by installer/build_installer.py and launched by the
Go bootstrap with:  setup.exe --payload <tmp>
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import winreg

APP_NAME = "ArxivCrawler"     # shown in UI, Start Menu, registry
APP_VERSION = "0.14.0"        # patched from VERSION by build_installer.py
PUBLISHER = "WorkBuddy User"
PROGRAM_FOLDER = "程序本体"   # folder inside the payload containing the real app

# v0.14.0：同包内附带的额外 exes（启动器 = 主程序；激活码生成器 = 你自己的工具）
EXTRA_EXES = [
    # (在程序本体/ 里的文件名, 快捷方式显示名, 描述)
    ("LicenseGenerator.exe", "激活码生成器", "给客户签发一次性激活码（仅授权方使用）"),
]


def resource_path(name, payload_dir):
    if payload_dir:
        p = os.path.join(payload_dir, name)
        if os.path.exists(p):
            return p
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def create_shortcut(target, link_path, description="", work_dir=""):
    vbs = os.path.join(tempfile.gettempdir(), "mklink_%d.vbs" % os.getpid())
    with open(vbs, "w", encoding="utf-8") as f:
        f.write('Set oWS = WScript.CreateObject("WScript.Shell")\n')
        f.write('sLink = "%s"\n' % link_path.replace("\\", "\\\\"))
        f.write('Set oLink = oWS.CreateShortcut(sLink)\n')
        f.write('oLink.TargetPath = "%s"\n' % target.replace("\\", "\\\\"))
        if work_dir:
            f.write('oLink.WorkingDirectory = "%s"\n' % work_dir.replace("\\", "\\\\"))
        if description:
            f.write('oLink.Description = "%s"\n' % description)
        f.write("oLink.Save\n")
    try:
        subprocess.run(["cscript", "//nologo", vbs], check=True,
                       creationflags=0x08000000)
    finally:
        try:
            os.remove(vbs)
        except OSError:
            pass


def write_uninstall(install_dir):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\%s" % APP_NAME
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, install_dir)
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ,
                          '"%s\\uninstall.bat"' % install_dir)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
    except OSError as e:
        messagebox.showwarning("卸载信息写入失败",
                               "无法写入注册表卸载项（不影响主程序）：%s" % e)

    bat = os.path.join(install_dir, "uninstall.bat")
    content = (
        "@echo off\n"
        "set \"DIR=%%~dp0\"\n"
        'start "" /min cmd /c "ping 127.0.0.1 -n 2 >nul & '
        'rmdir /s /q \"%%DIR%%\" & '
        'reg delete \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\%s\" /f"\n'
        "exit\n"
    ) % APP_NAME
    with open(bat, "w", encoding="utf-8") as f:
        f.write(content)


def do_install(install_dir, payload_dir, log):
    os.makedirs(install_dir, exist_ok=True)
    src = resource_path(PROGRAM_FOLDER, payload_dir)
    if not os.path.isdir(src):
        raise FileNotFoundError("找不到程序目录: %s" % src)
    log("复制程序文件 -> %s" % install_dir)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(install_dir, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

    main_exe = os.path.join(install_dir, APP_NAME + ".exe")
    if not os.path.exists(main_exe):
        for root, _, files in os.walk(install_dir):
            for fn in files:
                if fn.lower().endswith(".exe"):
                    main_exe = os.path.join(root, fn)
                    break
            else:
                continue
            break

    log("创建快捷方式...")
    desktop = os.path.join(os.path.expandvars("%USERPROFILE%"), "Desktop")
    start_menu = os.path.join(os.path.expandvars("%APPDATA%"),
                              "Microsoft", "Windows", "Start Menu", "Programs")
    os.makedirs(start_menu, exist_ok=True)
    if os.path.exists(main_exe):
        create_shortcut(main_exe, os.path.join(desktop, APP_NAME + ".lnk"),
                        APP_NAME, os.path.dirname(main_exe))
        create_shortcut(main_exe, os.path.join(start_menu, APP_NAME + ".lnk"),
                        APP_NAME, os.path.dirname(main_exe))

    # v0.14.0：附带的额外 exes 也建快捷方式（仅当 install_dir 里有才建）
    for fname, label, desc in EXTRA_EXES:
        extra_exe = os.path.join(install_dir, fname)
        if not os.path.isfile(extra_exe):
            continue
        create_shortcut(extra_exe, os.path.join(desktop, label + ".lnk"),
                        desc, os.path.dirname(extra_exe))
        create_shortcut(extra_exe, os.path.join(start_menu, label + ".lnk"),
                        desc, os.path.dirname(extra_exe))
        log("附带工具: %s" % label)

    log("写入卸载信息...")
    write_uninstall(install_dir)
    log("完成。")


class Wizard(tk.Tk):
    def _has_extra_tools(self):
        """payload 的 程序本体/ 里是否含 EXTRA_EXES 中的任一工具。

        客户版安装包不打包 LicenseGenerator.exe，此处返回 False，
        界面上就不会出现「附带激活码生成器」的字样。
        """
        try:
            prog = resource_path(PROGRAM_FOLDER, self.payload_dir)
            if not os.path.isdir(prog):
                return False
            names = {n.lower() for n in os.listdir(prog)}
            return any(f.lower() in names for f, _, _ in EXTRA_EXES)
        except Exception:
            return False

    def __init__(self, payload_dir):
        super().__init__()
        self.payload_dir = payload_dir
        self.title("安装 %s" % APP_NAME)
        self.geometry("460x260")
        self.resizable(False, False)

        default = os.path.join(os.path.expandvars("%LOCALAPPDATA%"),
                               "Programs", APP_NAME)
        self.install_var = tk.StringVar(value=default)

        ttk.Label(self, text="欢迎安装 %s v%s" % (APP_NAME, APP_VERSION),
                  font=("Segoe UI", 12, "bold")).pack(padx=16, pady=(16, 6))
        ttk.Label(self, text="选择安装位置：").pack(anchor="w", padx=16)
        # 仅当 payload 里真带了额外工具时才提示（客户版不含生成器）
        if self._has_extra_tools():
            ttk.Label(self, text="（本包附带「激活码生成器」，仅授权方使用）",
                      foreground="#888").pack(anchor="w", padx=16)

        row = ttk.Frame(self)
        row.pack(fill="x", padx=16, pady=(4, 12))
        ttk.Entry(row, textvariable=self.install_var).pack(side="left",
                                                            fill="x", expand=True)

        ttk.Button(row, text="浏览...", command=self.browse).pack(side="left", padx=6)

        self.log = tk.StringVar(value="准备就绪。")
        ttk.Label(self, textvariable=self.log, foreground="#555").pack(
            anchor="w", padx=16)

        self.install_btn = ttk.Button(self, text="安装", command=self.install)
        self.install_btn.pack(side="right", padx=16, pady=16)
        ttk.Button(self, text="退出", command=self.destroy).pack(
            side="right", pady=16)

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.install_var.get())
        if d:
            self.install_var.set(d)

    def install(self):
        self.install_btn.config(state="disabled")
        try:
            do_install(self.install_var.get(), self.payload_dir,
                       lambda m: self.log.set(m))
            messagebox.showinfo("完成", "%s 已安装到：\n%s" % (APP_NAME,
                                                            self.install_var.get()))
            self.destroy()
        except Exception as e:
            messagebox.showerror("安装失败", str(e))
            self.install_btn.config(state="normal")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", default="")
    args = parser.parse_args()
    Wizard(args.payload).mainloop()


if __name__ == "__main__":
    main()
