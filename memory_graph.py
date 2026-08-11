"""Tenant-scoped local semantic recall for lessons and conversation history."""

import re
import threading
import time

import history as history_store
from tenant_state import atomic_write_json, load_json, tenant_name

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False


_CACHE = {}
_CACHE_LOCK = threading.RLock()
_CACHE_TTL = 60
_CACHE_TENANTS = 64


def _normalize(text):
    return re.sub(r"[\s\u3000]+", " ", str(text or "").lower()).strip()


def _chunk_text(text, max_len=400):
    chunks, current = [], ""
    for segment in re.split(r"[\n。！？；!?;]", str(text or "")):
        segment = segment.strip()
        if not segment:
            continue
        if current and len(current) + len(segment) > max_len:
            chunks.append(current)
            current = segment
        else:
            current = (current + " " + segment).strip()
    if current:
        chunks.append(current)
    return chunks


def _load_lessons():
    value = load_json("lessons.json", [])
    return value if isinstance(value, list) else []


def _load_memory():
    value = load_json("memory_graph.json", [])
    return value if isinstance(value, list) else []


def _add_document(docs, metadata, text, meta):
    for chunk in _chunk_text(text):
        normalized = _normalize(chunk)
        if normalized:
            docs.append(normalized)
            metadata.append({**meta, "text": chunk})


def _rebuild_index(force=False):
    tenant = tenant_name()
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(tenant)
        if cached and not force and now - cached["ts"] < _CACHE_TTL:
            return cached["docs"], cached["meta"], cached["tfidf"]

    docs, metadata = [], []
    for lesson in _load_lessons():
        if isinstance(lesson, dict):
            _add_document(docs, metadata, lesson.get("text", ""), {
                "source": "lesson", "ts": lesson.get("ts", 0),
                "origin": lesson.get("source", ""), "cid": "", "title": "",
            })
    for memory in _load_memory():
        if isinstance(memory, dict):
            _add_document(docs, metadata, memory.get("text", ""), {
                "source": "memory", "ts": memory.get("ts", 0),
                "cid": memory.get("cid", ""), "title": memory.get("title", ""),
            })
    # history_store resolves projects and the authenticated tenant for us.
    for conversation in history_store.scan_all_conversations():
        cid, title = conversation.get("id", ""), conversation.get("title", "")
        for message in conversation.get("messages", []):
            if not isinstance(message, dict):
                continue
            text = message.get("content")
            if message.get("role") != "user" or not isinstance(text, str) or len(text.strip()) < 8:
                continue
            if text.startswith("图片附件") or text.startswith("/"):
                continue
            _add_document(docs, metadata, text, {
                "source": "history", "ts": conversation.get("updated", 0),
                "cid": cid, "title": title,
            })

    vectorizer = None
    if _HAS_SKLEARN and docs:
        try:
            vectorizer = TfidfVectorizer(
                analyzer="char_wb", ngram_range=(2, 3), max_features=20_000,
                sublinear_tf=True)
            vectorizer.fit(docs)
        except Exception:
            vectorizer = None
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_TENANTS and tenant not in _CACHE:
            oldest = min(_CACHE, key=lambda key: _CACHE[key]["ts"])
            _CACHE.pop(oldest, None)
        _CACHE[tenant] = {"ts": now, "docs": docs, "meta": metadata, "tfidf": vectorizer}
    return docs, metadata, vectorizer


def _tokens(text):
    result = {}
    for segment in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9][a-z0-9_-]+", _normalize(text)):
        pieces = [segment] + [segment[index:index + 2] for index in range(len(segment) - 1)]
        for piece in pieces:
            result[piece] = result.get(piece, 0) + 1
    return result


def _cosine(left, right):
    if not left or not right:
        return 0.0
    numerator = sum(value * right.get(key, 0) for key, value in left.items())
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def recall(query, top_k=5, min_score=0.12, exclude_cid=None):
    if not str(query or "").strip():
        return []
    docs, metadata, vectorizer = _rebuild_index()
    if not docs:
        return []
    if vectorizer is not None and _HAS_SKLEARN:
        try:
            scores = cosine_similarity(vectorizer.transform([_normalize(query)]),
                                       vectorizer.transform(docs))[0]
        except Exception:
            scores = []
    else:
        query_tokens = _tokens(query)
        scores = [_cosine(query_tokens, _tokens(document)) for document in docs]
    ranked = []
    for index, score in enumerate(scores):
        if float(score) < min_score:
            continue
        meta = metadata[index]
        if exclude_cid and meta.get("cid") == exclude_cid:
            continue
        ranked.append({**meta, "score": round(float(score), 4)})
    ranked.sort(key=lambda item: -item["score"])
    seen, output = set(), []
    for item in ranked:
        identity = (item.get("cid", ""), item.get("text", "")[:80])
        if identity in seen:
            continue
        seen.add(identity)
        output.append(item)
        if len(output) >= max(1, min(int(top_k), 20)):
            break
    return output


def memory_system_prompt(query, top_k=4, exclude_cid=None):
    hits = recall(query, top_k=top_k, exclude_cid=exclude_cid)
    if not hits:
        return ""
    lines = ["【相关历史记忆】只把以下内容当作可能有帮助的旧资料；当前用户指令优先。"]
    for hit in hits:
        label = {"lesson": "经验", "memory": "记忆", "history": "历史对话"}.get(
            hit.get("source"), "历史")
        lines.append("- [%s] %s" % (label, hit.get("text", "")[:200]))
    return "\n".join(lines)


def add_memory(text, source="memory", cid="", title="", ts=None):
    value = str(text or "").strip()
    if not value:
        return False
    memories = _load_memory()
    memories.append({"text": value[:500], "source": source, "cid": cid,
                     "title": title, "ts": ts or time.time()})
    try:
        atomic_write_json("memory_graph.json", memories[-500:])
        with _CACHE_LOCK:
            _CACHE.pop(tenant_name(), None)
        return True
    except OSError:
        return False
