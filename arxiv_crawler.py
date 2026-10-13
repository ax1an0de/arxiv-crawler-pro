import asyncio
import re
import os
from datetime import datetime, timedelta, UTC
from itertools import chain
from pathlib import Path

import aiohttp
import aiofiles  # 新增，用于异步文件写入
from bs4 import BeautifulSoup, NavigableString, Tag
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn
from arxiv_time import next_arxiv_update_day
from paper import Paper, PaperDatabase, PaperExporter


class ArxivScraper(object):
    def __init__(
            self,
            date_from,
            date_until,
            category_blacklist=[],
            category_whitelist=["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"],
            optional_keywords=["LLM", "LLMs", "language model", "language models", "multimodal", "finetuning", "GPT"],
            trans_to="zh-CN",
            proxy=None,
            db_path="papers.db",
    ):
        """
        一个抓取指定日期范围内的arxiv文章的类,
        搜索基于https://arxiv.org/search/advanced,
        一个文件被爬取到的条件是：首次提交时间在`date_from`和`date_until`之间，并且包含至少一个关键词。
        一个文章被详细展示（不被过滤）的条件是：至少有一个领域在白名单中，并且没有任何一个领域在黑名单中。
        翻译基于google-translate

        Args:
            date_from (str): 开始日期(含当天)
            date_until (str): 结束日期(含当天)
            category_blacklist (list, optional): 黑名单. Defaults to [].
            category_whitelist (list, optional): 白名单. Defaults to ["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"].
            optional_keywords (list, optional): 关键词, 各词之间关系为OR, 在标题/摘要中至少要出现一个关键词才会被爬取.
                Defaults to [ "LLM", "LLMs", "language model", "language models", "multimodal", "finetuning", "GPT"]
            trans_to: 翻译的目标语言, 若设为可转换为False的值则不会翻译
            proxy (str | None, optional): 用于翻译和爬取arxiv时要使用的代理, 通常是http://127.0.0.1:7890. Defaults to None
        """
        # announced_date_first 日期处理为年月，从from到until的所有月份都会被爬取
        # 如果from和until是同一个月，则until设置为下个月(from+31)
        self.search_from_date = datetime.strptime(date_from[:-3], "%Y-%m")
        self.search_until_date = datetime.strptime(date_until[:-3], "%Y-%m")
        if self.search_from_date.month == self.search_until_date.month:
            self.search_until_date = (self.search_from_date + timedelta(days=31)).replace(day=1)
        # 由于arxiv的奇怪机制，每个月的第一天公布的文章总会被视作上个月的文章, 所以需要将月初文章的首次公布日期往后推一天
        self.fisrt_announced_date = next_arxiv_update_day(
            next_arxiv_update_day(self.search_from_date) + timedelta(days=1))

        self.category_blacklist = category_blacklist  # used as metadata
        self.category_whitelist = category_whitelist  # used as metadata
        self.optional_keywords = [kw.replace(" ", "+") for kw in optional_keywords]  # url转义

        self.trans_to = trans_to  # translate
        self.proxy = proxy

        self.filt_date_by = "announced_date_first"  # url
        self.order = "-announced_date_first"  # url(结果默认按首次公布日期的降序排列，这样最新公布的会在前面)
        self.total = None  # fetch_all
        self.step = 50  # url, fetch_all
        self.papers: list[Paper] = []  # fetch_all

        self.paper_db = PaperDatabase(db_path)
        self.paper_exporter = PaperExporter(date_from, date_until, category_blacklist, category_whitelist)
        self.console = Console()

        # 保存原始日期字符串，用于首次运行
        self.date_from = date_from
        self.date_until = date_until

    @property
    def meta_data(self):
        """
        返回搜索的元数据
        """
        return dict(repo_url="https://github.com/huiyeruzhou/arxiv_crawler", **self.__dict__)

    def get_url(self, start):
        """
        获取用于搜索的url

        Args:
            start (int): 返回结果的起始序号, 每个页面只会包含序号为[start, start+50)的文章
            filter_date_by (str, optional): 日期筛选方式. Defaults to "submitted_date_first".
        """
        kwargs = "".join(
            f"&terms-{i}-operator=OR&terms-{i}-term={kw}&terms-{i}-field=all"
            for i, kw in enumerate(self.optional_keywords)
        )
        date_from = self.search_from_date.strftime("%Y-%m")
        date_until = self.search_until_date.strftime("%Y-%m")
        return (
            f"https://arxiv.org/search/advanced?advanced={kwargs}"
            f"&classification-computer_science=y&classification-physics_archives=all&"
            f"classification-include_cross_list=include&"
            f"date-year=&date-filter_by=date_range&date-from_date={date_from}&date-to_date={date_until}&"
            f"date-date_type={self.filt_date_by}&abstracts=show&size={self.step}&order={self.order}&start={start}"
        )

    async def request(self, start):
        """
        异步请求网页，重试至多3次
        """
        error = 0
        last_exc = None
        url = self.get_url(start)
        while error <= 3:
            try:
                async with aiohttp.ClientSession(trust_env=True, read_timeout=10) as session:
                    async with session.get(url, proxy=self.proxy) as response:
                        response.raise_for_status()
                        content = await response.text()
                        return content
            except Exception as e:
                error += 1
                last_exc = e
                self.console.log(f"[bold red]Request {start} cause error: {e}")
                self.console.log(f"[bold red]Retrying {start}... {error}/3")
        # 重试 3 次仍失败：抛出最后一个异常，交由上层处理（不再返回 None 导致下游崩溃）
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Request {start} failed after 3 retries")

    async def fetch_all(self):
        """
        (aio)获取所有文章
        """
        self.console.log(f"[bold green]Fetching the first {self.step} papers...")
        self.console.print(f"[grey] {self.get_url(0)}")
        content = await self.request(0)
        self.papers.extend(self.parse_search_html(content))

        with Progress(
                SpinnerColumn(),
                *Progress.get_default_columns(),
                TimeElapsedColumn(),
                console=self.console,
                transient=False,
        ) as p:
            task = p.add_task(
                description=f"[bold green]Fetching {self.total} results",
                total=self.total,
            )
            p.update(task, advance=self.step)

            async def wrapper(start):
                content = await self.request(start)
                papers = self.parse_search_html(content)
                p.update(task, advance=self.step)
                return papers

            fetch_tasks = []
            for start in range(self.step, self.total, self.step):
                fetch_tasks.append(wrapper(start))
            papers_list = await asyncio.gather(*fetch_tasks)
            self.papers.extend(chain(*papers_list))

        self.console.log(f"[bold green]Fetching completed. ")
        if self.trans_to:
            await self.translate()
        self.process_papers()

    def fetch_update(self):
        """
        更新文章, 这会从最新公布的文章开始更新, 直到遇到已经爬取过的文章为止。
        为了效率，建议在运行fetch_all后再运行fetch_update
        """
        utc_now = datetime.now(UTC).replace(tzinfo=None)
        last_update = self.paper_db.newest_update_time()

        if last_update is None:
            self.search_from_date = datetime.strptime(self.date_from, "%Y-%m-%d")
            self.console.log(f"[bold yellow]Database is empty. Starting from user-specified date: {self.date_from}")
        else:
            self.search_from_date = next_arxiv_update_day(last_update)
            self.console.log(f"[bold yellow]last update: {last_update.strftime('%Y-%m-%d %H:%M:%S')}, "
                             f"next arxiv update: {self.search_from_date.strftime('%Y-%m-%d')}"
                             )
        self.console.log(f"[bold yellow]UTC now: {utc_now.strftime('%Y-%m-%d %H:%M:%S')}")

        if last_update is not None and self.search_from_date >= utc_now:
            self.console.log(f"[bold red]Your database is already up to date.")
            return

        self.fisrt_announced_date = self.search_from_date
        if last_update is not None and self.search_from_date == next_arxiv_update_day(
                self.search_from_date.replace(day=1)):
            self.search_from_date = self.search_from_date - timedelta(days=31)
            self.console.log(
                f"[bold yellow]The update in {self.fisrt_announced_date.strftime('%Y-%m-%d')} can only be found in the previous month.")
        else:
            self.console.log(
                f"[bold green]Searching from {self.search_from_date.strftime('%Y-%m-%d')} "
                f"to {self.search_until_date.strftime('%Y-%m-%d')}, fetch the first {self.step} papers..."
            )
        self.console.print(f"[grey] {self.get_url(0)}")

        continue_update = self.update(0)
        for start in range(self.step, self.total, self.step):
            if not continue_update:
                break
            continue_update = self.update(start)
        self.console.log(f"[bold green]Fetching completed. {len(self.papers)} new papers.")
        if self.trans_to:
            asyncio.run(self.translate())
        self.process_papers()

    def process_papers(self):
        announced_date = next_arxiv_update_day(self.fisrt_announced_date)
        self.console.log(f"fisrt announced date: {announced_date.strftime('%Y-%m-%d')}")
        for paper in reversed(self.papers):
            next_possible_annouced_date = next_arxiv_update_day(paper.first_submitted_date + timedelta(days=1))
            if announced_date < next_possible_annouced_date:
                announced_date = next_possible_annouced_date
            paper.first_announced_date = announced_date
        self.paper_db.add_papers(self.papers)

    def reprocess_papers(self):
        self.papers = self.paper_db.fetch_all()
        self.process_papers()
        with open("announced_date.csv", "w") as f:
            f.write("url,title,announced_date,submitted_date\n")
            for paper in self.papers:
                f.write(
                    f"{paper.url},{paper.title},{paper.first_announced_date.strftime('%Y-%m-%d')},{paper.first_submitted_date.strftime('%Y-%m-%d')}\n"
                )

    def update(self, start) -> bool:
        content = asyncio.run(self.request(start))
        self.papers.extend(self.parse_search_html(content))
        cnt_new = self.paper_db.count_new_papers(self.papers[start: start + self.step])
        if cnt_new < self.step:
            self.papers = self.papers[: start + cnt_new]
            return False
        else:
            return True

    def _parse_arxiv_date(self, text: str) -> datetime:
        """
        从 arxiv 搜索结果的日期文本中提取「首次提交日期」。

        arxiv 的日期文本形如：
          "Submitted 12 July, 2024; originally announced July 2024"
          "v1 submitted 12 July, 2024; originally announced July 2024"

        早期实现用字符串切片（date[find('Submitted')+9 : find(';')]），会带上
        前导空格导致 strptime 直接 ValueError 崩溃；而 "v1 submitted"（带空格）
        分支因找不到 'v1submitted' 会切出整段乱码再崩溃。这里改用正则提取
        「日 月 年」并兼容月份全称/缩写；解析失败则回退当前时间，避免整批中断。
        """
        if not text:
            self.console.log("[yellow]parse_search_html: 缺少日期文本，使用当前时间兜底")
            return datetime.now()
        m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\.?,?\s+(\d{4})", text)
        if not m:
            self.console.log(f"[yellow]parse_search_html: 无法从 {text!r} 解析日期，使用当前时间兜底")
            return datetime.now()
        day, mon, year = m.group(1), m.group(2), m.group(3)
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(f"{day} {mon} {year}", fmt)
            except ValueError:
                continue
        self.console.log(f"[yellow]parse_search_html: 月份无法识别 {mon!r}，使用当前时间兜底")
        return datetime.now()

    def parse_search_html(self, content) -> list[Paper]:
        soup = BeautifulSoup(content, "html.parser")
        if not self.total:
            h1_tags = soup.select("#main-container > div.level.is-marginless > div.level-left > h1")
            if not h1_tags:
                # 页面结构异常（如 arXiv 改版或返回了错误页），无法解析结果总数
                self.console.log("[bold red]parse_search_html: 未找到结果总数 h1，疑似页面结构变化/网络异常，按 0 条处理")
                self.total = 0
                return []
            total_text = h1_tags[0].get_text()
            if "Sorry" in total_text:
                self.total = 0
                return []
            try:
                of_idx = total_text.find("of")
                res_idx = total_text.find("results")
                if of_idx == -1 or res_idx == -1:
                    raise ValueError(f"标题中无 'of ... results': {total_text!r}")
                total = int(total_text[of_idx + 3: res_idx].replace(",", "").strip())
            except (ValueError, IndexError) as e:
                self.console.log(f"[bold red]parse_search_html: 解析结果总数失败 ({e})，按 0 条处理")
                self.total = 0
                return []
            self.total = total

        results = soup.find_all("li", {"class": "arxiv-result"})
        papers = []
        for result in results:

            url_tag = result.find("a")
            url = url_tag["href"] if url_tag else "No link"

            title_tag = result.find("p", class_="title")
            title = self.parse_search_text(title_tag) if title_tag else "No title"
            title = title.strip()

            date_tag = result.find("p", class_="is-size-7")
            date_text = date_tag.get_text(strip=True) if date_tag else ""
            first_submitted_date = self._parse_arxiv_date(date_text)

            category_tag = result.find_all("span", class_="tag")
            categories = [
                category.get_text(strip=True) for category in category_tag if "tooltip" in category.get("class")
            ]

            authors_tag = result.find("p", class_="authors")
            authors = authors_tag.get_text(strip=True)[len("Authors:"):] if authors_tag else "No authors"

            summary_tag = result.find("span", class_="abstract-full")
            abstract = self.parse_search_text(summary_tag) if summary_tag else "No summary"
            abstract = abstract.strip()

            comments_tag = result.find("p", class_="comments")
            comments = comments_tag.get_text(strip=True)[len("Comments:"):] if comments_tag else "No comments"

            papers.append(
                Paper(
                    url=url,
                    title=title,
                    first_submitted_date=first_submitted_date,
                    categories=categories,
                    authors=authors,
                    abstract=abstract,
                    comments=comments,
                )
            )
        return papers

    def parse_search_text(self, tag):
        string = ""
        for child in tag.children:
            if isinstance(child, NavigableString):
                string += re.sub(r"\s+", " ", child)
            elif isinstance(child, Tag):
                if child.name == "span" and "search-hit" in child.get("class"):
                    string += re.sub(r"\s+", " ", child.get_text(strip=False))
                elif child.name == "a" and ".style.display" in child.get("onclick"):
                    pass
                else:
                    # 遇到未预期的标签：记录告警并保留其文本，避免卡在调试器或丢失内容
                    self.console.log(
                        f"[yellow]parse_search_text: 遇到未处理的标签 <{child.name}>，已保留其文本"
                    )
                    string += child.get_text(" ", strip=True)
        return string

    async def translate(self):
        if not self.trans_to:
            raise ValueError("No target language specified.")
        self.console.log("[bold green]Translating...")
        with Progress(
                SpinnerColumn(),
                *Progress.get_default_columns(),
                TimeElapsedColumn(),
                console=self.console,
                transient=False,
        ) as p:
            total = len(self.papers)
            task = p.add_task(
                description=f"[bold green]Translating {total} papers",
                total=total,
            )

            async def worker(paper):
                await paper.translate(langto=self.trans_to)
                p.update(task, advance=1)

            await asyncio.gather(*[worker(paper) for paper in self.papers])

    def to_markdown(self, output_dir="./output_llms", filename_format="%Y-%m-%d", meta=False):
        self.paper_exporter.to_markdown(output_dir, filename_format, self.meta_data if meta else None)

    def to_csv(self, output_dir="./output_llms", filename_format="%Y-%m-%d", header=False, csv_config={}, ):
        self.paper_exporter.to_csv(output_dir, filename_format, header, csv_config)

    # ======================== 新增 PDF 下载功能 ========================
    async def _download_one(self, session, paper, output_dir, semaphore):
        """
        异步下载单篇论文的 PDF 文件（带并发控制）
        """
        async with semaphore:
            # 从 URL 提取论文 ID（如 2401.12345）
            paper_id = paper.url.split("/abs/")[-1]
            filename = f"{paper_id}.pdf"
            filepath = Path(output_dir) / filename

            # 如果文件已存在，跳过
            if filepath.exists():
                self.console.log(f"[yellow]⏭️ 已存在: {filename}")
                return

            pdf_url = paper.pdf_url
            try:
                async with session.get(pdf_url, timeout=30) as resp:
                    if resp.status == 200:
                        # 异步写入文件（分块写入，防止内存占用过大）
                        async with aiofiles.open(filepath, "wb") as f:
                            while True:
                                chunk = await resp.content.read(1024 * 1024)  # 1MB 块
                                if not chunk:
                                    break
                                await f.write(chunk)
                        self.console.log(f"[green]✅ 下载完成: {filename}")
                    else:
                        self.console.log(f"[red]❌ 下载失败 {filename}: HTTP {resp.status}")
            except Exception as e:
                self.console.log(f"[red]❌ 下载异常 {filename}: {e}")

    async def download_pdfs(self, output_dir="./pdfs", papers=None, max_concurrent=5):
        """
        异步下载指定论文列表的 PDF 文件

        :param output_dir: 保存 PDF 的目录（默认 ./pdfs）
        :param papers: 要下载的论文列表（若为 None，则使用 self.papers）
        :param max_concurrent: 最大并发下载数（默认 5）
        """
        if papers is None:
            papers = self.papers
        if not papers:
            self.console.log("[yellow]没有论文需要下载")
            return

        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        self.console.log(f"[bold green]开始下载 {len(papers)} 篇论文 PDF 到 {output_dir}")

        semaphore = asyncio.Semaphore(max_concurrent)
        async with aiohttp.ClientSession() as session:
            tasks = [self._download_one(session, paper, output_dir, semaphore) for paper in papers]
            with Progress(
                    SpinnerColumn(),
                    *Progress.get_default_columns(),
                    TimeElapsedColumn(),
                    console=self.console,
                    transient=False,
            ) as p:
                task = p.add_task("[bold green]下载 PDF", total=len(tasks))
                for coro in asyncio.as_completed(tasks):
                    await coro
                    p.update(task, advance=1)

        self.console.log("[bold green]所有 PDF 下载完成")

    def download_all_pdfs(self, output_dir="./pdfs_all", max_concurrent=5):
        """
        同步包装器：下载数据库中全部论文的 PDF（用于在同步代码中调用）
        """
        all_papers = self.paper_db.fetch_all()
        if not all_papers:
            self.console.log("[yellow]数据库中没有论文")
            return
        asyncio.run(self.download_pdfs(output_dir, papers=all_papers, max_concurrent=max_concurrent))


if __name__ == "__main__":
    from datetime import date, timedelta

    today = date.today()

    scraper = ArxivScraper(
        date_from=today.strftime("%Y-%m-%d"),
        date_until=today.strftime("%Y-%m-%d"),
    )
    asyncio.run(scraper.fetch_all())
    scraper.to_markdown(meta=True)