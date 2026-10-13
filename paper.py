import asyncio
import csv
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, UTC
from pathlib import Path

from rich.console import Console
from typing_extensions import Iterable

from async_translator import async_translate
from categories import parse_categories


def infer_language(title: str, source: str = "", venue: str = "") -> str:
    """推断论文语言标记：en / zh / ja / ko / multi / other。

    策略：
    1. 标题字符扫描：检测 Hangul(韩) / 假名(日) / CJK(中)。
       - 含 Hangul              -> ko
       - 含假名(平/片假名)      -> ja（日文由汉字+假名构成）
       - 仅含 CJK(无假名无Hangul) -> zh
       - 同时含 CJK 与 假名/Hangul -> multi（中日/中韩混合）
    2. 无明显字符特征时，按来源默认：中文平台(ChinaXiv/CNKI/WanFang) -> zh，其余 -> en。
    """
    ZH_SOURCES = {"ChinaXiv", "CNKI", "WanFang", "知网", "万方"}
    title = title or ""
    has_hangul = bool(re.search(r"[\uac00-\ud7a3]", title))
    has_kana = bool(re.search(r"[\u3040-\u30ff]", title))
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", title))

    langs = set()
    if has_kana:
        langs.add("ja")              # 假名是日文本质特征，优先判日
    if has_hangul:
        langs.add("ko")
    if has_cjk and not has_kana and not has_hangul:
        langs.add("zh")
    if has_cjk and has_hangul and not has_kana:
        langs.add("multi")           # 中韩混排

    if langs:
        if "multi" in langs or len(langs) > 1:
            return "multi"
        return next(iter(langs))
    if source in ZH_SOURCES:
        return "zh"
    return "en"



@dataclass
class Paper:
    first_submitted_date: datetime
    title: str
    categories: list
    url: str
    authors: str
    abstract: str
    comments: str
    title_translated: str | None = None
    abstract_translated: str | None = None
    first_announced_date: datetime | None = None
    # ---- 多平台扩展字段（v0.6.0）：用于覆盖 arXiv 之外的 AI 顶会/顶刊 ----
    source: str = "arXiv"          # 数据来源平台，如 arXiv / SemanticScholar / OpenReview / CVF / ACL
    venue: str = ""                # 会议/期刊名，如 "CVPR 2024" / "TPAMI"
    pdf_link: str | None = None    # 显式存储的 PDF 直链（非 arxiv 来源由对应 source 填充）
    doi: str | None = None         # 数字对象唯一标识
    # ---- 文献管理扩展字段（v0.9.0）：用户侧阅读/整理元数据 ----
    user_tags: str = ""            # 用户自定义标签，逗号分隔
    notes: str = ""                # 阅读笔记
    read_status: str = "unread"    # 阅读状态：unread / reading / read
    rating: int = 0                # 评分 0-5
    favorite: int = 0             # 是否收藏：0/1
    # ---- 本地 PDF 库（v0.16.0）：抓取到的论文可下载 PDF 到本地并在软件内管理 ----
    local_pdf: str = ""            # 本地 PDF 文件名（相对于 PdfStore 的存储目录，空=未下载）
    # ---- 语言标记（v0.19.0）：用于「不同语言刊物区分」 ----
    language: str = "en"           # en / zh / ja / ko / multi / other

    @classmethod
    def from_row(cls, row: sqlite3.Row):
        # 注：sqlite3.Row 不支持 .get()，此处用索引；_row_factory 已保证这些列存在。
        # 旧库若缺列则不会走到这里（直接返回原始 row）。
        return cls(
            first_submitted_date=datetime.strptime(row["first_submitted_date"], "%Y-%m-%d"),
            title=row["title"],
            categories=[c for c in (row["categories"] or "").split(",") if c],
            url=row["url"],
            authors=row["authors"],
            abstract=row["abstract"],
            comments=row["comments"],
            title_translated=row["title_translated"],
            abstract_translated=row["abstract_translated"],
            first_announced_date=datetime.strptime(row["first_announced_date"], "%Y-%m-%d"),
            source=row["source"] or "arXiv",
            venue=row["venue"] or "",
            pdf_link=row["pdf_link"],
            doi=row["doi"],
            user_tags=row["user_tags"] or "",
            notes=row["notes"] or "",
            read_status=row["read_status"] or "unread",
            rating=int(row["rating"] or 0),
            favorite=int(row["favorite"] or 0),
            local_pdf=row["local_pdf"] or "",
            language=row["language"] or "en",
        )

    @property
    def papers_cool_url(self):
        if "arxiv.org/abs" in self.url:
            return self.url.replace("https://arxiv.org/abs", "https://papers.cool/arxiv")
        return self.url

    @property
    def pdf_url(self):
        """推导 PDF 直链。无可信直链时返回 None（不要让 caller 误用 url fallback）。

        优先使用各平台提供的 pdf_link；arxiv 来源按惯例把 abs 链接转 pdf 链接。
        其它情况（OpenReview/CVF/ACL 详情页、SemanticScholar 无 openAccessPdf），
        若没有显式 pdf_link 就不给出"假 PDF 链接"——caller 应当跳过该论文并提示。
        """
        if self.pdf_link:
            return self.pdf_link
        if "arxiv.org/abs" in self.url:
            return self.url.replace("https://arxiv.org/abs", "https://arxiv.org/pdf")
        return None  # 非 arxiv 且无 pdf_link：宁可让下载器跳过，也不要拿详情页去碰磁

    def to_markdown(self):
        categories = ",".join(parse_categories(self.categories))
        abstract = (
            f"- **摘要**: {self.abstract_translated}"
            if self.abstract_translated
            else f"- **Abstract**: {self.abstract}"
        )

        return f"""### {self.title} 
[[arxiv]({self.url})] [[cool]({self.papers_cool_url})] [[pdf]({self.pdf_url})]
> **Authors**: {self.authors}
> **First submission**: {self.first_submitted_date.strftime("%Y-%m-%d")}
> **First announcement**: {self.first_announced_date.strftime("%Y-%m-%d")}
> **comment**: {self.comments}
- **标题**: {self.title_translated}
- **领域**: {categories}
{abstract}

"""

    async def translate(self, langto="zh-CN"):
        self.title_translated = await async_translate(self.title, langto=langto)
        self.abstract_translated = await async_translate(self.abstract, langto=langto)


@dataclass
class PaperRecord:
    paper: Paper
    comment: str

    def to_markdown(self):
        if self.comment != "-":
            return f"""- [{self.paper.title}]({self.paper.url})
  - **标题**: {self.paper.title_translated}
  - **Filtered Reason**: {self.comment}
"""
        else:
            return self.paper.to_markdown()


class PaperDatabase:
    def __init__(self, db_path="papers.db"):
        # 关键：下载/抓取等后台 worker 线程会写库（PdfStore.set_local_pdf、
        # MultiSourceScraper.add_papers 等都从非 GUI 线程调）。Python sqlite3 默认
        # check_same_thread=True，会抛 "SQLite objects created in a thread can only
        # be used in that same thread"。这里打开跨线程访问 + WAL 模式，让后台写
        # 与 GUI 的读不互相阻塞（v0.19.1 修：之前偶发「下载失败」弹窗就是这个原因）。
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self.conn.row_factory = self._row_factory
        # WAL 让并发读不阻塞后台写；busy_timeout 让偶发的锁等待自动重试而不是抛错。
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
        except sqlite3.Error:
            pass
        self._create_table()

    @staticmethod
    def _row_factory(cursor, row):
        row = sqlite3.Row(cursor, row)
        # 兼容旧库：只要 row 包含 Paper 的全部字段即可构造，不再严格要求列数精确相等
        paper_fields = {f.name for f in fields(Paper)}
        if paper_fields.issubset(set(row.keys())):
            return Paper.from_row(row)
        else:
            return row

    def _create_table(self):
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    url TEXT PRIMARY KEY,
                    authors TEXT NOT NULL,
                    title_translated TEXT,
                    first_submitted_date DATE NOT NULL,
                    first_announced_date DATE NOT NULL,
                    update_time DATETIME NOT NULL,
                    categories TEXT NOT NULL,
                    title TEXT NOT NULL,
                    comments TEXT NOT NULL,
                    abstract TEXT NOT NULL,
                    abstract_translated TEXT,
                    user_tags TEXT,
                    notes TEXT,
                    read_status TEXT,
                    rating INTEGER,
                    favorite INTEGER,
                    local_pdf TEXT
                )
                """
            )
        # 多平台扩展（v0.6.0）：为已有数据库补齐新增列，保证旧库可继续读写
        self._migrate()

    def _migrate(self):
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(papers)")}
        # 与 CREATE TABLE 一致：rating/favorite 为 INTEGER，其余为 TEXT。
        integer_cols = {"rating", "favorite"}
        for col in ("source", "venue", "pdf_link", "doi",
                    "user_tags", "notes", "read_status", "rating", "favorite",
                    "local_pdf", "language"):
            if col not in existing:
                try:
                    typ = "INTEGER" if col in integer_cols else "TEXT"
                    self.conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {typ}")
                    self.conn.commit()
                except sqlite3.OperationalError:
                    pass

    def add_papers(self, papers: Iterable[Paper]):
        assert all([paper.first_announced_date is not None for paper in papers])
        with self.conn:
            data_to_insert = [
                (
                    paper.url,
                    paper.authors,
                    paper.abstract,
                    paper.title,
                    ",".join(paper.categories),
                    paper.first_submitted_date.strftime("%Y-%m-%d"),
                    paper.first_announced_date.strftime("%Y-%m-%d"),
                    paper.title_translated,
                    paper.abstract_translated,
                    paper.comments,
                    paper.source,
                    paper.venue,
                    paper.pdf_link,
                    paper.doi,
                    paper.user_tags,
                    paper.notes,
                    paper.read_status,
                    paper.rating,
                    paper.favorite,
                    paper.local_pdf,
                    paper.language,
                    datetime.now(UTC).replace(tzinfo=None)
                )
                for paper in papers
            ]
            # v0.16.0：改用 UPSERT 而非 INSERT OR REPLACE。
            # 原实现在重复抓取同一篇论文时，会把用户侧字段（笔记 / 标签 / 阅读状态 /
            # 收藏 / 评分 / 本地 PDF 记录）整行覆盖成默认值，等于抹掉用户的整理成果。
            # 现在只刷新平台侧元数据，用户字段一律保留。
            self.conn.executemany(
                """
                INSERT INTO papers
                (url, authors, abstract, title, categories, first_submitted_date, first_announced_date, title_translated, abstract_translated, comments, source, venue, pdf_link, doi, user_tags, notes, read_status, rating, favorite, local_pdf, language, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    authors=excluded.authors,
                    abstract=excluded.abstract,
                    title=excluded.title,
                    categories=excluded.categories,
                    first_submitted_date=excluded.first_submitted_date,
                    first_announced_date=excluded.first_announced_date,
                    title_translated=COALESCE(excluded.title_translated, papers.title_translated),
                    abstract_translated=COALESCE(excluded.abstract_translated, papers.abstract_translated),
                    comments=excluded.comments,
                    source=excluded.source,
                    venue=excluded.venue,
                    pdf_link=COALESCE(excluded.pdf_link, papers.pdf_link),
                    doi=COALESCE(excluded.doi, papers.doi),
                    language=excluded.language,
                    update_time=excluded.update_time
                """,
                data_to_insert,
            )

    def count_new_papers(self, papers: Iterable[Paper]) -> int:
        cnt = 0
        for paper in papers:
            with self.conn:
                cursor = self.conn.execute(
                    """
                    SELECT * FROM papers WHERE url = ?
                    """,
                    (paper.url,),
                )
                if cursor.fetchone():
                    break
                else:
                    cnt += 1
        return cnt

    def fetch_papers_on_date(self, date: datetime) -> list[Paper]:
        with self.conn:
            cursor = self.conn.execute(
                """
                SELECT * FROM papers WHERE first_announced_date = ?
                """,
                (date.strftime("%Y-%m-%d"),),
            )
            return cursor.fetchall()

    def fetch_all(self) -> list[Paper]:
        with self.conn:
            cursor = self.conn.execute(
                """
                SELECT * FROM papers ORDER BY url DESC
                """
            )
            return cursor.fetchall()

    def get_by_url(self, url: str) -> Paper | None:
        with self.conn:
            cursor = self.conn.execute(
                "SELECT * FROM papers WHERE url = ?", (url,)
            )
            rows = cursor.fetchall()
            return rows[0] if rows else None

    def update_meta(self, url: str, user_tags=None, notes=None,
                    read_status=None, rating=None, favorite=None):
        """更新单篇论文的阅读管理元数据（仅更新传入的非 None 字段）。"""
        fields = {}
        if user_tags is not None:
            # 支持传入列表或字符串，统一以逗号分隔存储（与 from_row 的 split(",") 对应）
            fields["user_tags"] = ",".join(user_tags) if isinstance(user_tags, (list, tuple)) else user_tags
        if notes is not None:
            fields["notes"] = notes
        if read_status is not None:
            fields["read_status"] = read_status
        if rating is not None:
            fields["rating"] = int(rating)
        if favorite is not None:
            fields["favorite"] = int(favorite)
        if not fields:
            return 0
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with self.conn:
            cur = self.conn.execute(
                f"UPDATE papers SET {set_clause} WHERE url=?",
                list(fields.values()) + [url],
            )
            return cur.rowcount

    # ---------------- 本地 PDF 库支持（v0.16.0） ----------------
    def set_local_pdf(self, url: str, filename: str | None) -> int:
        """记录/清除某篇论文的本地 PDF 文件名（None 或 "" 表示未下载）。"""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE papers SET local_pdf=? WHERE url=?", (filename or "", url)
            )
            return cur.rowcount

    def fetch_downloaded(self) -> list[Paper]:
        """返回所有已记录本地 PDF 的论文。"""
        with self.conn:
            cursor = self.conn.execute(
                "SELECT * FROM papers WHERE local_pdf IS NOT NULL AND local_pdf != ''"
            )
            return cursor.fetchall()

    def delete_papers(self, urls: Iterable[str]) -> int:
        """从数据库彻底删除若干论文记录（不负责删除本地 PDF 文件，由调用方处理）。"""
        urls = list(urls)
        if not urls:
            return 0
        with self.conn:
            cur = self.conn.executemany(
                "DELETE FROM papers WHERE url=?", [(u,) for u in urls]
            )
            return cur.rowcount

    def clear_all(self) -> int:
        """清空全部论文记录（危险操作，调用方需二次确认）。"""
        with self.conn:
            cur = self.conn.execute("DELETE FROM papers")
            return cur.rowcount

    def newest_update_time(self) -> datetime | None:
        """
        最新更新时间是“上一次爬取最新论文的时间”
        由于数据库可能补充爬取过去的论文，所以先选最新论文，再从其中选最新的爬取时间

        若数据库为空，则返回 None（调用方需自行处理）
        """
        with self.conn:
            cursor = self.conn.execute(
                """
                SELECT MAX(update_time) as max_updated_time
                FROM papers
                WHERE first_announced_date = (SELECT MAX(first_announced_date) FROM papers)
                """
            )
            row = cursor.fetchone()
            # 处理空表或查询结果为空的情况
            if row is None or row["max_updated_time"] is None:
                return None
            time_str = row["max_updated_time"].split(".")[0]
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

    async def translate_missing(self, langto="zh-CN"):
        with self.conn:
            cursor = self.conn.execute(
                "SELECT url, title, abstract FROM papers WHERE title_translated IS NULL OR abstract_translated IS NULL"
            )
            papers = cursor.fetchall()

        async def worker(url, title, abstract):
            title_translated = await async_translate(title, langto=langto) if title else None
            abstract_translated = await async_translate(abstract, langto=langto) if abstract else None
            with self.conn:
                self.conn.execute(
                    "UPDATE papers SET title_translated = ?, abstract_translated = ? WHERE url = ?",
                    (title_translated, abstract_translated, url),
                )

        await asyncio.gather(*[worker(url, title, abstract) for url, title, abstract in papers])


class PaperExporter:
    def __init__(
        self,
        date_from: str,
        date_until: str,
        categories_blacklist: list[str] = [],
        categories_whitelist: list[str] = ["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"],
        database_path="papers.db",
    ):
        self.db = PaperDatabase(database_path)
        self.date_from = datetime.strptime(date_from, "%Y-%m-%d")
        self.date_until = datetime.strptime(date_until, "%Y-%m-%d")
        self.date_range_days = (self.date_until - self.date_from).days + 1
        self.categories_blacklist = set(categories_blacklist)
        self.categories_whitelist = set(categories_whitelist)
        self.console = Console()

    def filter_papers(self, papers: list[Paper]) -> tuple[list[PaperRecord], list[PaperRecord]]:
        filtered_paper_records = []
        chosen_paper_records = []
        for paper in papers:
            categories = set(paper.categories)
            # 非 arxiv 来源（categories 为空）的论文已在各自 source 内按 venue 过滤，
            # 不再用 arxiv 领域白/黑名单二次过滤，直接入选，避免被整体误杀。
            if not categories:
                chosen_paper_records.append(PaperRecord(paper, "-"))
                continue
            if not (self.categories_whitelist & categories):
                categories_str = ",".join(categories)
                filtered_paper_records.append(PaperRecord(paper, f"none of {categories_str} in whitelist"))
            elif black := self.categories_blacklist & categories:
                black_str = ",".join(black)
                filtered_paper_records.append(PaperRecord(paper, f"cat:{black_str} in blacklist"))
            else:
                chosen_paper_records.append(PaperRecord(paper, "-"))
        return chosen_paper_records, filtered_paper_records

    def to_markdown(self, output_dir="./output_llms", filename_format="%Y-%m-%d", metadata=None):
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        if metadata:
            repo_url = metadata["repo_url"]
            categories = ",".join(metadata["category_whitelist"])
            optional_keywords = ", ".join(metadata["optional_keywords"])
            preface_str = f"""
> 本文由 [{repo_url}]({repo_url}) 自动生成
>
> 领域白名单：{categories}
> 关键词： {optional_keywords}

""".lstrip()
        else:
            preface_str = ""

        for i in range(self.date_range_days):
            current = self.date_from + timedelta(days=i)
            current_filename = current.strftime(filename_format)

            with open(output_dir / f"{current_filename}.md", "w", encoding="utf-8") as file:
                papers = self.db.fetch_papers_on_date(current)
                chosen_records, filtered_records = self.filter_papers(papers)
                papers_str = f"# 论文全览：{current_filename}\n\n共有{len(chosen_records)}篇相关领域论文, 另有{len(filtered_records)}篇其他\n\n"

                chosen_dict = defaultdict(list)
                for record in chosen_records:
                    # 非 arxiv 来源论文无 arxiv 分类，按会议/期刊名（venue）分组，缺省归到 "Other"
                    cat = record.paper.categories[0] if record.paper.categories else (record.paper.venue or "Other")
                    chosen_dict[cat].append(record)
                for category in sorted(chosen_dict.keys()):
                    # arxiv 分类走翻译映射；其它（venue 字符串）原样输出，避免 KeyError
                    try:
                        category_en = parse_categories([category], lang="en")[0]
                        category_zh = parse_categories([category], lang="zh-CN")[0]
                        header = f"{category_zh}({category}:{category_en})"
                    except KeyError:
                        header = category
                    papers_str += f"## {header}\n\n"
                    for record in chosen_dict[category]:
                        papers_str += record.to_markdown()

                papers_str += "## 其他论文\n\n"
                for record in filtered_records:
                    papers_str += record.to_markdown()

                file.write(preface_str + papers_str)

            self.console.log(
                f"[bold green]Output {current_filename}.md completed. {len(chosen_records)} papers chosen, {len(filtered_records)} papers filtered"
            )

    def to_csv(self, output_dir="./output_llms", filename_format="%Y-%m-%d", header=True, csv_config=None):
        csv_config = dict(csv_config) if csv_config else {}
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        csv_table = {
            "Title": lambda record: record.paper.title,
            "Interest": lambda record: ("chosen" if record.comment == "-" else "filtered"),
            "Title Translated": lambda record: (
                record.paper.title_translated if record.paper.title_translated else "-"
            ),
            "Categories": lambda record: ",".join(record.paper.categories),
            "Authors": lambda record: record.paper.authors,
            "URL": lambda record: record.paper.url,
            "PapersCool": lambda record: record.paper.url.replace("https://arxiv.org/abs", "https://papers.cool/arxiv"),
            "First Submitted Date": lambda record: record.paper.first_submitted_date.strftime("%Y-%m-%d"),
            "First Announced Date": lambda record: record.paper.first_announced_date.strftime("%Y-%m-%d"),
            "Abstract": lambda record: record.paper.abstract,
            "Abstract Translated": lambda record: (
                record.paper.abstract_translated if record.paper.abstract_translated else "-"
            ),
            "Comments": lambda record: record.paper.comments,
            "Note": lambda record: record.comment,
        }

        headers = list(csv_table.keys())

        for i in range(self.date_range_days):
            current = self.date_from + timedelta(days=i)
            papers = self.db.fetch_papers_on_date(current)
            if not papers:  # 如果该日期没有论文，跳过
                continue
            chosen_records, filtered_records = self.filter_papers(papers)
            records_to_write = chosen_records + filtered_records
            if not records_to_write:
                continue

            current_filename = current.strftime(filename_format)
            with open(output_dir / f"{current_filename}.csv", "w", encoding="utf-8") as file:
                if "lineterminator" not in csv_config:
                    csv_config["lineterminator"] = "\n"
                csv_writer_kwargs = {k: v for k, v in csv_config.items() if k != 'header'}
                writer = csv.writer(file, **csv_writer_kwargs)
                if header:
                    writer.writerow(headers)

                for record in records_to_write:
                    writer.writerow([fn(record) for fn in csv_table.values()])

                self.console.log(
                    f"[bold green]Output {current_filename}.csv completed. {len(chosen_records)} papers chosen, {len(filtered_records)} papers filtered"
                )


if __name__ == "__main__":
    from datetime import date, timedelta

    today = date.today()

    exporter = PaperExporter(today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))
    exporter.to_markdown()
    exporter.to_csv(csv_config=dict(delimiter="\t", header=False))