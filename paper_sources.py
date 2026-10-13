"""
paper_sources.py — 多平台论文采集层（v0.6.0）

在原有 arXiv 爬虫之上，扩展覆盖人工智能顶会与顶刊。设计原则：
  1. 统一的 PaperSource 抽象：每个平台实现 fetch() 返回 list[Paper]，互不依赖。
  2. 失败隔离：单个平台出错只记录告警并返回 []，绝不拖垮整次抓取。
  3. 复用现有模型：所有平台产出统一的 Paper 对象（已含 source/venue/pdf_link/doi），
     下游的飞书写入 / Markdown / CSV / PDF 下载逻辑完全复用，无需改动。
  4. 并发聚合：MultiSourceScraper 在每个平台各自的线程中运行（避免 arxiv 内部
     asyncio.run 与外层事件循环冲突），按规范化标题去重后合并。

已覆盖平台：
  - arXiv           : 复用 ArxivScraper（预印本，含 cs.CV/AI/LG/CL/IR/MA）
  - SemanticScholar : 元索引，按 venue 过滤覆盖 CVPR/ICCV/ECCV/NeurIPS/ICML/ICLR/
                      AAAI/ACL/EMNLP/NAACL/TPAMI/IJCV/JMLR 等绝大多数 AI 顶会顶刊
  - OpenReview      : ICLR / NeurIPS / ICML 官方录用（含 PDF）
  - CVF             : CVPR / ICCV / ECCV 开放获取平台（openaccess.thecvf.com）
  - ACL Anthology   : ACL / EMNLP / NAACL / EACL / COLING（aclanthology.org）

注：本沙箱无外网，无法实跑联调；所有网络代码均按官方 API 文档编写并做了防御性解析，
    离线单元测试（合成响应/HTML）已验证解析与去重逻辑不崩。首次联网运行请留意平台反爬/限流。
"""

import asyncio
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
from typing import Iterable

import aiohttp
from bs4 import BeautifulSoup
from rich.console import Console

from paper import Paper, PaperDatabase, PaperExporter, infer_language


# 默认聚焦的人工智能顶会与顶刊（用于 SemanticScholar 的 venue 过滤，大小写不敏感子串匹配）
DEFAULT_AI_VENUES = [
    "cvpr", "iccv", "eccv",
    "neurips", "nips", "icml", "iclr", "aaai",
    "acl", "emnlp", "naacl", "eacl", "coling",
    "sigir", "siggraph",
    "tpami", "ijcv", "jmlr", "tmlr",
    "kdd", "www", "wsdm", "cikm",
    "artificial intelligence", "pattern recognition",
    "ieee transactions on pattern analysis",
]

ALL_SOURCE_NAMES = ["arXiv", "SemanticScholar", "OpenReview", "CVF", "ACL",
                    "ChinaXiv", "PLOS", "bioRxiv", "PMC",
                    "Nature", "Science", "IEEE", "ACM", "Springer", "Elsevier",
                    "CNKI", "WanFang"]


def _as_date(d):
    """把 'YYYY-MM-DD' 字符串或 datetime 统一成 datetime（归零到当天）。"""
    if d is None:
        raise ValueError(
            "_as_date 收到 None：请在调用 MultiSourceScraper 时显式传入 date_from / date_until"
        )
    if isinstance(d, datetime):
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime.strptime(d, "%Y-%m-%d")


def _norm_title(title: str) -> str:
    """规范化标题用于去重：去标点、小写、去空白。"""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _venue_matches(venue: str, ai_venues: Iterable[str]) -> bool:
    """判断 venue 字符串是否命中 AI 顶会/顶刊（大小写不敏感子串匹配）。"""
    if not venue:
        return False
    low = venue.lower()
    return any(tok in low for tok in ai_venues)


class PaperSource:
    """所有论文平台的统一抽象。fetch() 为同步方法（内部自行管理异步/网络），
    便于在独立线程中并发执行而不嵌套事件循环。"""

    name = "base"

    def __init__(self, proxy=None, trans_to=None, ai_venues=None, console=None,
                 db_path="papers.db"):
        self.proxy = proxy
        self.trans_to = trans_to
        self.ai_venues = ai_venues if ai_venues is not None else DEFAULT_AI_VENUES
        self.console = console or Console()
        # db_path：抓取结果最终写回的数据库路径。GUI / WebUI 会传入各自的真实路径
        # （如 %LOCALAPPDATA%/ArxivCrawler/papers.db），不能默认写 CWD，否则抓取到的
        # 论文不会出现在它们读取的库里（v0.19.3 修）。
        self.db_path = db_path

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None) -> list:
        """返回该平台在指定时间范围内、匹配关键词的论文列表（list[Paper]）。
        任何异常都不应向外抛出——记录告警并返回空列表即可。"""
        raise NotImplementedError


# ============================ arXiv ============================
class ArxivSource(PaperSource):
    name = "arXiv"

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        from arxiv_crawler import ArxivScraper

        try:
            scraper = ArxivScraper(
                date_from=date_from,
                date_until=date_until,
                optional_keywords=keywords,
                category_whitelist=whitelist,
                trans_to=None,  # 翻译由 MultiSourceScraper 统一在最后做，避免重复
                proxy=proxy or self.proxy,
                db_path=self.db_path,
            )
            scraper.fetch_update()
            for p in scraper.papers:
                p.source = "arXiv"
            return list(scraper.papers)
        except Exception as e:
            self.console.log(f"[red][ArxivSource] 抓取失败: {e}")
            return []


# ======================= Semantic Scholar =======================
class SemanticScholarSource(PaperSource):
    name = "SemanticScholar"

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(_as_date(date_from), _as_date(date_until), keywords))
        except Exception as e:
            self.console.log(f"[red][SemanticScholar] 抓取失败: {e}")
            return []

    async def _run(self, date_from: datetime, date_until: datetime, keywords):
        papers = []
        base = "https://api.semanticscholar.org/graph/v1/paper/search"
        query = " ".join(keywords) if keywords else "deep learning"
        fields = "title,abstract,authors,year,venue,publicationVenue,externalIds,url,publicationDate,openAccessPdf"
        params = {
            "query": query,
            "year": f"{date_from.year}-{date_until.year}",
            "limit": 100,
            "fields": fields,
        }
        headers = {"User-Agent": "arxiv-crawler/0.6"}
        proxy = self.proxy
        async with aiohttp.ClientSession(trust_env=True) as session:
            while True:
                try:
                    async with session.get(base, params=params, headers=headers,
                                           proxy=proxy, timeout=30) as resp:
                        if resp.status == 429:
                            await asyncio.sleep(3)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                except Exception as e:
                    self.console.log(f"[yellow][SemanticScholar] 请求失败: {e}")
                    break

                for item in data.get("data", []):
                    p = self._to_paper(item, date_from)
                    if p:
                        papers.append(p)

                token = data.get("next")
                if not token:
                    break
                params = dict(params)
                params["token"] = token
                await asyncio.sleep(1)  # 未鉴权限流约 1 req/s，主动限速
        return papers

    def _to_paper(self, item: dict, date_from: datetime):
        title = (item.get("title") or "").strip()
        if not title:
            return None
        # 仅保留命中风控 venue 的论文（arXiv 已由 ArxivSource 覆盖，这里聚焦已发表顶会/顶刊）
        venue = (item.get("venue") or item.get("publicationVenue") or "")
        if self.ai_venues and not _venue_matches(venue, self.ai_venues):
            return None

        abstract = item.get("abstract") or ""
        authors_raw = item.get("authors") or []
        authors = ", ".join(a.get("name", "") for a in authors_raw if a.get("name")) or "Unknown"

        ext = item.get("externalIds") or {}
        arxiv_id = ext.get("ArXiv")
        doi = ext.get("DOI")
        if arxiv_id:
            url = f"https://arxiv.org/abs/{arxiv_id}"
        elif doi:
            url = f"https://doi.org/{doi}"
        elif item.get("url"):
            url = item["url"]
        else:
            url = f"https://www.semanticscholar.org/paper/{item.get('paperId', '')}"

        # 日期：优先 publicationDate，其次 year
        dt = None
        if item.get("publicationDate"):
            try:
                dt = datetime.strptime(item["publicationDate"], "%Y-%m-%d")
            except ValueError:
                pass
        if dt is None and item.get("year"):
            try:
                dt = datetime(int(item["year"]), 1, 1)
            except (ValueError, TypeError):
                pass
        if dt is None:
            dt = date_from

        pdf = (item.get("openAccessPdf") or {}).get("url")
        return Paper(
            url=url,
            title=title,
            categories=[],  # 非 arxiv，领域留空（下游按 venue 分组且直接入选）
            authors=authors,
            abstract=abstract,
            comments=venue,  # 顶会/顶刊名暂存 comments，便于 Markdown 展示
            first_submitted_date=dt,
            first_announced_date=None,
            source="SemanticScholar",
            venue=venue,
            pdf_link=pdf,
            doi=doi,
        )


# ========================= OpenReview =========================
class OpenReviewSource(PaperSource):
    name = "OpenReview"

    # 已知官方 venue 标识（邀请字符串前缀）
    _VENUE_PREFIXES = {
        "ICLR": "ICLR.cc",
        "NeurIPS": "NeurIPS",
        "ICML": "ICML",
    }

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(_as_date(date_from), _as_date(date_until), keywords))
        except Exception as e:
            self.console.log(f"[red][OpenReview] 抓取失败: {e}")
            return []

    async def _run(self, date_from: datetime, date_until: datetime, keywords):
        papers = []
        base = "https://api.openreview.net/notes/search"
        term = " ".join(keywords) if keywords else "deep learning"
        params = {"term": term, "source": "forum", "limit": 200, "offset": 0}
        years = {date_from.year, date_until.year}
        proxy = self.proxy
        async with aiohttp.ClientSession(trust_env=True) as session:
            while True:
                try:
                    async with session.get(base, params=params, proxy=proxy, timeout=30) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                except Exception as e:
                    self.console.log(f"[yellow][OpenReview] 请求失败: {e}")
                    break

                notes = data.get("notes") or []
                if not notes:
                    break
                for note in notes:
                    p = self._to_paper(note, years)
                    if p:
                        papers.append(p)

                if len(notes) < 200:
                    break
                params = dict(params)
                params["offset"] = params.get("offset", 0) + 200
        return papers

    def _to_paper(self, note: dict, years: set):
        try:
            content = note.get("content") or {}
            title = (content.get("title") or note.get("title") or "").strip()
            if not title:
                return None
            abstract = content.get("abstract") or ""
            authors = ", ".join(note.get("authors") or []) or "Unknown"
            forum_id = note.get("id")
            if not forum_id:
                return None
            url = f"https://openreview.net/forum?id={forum_id}"

            # 从 invitation 推断 venue 与年份，例如 "ICLR.cc/2024/Conference/..."
            invitation = note.get("invitation", "") or ""
            venue = ""
            year = None
            m = re.search(r"(ICLR\.cc|NeurIPS|ICML)[./](\d{4})", invitation)
            if m:
                prefix, yr = m.group(1), m.group(2)
                venue = {"ICLR.cc": "ICLR", "NeurIPS": "NeurIPS", "ICML": "ICML"}.get(prefix, prefix)
                year = int(yr)
            if year is not None and years and year not in years:
                return None  # 不在目标年份范围

            dt = datetime(year, 1, 1) if year else datetime(min(years) if years else 2000, 1, 1)
            pdf = note.get("pdf") or content.get("pdf")
            if isinstance(pdf, dict):
                pdf = pdf.get("url")
            doi = None
            return Paper(
                url=url,
                title=title,
                categories=[],
                authors=authors,
                abstract=abstract,
                comments=venue,
                first_submitted_date=dt,
                first_announced_date=None,
                source="OpenReview",
                venue=venue,
                pdf_link=pdf,
                doi=doi,
            )
        except Exception as e:
            self.console.log(f"[yellow][OpenReview] 单条解析跳过: {e}")
            return None


# ============================ CVF ============================
class CvfSource(PaperSource):
    name = "CVF"

    _CONFS = ["CVPR", "ICCV", "ECCV"]

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(_as_date(date_from), _as_date(date_until), keywords))
        except Exception as e:
            self.console.log(f"[red][CVF] 抓取失败: {e}")
            return []

    async def _run(self, date_from: datetime, date_until: datetime, keywords):
        papers = []
        years = range(date_from.year, date_until.year + 1)
        proxy = self.proxy
        kw = [k.lower() for k in keywords] if keywords else None
        async with aiohttp.ClientSession(trust_env=True) as session:
            for year in years:
                for conf in self._CONFS:
                    url = f"https://openaccess.thecvf.com/{conf}{year}?day=all"
                    try:
                        async with session.get(url, proxy=proxy, timeout=30) as resp:
                            if resp.status != 200:
                                continue
                            html = await resp.text()
                    except Exception as e:
                        self.console.log(f"[yellow][CVF] {url} 请求失败: {e}")
                        continue
                    for p in self._parse(html, conf, year, kw):
                        papers.append(p)
        return papers

    def _parse(self, html: str, conf: str, year: int, keywords):
        soup = BeautifulSoup(html, "html.parser")
        out = []
        # 每篇论文：<dt class="ptitle"><a href="...html">Title</a></dt> 后跟 <dd> 含作者与 pdf 链接
        for dt in soup.find_all("dt", class_="ptitle"):
            a = dt.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            detail_href = a.get("href", "")
            pdf_link = None
            # pdf 链接通常位于紧邻的 <dd> 中
            dd = dt.find_next_sibling("dd")
            if dd:
                pdf_a = dd.find("a", href=re.compile(r"\.pdf$", re.I))
                if pdf_a:
                    pdf_link = pdf_a.get("href", "")
                    if pdf_link.startswith("/"):
                        pdf_link = "https://openaccess.thecvf.com" + pdf_link
                authors = dd.get_text(" ", strip=True)
            else:
                authors = "Unknown"
            if keywords and not any(k in title.lower() for k in keywords):
                continue
            url = detail_href if detail_href.startswith("http") else "https://openaccess.thecvf.com" + detail_href
            out.append(Paper(
                url=url,
                title=title,
                categories=[],
                authors=authors or "Unknown",
                abstract="",  # CVF 列表页不含摘要，详情页才有，这里留空以省请求
                comments=f"{conf} {year}",
                first_submitted_date=datetime(year, 1, 1),
                first_announced_date=None,
                source="CVF",
                venue=f"{conf} {year}",
                pdf_link=pdf_link,
                doi=None,
            ))
        return out


# ========================= ACL Anthology =========================
class AclAnthologySource(PaperSource):
    name = "ACL"

    # 事件页 slug -> 展示用 venue 名
    _EVENTS = {
        "acl": "ACL",
        "emnlp": "EMNLP",
        "naacl": "NAACL",
        "eacl": "EACL",
        "coling": "COLING",
    }

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(_as_date(date_from), _as_date(date_until), keywords))
        except Exception as e:
            self.console.log(f"[red][ACL] 抓取失败: {e}")
            return []

    async def _run(self, date_from: datetime, date_until: datetime, keywords):
        papers = []
        years = range(date_from.year, date_until.year + 1)
        proxy = self.proxy
        kw = [k.lower() for k in keywords] if keywords else None
        async with aiohttp.ClientSession(trust_env=True) as session:
            for year in years:
                for slug, venue in self._EVENTS.items():
                    url = f"https://aclanthology.org/events/{slug}-{year}/"
                    try:
                        async with session.get(url, proxy=proxy, timeout=30) as resp:
                            if resp.status != 200:
                                continue
                            html = await resp.text()
                    except Exception as e:
                        self.console.log(f"[yellow][ACL] {url} 请求失败: {e}")
                        continue
                    for p in self._parse(html, year, venue, kw):
                        papers.append(p)
        return papers

    def _parse(self, html: str, year: int, venue: str, keywords):
        soup = BeautifulSoup(html, "html.parser")
        out = []
        # 事件页每篇论文形如 <a href="/2024.acl-long.1/">Title</a>
        for a in soup.find_all("a", href=re.compile(r"/\d{4}\.[a-z\-]+\.\d+/?$")):
            title = a.get_text(strip=True)
            if not title:
                continue
            pid = a.get("href", "").strip("/").rstrip("/")
            if keywords and not any(k in title.lower() for k in keywords):
                continue
            url = f"https://aclanthology.org/{pid}/"
            pdf_link = f"https://aclanthology.org/{pid}.pdf"
            out.append(Paper(
                url=url,
                title=title,
                categories=[],
                authors="",  # 事件页不直接给作者，留空（如需可再抓详情页）
                abstract="",
                comments=f"{venue} {year}",
                first_submitted_date=datetime(year, 1, 1),
                first_announced_date=None,
                source="ACL",
                venue=f"{venue} {year}",
                pdf_link=pdf_link,
                doi=None,
            ))
        return out


# ======================= 聚合调度器 =======================
class MultiSourceScraper:
    """
    并发聚合多个论文平台，产出统一的 Paper 列表。
    对外暴露与 ArxivScraper 兼容的属性（paper_db / paper_exporter / papers），
    以便 run.py 与手机版 main.py 几乎无改动地复用下游逻辑。
    """

    def __init__(
        self,
        date_from,
        date_until,
        optional_keywords,
        category_whitelist=None,
        trans_to=None,
        proxy=None,
        source_names=None,
        ai_venues=None,
        db_path="papers.db",    # v0.20.0：抓取结果最终写回的库路径。GUI/WebUI 传入各自真实路径，
                               # 否则默认写 CWD 的 papers.db，与界面实际读取的库不一致导致抓取结果不显示。
        on_source_start=None,   # v0.14.0：fn(name) — 源开始抓时调用（GUI 实时进度）
        on_source_done=None,    # v0.14.0：fn(name, n_or_exc) — 源抓完时调用（n 为 int，异常为 BaseException）
    ):
        self.date_from = date_from
        self.date_until = date_until
        self.optional_keywords = optional_keywords
        self.category_whitelist = category_whitelist or ["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"]
        self.trans_to = trans_to
        self.proxy = proxy
        self.source_names = source_names or list(ALL_SOURCE_NAMES)
        self.ai_venues = ai_venues
        self.db_path = db_path
        self.papers: list[Paper] = []

        self.console = Console()
        self.paper_db = PaperDatabase(self.db_path)
        self.paper_exporter = PaperExporter(
            date_from, date_until,
            categories_blacklist=[], categories_whitelist=self.category_whitelist,
        )
        self._on_source_start = on_source_start
        self._on_source_done = on_source_done
        self._sources = self._build_sources()

    def _build_sources(self):
        registry = {
            "arXiv": ArxivSource,
            "SemanticScholar": SemanticScholarSource,
            "OpenReview": OpenReviewSource,
            "CVF": CvfSource,
            "ACL": AclAnthologySource,
            # ---- 新增论文源（v0.19.0）----
            "ChinaXiv": ChinaXivSource,        # 中文开放预印本
            "PLOS": PlosSource,                # 开放获取期刊
            "bioRxiv": BiorxivSource,          # 生物预印本
            "PMC": PmcSource,                  # PubMed Central 开放全文
            "Nature": NatureSource,            # 顶刊（元数据，Crossref 聚合）
            "Science": ScienceSource,
            "IEEE": IeeeSource,
            "ACM": AcmSource,
            "Springer": SpringerSource,
            "Elsevier": ElsevierSource,
            "CNKI": CnkiSource,                # 占位：需登录/强反爬
            "WanFang": WanfangSource,          # 占位：需登录/强反爬
        }
        sources = []
        for name in self.source_names:
            cls = registry.get(name)
            if cls is None:
                self.console.log(f"[yellow]未知平台 {name}，已跳过")
                continue
            sources.append(cls(proxy=self.proxy, trans_to=self.trans_to,
                              ai_venues=self.ai_venues, console=self.console,
                              db_path=self.db_path))
        if not sources:
            self.console.log("[red]没有启用任何平台，退化为仅 arXiv")
            sources.append(ArxivSource(proxy=self.proxy, console=self.console))
        return sources

    def fetch_update(self):
        """并发抓取各平台（每个源在独立线程中运行），合并、去重、补全日期、统一翻译、写库。"""
        collected = []

        def _run_source(src):
            try:
                return src.fetch(self.date_from, self.date_until,
                                 self.optional_keywords, self.category_whitelist, self.proxy)
            except Exception as e:  # 双保险：理论上各 source 内部已隔离，这里再兜一层
                return e

        with ThreadPoolExecutor(max_workers=len(self._sources)) as ex:
            futures = {ex.submit(_run_source, s): s for s in self._sources}
            for fut in futures:
                src = futures[fut]
                if self._on_source_start:
                    try:
                        self._on_source_start(src.name)
                    except Exception:
                        pass
                res = fut.result()
                if isinstance(res, Exception):
                    self.console.log(f"[red]{src.name} 抓取异常: {res}")
                    if self._on_source_done:
                        try:
                            self._on_source_done(src.name, res)
                        except Exception:
                            pass
                    continue
                self.console.log(f"[green]{src.name} 抓到 {len(res)} 篇")
                collected.extend(res)
                if self._on_source_done:
                    try:
                        self._on_source_done(src.name, len(res))
                    except Exception:
                        pass

        # 按规范化标题去重：同一篇论文可能同时出现在 arXiv 与各会议平台，
        # 优先保留「带 venue（会议/期刊）信息」的版本，避免丢失平台元数据。
        index = {}
        deduped = []
        for p in collected:
            key = _norm_title(p.title)
            if not key:
                deduped.append(p)
                continue
            if key not in index:
                index[key] = len(deduped)
                deduped.append(p)
            else:
                i = index[key]
                if not deduped[i].venue and p.venue:
                    deduped[i] = p

        self.console.log(
            f"[green]合并去重后共 {len(deduped)} 篇（原始 {len(collected)} 条）"
        )

        # 补齐 first_announced_date（非 arxiv 来源下游需要它做分组/写库）
        for p in deduped:
            if p.first_announced_date is None:
                p.first_announced_date = p.first_submitted_date

        # 语言标记推断（v0.19.0）：统一按「来源 + 标题字符特征」判定 en/zh/ja/ko/multi，
        # 各 source 无需各自处理；中文平台(ChinaXiv/CNKI/WanFang) 默认 zh。
        for p in deduped:
            if not getattr(p, "language", "") or p.language == "en":
                p.language = infer_language(p.title, p.source or "", p.venue or "")

        # 统一翻译（避免各 source 重复翻译）
        if self.trans_to:
            try:
                asyncio.run(self._translate(deduped))
            except Exception as e:
                self.console.log(f"[yellow]翻译阶段出错（保留原文）: {e}")

        self.papers = deduped
        # 写库（INSERT OR REPLACE，重复 URL 自动更新）
        if self.papers:
            self.paper_db.add_papers(self.papers)

    async def _translate(self, papers):
        async def worker(p):
            await p.translate(langto=self.trans_to)
        await asyncio.gather(*[worker(p) for p in papers])

    # ---- 兼容 ArxivScraper 的便捷方法（供手机版/CLI 直接调用）----
    def to_markdown(self, output_dir="./output_llms", filename_format="%Y-%m-%d", meta=False):
        self.paper_exporter.to_markdown(output_dir, filename_format,
                                        self._meta() if meta else None)

    def to_csv(self, output_dir="./output_llms", filename_format="%Y-%m-%d", header=False, csv_config=None):
        self.paper_exporter.to_csv(output_dir, filename_format, header, csv_config)

    async def download_pdfs(self, output_dir="./pdfs", papers=None, max_concurrent=5):
        """平台无关 PDF 下载：arxiv 用推导链接，其它平台用各自提供的 pdf_link。"""
        if papers is None:
            papers = self.papers
        if not papers:
            self.console.log("[yellow]没有论文需要下载")
            return
        output_dir = __import__("pathlib").Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)
        sem = asyncio.Semaphore(max_concurrent)
        async with aiohttp.ClientSession() as session:
            tasks = [self._download_one(session, p, output_dir, sem) for p in papers]
            for coro in asyncio.as_completed(tasks):
                await coro

    async def _download_one(self, session, paper, output_dir, sem):
        async with sem:
            pdf_url = paper.pdf_url
            if not pdf_url or pdf_url == paper.url:
                self.console.log(f"[yellow]⏭️ 无可用 PDF 链接: {paper.title[:40]}")
                return
            # 文件名：优先用论文 ID，否则用标题哈希
            import hashlib
            fname = re.sub(r"[^\w\-.]", "_", pdf_url.split("/")[-1]) or (
                hashlib.md5(paper.title.encode()).hexdigest() + ".pdf"
            )
            if not fname.endswith(".pdf"):
                fname += ".pdf"
            fpath = output_dir / fname
            if fpath.exists():
                self.console.log(f"[yellow]⏭️ 已存在: {fname}")
                return
            try:
                async with session.get(pdf_url, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        fpath.write_bytes(data)
                        self.console.log(f"[green]✅ 下载完成: {fname}")
                    else:
                        self.console.log(f"[red]❌ 下载失败 {fname}: HTTP {resp.status}")
            except Exception as e:
                self.console.log(f"[red]❌ 下载异常 {fname}: {e}")

    def _meta(self):
        return dict(
            repo_url="https://github.com/huiyeruzhou/arxiv_crawler",
            category_whitelist=self.category_whitelist,
            optional_keywords=self.optional_keywords,
            sources=self.source_names,
        )


# ========================= 新增论文源（v0.19.0） =========================
# 说明：沙箱无外网，以下网络代码按官方 API 文档编写并做防御性解析；离线单测
# （合成 JSON/XML/HTML）已验证解析与去重逻辑不崩。首次联网运行请留意各平台
# 反爬/限流。语言标记统一由 MultiSourceScraper.fetch_update 阶段推断。
import urllib.parse


class ChinaXivSource(PaperSource):
    """中科院科技论文预发布平台（中文开放预印本）。best-effort HTML 解析。"""
    name = "ChinaXiv"

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(keywords, proxy))
        except Exception as e:
            self.console.log(f"[red][ChinaXiv] 抓取失败: {e}")
            return []

    async def _run(self, keywords, proxy):
        papers = []
        q = " ".join(keywords) if keywords else "人工智能"
        url = "https://www.chinaxiv.org/search"
        params = {"searchString": q, "page": 1}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(url, params=params, headers=headers,
                                       proxy=proxy, timeout=30) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
        except Exception as e:
            self.console.log(f"[yellow][ChinaXiv] 请求失败: {e}")
            return papers
        papers.extend(self._parse(html))
        return papers

    def _parse(self, html):
        out = []
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return out
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            if "/article/" in href or "/abs/" in href or "/detail/" in href:
                if not href.startswith("http"):
                    href = "https://www.chinaxiv.org" + href
                pdf = href.rstrip("/") + "/download" if "/article/" in href else None
                out.append(Paper(
                    url=href, title=title, categories=[],
                    authors="", abstract="", comments="ChinaXiv",
                    first_submitted_date=datetime(2000, 1, 1),
                    first_announced_date=None, source="ChinaXiv",
                    venue="ChinaXiv", pdf_link=pdf, doi=None,
                ))
        seen, uniq = set(), []
        for p in out:
            if p.title in seen:
                continue
            seen.add(p.title)
            uniq.append(p)
        return uniq


class PlosSource(PaperSource):
    """PLOS 开放获取期刊（有公开 API，PDF 可直接下载）。"""
    name = "PLOS"

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(keywords, proxy))
        except Exception as e:
            self.console.log(f"[red][PLOS] 抓取失败: {e}")
            return []

    async def _run(self, keywords, proxy):
        papers = []
        q = " ".join(keywords) if keywords else "deep learning"
        base = "https://journals.plos.org/plosone/api/v1/search"
        params = {"q": q, "rows": 50, "start": 0,
                  "fq": "doc_type:research_article",
                  "fl": "id,title,abstract,authors,publication_date"}
        headers = {"User-Agent": "arxiv-crawler/0.19"}
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(base, params=params, headers=headers,
                                       proxy=proxy, timeout=30) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as e:
            self.console.log(f"[yellow][PLOS] 请求失败: {e}")
            return papers
        for d in data.get("response", {}).get("docs", []) or []:
            p = self._to_paper(d)
            if p:
                papers.append(p)
        return papers

    def _to_paper(self, d):
        title = (d.get("title") or "").strip()
        if not title:
            return None
        authors_raw = d.get("authors", []) or []
        authors = ", ".join(
            (a.get("first_name", "") + " " + a.get("last_name", "")).strip()
            for a in authors_raw if a.get("last_name")
        ) or "Unknown"
        abstract = (d.get("abstract") or "")
        raw_date = d.get("publication_date")
        dt = None
        if raw_date:
            try:
                dt = datetime.strptime(raw_date[:10], "%Y-%m-%d")
            except Exception:
                dt = None
        if dt is None:
            dt = datetime(2000, 1, 1)
        doi = d.get("id")
        url = f"https://doi.org/{doi}" if doi else ""
        pdf = (f"https://journals.plos.org/plosone/article/file?id={doi}"
               f"&type=printable") if doi else None
        return Paper(
            url=url, title=title, categories=[],
            authors=authors, abstract=abstract or "(PLOS 未提供摘要)",
            comments="PLOS", first_submitted_date=dt, first_announced_date=None,
            source="PLOS", venue="PLOS", pdf_link=pdf, doi=doi,
        )


class BiorxivSource(PaperSource):
    """bioRxiv / medRxiv 预印本平台（best-effort HTML 解析，PDF 可下载）。"""
    name = "bioRxiv"

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(keywords, proxy))
        except Exception as e:
            self.console.log(f"[red][bioRxiv] 抓取失败: {e}")
            return []

    async def _run(self, keywords, proxy):
        papers = []
        q = " ".join(keywords) if keywords else "deep learning"
        url = "https://www.biorxiv.org/search/" + urllib.parse.quote(q)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(url, headers=headers, proxy=proxy,
                                       timeout=30) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
        except Exception as e:
            self.console.log(f"[yellow][bioRxiv] 请求失败: {e}")
            return papers
        papers.extend(self._parse(html))
        return papers

    def _parse(self, html):
        out = []
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return out
        for node in soup.select(".highwire-article-citation, .article-listing"):
            a = node.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href:
                continue
            if not href.startswith("http"):
                href = "https://www.biorxiv.org" + href
            content_id = ""
            m = re.search(r"/content/([^\s?]+)", href)
            if m:
                content_id = m.group(1)
            pdf = (f"https://www.biorxiv.org/content/{content_id}.full.pdf"
                   if content_id else None)
            out.append(Paper(
                url=href, title=title, categories=[],
                authors="", abstract="", comments="bioRxiv",
                first_submitted_date=datetime(2000, 1, 1),
                first_announced_date=None, source="bioRxiv",
                venue="bioRxiv", pdf_link=pdf, doi=None,
            ))
        return out


class PmcSource(PaperSource):
    """PubMed Central（NCBI 开放全文，E-utilities API，PDF 可下载）。"""
    name = "PMC"

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(keywords, proxy))
        except Exception as e:
            self.console.log(f"[red][PMC] 抓取失败: {e}")
            return []

    async def _run(self, keywords, proxy):
        papers = []
        q = " ".join(keywords) if keywords else "deep learning"
        search = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        sp = {"db": "pmc", "term": q + " open access",
              "retmode": "json", "retmax": 30}
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(search, params=sp, proxy=proxy,
                                       timeout=30) as resp:
                    resp.raise_for_status()
                    sdata = await resp.json()
                ids = sdata.get("esearchresult", {}).get("idlist", []) or []
                if not ids:
                    return papers
                fetch = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                fp = {"db": "pmc", "id": ",".join(ids), "retmode": "xml"}
                async with session.get(fetch, params=fp, proxy=proxy,
                                       timeout=30) as resp2:
                    resp2.raise_for_status()
                    xdata = await resp2.text()
        except Exception as e:
            self.console.log(f"[yellow][PMC] 请求失败: {e}")
            return papers
        papers.extend(self._parse_xml(xdata))
        return papers

    def _parse_xml(self, xml_text):
        out = []
        try:
            from xml.etree import ElementTree as ET
            root = ET.fromstring(xml_text)
            # JATS 响应带命名空间（标签形如 {http://jats.nlm.nih.gov}article-title），
            # 直接 find 本地名会全部落空、每篇被 continue 跳过 → PMC 永远 0 结果（v0.20.0 修）。
            # 剥掉 {uri} 命名空间前缀，使本地名可被 find 命中。
            for el in root.iter():
                if isinstance(el.tag, str) and "}" in el.tag:
                    el.tag = el.tag.split("}", 1)[1]
        except Exception:
            return out
        for art in root.iter():
            if art.tag != "article":
                continue
            title_el = art.find(".//article-title")
            title = ("".join(title_el.itertext())).strip() if title_el is not None else ""
            if not title:
                continue
            authors = []
            for a in art.findall(".//contrib[@contrib-type='author']"):
                gn = a.find("name/given-names")
                sn = a.find("name/surname")
                nm = (("".join(gn.itertext()) if gn is not None else "") + " " +
                      ("".join(sn.itertext()) if sn is not None else "")).strip()
                if nm:
                    authors.append(nm)
            abs_el = art.find(".//abstract")
            abstract = ("".join(abs_el.itertext())).strip() if abs_el is not None else ""
            pmc = art.find(".//article-id[@pub-id-type='pmc']")
            pmc_id = ("".join(pmc.itertext())) if pmc is not None else ""
            if not pmc_id:
                continue
            url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/"
            pdf = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}/pdf"
            out.append(Paper(
                url=url, title=title, categories=[],
                authors=", ".join(authors) or "Unknown",
                abstract=abstract or "(PMC 未提供摘要)",
                comments="PMC", first_submitted_date=datetime(2000, 1, 1),
                first_announced_date=None, source="PMC", venue="PubMed Central",
                pdf_link=pdf, doi=None,
            ))
        return out


class CrossrefSource(PaperSource):
    """基于 Crossref REST API 的顶刊/期刊元数据源（稳定、无需鉴权）。

    子类通过 container_title 限定期刊名（作为检索词）。PDF 多为 DOI 链接，
    下载时由 pdf_store 诚实标记「需订阅/付费墙」或尽力下载开放获取版本。
    """
    name = "Crossref"
    container_title = ""

    def fetch(self, date_from, date_until, keywords, whitelist, proxy=None):
        try:
            return asyncio.run(self._run(_as_date(date_from), _as_date(date_until),
                                         keywords, proxy))
        except Exception as e:
            self.console.log(f"[red][{self.name}] 抓取失败: {e}")
            return []

    async def _run(self, date_from, date_until, keywords, proxy):
        papers = []
        base = "https://api.crossref.org/works"
        q = " ".join(keywords) if keywords else "deep learning"
        if self.container_title:
            q = f"{q} {self.container_title}"
        params = {
            "query": q,
            "filter": f"from-pub-date:{date_from:%Y-%m-%d},"
                      f"until-pub-date:{date_until:%Y-%m-%d}",
            "rows": 50,
        }
        headers = {"User-Agent": "arxiv-crawler/0.19 (mailto:user@example.com)"}
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(base, params=params, headers=headers,
                                       proxy=proxy, timeout=30) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as e:
            self.console.log(f"[yellow][{self.name}] 请求失败: {e}")
            return papers
        for item in data.get("message", {}).get("items", []):
            p = self._to_paper(item)
            if p:
                papers.append(p)
        return papers

    def _to_paper(self, item):
        title = " ".join(item.get("title", []) or [])
        if not title:
            return None
        authors_raw = item.get("author", []) or []
        authors = ", ".join(
            (a.get("given", "") + " " + a.get("family", "")).strip()
            for a in authors_raw if a.get("family")
        ) or "Unknown"
        abstract = (item.get("abstract") or "").replace("<jats:p>", "").replace(
            "</jats:p>", "")
        dt = None
        dp = (item.get("published", {}) or {}).get("date-parts")
        if dp:
            try:
                y, m, d = (dp[0] + [1, 1])[:3]
                dt = datetime(int(y), int(m), int(d))
            except Exception:
                dt = None
        if dt is None:
            dt = datetime(2000, 1, 1)
        doi = item.get("DOI")
        url = f"https://doi.org/{doi}" if doi else (item.get("URL") or "")
        pdf = None
        for link in item.get("link", []) or []:
            if (link.get("content-type") or "").startswith("application/pdf"):
                pdf = link.get("URL")
                break
        container = (item.get("container-title") or [""])[0]
        return Paper(
            url=url, title=title, categories=[],
            authors=authors, abstract=abstract or "(Crossref 未提供摘要)",
            comments=container, first_submitted_date=dt, first_announced_date=None,
            source=self.name, venue=container, pdf_link=pdf, doi=doi,
        )


class NatureSource(CrossrefSource):
    name = "Nature"; container_title = "Nature"
class ScienceSource(CrossrefSource):
    name = "Science"; container_title = "Science"
class IeeeSource(CrossrefSource):
    name = "IEEE"; container_title = "IEEE"
class AcmSource(CrossrefSource):
    name = "ACM"; container_title = "ACM"
class SpringerSource(CrossrefSource):
    name = "Springer"; container_title = "Springer"
class ElsevierSource(CrossrefSource):
    name = "Elsevier"; container_title = "Elsevier"


class CnkiSource(PaperSource):
    """知网(CNKI)占位源：需登录且强反爬，暂不支持自动抓取。"""
    name = "CNKI"

    def fetch(self, *args, **kwargs):
        self.console.log("[yellow][CNKI] 知网需登录且强反爬，暂不支持自动抓取（仅占位）")
        return []


class WanfangSource(PaperSource):
    """万方(WanFang)占位源：需登录且强反爬，暂不支持自动抓取。"""
    name = "WanFang"

    def fetch(self, *args, **kwargs):
        self.console.log("[yellow][WanFang] 万方需登录且强反爬，暂不支持自动抓取（仅占位）")
        return []


if __name__ == "__main__":
    # 简单自测：不联网，仅验证类结构可实例化
    ms = MultiSourceScraper(
        date_from="2024-01-01", date_until="2024-12-31",
        optional_keywords=["object detection"], source_names=["arXiv", "SemanticScholar"],
    )
    print("启用平台:", [s.name for s in ms._sources])
    print("OK")
