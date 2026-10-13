"""版本快照脚本：把当前工作目录复制为带版本号的副本。"""
import os
import shutil

SRC = r"D:\arxiv_crawler-main"
DST = r"D:\arxiv_crawler_v0.20.0"

EXCLUDE_DIRS = {
    ".venv", ".idea", "__pycache__", "output_llms", "pdfs_all",
    ".workbuddy", "dist", "build", "build_exe", "license_keys",
}
EXCLUDE_FILES = {"papers.db"}


def ignore(src, names):
    out = set()
    for n in names:
        full = os.path.join(src, n)
        if os.path.isdir(full):
            if n in EXCLUDE_DIRS or n.startswith("build_stage_"):
                out.add(n)
        else:
            if n in EXCLUDE_FILES or n.endswith(".spec"):
                out.add(n)
            if n.startswith("_t_") and n.endswith(".py"):   # 临时测试脚本
                out.add(n)
            if n in ("_snap.py",):                           # 快照脚本自身
                out.add(n)
    return out


if os.path.exists(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=ignore)
print(f"snapshot -> {DST}")
# 写下 VERSION / CHANGELOG 以备查
for fn in ("VERSION", "CHANGELOG.md"):
    sp = os.path.join(SRC, fn)
    if os.path.isfile(sp):
        shutil.copy2(sp, os.path.join(DST, fn))
print("done")
