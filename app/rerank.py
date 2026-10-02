# -*- coding: utf-8 -*-
"""Второй этап поиска (День 23): переписывание запроса + фильтрация + реранкинг.

Воронка для одного вопроса:

  вопрос ─► rewrite (raw | en | hyde) ─► векторный поиск top-K_before
        ─► фильтр 1: косинус ≥ sim_min
        ─► реранкер Qwen3-Reranker (кросс-энкодер в Ollama): P(yes) «отрывок отвечает на вопрос»
        ─► фильтр 2: оценка ≥ rel_min
        ─► сортировка по оценке реранкера ─► top-K_after уходит в LLM

Эмбеддинги вопроса и отрывка считаются порознь — поиск быстрый, но грубый. Реранкер читает
вопрос и отрывок вместе и отвечает yes/no; оценка — P(yes) / (P(yes) + P(no)) по logprobs
первого токена. Если после фильтров не осталось ни одного отрывка — модель получает пустой
контекст и должна честно ответить, что в книге этого нет.
"""
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import analysis
import embed
import llm

RERANK_MODEL = os.getenv("RERANK_MODEL", "B-A-M-N/qwen3-reranker-0.6b-fp16")
STRATEGY = "structure"
ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "data" / "rerank-cache.json"

# Параметры «базового» RAG (День 22) и «улучшенного» (День 23). Значения улучшенного подобраны
# прогоном на вкладке «Реранкинг» (см. README) и могут меняться на странице для своих запросов.
BASE = {"query": "raw", "k_before": 5, "sim_min": 0.0, "rerank": False, "rel_min": 0.0, "k_after": 5}
IMPROVED = {"query": "raw", "k_before": 20, "sim_min": 0.45, "rerank": True, "rel_min": 0.65, "k_after": 5}
LIMITS = {"k_before": (1, 40), "k_after": (1, 10), "sim_min": (-1.0, 1.0), "rel_min": (0.0, 1.0)}
QUERY_MODES = ("raw", "en", "hyde")


def params(p=None, base=IMPROVED):
    """Нормализует параметры воронки: значения по умолчанию + проверка диапазонов."""
    out = dict(base)
    for k, v in (p or {}).items():
        if k in out and v is not None:
            out[k] = type(base[k])(v) if not isinstance(base[k], bool) else bool(v)
    if out["query"] not in QUERY_MODES:
        raise ValueError("query: raw, en или hyde")
    for k, (lo, hi) in LIMITS.items():
        if not lo <= out[k] <= hi:
            raise ValueError("{}: от {} до {}".format(k, lo, hi))
    out["k_before"] = max(out["k_before"], out["k_after"])
    return out


# ═════════════════════════════ кэш (переписывания и оценки реранкера) ═════════════════════════════

_lock = threading.Lock()
_cache = None


def _load_cache():
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
        _cache.setdefault("rewrite", {})
        _cache.setdefault("rel", {})
    return _cache


def _save_cache():
    with _lock:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


# ═════════════════════════════ переписывание запроса ═════════════════════════════

REWRITE_PROMPTS = {
    "en": (
        "Translate the user's question about Herman Melville's novel «Moby-Dick» into a short English search query. "
        "Use the English spelling of names and places as in the novel. Output only the query, nothing else."
    ),
    "hyde": (
        "Write a short passage (2–4 sentences) in English, in the style of Herman Melville's «Moby-Dick», that could "
        "be the fragment of the novel answering the user's question. Use the English names from the novel. "
        "If you do not know the details, write a plausible scene with the right characters and setting. "
        "Output only the passage."
    ),
}


def rewrite(question, mode, model):
    """Запрос для векторного поиска. raw — как есть, en — перевод, hyde — гипотетический отрывок."""
    if mode == "raw":
        return question, 0
    c = _load_cache()
    key = "{}|{}|{}".format(model, mode, question)
    if key in c["rewrite"]:
        return c["rewrite"][key], 0
    t0 = time.time()
    res = llm.chat(model, [{"role": "system", "content": REWRITE_PROMPTS[mode]},
                           {"role": "user", "content": question}], temperature=0.0, max_tokens=200)
    text = res["text"].strip().strip('"«»').strip() or question
    with _lock:
        c["rewrite"][key] = text
    _save_cache()
    return text, round((time.time() - t0) * 1000)


# ═════════════════════════════ реранкер ═════════════════════════════

INSTRUCTION = "Given a question about the novel Moby-Dick, retrieve passages of the novel that answer the question"


def _prompt(query, doc, instruction=INSTRUCTION):
    """Шаблон Qwen3-Reranker: ответ ассистента начинается с пустых рассуждений, следующий токен — yes или no."""
    return ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the "
            "Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
            "<Instruct>: " + instruction + "\n<Query>: " + query + "\n<Document>: " + doc +
            "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")


def _score_one(query, doc, instruction=INSTRUCTION):
    body = {"model": RERANK_MODEL, "prompt": _prompt(query, doc[:4000], instruction), "raw": True, "stream": False,
            "logprobs": True, "top_logprobs": 20,
            "options": {"num_predict": 1, "temperature": 0, "num_ctx": 2048}}
    req = urllib.request.Request(embed.OLLAMA_URL + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise llm.LlmError("реранкер {}: HTTP {}: {}".format(RERANK_MODEL, e.code, e.read().decode("utf-8", "replace")[:200]))
    except urllib.error.URLError as e:
        raise llm.LlmError("реранкер недоступен: {}".format(e.reason))
    lp = data.get("logprobs") or []
    if not lp:
        raise llm.LlmError("Ollama не вернула logprobs — нужна версия с поддержкой logprobs")
    yes = no = 0.0
    for t in lp[0].get("top_logprobs", []):
        tok = t["token"].strip().lower()
        if tok == "yes":
            yes += math.exp(t["logprob"])
        elif tok == "no":
            no += math.exp(t["logprob"])
    if yes + no == 0:
        raise llm.LlmError("реранкер не ответил yes/no: проверьте, что RERANK_MODEL — Qwen3-Reranker")
    return yes / (yes + no)


def score(question, chunks):
    """Оценки реранкера для чанков (кэшируются по вопросу и chunk_id). Возвращает (оценки, мс, из кэша)."""
    c = _load_cache()
    out, t0, fresh = [], time.time(), 0
    for ch in chunks:
        key = "{}|{}|{}".format(RERANK_MODEL, question, ch["chunk_id"])
        if key not in c["rel"]:
            body = ch["text"][ch["body_start"] - ch["start"]:]
            s = _score_one(question, "Moby-Dick — {}\n{}".format(ch["section"], body))
            with _lock:
                c["rel"][key] = round(s, 4)
            fresh += 1
        out.append(c["rel"][key])
    if fresh:
        _save_cache()
    return out, round((time.time() - t0) * 1000), len(chunks) - fresh


SUPPORT_INSTRUCTION = ("Given a statement written in Russian about the novel Moby-Dick, judge whether the Document "
                       "(verbatim quotes from the novel) supports the statement")


def support(statement, quotes_text):
    """Подтверждают ли цитаты утверждение: P(yes) того же кросс-энкодера с другой инструкцией."""
    return round(_score_one(statement, quotes_text, SUPPORT_INSTRUCTION), 4)


def status():
    st = {"model": RERANK_MODEL}
    try:
        tags = json.loads(urllib.request.urlopen(embed.OLLAMA_URL + "/api/tags", timeout=5).read())
        st["pulled"] = any(m["name"] == RERANK_MODEL or m["name"].split(":")[0] == RERANK_MODEL.split(":")[0]
                           for m in tags.get("models", []))
    except Exception as e:
        st["pulled"], st["error"] = False, str(e)
    return st


# ═════════════════════════════ воронка ═════════════════════════════

def funnel(question, p, model, chapter_range=None):
    """Полный второй этап для одного вопроса. Возвращает итог с путём каждого кандидата.
    chapter_range — (с, по): ограничение из памяти задачи чата; чанки вне диапазона в поиск не попадают."""
    t0 = time.time()
    query, rewrite_ms = rewrite(question, p["query"], model)
    t1 = time.time()
    if chapter_range:
        lo, hi = chapter_range
        hits = [h for h in analysis.rank(query, STRATEGY, 1000)
                if all(lo <= n <= hi for n in h[1]["chapters"])][:p["k_before"]]
    else:
        hits = analysis.rank(query, STRATEGY, p["k_before"])
    search_ms = round((time.time() - t1) * 1000)

    cands = []
    for rank, (cos, ch) in enumerate(hits, 1):
        cands.append({"rank": rank, "chunk_id": ch["chunk_id"], "section": ch["section"], "chapters": ch["chapters"],
                      "tokens": ch["tokens"], "cos": round(cos, 4), "rel": None, "stage": None, "reason": "",
                      "final": None, "_chunk": ch})

    passed_sim = [c for c in cands if c["cos"] >= p["sim_min"]]
    for c in cands:
        if c["cos"] < p["sim_min"]:
            c["stage"], c["reason"] = "sim", "косинус {:.3f} ниже порога {:.2f}".format(c["cos"], p["sim_min"])

    rerank_ms, cached = 0, 0
    if p["rerank"] and passed_sim:
        rels, rerank_ms, cached = score(question, [c["_chunk"] for c in passed_sim])
        for c, r in zip(passed_sim, rels):
            c["rel"] = r
        passed = [c for c in passed_sim if c["rel"] >= p["rel_min"]]
        for c in passed_sim:
            if c["rel"] < p["rel_min"]:
                c["stage"], c["reason"] = "rel", "реранкер {:.2f} ниже порога {:.2f}".format(c["rel"], p["rel_min"])
        passed.sort(key=lambda c: c["rel"], reverse=True)
    else:
        passed = passed_sim

    for n, c in enumerate(passed, 1):
        if n <= p["k_after"]:
            c["stage"], c["final"] = "kept", n
            c["reason"] = ("реранкер {:.2f}".format(c["rel"]) if c["rel"] is not None else "косинус {:.3f}".format(c["cos"]))
        else:
            c["stage"], c["reason"] = "top", "прошёл фильтры, но не вошёл в top-{}".format(p["k_after"])

    kept = sorted((c for c in cands if c["stage"] == "kept"), key=lambda c: c["final"])
    sources = []
    for c in kept:
        ch = c["_chunk"]
        sources.append({"n": c["final"], "chunk_id": ch["chunk_id"], "section": ch["section"], "chapters": ch["chapters"],
                        "score": c["cos"], "similarity": round((1 + c["cos"]) / 2, 4), "rel": c["rel"],
                        "tokens": ch["tokens"], "text": ch["text"][ch["body_start"] - ch["start"]:]})
    for c in cands:
        c.pop("_chunk")
    return {
        "question": question, "query": query, "params": p, "candidates": cands, "sources": sources,
        "counts": {"before": len(cands), "passed_sim": len(passed_sim),
                   "passed_rel": len(passed) if p["rerank"] else None, "kept": len(kept)},
        "ms": {"rewrite": rewrite_ms, "search": search_ms, "rerank": rerank_ms,
               "total": round((time.time() - t0) * 1000)},
        "rerank_cached": cached, "rerank_model": RERANK_MODEL if p["rerank"] else None,
    }
