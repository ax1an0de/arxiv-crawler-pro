"""
一键构建 ArxivCrawler 的 Windows 安装包：
  1) 把 desktop_app.py 编译成单文件 exe：dist/ArxivCrawler.exe（桌面软件本体）
  2) 把 installer_src.py 编译成单文件安装程序：dist/ArxivCrawler_Setup.exe
     （内部捆绑 ArxivCrawler.exe，双击即安装到 Program Files + 快捷方式 + 卸载项）

用法：在已装好依赖的虚拟环境中运行
    python build_installer.py
"""
import os
import sys

import PyInstaller.__main__


HERE = os.path.dirname(os.path.abspath(__file__))
APP_HIDDEN = [
    "paper", "paper_sources", "litman", "webui", "run", "categories",
    "async_translator", "arxiv_time", "arxiv_crawler",
    "aiohttp", "requests", "bs4", "lxml", "rich",
]


def build_app():
    print(">>> [1/2] 构建 ArxivCrawler.exe（桌面软件）...")
    PyInstaller.__main__.run([
        os.path.join(HERE, "desktop_app.py"),
        "--name", "ArxivCrawler",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--hidden-import", "paper",
        "--hidden-import", "paper_sources",
        "--hidden-import", "litman",
        "--hidden-import", "webui",
        "--hidden-import", "run",
        "--hidden-import", "categories",
        "--hidden-import", "async_translator",
        "--hidden-import", "arxiv_time",
        "--hidden-import", "aiohttp",
    ])
    print("<<< ArxivCrawler.exe 构建完成")


def build_setup():
    print(">>> [2/2] 构建 ArxivCrawler_Setup.exe（安装程序）...")
    app_exe = os.path.join(HERE, "dist", "ArxivCrawler.exe")
    assert os.path.exists(app_exe), f"未找到 {app_exe}，请先成功构建应用 exe"
    PyInstaller.__main__.run([
        os.path.join(HERE, "installer_src.py"),
        "--name", "ArxivCrawler_Setup",
        "--onefile",
        "--windowed",
        "--uac-admin",
        "--noconfirm",
        "--clean",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--add-data", f"{app_exe};app",
    ])
    print("<<< ArxivCrawler_Setup.exe 构建完成")


if __name__ == "__main__":
    os.chdir(HERE)
    build_app()
    build_setup()
    print("\n完成。产物：")
    print("  ", os.path.join(HERE, "dist", "ArxivCrawler.exe"))
    print("  ", os.path.join(HERE, "dist", "ArxivCrawler_Setup.exe"))
