"""
pdf_store.py — 本地 PDF 库（v0.16.0）

在此之前，抓取只把论文的**元数据**（标题 / 作者 / 摘要 / 链接）写进 SQLite，
PDF 始终留在远端，界面上的 "PDF" 按钮只是拿浏览器打开链接。

本模块补上"真正把文件下载到本地并纳入管理"的能力：

- 下载：并发受限、断点安全（先写 .part 再原子改名）、可取消、带进度回调
- 记录：下载成功后把文件名写回 papers.local_pdf，界面即可显示 ✓
- 管理：打开 / 定位 / 删除本地文件、扫描目录回填、清理孤立文件、统计占用
- 配置：存储目录与"抓取后自动下载"开关持久化到 config.json

只依赖 aiohttp（项目已有），不引入新的第三方库。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import threading

import aiohttp

# arXiv 等站点对没有 UA 的请求可能直接拒绝，这里统一带一个常规浏览器 UA
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def get_app_dir() -> str:
    """用户可写的应用数据目录（安装到 Program Files 后普通用户也能读写）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ArxivCrawler")
    os.makedirs(d, exist_ok=True)
    return d


def get_config_path() -> str:
    return os.path.join(get_app_dir(), "config.json")


def load_config() -> dict:
    try:
        with open(get_config_path(), "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_config(cfg: dict) -> bool:
    """原子写 config.json。返回 True 表示成功落盘，False 表示失败（调用方应提示）。

    历史缺陷：旧版用 `except Exception: pass` 把写入失败完全静默吞掉，导致 GUI
    「飞书设置」点了“保存”却实际没写进去，却还显示“✓ 已保存”，用户被误导。
    现改为显式返回成功/失败，由 UI 层决定如何告知用户，不再假装成功。
    """
    path = get_config_path()
    d = os.path.dirname(path)
    tmp = path + ".tmp"
    try:
        os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        # 清理可能残留的临时文件，避免下次加载到半截 JSON
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def default_pdf_dir() -> str:
    return os.path.join(get_app_dir(), "pdfs")


def open_in_explorer(path: str) -> None:
    """在文件管理器中定位到某个文件（跨平台尽力而为）。"""
    path = os.path.abspath(path)
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass


def open_file(path: str) -> None:
    """用系统默认程序打开文件。"""
    path = os.path.abspath(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606  (Windows 专有 API)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        raise RuntimeError(f"无法打开文件：{e}")


def human_size(n: int | float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class DownloadResult:
    """一次批量下载的结果汇总。"""

    def __init__(self):
        self.ok: list[str] = []        # 新下载成功的 url
        self.skipped: list[str] = []   # 本地已存在，跳过
        self.failed: list[tuple[str, str]] = []  # (url, 原因)
        self.cancelled = False
        self.bytes = 0

    @property
    def total(self):
        return len(self.ok) + len(self.skipped) + len(self.failed)

    def summary(self) -> str:
        parts = [f"成功 {len(self.ok)}"]
        if self.skipped:
            parts.append(f"已存在 {len(self.skipped)}")
        if self.failed:
            parts.append(f"失败 {len(self.failed)}")
        if self.bytes:
            parts.append(human_size(self.bytes))
        s = "，".join(parts)
        return ("已取消（" + s + "）") if self.cancelled else s


class PdfStore:
    """本地 PDF 文件库：负责下载、命名、删除、扫描与统计。"""

    def __init__(self, db, directory: str | None = None):
        """
        :param db: PaperDatabase 实例（用于回写 local_pdf 字段）
        :param directory: 存储目录；为空则读配置，配置也没有就用默认值
        """
        self.db = db
        cfg = load_config()
        self._dir = directory or cfg.get("pdf_dir") or default_pdf_dir()
        os.makedirs(self._dir, exist_ok=True)

    # ---------------- 目录 ----------------
    @property
    def directory(self) -> str:
        return self._dir

    def set_directory(self, path: str, move_files: bool = False) -> int:
        """切换存储目录。move_files=True 时把已有 PDF 一并搬过去，返回搬运数量。"""
        path = os.path.abspath(path)
        os.makedirs(path, exist_ok=True)
        moved = 0
        if move_files and os.path.abspath(self._dir) != path:
            for fn in os.listdir(self._dir):
                if not fn.lower().endswith(".pdf"):
                    continue
                src, dst = os.path.join(self._dir, fn), os.path.join(path, fn)
                if os.path.exists(dst):
                    continue
                try:
                    os.replace(src, dst)
                    moved += 1
                except OSError:
                    pass
        self._dir = path
        cfg = load_config()
        cfg["pdf_dir"] = path
        save_config(cfg)
        return moved

    # ---------------- 命名与定位 ----------------
    @staticmethod
    def filename_for(paper) -> str:
        """为一篇论文生成稳定、合法的本地文件名。

        arXiv 论文用其 ID（如 2401.12345.pdf）；其它来源用「标题片段_URL 哈希」，
        既可读又不会因标题重复而互相覆盖。
        """
        url = paper.url or ""
        if "arxiv.org/abs/" in url:
            pid = url.split("/abs/")[-1].strip("/").replace("/", "_")
            if pid:
                return f"{pid}.pdf"
        title = _ILLEGAL.sub("_", (paper.title or "paper").strip())[:60].rstrip(" .")
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        return f"{title or 'paper'}_{h}.pdf"

    def path_for(self, paper) -> str:
        """该论文在本地应处的绝对路径（不代表文件已存在）。"""
        return os.path.join(self._dir, paper.local_pdf or self.filename_for(paper))

    def local_path(self, paper) -> str | None:
        """已下载则返回绝对路径，否则 None。"""
        p = self.path_for(paper)
        return p if os.path.isfile(p) else None

    def is_downloaded(self, paper) -> bool:
        return self.local_path(paper) is not None

    # ---------------- 下载 ----------------
    @staticmethod
    def _host_of(url: str) -> str:
        """取 URL 的 host（用于 Referer / 诊断）。"""
        m = re.match(r"https?://([^/]+)", url or "")
        return m.group(1) if m else ""

    @staticmethod
    def _reason_4xx(status, paper) -> str:
        """把 4xx 状态码翻译成可诊断的原因（带来源与 PDF 链接，便于排查）。"""
        url = getattr(paper, "pdf_url", None) or ""
        src = getattr(paper, "source", "") or ""
        if status == 404:
            return f"PDF 链接失效(404)：{url}"
        if status in (401, 403):
            return f"需订阅/被拦截({status})"
        return f"请求被拒({status})：{url}（来源 {src}）"

    async def _fetch_one(self, session, paper, sem, cancel, result, on_progress, lock,
                         proxy=None, max_retries=3):
        """下载单篇 PDF，内置反爬/限流健壮性：

        - 重试：对 429 / 5xx / 连接超时 / 魔数校验失败 做指数退避重试（默认 3 次）
        - 状态码细分：
            · 429  → 读 Retry-After 退避后重试
            · 401/403 → 付费墙 / 需订阅 / 被反爬拦截，直接标记「需订阅」不再浪费请求
            · 其它 4xx → 请求被拒，不重试
            · 5xx   → 服务器抖动，退避重试
        - Referer：部分期刊站（IEEE / Science / Nature 等）下 PDF 需要带 Referer
        - 代理：通过 session.get(proxy=) 正确下传（不再用无效的 _default_proxy）
        """
        if cancel is not None and cancel.is_set():
            return
        async with sem:
            if cancel is not None and cancel.is_set():
                return
            fname = self.filename_for(paper)
            dest = os.path.join(self._dir, fname)
            title = (paper.title or paper.url or "")[:60]

            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                # 本地已有文件：补一条数据库记录即可（覆盖手工拷入 / 换机后重扫的场景）
                if (paper.local_pdf or "") != fname:
                    self.db.set_local_pdf(paper.url, fname)
                result.skipped.append(paper.url)
                await self._report(on_progress, result, title, "已存在", lock, paper.url)
                return

            tmp = dest + ".part"
            url = paper.pdf_url
            if not url:
                # 非 arxiv 且平台没提供 pdf_link：直接跳过，别拿详情页硬下
                src = getattr(paper, "source", "") or "?"
                result.failed.append(
                    (paper.url, f"无 PDF 直链（来源 {src} 未提供，无法下载）"))
                await self._report(on_progress, result, title, "跳过：无可用链接", lock, paper.url)
                return

            referer = getattr(paper, "url", None) or self._host_of(url)
            headers = {"Referer": referer} if referer else {}
            last_err = "未知错误"
            for attempt in range(max_retries):
                if cancel is not None and cancel.is_set():
                    return
                try:
                    async with session.get(url, allow_redirects=True, proxy=proxy,
                                           headers=headers) as resp:
                        status = resp.status
                        # ---- 反爬 / 限流 / 付费墙 细分处理 ----
                        if status == 429:
                            ra = resp.headers.get("Retry-After")
                            try:
                                delay = int(ra) if ra and str(ra).isdigit() else 2 ** attempt
                            except (ValueError, TypeError):
                                delay = 2 ** attempt
                            await asyncio.sleep(min(delay, 30))
                            last_err = "限流(429)，已退避重试"
                            continue
                        if status in (401, 403):
                            # 付费墙 / 需订阅 / 被反爬拦截：不重试，诚实标记
                            result.failed.append((paper.url, f"需订阅/被拦截({status})：{url}"))
                            await self._report(on_progress, result, title, "付费墙", lock, paper.url)
                            return
                        if 400 <= status < 500:
                            reason = self._reason_4xx(status, paper)
                            result.failed.append((paper.url, reason))
                            await self._report(on_progress, result, title, f"失败:{status}", lock, paper.url)
                            return
                        if status >= 500:
                            last_err = f"服务器错误({status})"
                            await asyncio.sleep(min(2 ** attempt, 8))
                            continue
                        # ---- 200 OK：校验内容 ----
                        ctype = (resp.headers.get("Content-Type") or "").lower()
                        first = b""
                        size = 0
                        with open(tmp, "wb") as f:
                            while True:
                                if cancel is not None and cancel.is_set():
                                    raise asyncio.CancelledError()
                                chunk = await resp.content.read(256 * 1024)
                                if not chunk:
                                    break
                                if not first:
                                    first = chunk[:5]
                                f.write(chunk)
                                size += len(chunk)
                        # 有些站点在被限流 / 付费墙 / 人机验证时会返回 200 + HTML 页面，
                        # 若不校验就会把一堆假 PDF 存进库里，这里按魔数拒收。
                        if first[:4] != b"%PDF" and "pdf" not in ctype:
                            raise RuntimeError(
                                "返回网页而非 PDF（多为付费墙/需订阅，或限流验证页）：" + (url or ""))
                        if size == 0:
                            raise RuntimeError("下载内容为空：" + (url or ""))
                        os.replace(tmp, dest)
                        self.db.set_local_pdf(paper.url, fname)
                        result.ok.append(paper.url)
                        result.bytes += size
                        await self._report(on_progress, result, title, "完成", lock, paper.url)
                        return
                except asyncio.CancelledError:
                    self._silent_remove(tmp)
                    result.cancelled = True
                    raise
                except Exception as e:
                    self._silent_remove(tmp)
                    last_err = str(e)
                    # 连接 / 超时 / 魔数等非 4xx 错误：退避后重试
                    await asyncio.sleep(min(2 ** attempt, 8))
                    continue
            # 重试耗尽
            result.failed.append((paper.url, last_err))
            await self._report(on_progress, result, title, f"失败：{last_err}", lock, paper.url)

    @staticmethod
    def _silent_remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    async def _report(on_progress, result, title, status, lock, paper_url=None):
        if on_progress is None:
            return
        async with lock:
            try:
                on_progress(result.total, title, status, paper_url)
            except Exception:
                pass

    async def download_async(self, papers, on_progress=None, cancel=None,
                             max_concurrent=4, proxy=None, timeout=120, ssl=None):
        result = DownloadResult()
        papers = [p for p in papers if p and p.url]
        if not papers:
            return result
        sem = asyncio.Semaphore(max_concurrent)
        lock = asyncio.Lock()
        # 默认按系统 CA 校验证书（ssl=None）。之前 ssl=False 会关闭 TLS 校验，存在中间人风险（v0.20.0 修）。
        # 如确需跳过自签名证书，可由调用方显式传入 ssl=False。
        conn = aiohttp.TCPConnector(limit=max_concurrent, ssl=ssl)
        tmo = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=timeout)
        async with aiohttp.ClientSession(connector=conn, timeout=tmo,
                                         headers={"User-Agent": _UA},
                                         trust_env=True) as session:
            # 代理通过逐请求 session.get(proxy=) 正确下传
            tasks = [asyncio.create_task(
                self._fetch_one(session, p, sem, cancel, result, on_progress, lock,
                                proxy=proxy))
                for p in papers]
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                result.cancelled = True
        return result

    def download(self, papers, on_progress=None, cancel=None,
                 max_concurrent=4, proxy=None, ssl=None) -> DownloadResult:
        """同步入口：在后台线程里调用即可（内部自建事件循环）。"""
        try:
            return asyncio.run(self.download_async(
                papers, on_progress=on_progress, cancel=cancel,
                max_concurrent=max_concurrent, proxy=proxy, ssl=ssl))
        except asyncio.CancelledError:
            r = DownloadResult()
            r.cancelled = True
            return r

    # ---------------- 删除 / 扫描 / 统计 ----------------
    def delete_local(self, papers) -> tuple[int, int]:
        """删除这些论文的本地 PDF 文件并清除数据库标记，返回 (删除数, 释放字节)。"""
        n, freed = 0, 0
        for p in papers:
            path = self.path_for(p)
            if os.path.isfile(path):
                try:
                    freed += os.path.getsize(path)
                    os.remove(path)
                    n += 1
                except OSError:
                    continue
            if p.local_pdf:
                self.db.set_local_pdf(p.url, "")
        return n, freed

    def scan(self) -> dict:
        """扫描存储目录，与数据库对账。

        - 目录里存在、数据库没标记 → 补标记（换机 / 手工拷入后恢复关联）
        - 数据库标记了、文件已不在 → 清标记（用户在资源管理器里删过）
        """
        linked, unlinked = 0, 0
        on_disk = {fn for fn in os.listdir(self._dir)
                   if fn.lower().endswith(".pdf")} if os.path.isdir(self._dir) else set()
        for p in self.db.fetch_all():
            expect = self.filename_for(p)
            has_file = expect in on_disk
            marked = bool(p.local_pdf)
            if has_file and not marked:
                self.db.set_local_pdf(p.url, expect)
                linked += 1
            elif marked and p.local_pdf not in on_disk:
                self.db.set_local_pdf(p.url, "")
                unlinked += 1
        return {"linked": linked, "unlinked": unlinked, "files": len(on_disk)}

    def orphans(self) -> list[str]:
        """目录里存在、但数据库中没有任何论文认领的 PDF 文件名。"""
        if not os.path.isdir(self._dir):
            return []
        known = {self.filename_for(p) for p in self.db.fetch_all()}
        known |= {p.local_pdf for p in self.db.fetch_downloaded() if p.local_pdf}
        return sorted(fn for fn in os.listdir(self._dir)
                      if fn.lower().endswith(".pdf") and fn not in known)

    def clean_orphans(self) -> tuple[int, int]:
        n, freed = 0, 0
        for fn in self.orphans():
            path = os.path.join(self._dir, fn)
            try:
                freed += os.path.getsize(path)
                os.remove(path)
                n += 1
            except OSError:
                continue
        return n, freed

    def stats(self) -> dict:
        files, total = 0, 0
        if os.path.isdir(self._dir):
            for fn in os.listdir(self._dir):
                if not fn.lower().endswith(".pdf"):
                    continue
                try:
                    total += os.path.getsize(os.path.join(self._dir, fn))
                    files += 1
                except OSError:
                    pass
        orphan = self.orphans()
        orphan_bytes = 0
        for fn in orphan:
            try:
                orphan_bytes += os.path.getsize(os.path.join(self._dir, fn))
            except OSError:
                pass
        return {
            "dir": self._dir,
            "files": files,
            "bytes": total,
            "human": human_size(total),
            "orphans": len(orphan),
            "orphan_bytes": orphan_bytes,
            "orphan_human": human_size(orphan_bytes),
        }


def make_cancel_flag() -> threading.Event:
    return threading.Event()
