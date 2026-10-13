# main.py — arXiv 论文爬虫 · 手机版（Kivy）入口
#
# 设计原则：完全复用桌面版逻辑，不重写爬虫。
#   - ArxivScraper 负责抓取 / 翻译 / 处理 / PDF 下载
#   - PaperExporter.filter_papers 负责筛选
#   - run.write_to_feishu 负责飞书写入（与桌面版字段映射一致）
# 本文件只做三件事：① 提供触屏 UI；② 在后台线程跑 pipeline；③ 把日志回流到界面。

import io
import os
import re
import sys
import threading
import traceback
from datetime import date, timedelta

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

# 在手机上把工作目录切到 app 私有存储，保证 papers.db / output_llms / pdfs 可写且持久。
# 桌面（platform != "android"）不执行此分支，便于本地直接跑测试。
if platform == "android":
    try:
        from android.storage import app_storage_path
        os.chdir(app_storage_path())
    except Exception:
        pass

# 复用桌面版模块（与 main.py 同目录）
from run import (
    write_to_feishu,
    search_queries as DEFAULT_QUERIES,
    category_whitelist as CATEGORY_WHITELIST,
)
from paper_sources import MultiSourceScraper, DEFAULT_AI_VENUES, ALL_SOURCE_NAMES


# 去除 rich 的颜色转义与 [markup] 标记，避免日志里出现一堆控制字符
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_MARKUP_RE = re.compile(r"\[[^\]]*\]")


class UiFile(io.StringIO):
    """把 rich / print 的输出重定向到 UI 日志（去掉颜色与 rich 标记后逐行回调）"""

    def __init__(self, log_fn):
        super().__init__()
        self._log_fn = log_fn

    def write(self, s):
        if s:
            clean = _ANSI_RE.sub("", s)
            clean = _MARKUP_RE.sub("", clean)
            for line in clean.splitlines():
                if line.strip():
                    self._log_fn(line.rstrip())
        return len(s)


class CrawlerApp(App):
    def build(self):
        self.log_lines = []

        root = BoxLayout(orientation="vertical", padding=10, spacing=8)

        root.add_widget(
            Label(text="arXiv 论文爬虫 · 手机版", size_hint_y=None, height=36,
                  font_size=20, bold=True)
        )

        # ---- 时间范围模式 ----
        self.mode_spinner = Spinner(
            text="默认30天",
            values=("默认30天", "最近N天", "起止日期"),
            size_hint_y=None, height=40,
        )
        self.mode_spinner.bind(text=self._on_mode_change)
        root.add_widget(self.mode_spinner)

        # ---- 动态参数区（天数 / 起止日期）----
        self.dyn_box = BoxLayout(orientation="vertical", size_hint_y=None, height=0)
        root.add_widget(self.dyn_box)
        self.days_input = None
        self.date_from_input = None
        self.date_until_input = None
        self._on_mode_change(self.mode_spinner, "默认30天")

        # ---- 关键词 ----
        root.add_widget(Label(text="搜索关键词（每行一个，OR 关系）", size_hint_y=None, height=24))
        self.queries_input = TextInput(
            text="\n".join(DEFAULT_QUERIES),
            size_hint_y=None, height=120, multiline=True,
        )
        root.add_widget(self.queries_input)

        # ---- 开关 ----
        opt_box = GridLayout(cols=2, size_hint_y=None, height=80)
        self.cb_feishu = CheckBox(active=True)
        self.cb_pdf = CheckBox(active=True)
        opt_box.add_widget(Label(text="写入飞书多维表格"))
        opt_box.add_widget(self.cb_feishu)
        opt_box.add_widget(Label(text="下载 PDF"))
        opt_box.add_widget(self.cb_pdf)
        root.add_widget(opt_box)

        # ---- 数据源平台（默认全选）----
        root.add_widget(Label(text="论文平台（默认全选）", size_hint_y=None, height=24))
        self.platform_cbs = {}
        plat_box = GridLayout(cols=2, size_hint_y=None, height=240)
        for name in ALL_SOURCE_NAMES:
            cb = CheckBox(active=True)
            self.platform_cbs[name] = cb
            plat_box.add_widget(Label(text=name))
            plat_box.add_widget(cb)
        root.add_widget(plat_box)

        # ---- 运行按钮 ----
        self.run_btn = Button(
            text="开始运行", size_hint_y=None, height=48,
            background_color=(0.2, 0.6, 0.9, 1),
        )
        self.run_btn.bind(on_press=lambda *a: self._on_run())
        root.add_widget(self.run_btn)

        # ---- 日志 ----
        root.add_widget(Label(text="运行日志", size_hint_y=None, height=24))
        self.scroll = ScrollView(size_hint=(1, 1))
        self.log_label = Label(
            text="", size_hint_y=None, markup=False,
            halign="left", valign="top", text_size=(360, None),
        )
        self.log_label.bind(texture_size=self._update_log_height)
        self.scroll.bind(
            width=lambda *a: setattr(self.log_label, "text_size", (self.scroll.width, None))
        )
        self.scroll.add_widget(self.log_label)
        root.add_widget(self.scroll)

        return root

    def _update_log_height(self, *args):
        self.log_label.height = self.log_label.texture_size[1]
        self.scroll.scroll_y = 0

    def _on_mode_change(self, spinner, text):
        self.dyn_box.clear_widgets()
        self.dyn_box.height = 0
        if text == "最近N天":
            self.days_input = TextInput(text="30", size_hint_y=None, height=40, input_filter="int")
            self.dyn_box.add_widget(Label(text="天数", size_hint_y=None, height=24))
            self.dyn_box.add_widget(self.days_input)
            self.dyn_box.height = 64
        elif text == "起止日期":
            today = date.today().strftime("%Y-%m-%d")
            self.date_from_input = TextInput(text=today, size_hint_y=None, height=40)
            self.date_until_input = TextInput(text=today, size_hint_y=None, height=40)
            self.dyn_box.add_widget(Label(text="起始日期 (YYYY-MM-DD)", size_hint_y=None, height=24))
            self.dyn_box.add_widget(self.date_from_input)
            self.dyn_box.add_widget(Label(text="结束日期 (YYYY-MM-DD)", size_hint_y=None, height=24))
            self.dyn_box.add_widget(self.date_until_input)
            self.dyn_box.height = 128

    def log(self, msg):
        # 从后台线程安全地更新 UI
        Clock.schedule_once(lambda dt: self._append_log(str(msg)))

    def _append_log(self, msg):
        self.log_label.text += msg + "\n"

    def _on_run(self):
        self.run_btn.disabled = True
        self.log("▶ 开始任务...")
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    def _resolve_dates(self):
        mode = self.mode_spinner.text
        today = date.today()
        if mode == "最近N天":
            days = int(self.days_input.text or 30)
            days = max(1, days)
            return (today - timedelta(days=days)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")
        if mode == "起止日期":
            return self.date_from_input.text.strip(), self.date_until_input.text.strip()
        return (today - timedelta(days=30)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    def _run_pipeline(self):
        old_stdout = sys.stdout
        sys.stdout = UiFile(self.log)  # 捕获 print（write_to_feishu 用 print）
        try:
            from rich.console import Console

            date_from, date_until = self._resolve_dates()
            queries = [q.strip() for q in self.queries_input.text.splitlines() if q.strip()]

            self.log(f"时间范围: {date_from} ~ {date_until}")
            self.log(f"关键词: {queries}")

            # ① 多平台抓取（内部含翻译、写库、去重、日期推算）
            selected = [n for n, cb in self.platform_cbs.items() if cb.active]
            self.log(f"启用平台: {selected or ['（无，仅 arXiv）']}")
            scraper = MultiSourceScraper(
                date_from=date_from,
                date_until=date_until,
                optional_keywords=queries,
                category_whitelist=CATEGORY_WHITELIST,
                trans_to="zh-CN",
                source_names=selected or None,
                ai_venues=DEFAULT_AI_VENUES,
            )
            # 让 rich 的输出也走 UiFile（去掉颜色/标记）
            scraper.console = Console(
                file=sys.stdout, no_color=True, markup=False, emoji=False, highlight=False
            )

            self.log("① 抓取 arXiv ...")
            scraper.fetch_update()

            all_papers = scraper.paper_db.fetch_all()
            chosen, filtered = scraper.paper_exporter.filter_papers(all_papers)
            records = chosen + filtered
            self.log(f"共 {len(all_papers)} 篇，入选 {len(chosen)}，过滤 {len(filtered)}")

            # ② 飞书写入
            if self.cb_feishu.active:
                self.log("② 写入飞书 ...")
                write_to_feishu(records)
            else:
                self.log("② 跳过飞书写入")

            # ③ 本地 Markdown / CSV（与桌面版一致）
            self.log("③ 生成本地 Markdown / CSV ...")
            scraper.to_markdown(meta=True)
            scraper.to_csv(
                output_dir="./output_llms", filename_format="%Y-%m-%d",
                header=False, csv_config=dict(delimiter="\t"),
            )

            # ④ 下载 PDF（仅新抓到的）
            if self.cb_pdf.active and scraper.papers:
                self.log(f"④ 下载 {len(scraper.papers)} 篇 PDF ...")
                import asyncio
                asyncio.run(scraper.download_pdfs(output_dir="./pdfs", max_concurrent=5))
            else:
                self.log("④ 跳过 PDF 下载")

            self.log("🎉 全部任务完成！")
        except Exception:
            self.log("❌ 运行出错:\n" + traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            Clock.schedule_once(lambda dt: setattr(self.run_btn, "disabled", False))


if __name__ == "__main__":
    CrawlerApp().run()
