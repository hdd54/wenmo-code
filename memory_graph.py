"""记忆图（Memory Graph）：跨会话语义召回。
借鉴 jcode 的 Agent Memory 思路——每轮对话/每条教训被索引，按当前用户消息的
语义相似度（TF-IDF + 余弦）召回最相关历史片段，注入系统提示。
纯本地（sklearn TfidfVectorizer），零 API 调用，零网络依赖。
"""

import io
import json
import os
import re
import time

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 打包版：记忆数据存 WENMO_DATA_DIR
_env_data = os.environ.get("WENMO_DATA_DIR")
if _env_data:
    BASE_DIR = _env_data
LESSONS_FILE = os.path.join(BASE_DIR, "lessons.json")
# 记忆库文件：跨会话可召回的历史片段（含对话摘要与教训）
MEMORY_FILE = os.path.join(BASE_DIR, "memory_graph.json")

_CACHE = {"ts": 0, "docs": [], "meta": [], "tfidf": None}


def _load_lessons():
    """读取 lessons.json（教训库，与 gui_server 共用）"""
    try:
        with open(LESSONS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _load_memory():
    """读取记忆库（对话片段索引）"""
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _normalize(text):
    """归一化：去空白/标点，仅保留中英文与数字（降噪，提高召回质量）"""
    if not text:
        return ""
    t = str(text).lower()
    t = re.sub(r"[\s\u3000]+", " ", t)
    return t


def _chunk_text(text, max_len=400):
    """把长文本切成语义片段（按句/换行），每段 ≤ max_len 字符"""
    if not text:
        return []
    t = str(text)
    # 按换行/句号切分，合并小段
    segs = re.split(r"[\n。！？!?；;]", t)
    chunks = []
    cur = ""
    for s in segs:
        s = s.strip()
        if not s:
            continue
        if len(cur) + len(s) > max_len and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks


def _rebuild_index(force=False):
    """重建 TF-IDF 索引（60 秒缓存）。返回 (docs, meta, vectorizer)"""
    now = time.time()
    if not force and _CACHE["tfidf"] is not None and now - _CACHE["ts"] < 60:
        return _CACHE["docs"], _CACHE["meta"], _CACHE["tfidf"]

    docs = []   # 索引文本
    meta = []   # 对应元信息 {source, cid, ts, title, text}

    # ① 教训库（lessons.json）——高优先级记忆
    for l in _load_lessons():
        text = l.get("text", "")
        if not text:
            continue
        for ch in _chunk_text(text):
            docs.append(_normalize(ch))
            meta.append({"source": "lesson", "ts": l.get("ts", 0),
                         "text": ch, "origin": l.get("source", "")})
    # ② 记忆库（memory_graph.json 手工/自动添加的对话片段）
    for m in _load_memory():
        text = m.get("text", "")
        if not text:
            continue
        for ch in _chunk_text(text):
            docs.append(_normalize(ch))
            meta.append({"source": "memory", "ts": m.get("ts", 0),
                         "text": ch, "cid": m.get("cid", ""), "title": m.get("title", "")})
    # ③ 历史对话（history/**/*.json）——跨会话对话记忆
    hdir = os.path.join(BASE_DIR, "history")
    if os.path.isdir(hdir):
        for root, dirs, files in os.walk(hdir):
            for fn in files:
                if not fn.endswith(".json") or ".tmp" in fn:
                    continue
                try:
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        c = json.load(f)
                    if not isinstance(c, dict):
                        continue
                    cid = c.get("id", fn[:-5])
                    title = c.get("title", "")
                    msgs = c.get("messages", [])
                    # 只取用户消息做索引（用户意图是记忆主体；助手长回复噪音大）
                    for m in msgs:
                        if m.get("role") != "user" or not isinstance(m.get("content"), str):
                            continue
                        text = m["content"].strip()
                        if not text or len(text) < 8:
                            continue
                        # 图片附件标记等噪音跳过
                        if text.startswith("图片附件") or text.startswith("/"):
                            continue
                        for ch in _chunk_text(text):
                            docs.append(_normalize(ch))
                            meta.append({"source": "history", "ts": c.get("updated", 0),
                                         "text": ch, "cid": cid, "title": title})
                except Exception:
                    continue

    tfidf = None
    if _HAS_SKLEARN and docs:
        try:
            tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3),
                                    max_features=20000, sublinear_tf=True)
            tfidf.fit(docs)
        except Exception:
            tfidf = None

    _CACHE.update(ts=now, docs=docs, meta=meta, tfidf=tfidf)
    return docs, meta, tfidf


def _cosine_sim(a, b):
    """余弦相似度（纯 Python，防 sklearn 不可用时兜底）"""
    if not a or not b:
        return 0.0
    inter = sum(a.get(k, 0) * b.get(k, 0) for k in a)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return inter / (na * nb)


def _fallback_tokenize(text):
    """无 sklearn 时的兜底：字符 n-gram 词袋（一元+二元，中文按字、英文按词）。
    比纯二元组更抗噪音：单字也能匹配主题词。"""
    tokens = {}
    t = _normalize(text)
    if not t:
        return tokens
    # 中文连续串 + 英文单词
    cn = re.findall(r"[\u4e00-\u9fff]+", t)
    en = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", t)
    for seg in cn + en:
        if len(seg) <= 1:
            tokens[seg] = tokens.get(seg, 0) + 1
            continue
        # 整串 + 二元字符
        tokens[seg] = tokens.get(seg, 0) + 2
        for i in range(len(seg) - 1):
            bigram = seg[i:i + 2]
            tokens[bigram] = tokens.get(bigram, 0) + 1
    return tokens


def recall(query, top_k=5, min_score=0.12, exclude_cid=None):
    """按查询语义召回最相关记忆片段。
    返回 [{text, source, score, ts, cid, title}]（按相关度降序）。
    exclude_cid：排除当前对话自身（避免自引用当前轮）"""
    if not query or not query.strip():
        return []
    docs, meta, tfidf = _rebuild_index()
    if not docs:
        return []
    q = _normalize(query)

    if tfidf is not None and _HAS_SKLEARN:
        try:
            q_vec = tfidf.transform([q])
            X = tfidf.transform(docs)
            from sklearn.metrics.pairwise import cosine_similarity
            scores = cosine_similarity(q_vec, X)[0]
        except Exception:
            scores = None
    else:
        q_tok = _fallback_tokenize(q)
        scores = [_cosine_sim(q_tok, _fallback_tokenize(d)) for d in docs]

    if scores is None:
        return []

    ranked = []
    for i, s in enumerate(scores):
        if s < min_score:
            continue
        m = meta[i] if i < len(meta) else {}
        if exclude_cid and m.get("cid") == exclude_cid:
            continue
        ranked.append({"text": m.get("text", ""), "source": m.get("source", ""),
                       "score": round(float(s), 4), "ts": m.get("ts", 0),
                       "cid": m.get("cid", ""), "title": m.get("title", "")})
    ranked.sort(key=lambda x: -x["score"])
    # 去重（同一片段可能命中多段）
    seen = set()
    out = []
    for r in ranked:
        key = (r["cid"], r["text"][:40])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= top_k:
            break
    return out


def memory_system_prompt(query, top_k=4, exclude_cid=None):
    """生成记忆图注入文本（供系统提示使用）。无相关记忆返回空串。"""
    hits = recall(query, top_k=top_k, exclude_cid=exclude_cid)
    if not hits:
        return ""
    parts = ["【历史记忆：以下是其他对话/经验中与你当前问题语义最相关的记录，参考它们回答】"]
    for h in hits:
        if h["source"] == "lesson":
            tag = "经验教训"
        elif h["source"] == "memory":
            tag = "记忆"
        else:
            tag = "历史对话" + (f"《{h['title'][:20]}》" if h.get("title") else "")
        parts.append(f"- [{tag}] {h['text'][:200]}")
    return "\n".join(parts)


def add_memory(text, source="memory", cid="", title="", ts=None):
    """手工/自动添加一条记忆片段到 memory_graph.json（保留最近 500 条）"""
    if not text or not str(text).strip():
        return False
    mem = _load_memory()
    mem.append({"text": str(text).strip()[:500], "source": source,
                "cid": cid, "title": title, "ts": ts or time.time()})
    mem = mem[-500:]
    try:
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, MEMORY_FILE)
        _CACHE["ts"] = 0   # 强制下次重建索引
        return True
    except Exception:
        return False
