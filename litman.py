"""
litman.py — 文献管理核心：关键词抽取 / 主题分类 / 抽取式摘要（v0.9.0）

设计目标：
  - 零第三方依赖（仅用 Python 标准库），与 webui.py 的零依赖风格一致，
    因此可随「可移植安装包」一起直接分发，离线可用。
  - 所有算法都是本地、确定性的，不调用任何外部 API / 模型。

主要能力：
  - extract_keywords(text, top_k)      : 从标题/摘要抽取关键词（含英文二元词组）
  - classify_topics(text, top_k)       : 按内置 AI/ML 主题词典做关键词/摘要分类
  - summarize(text, n_sentences)       : 抽取式摘要（基于词频中心的句子打分）
  - analyze(title, abstract, ...)      : 聚合上述三项，供 UI 一键「智能分析」
  - suggested_tags(...)                : 生成推荐标签（关键词 + 命中主题）
"""

import re
from collections import Counter

# ---------------------------------------------------------------------------
# 停用词（英文，覆盖常见功能词与论文套话，降低噪声关键词）
# ---------------------------------------------------------------------------
_STOPWORDS = set(
    """
a an the of and or to in on for with by from as at is are be been being was were
this that these those we our their his her its their they them it he she you your i we us
using used use can may might will would should could if then than thus however but also
more most less least such into over under between within which what when where who whom how
why not no nor do does did has have had having new our paper method methods approach
results result results proposed show shows shown shows based model models data set sets
training test testing experiment experiments experimental performance compared comparison
state art via per than eg ie et al fig figure table section using towards toward
    """.split()
)

# ---------------------------------------------------------------------------
# 主题分类词典（AI / ML 子领域 -> 命中关键词，子串匹配，大小写不敏感）
# 用户可在 UI 中叠加自定义标签；此词典用于「自动分类」初筛。
# ---------------------------------------------------------------------------
TOPIC_TAXONOMY = {
    "目标检测": ["detection", "detector", "yolo", "object", "bounding box", "rcnn", "faster", "ssd"],
    "图像分割": ["segmentation", "segment", "mask", "semantic", "instance", "panoptic"],
    "图像分类": ["classification", "classify", "recogni", "imagenet"],
    "视觉Transformer": ["vision transformer", "vit", "deit", "swin", "attention"],
    "扩散模型": ["diffusion", "denoising", "score-based", "ddpm", "latent diffusion"],
    "生成模型": ["generative", "gan", "generation", "text-to-image", "synthesis"],
    "多模态": ["multimodal", "multimodality", "vision-language", "vlm", "clip", "image-text"],
    "大语言模型": ["large language model", "llm", "gpt", "instruction tuning", "in-context", "chatgpt", "prompt"],
    "强化学习": ["reinforcement", "rlhf", "policy", "reward", "q-learning", "actor-critic"],
    "图神经网络": ["graph neural", "gnn", "gcn", "message passing", "node classification"],
    "自监督/对比学习": ["self-supervised", "contrastive", "simclr", "moco", "pretrain", "pretext", "masked"],
    "3D/神经渲染": ["3d", "neural rendering", "nerf", "point cloud", "reconstruction", "radiance"],
    "视频理解": ["video", "temporal", "action recognition", "optical flow", "tracking"],
    "联邦/隐私": ["federated", "privacy", "differential privacy", "secure aggregation"],
    "小样本/迁移": ["few-shot", "zero-shot", "meta-learning", "transfer learning", "domain adaptation"],
    "定位/导航": ["localization", "positioning", "slam", "navigation", "gnss", "uav", "drone"],
    "综述/基准": ["survey", "benchmark", "dataset", "review", "baseline"],
}

_READY_STATUSES = ("unread", "reading", "read")


# ---------------------------------------------------------------------------
# 文本预处理
# ---------------------------------------------------------------------------
def _tokenize(text: str):
    """转小写后抽取英文/数字词与连字符词（如 res-net、yolo-v8）。"""
    if not text:
        return []
    return re.findall(r"[a-z][a-z0-9\-]+", text.lower())


def _split_sentences(text: str):
    """按句号/问号/感叹号切句，保留句尾标点。"""
    if not text:
        return []
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s for s in sents if s]


# ---------------------------------------------------------------------------
# 关键词抽取
# ---------------------------------------------------------------------------
def extract_keywords(text: str, top_k: int = 8, min_len: int = 3):
    """
    从一段文本中抽取 top_k 个关键词。
    策略：一元词频 + 二元词组（bigram）词频，bigram 出现 >=2 优先；
    过滤停用词、纯数字、过短词、URL 域名噪声。对英文摘要/标题效果最佳。
    """
    if not text:
        return []
    # 先剥离 URL，避免把 https/github/com 之类当成关键词
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b(?:arxiv|doi)\s*:\s*\S+", " ", text)  # 清理 arxiv: / doi: 标识
    _URL_NOISE = {"https", "http", "com", "org", "www", "github",
                  "doi", "html", "pdf", "net", "io", "edu", "abs"}
    tokens = _tokenize(text)
    words = [
        t for t in tokens
        if t not in _STOPWORDS and t not in _URL_NOISE
        and len(t) >= min_len and not t.isdigit()
    ]

    unigram_counter = Counter(words)

    # 构造二元词组（过滤含停用词的 bigram，避免 "of detection" 之类噪声）
    bigrams = [
        f"{words[i]} {words[i + 1]}"
        for i in range(len(words) - 1)
        if words[i] not in _STOPWORDS and words[i + 1] not in _STOPWORDS
    ]
    bigram_counter = Counter(bigrams)

    # bigram 优先（更具体），再补高频 unigram
    scored = [(bg, c) for bg, c in bigram_counter.items() if c >= 2]
    for w, c in unigram_counter.most_common():
        if c >= 2 and w not in _STOPWORDS:
            scored.append((w, c))
    scored.sort(key=lambda x: -x[1])

    seen, result = set(), []
    for term, _ in scored:
        if term in seen:
            continue
        seen.add(term)
        result.append(term)
        if len(result) >= top_k:
            break
    return result


# ---------------------------------------------------------------------------
# 主题分类
# ---------------------------------------------------------------------------
def classify_topics(text: str, top_k: int = 3):
    """
    按 TOPIC_TAXONOMY 对文本做关键词/摘要分类。
    返回 list[(topic, score)]，score = 命中关键词出现次数之和，按分数降序。
    """
    if not text:
        return []
    low = text.lower()
    scores = {}
    for topic, kws in TOPIC_TAXONOMY.items():
        s = 0
        for kw in kws:
            s += low.count(kw)
        if s > 0:
            scores[topic] = s
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# 抽取式摘要
# ---------------------------------------------------------------------------
def summarize(text: str, n_sentences: int = 3, min_len: int = 3):
    """
    抽取式摘要：把每句按「句中有效词词频之和 / 句长」打分，
    取分数最高的 n_sentences 句，并按原文顺序拼回，避免语义跳跃。
    若原文句数 <= n_sentences，直接返回原文。
    """
    sents = _split_sentences(text)
    if len(sents) <= n_sentences:
        return text.strip()

    words = [w for w in _tokenize(text) if w not in _STOPWORDS and len(w) >= min_len]
    freq = Counter(words)

    def _score(s):
        ws = [w for w in _tokenize(s) if w not in _STOPWORDS and len(w) >= min_len]
        if not ws:
            return 0.0
        return sum(freq[w] for w in ws) / len(ws)

    scored = [(i, _score(s)) for i, s in enumerate(sents)]
    top = sorted(scored, key=lambda x: -x[1])[:n_sentences]
    top.sort(key=lambda x: x[0])  # 维持原文顺序
    out = " ".join(sents[i] for i, _ in top)
    # 剥离 URL，避免摘要里出现裸链接
    out = re.sub(r"https?://\S+", "(链接)", out)
    return out.strip()


# ---------------------------------------------------------------------------
# 聚合分析
# ---------------------------------------------------------------------------
def analyze(title: str = "", abstract: str = "", abstract_translated: str = "", top_k: int = 8):
    """
    聚合分析：对标题 + 摘要（含翻译）做关键词抽取、主题分类、摘要抽取。
    返回 dict，便于直接序列化为 JSON 给前端。
    """
    corpus = " ".join(x for x in (title, abstract, abstract_translated) if x)
    keywords = extract_keywords(corpus, top_k=top_k)
    topics = classify_topics(corpus, top_k=3)
    summary_src = abstract_translated or abstract or title
    summary = summarize(summary_src, n_sentences=3) if summary_src else ""
    return {
        "keywords": keywords,
        "topics": [{"topic": t, "score": s} for t, s in topics],
        "summary": summary,
        "suggested_tags": keywords + [t for t, _ in topics],
    }


def suggested_tags(title: str = "", abstract: str = "", abstract_translated: str = "", top_k: int = 8):
    """仅返回推荐标签列表（关键词 + 命中主题），供快速写入用户标签。"""
    a = analyze(title, abstract, abstract_translated, top_k=top_k)
    return a["suggested_tags"]


if __name__ == "__main__":
    demo_title = "Diffusion Models for Object Detection in Aerial Images"
    demo_abstract = (
        "We propose a novel diffusion-based detector for small object detection in "
        "aerial and drone imagery. Our method uses a vision transformer backbone and "
        "denoising score matching to improve localization under heavy occlusion. "
        "Experiments on two benchmarks show state-of-the-art performance. We further "
        "provide a survey of detection transformers and discuss future directions."
    )
    r = analyze(demo_title, demo_abstract)
    print("KEYWORDS:", r["keywords"])
    print("TOPICS:", [(t["topic"], t["score"]) for t in r["topics"]])
    print("SUMMARY:", r["summary"])
