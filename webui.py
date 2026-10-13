"""
webui.py — 可视化界面后端（v0.7.0）

一个零依赖（仅用 Python 标准库 http.server）的本地 Web 服务，提供：
  - 论文筛选：论文年龄（距首次提交天数）/ 关键词 / 标签(领域 & 会议) / 来源 / 状态 / 日期范围
  - 数据可视化：来源分布、会议/期刊分布、领域分布、年份时间线（纯 CSS 柱状图，前端渲染）
  - 一键抓取（触发 MultiSourceScraper）、示例数据加载、Markdown / CSV 导出

运行：
  python webui.py            # 默认 http://127.0.0.1:8000
  python webui.py --port 9000
  python webui.py --host 0.0.0.0
"""

import argparse
import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from paper import PaperDatabase, Paper
from paper_sources import MultiSourceScraper, ALL_SOURCE_NAMES, DEFAULT_AI_VENUES
from litman import analyze as litman_analyze, classify_topics

# 复用 run.py 的配置（关键词 / 领域白名单 / 启用平台 / AI 顶会词表）
try:
    import run
    DEFAULT_KEYWORDS = run.search_queries
    DEFAULT_WHITELIST = run.category_whitelist
    DEFAULT_SOURCES = run.ENABLED_SOURCES
    DEFAULT_VENUES = run.AI_VENUES
except Exception:
    DEFAULT_KEYWORDS = ["object detection"]
    DEFAULT_WHITELIST = ["cs.CV", "cs.AI", "cs.LG", "cs.CL", "cs.IR", "cs.MA"]
    DEFAULT_SOURCES = list(ALL_SOURCE_NAMES)
    DEFAULT_VENUES = DEFAULT_AI_VENUES

DB_PATH = str(HERE / "papers.db")
FRONTEND = HERE / "webui_frontend.html"

# ---------------- 抓取状态（线程安全） ----------------
_crawl_state = {"running": False, "done": False, "count": 0, "message": "", "ts": None}
_crawl_lock = threading.Lock()


# ---------------- 工具函数 ----------------
def _paper_to_dict(p: Paper) -> dict:
    fsd = p.first_submitted_date
    fad = p.first_announced_date
    age = (datetime.now() - fsd).days if fsd else None
    return {
        "url": p.url,
        "title": p.title or "",
        "title_zh": p.title_translated or "",
        "authors": p.authors or "",
        "abstract": p.abstract or "",
        "abstract_zh": p.abstract_translated or "",
        "categories": p.categories or [],
        "source": p.source or "arXiv",
        "venue": p.venue or "",
        "comments": p.comments or "",
        "pdf_link": p.pdf_link or "",
        "doi": p.doi or "",
        "first_submitted": fsd.strftime("%Y-%m-%d") if fsd else "",
        "first_announced": fad.strftime("%Y-%m-%d") if fad else "",
        "age": age,
        # ---- 文献管理字段（v0.9.0）----
        "user_tags": [t for t in (p.user_tags or "").split(",") if t],
        "notes": p.notes or "",
        "read_status": p.read_status or "unread",
        "rating": int(p.rating or 0),
        "favorite": int(p.favorite or 0),
    }


def _status_of(p: Paper, whitelist) -> str:
    cats = set(p.categories or [])
    if not cats:
        return "chosen"
    return "chosen" if (set(whitelist) & cats) else "filtered"


def _load_all() -> list:
    """读取数据库全部论文（已迁移补齐列，返回 Paper 对象）。"""
    db = PaperDatabase(DB_PATH)
    rows = db.fetch_all()
    return [r for r in rows if isinstance(r, Paper)]


def _apply_filters(papers: list, params: dict) -> list:
    kw = (params.get("keyword") or "").strip().lower()
    max_age = params.get("max_age")
    if max_age in (None, "", "null"):
        max_age = None
    else:
        try:
            max_age = int(max_age)
        except (ValueError, TypeError):
            max_age = None

    sources = params.get("sources") or []
    cats = params.get("categories") or []
    venues = params.get("venues") or []
    status = params.get("status") or "all"
    read_status = params.get("read_status") or "all"   # 文献管理：unread/reading/read
    favorite_only = params.get("favorite") in (True, "true", "1", 1)
    user_tags = params.get("user_tags") or []          # 文献管理：用户标签筛选
    date_from = params.get("date_from") or ""
    date_until = params.get("date_until") or ""
    sort_by = params.get("sort_by") or "date"
    whitelist = params.get("whitelist") or DEFAULT_WHITELIST

    out = []
    for p in papers:
        d = _paper_to_dict(p)
        if kw:
            hay = (d["title"] + " " + d["title_zh"] + " " + d["abstract"] + " " + d["abstract_zh"]).lower()
            if kw not in hay:
                continue
        if max_age is not None:
            if d["age"] is None or d["age"] > max_age:
                continue
        if sources and d["source"] not in sources:
            continue
        # 标签：领域（categories）与会议（venue）两组之间为「或」语义；
        # 仅当某一组有选中项时才参与匹配，两组都未选表示不按标签过滤。
        cats_selected = bool(cats)
        venues_selected = bool(venues)
        if cats_selected or venues_selected:
            cat_ok = bool(set(d["categories"]) & set(cats)) if cats_selected else False
            ven_ok = any(v.lower() in (d["venue"] or "").lower() for v in venues) if venues_selected else False
            if not (cat_ok or ven_ok):
                continue
        st = _status_of(p, whitelist)
        if status == "chosen" and st != "chosen":
            continue
        if status == "filtered" and st != "filtered":
            continue
        # 文献管理筛选
        if read_status != "all" and d["read_status"] != read_status:
            continue
        if favorite_only and not d["favorite"]:
            continue
        if user_tags:
            if not (set(user_tags) & set(d["user_tags"])):
                continue
        if date_from and d["first_submitted"] and d["first_submitted"] < date_from:
            continue
        if date_until and d["first_submitted"] and d["first_submitted"] > date_until:
            continue
        d["status"] = st
        out.append(d)

    if sort_by == "date":
        out.sort(key=lambda x: x["first_submitted"], reverse=True)
    elif sort_by == "source":
        out.sort(key=lambda x: x["source"])
    elif sort_by == "venue":
        out.sort(key=lambda x: x["venue"])
    elif sort_by == "age":
        out.sort(key=lambda x: (x["age"] if x["age"] is not None else 10 ** 9))
    return out


def _stats(filtered: list) -> dict:
    by_source, by_venue, by_cat, by_year, by_topic = {}, {}, {}, {}, {}
    chosen = 0
    for p in filtered:
        by_source[p["source"]] = by_source.get(p["source"], 0) + 1
        if p["venue"]:
            by_venue[p["venue"]] = by_venue.get(p["venue"], 0) + 1
        for c in p["categories"]:
            by_cat[c] = by_cat.get(c, 0) + 1
        if p["first_submitted"]:
            by_year[p["first_submitted"][:4]] = by_year.get(p["first_submitted"][:4], 0) + 1
        if p.get("status") == "chosen":
            chosen += 1
        # 文献管理：按主题词典对标题+摘要做分类，聚合主题分布
        corpus = (p.get("title", "") + " " + p.get("abstract", "") + " " + p.get("abstract_zh", ""))
        for topic, _ in classify_topics(corpus, top_k=3):
            by_topic[topic] = by_topic.get(topic, 0) + 1
    return {
        "total": len(filtered),
        "chosen": chosen,
        "filtered": len(filtered) - chosen,
        "by_source": by_source,
        "by_venue": dict(sorted(by_venue.items(), key=lambda x: -x[1])),
        "by_category": dict(sorted(by_cat.items(), key=lambda x: -x[1])),
        "by_topic": dict(sorted(by_topic.items(), key=lambda x: -x[1])),
        "by_year": dict(sorted(by_year.items())),
    }


def _distinct_tags(papers: list) -> dict:
    cats, venues, sources, utags = set(), set(), set(), set()
    for p in papers:
        for c in (p.categories or []):
            cats.add(c)
        if p.venue:
            venues.add(p.venue)
        sources.add(p.source or "arXiv")
        for t in (p.user_tags or "").split(","):
            if t:
                utags.add(t)
    return {
        "categories": sorted(cats),
        "venues": sorted(venues),
        "sources": sorted(sources),
        "user_tags": sorted(utags),
    }


def _seed_demo(database_path: str | None = None) -> int:
    """灌入示例数据，便于在没有联网抓取时直接体验界面。会写入 papers.db。"""
    base = datetime.now()
    venues_list = [
        ("CVPR 2024", "CVPR"), ("NeurIPS 2023", "NeurIPS"), ("ICML 2024", "ICML"),
        ("ICLR 2024", "ICLR"), ("AAAI 2024", "AAAI"), ("ACL 2024", "ACL"),
        ("EMNLP 2023", "EMNLP"), ("ECCV 2024", "ECCV"), ("ICCV 2023", "ICCV"),
        ("TPAMI", "TPAMI"), ("IJCV", "IJCV"), ("JMLR", "JMLR"),
    ]
    topics = [
        "Object Detection", "Image Segmentation", "Vision Transformer",
        "Diffusion Models", "Reinforcement Learning", "Graph Neural Networks",
        "Multimodal Learning", "Self-Supervised Learning", "Neural Rendering",
        "Federated Learning", "Large Language Models", "3D Reconstruction",
    ]
    sources_cycle = ["arXiv", "SemanticScholar", "OpenReview", "CVF", "ACL"]
    cats_pool = [["cs.CV"], ["cs.AI"], ["cs.LG"], ["cs.CL"], ["cs.IR"],
                 ["cs.MA"], ["cs.CV", "cs.AI"], []]
    papers = []
    n = 0
    for vi, (venue, abbr) in enumerate(venues_list):
        for k in range(3):
            n += 1
            age_days = (n * 23) % 700
            dt = base - timedelta(days=age_days)
            src = sources_cycle[(vi + k) % len(sources_cycle)]
            cats = cats_pool[(vi + k) % len(cats_pool)]
            topic = topics[(vi * 3 + k) % len(topics)]
            title = f"{topic}: A Study on {abbr} Benchmark #{k + 1}"
            if src == "arXiv":
                url = f"https://arxiv.org/abs/24{n:02d}.{(n * 7) % 10000:04d}"
                pdf = None
                v = ""
            else:
                url = f"https://example.org/{abbr.lower()}/{n:04d}"
                pdf = url + ".pdf"
                v = venue
            papers.append(Paper(
                url=url,
                title=title,
                categories=cats,
                authors=f"Author {(n % 9) + 1}, Author {(n % 7) + 1}",
                abstract=f"This paper proposes a new method for {topic.lower()} evaluated on {venue}.",
                comments=venue,
                first_submitted_date=dt,
                first_announced_date=dt,
                source=src,
                venue=v,
                pdf_link=pdf,
                doi=f"10.1000/xyz{n}" if src == "SemanticScholar" else None,
            ))
    PaperDatabase(database_path or DB_PATH).add_papers(papers)
    return len(papers)


def _do_crawl(params: dict):
    with _crawl_lock:
        _crawl_state.update(running=True, done=False, count=0,
                            message="抓取进行中…", ts=datetime.now())
    try:
        today = datetime.now()
        date_from = params.get("date_from") or (today - timedelta(days=30)).strftime("%Y-%m-%d")
        date_until = params.get("date_until") or today.strftime("%Y-%m-%d")
        ms = MultiSourceScraper(
            date_from=date_from,
            date_until=date_until,
            optional_keywords=params.get("keywords") or DEFAULT_KEYWORDS,
            category_whitelist=DEFAULT_WHITELIST,
            trans_to=params.get("trans_to") or "zh-CN",
            source_names=params.get("sources") or DEFAULT_SOURCES,
            ai_venues=DEFAULT_VENUES,
        )
        ms.fetch_update()
        count = len(ms.papers)
        with _crawl_lock:
            _crawl_state.update(running=False, done=True, count=count,
                                message=f"抓取完成，新增/更新 {count} 篇", ts=datetime.now())
    except Exception as e:
        with _crawl_lock:
            _crawl_state.update(running=False, done=True, count=0,
                                message=f"抓取失败: {e}", ts=datetime.now())


# ---------------- HTTP 处理 ----------------
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, text: str, code=200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            if FRONTEND.exists():
                self._send_html(FRONTEND.read_text(encoding="utf-8"))
            else:
                self._send_html("<h1>webui_frontend.html 缺失</h1>", 500)
            return

        if path == "/api/tags":
            self._send_json(_distinct_tags(_load_all()))
            return

        if path in ("/api/papers", "/api/stats"):
            params = {k: (v[0] if len(v) == 1 else v) for k, v in qs.items()}
            # 多选参数在 query string 中以逗号分隔字符串传递
            for key in ("sources", "categories", "venues"):
                if key in params and isinstance(params[key], str):
                    params[key] = [x for x in params[key].split(",") if x]
            papers = _load_all()
            filtered = _apply_filters(papers, params)
            if path == "/api/papers":
                # 限制单次返回条数，避免超大列表卡顿
                limit = int(qs.get("limit", [500])[0])
                self._send_json({"count": len(filtered),
                                 "papers": filtered[:limit]})
            else:
                self._send_json(_stats(filtered))
            return

        if path == "/api/crawl/status":
            with _crawl_lock:
                self._send_json(dict(_crawl_state))
            return

        if path == "/api/litman":
            # 对单篇论文做「智能分析」：关键词 / 主题分类 / 抽取式摘要 / 推荐标签
            url = qs.get("url", [""])[0]
            if not url:
                self._send_json({"error": "missing url"}, 400)
                return
            db = PaperDatabase(DB_PATH)
            paper = db.get_by_url(url)
            if not paper:
                self._send_json({"error": "not found"}, 404)
                return
            result = litman_analyze(
                title=paper.title or "",
                abstract=paper.abstract or "",
                abstract_translated=paper.abstract_translated or "",
            )
            self._send_json(result)
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/crawl":
            with _crawl_lock:
                if _crawl_state.get("running"):
                    self._send_json({"ok": False, "message": "已有抓取任务在进行"})
                    return
            t = threading.Thread(target=_do_crawl, args=(body,), daemon=True)
            t.start()
            self._send_json({"ok": True, "message": "已启动抓取任务"})
            return

        if path == "/api/demo":
            try:
                n = _seed_demo()
                self._send_json({"ok": True, "message": f"已加载 {n} 条示例数据"})
            except Exception as e:
                self._send_json({"ok": False, "message": f"示例数据加载失败: {e}"}, 500)
            return

        if path == "/api/export":
            try:
                from paper import PaperExporter
                papers = _load_all()
                if not papers:
                    self._send_json({"ok": False, "message": "数据库为空，先抓取或加载示例数据"})
                    return
                dates = [p.first_submitted_date for p in papers if p.first_submitted_date]
                dmin = min(dates).strftime("%Y-%m-%d")
                dmax = max(dates).strftime("%Y-%m-%d")
                exporter = PaperExporter(dmin, dmax, database_path=DB_PATH)
                exporter.to_markdown(output_dir=str(HERE / "output_llms"),
                                     filename_format="%Y-%m-%d", meta=True)
                exporter.to_csv(output_dir=str(HERE / "output_llms"),
                                filename_format="%Y-%m-%d", header=False,
                                csv_config=dict(delimiter="\t"))
                self._send_json({"ok": True, "message": "已导出到 output_llms/ 目录"})
            except Exception as e:
                self._send_json({"ok": False, "message": f"导出失败: {e}"}, 500)
            return

        if path == "/api/paper/meta":
            # 保存单篇论文的阅读管理元数据：笔记 / 用户标签 / 阅读状态 / 评分 / 收藏
            try:
                url = body.get("url")
                if not url:
                    self._send_json({"ok": False, "message": "缺少 url"}, 400)
                    return
                db = PaperDatabase(DB_PATH)
                if not db.get_by_url(url):
                    self._send_json({"ok": False, "message": "论文不存在"}, 404)
                    return
                rowcount = db.update_meta(
                    url,
                    user_tags=body.get("user_tags"),
                    notes=body.get("notes"),
                    read_status=body.get("read_status"),
                    rating=body.get("rating"),
                    favorite=body.get("favorite"),
                )
                self._send_json({"ok": True, "rows": rowcount})
            except Exception as e:
                self._send_json({"ok": False, "message": f"保存失败: {e}"}, 500)
            return

        self._send_json({"error": "not found"}, 404)

    # 静默日志（避免刷屏）
    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description="arxiv_crawler 可视化界面")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"🚀 可视化界面已启动： http://{args.host}:{args.port}")
    print("   按 Ctrl+C 停止。首次使用可点击「加载示例数据」体验界面。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 已停止")


if __name__ == "__main__":
    main()
