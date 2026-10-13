#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_installer.py — build arxiv_crawler's single-file Windows installer.

Uses the user-level skill `windows-installer-single-exe`
(~/.workbuddy/skills/windows-installer-single-exe/references) as the canonical
template. Run from the project root:

    python installer/build_installer.py              # 两个版本都出
    python installer/build_installer.py --client     # 只出客户版
    python installer/build_installer.py --full       # 只出自用版

两种版本（v0.15.0 起）
---------------------
* 客户版  dist/ArxivCrawler_Setup_Client.exe
      只含主程序 ArxivCrawler.exe。**这是发给别人的包。**
      装完桌面只有一个「ArxivCrawler」图标，没有激活码生成器。

* 自用版  dist/ArxivCrawler_Setup_Admin.exe
      主程序 + LicenseGenerator.exe（激活码生成器）。**只留在你自己机器上。**

注意：生成器 exe 本身不内嵌私钥，运行时从 C:/workbuddy_tools/ArxivCrawler_license_keys/ArxivCrawler_private_key.pem
（或 ARXIV_LICENSE_KEYS_DIR 指定位置）读取，
所以私钥永远不会进任何安装包。

Requirements on the build machine:
    * Python 3.12 venv with tkinter + aiohttp + pyinstaller  (uses .venv here)
    * Go toolchain (set GO_EXE env, or it defaults to C:/tmp/go/bin/go.exe)
    * C:/workbuddy_tools/vc_redist.x64.exe  (or set VC_REDIST env)
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.expanduser(
    "~/.workbuddy/skills/windows-installer-single-exe/references")

VENV = os.path.join(ROOT, ".venv", "Scripts", "python.exe")  # py3.12 + deps
GO = os.environ.get("GO_EXE", r"C:\tmp\go\bin\go.exe")
VC = os.environ.get("VC_REDIST", r"C:\workbuddy_tools\vc_redist.x64.exe")

WIZARD_SRC = os.path.join(ROOT, "installer", "setup_app.py")
BOOTSTRAP = os.path.join(SKILL, "bootstrap.go")
BUILDER = os.path.join(SKILL, "build_single_exe.py")

OUT_CLIENT = os.path.join(ROOT, "dist", "ArxivCrawler_Setup_Client.exe")
OUT_ADMIN = os.path.join(ROOT, "dist", "ArxivCrawler_Setup_Admin.exe")


def read_version():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        return f.read().strip()


def patched_wizard(version):
    """Return a temp copy of setup_app.py with APP_VERSION synced to VERSION."""
    with open(WIZARD_SRC, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r'APP_VERSION = "[^"]*"',
                 'APP_VERSION = "%s"' % version, src, count=1)
    fd, path = tempfile.mkstemp(suffix=".py", prefix="wiz_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    return path


def patched_app(version):
    """Return a temp copy of desktop_app.py with APP_VERSION pinned to VERSION.

    主程序从 VERSION 文件读版本（开发期方便），但 PyInstaller 默认不带该文件进
    exe；与其给打包模板加 --add-data，这里像 patched_wizard 一样直接在打包前把
    `APP_VERSION = _read_app_version()` 替换为常量，冻结后标题栏版本号即正确。
    """
    with open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r'APP_VERSION = _read_app_version\(\)',
                 'APP_VERSION = "%s"' % version, src, count=1)
    fd, path = tempfile.mkstemp(suffix=".py", prefix="app_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    return path


def build_license_generator(stage):
    """用 PyInstaller 单独打 LicenseGenerator.exe，返回其路径（仅自用版需要）。

    LicenseGenerator 依赖 generate_license.py，cryptography 已装在 .venv。
    该 exe 不内嵌私钥，运行时从 C:/workbuddy_tools/ArxivCrawler_license_keys/ArxivCrawler_private_key.pem
    （或 ARXIV_LICENSE_KEYS_DIR 指定位置）读取。
    """
    from PyInstaller.__main__ import run as pyi_run
    licgen_dist = os.path.join(stage, "licgen_dist")
    os.makedirs(licgen_dist, exist_ok=True)
    pyi_run([
        "--onefile", "--windowed",
        "--name", "LicenseGenerator",
        "--distpath", licgen_dist,
        "--workpath", os.path.join(stage, "licgen_build"),
        "--specpath", stage,
        os.path.join(ROOT, "license_generator.py"),
    ])
    src = os.path.join(licgen_dist, "LicenseGenerator.exe")
    if not os.path.isfile(src):
        raise FileNotFoundError("PyInstaller 未产出 LicenseGenerator.exe：%s" % src)
    return src


def build_one(version, output, stage, licgen_exe=None):
    """跑一次 build_single_exe，产出一个安装包。"""
    wizard = patched_wizard(version)
    app_script = patched_app(version)
    args = [
        VENV, BUILDER,
        "--app-script", app_script,
        "--app-name", "ArxivCrawler",
        "--app-version", version,
        "--vc-redist", VC,
        "--python312", VENV,
        "--go", GO,
        "--bootstrap", BOOTSTRAP,
        "--wizard", wizard,
        "--stage", stage,
        "--output", output,
    ]
    if licgen_exe:
        args += ["--extra-app", "%s=LicenseGenerator.exe" % licgen_exe]
    try:
        subprocess.run(args, check=True)
    finally:
        for p in (wizard, app_script):
            try:
                os.remove(p)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", action="store_true", help="只出客户版")
    ap.add_argument("--full", "--admin", dest="admin", action="store_true",
                    help="只出自用版（含激活码生成器）")
    a = ap.parse_args()
    do_client = a.client or not (a.client or a.admin)
    do_admin = a.admin or not (a.client or a.admin)

    for p, label in ((VENV, "venv python"), (GO, "go"), (VC, "vc_redist"),
                     (BOOTSTRAP, "bootstrap.go"), (BUILDER, "build script")):
        if not os.path.exists(p):
            sys.exit("缺少%s: %s" % (label, p))

    version = read_version()
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)

    if do_client:
        print("\n=== [客户版] 只含主程序 → %s ===" % OUT_CLIENT)
        stage = os.path.join(ROOT, "build_stage_client")
        os.makedirs(stage, exist_ok=True)
        build_one(version, OUT_CLIENT, stage)

    if do_admin:
        print("\n=== [自用版] 主程序 + 激活码生成器 → %s ===" % OUT_ADMIN)
        stage = os.path.join(ROOT, "build_stage_admin")
        os.makedirs(stage, exist_ok=True)
        print("[*] 打包 LicenseGenerator.exe …")
        licgen = build_license_generator(stage)
        build_one(version, OUT_ADMIN, stage, licgen_exe=licgen)

    print("\n完成。")


if __name__ == "__main__":
    main()
