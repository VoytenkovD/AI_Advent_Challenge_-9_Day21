# -*- coding: utf-8 -*-
"""RAG по индексу «Моби Дика»: вопрос → поиск чанков → вопрос + отрывки → LLM.

ask()       — два ответа на один вопрос параллельно: без RAG (по памяти модели) и с RAG
              (по найденным отрывкам, со ссылками [n] на источники).
run_eval()  — прогон 10 контрольных вопросов из eval/rag.json: оба режима + оценка судьи
              (верно / частично / неверно) + проверка кодом, попала ли нужная глава в отрывки.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import analysis
import llm
import rerank
import store
from indexer import Job

ROOT = Path(__file__).resolve().parent.parent
EVAL_FILE = ROOT / "eval" / "rag.json"
RESULTS_DIR = ROOT / "data" / "rag-eval"
STRATEGY = "structure"   # лучшая стратегия по итогам Дня 21
TOP_K = 5

SYSTEM_PLAIN = (
    "Ты эксперт по роману Германа Мелвилла «Моби Дик». Отвечай по-русски, кратко (2–5 предложений), "
    "по памяти. Если не знаешь или не уверен — честно так и скажи, не выдумывай подробностей."
)

SYSTEM_RAG = (
    "Ты отвечаешь на вопросы о романе Германа Мелвилла «Моби Дик» строго по приведённым отрывкам "
    "из английского оригинала. Правила:\n"
    "1. Используй только факты из отрывков, ничего не добавляй по памяти.\n"
    "2. Отвечай по-русски, кратко (2–5 предложений).\n"
    "3. После каждого факта обязательно ставь ссылку на номер отрывка в квадратных скобках, например: "
    "«Корабль вышел из гавани в холодный день [3].»\n"
    "4. Если в отрывках нет ответа, ответь: «В найденных отрывках ответа нет» — и ничего не придумывай."
)

JUDGE_PROMPT = (
    "Ты строгий экзаменатор. Сравни ответ с эталоном по существу (язык и формулировки не важны).\n"
    "верно — ответ содержит ключевые факты эталона и не противоречит ему;\n"
    "частично — есть часть ключевых фактов или существенные неточности;\n"
    "неверно — ключевого факта нет, он неправильный или выдуман.\n"
    "Если эталон требует честно сказать, что в книге этого нет, то выдуманный ответ — неверно, "
    "а честный отказ — верно.\n"
    "Верни строго JSON: {\"verdict\": \"верно|частично|неверно\", \"reason\": \"одно короткое предложение\"}"
)

VERDICTS = ("верно", "частично", "неверно")


# ═════════════════════════════ поиск и контекст ═════════════════════════════

def retrieve(question, k=TOP_K, strategy=STRATEGY):
    t0 = time.time()
    hits = analysis.rank(question, strategy, k)
    sources = []
    for n, (score, c) in enumerate(hits, 1):
        body = c["text"][c["body_start"] - c["start"]:]
        sources.append({"n": n, "chunk_id": c["chunk_id"], "section": c["section"], "chapters": c["chapters"],
                        "score": round(score, 4), "similarity": round((1 + score) / 2, 4),
                        "tokens": c["tokens"], "text": body})
    return sources, round((time.time() - t0) * 1000)


def build_context(sources):
    if not sources:  # фильтр отсёк всё: модель должна честно сказать, что ответа нет
        return "(по этому вопросу в книге не найдено ни одного подходящего отрывка)"
    return "\n\n".join("[{n}] Moby-Dick — {section}\n{text}".format(**s) for s in sources)


def rag_messages(question, sources):
    return [
        {"role": "system", "content": SYSTEM_RAG},
        {"role": "user", "content": "Отрывки:\n\n{}\n\n---\nВопрос: {}".format(build_context(sources), question)},
    ]


def plain_messages(question):
    return [{"role": "system", "content": SYSTEM_PLAIN}, {"role": "user", "content": question}]


def _cited(text, n):
    return sorted({int(x) for x in re.findall(r"\[(\d+)\]", text) if 1 <= int(x) <= n})


def answer_plain(question, model):
    return llm.chat(model, plain_messages(question))


def answer_rag(question, model, k=TOP_K, strategy=STRATEGY):
    sources, search_ms = retrieve(question, k, strategy)
    res = llm.chat(model, rag_messages(question, sources))
    res.update(sources=sources, search_ms=search_ms, cited=_cited(res["text"], len(sources)),
               prompt_chars=len(build_context(sources)))
    return res


def answer_rag2(question, model, params=None):
    """Улучшенный RAG (День 23): rewrite → top-K_before → порог косинуса → реранкер → порог → top-K_after."""
    f = rerank.funnel(question, rerank.params(params), model)
    res = llm.chat(model, rag_messages(question, f["sources"]))
    res.update(sources=f["sources"], funnel={k: f[k] for k in ("query", "params", "candidates", "counts", "ms",
                                                              "rerank_model", "rerank_cached")},
               search_ms=f["ms"]["total"], cited=_cited(res["text"], len(f["sources"])),
               prompt_chars=len(build_context(f["sources"])))
    return res


MODES = ("plain", "rag", "rag2")


def ask(question, model, k=TOP_K, strategy=STRATEGY, params=None, modes=MODES):
    """Режимы параллельно (без RAG / базовый RAG / улучшенный RAG); ошибка одного не ломает другие."""
    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return {"error": str(e)}
    jobs = {"plain": (answer_plain, question, model), "rag": (answer_rag, question, model, k, strategy),
            "rag2": (answer_rag2, question, model, params)}
    with ThreadPoolExecutor(3) as pool:
        futures = {m: pool.submit(safe, *jobs[m]) for m in modes}
        out = {"question": question, "model": model, "k": k, "strategy": strategy}
        out.update({m: f.result() for m, f in futures.items()})
        return out


# ═════════════════════════════ контрольные вопросы ═════════════════════════════

def load_questions():
    questions = json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    doc = store.get_document()
    spans = {c["num"]: doc["text"][c["start"]:c["end"]].lower() for c in doc["chapters"]} if doc else {}
    for q in questions:
        missing = [e for e in q["evidence"] if not any(e.lower() in spans.get(n, "") for n in q["chapters"])]
        q["verified"] = not missing
        q["missing"] = missing
        q["sources"] = ["Chapter {}".format(n) for n in q["chapters"]]
    return questions


def judge(question, expected, answer, judge_model):
    res = llm.chat(judge_model, [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": "Вопрос: {}\n\nЭталон: {}\n\nОтвет: {}".format(question, expected, answer)},
    ], temperature=0.0, max_tokens=200)
    m = re.search(r"\{.*\}", res["text"], re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    verdict = (data.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:  # слабая модель могла не соблюсти формат — ищем слово в тексте
        verdict = next((v for v in ("частично", "неверно", "верно") if v in res["text"].lower()), "неверно")
    return {"verdict": verdict, "reason": data.get("reason") or res["text"][:200]}


def _slug(model):
    return re.sub(r"[^\w.-]+", "_", model)


def result_path(model):
    return RESULTS_DIR / "{}.json".format(_slug(model))


def load_results(model):
    p = result_path(model)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def list_results():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(RESULTS_DIR.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"model": d["model"], "judge": d.get("judge"), "finished": d.get("finished"),
                    "totals": d.get("totals")})
    return out


def _hit(sources, chapters):
    if not chapters:
        return None
    got = {n for s in sources for n in s["chapters"]}
    return bool(got & set(chapters))


def totals(items, questions):
    t = {m: {v: 0 for v in VERDICTS} for m in MODES}
    t.update({"chapter_hit": 0, "chapter_hit2": 0, "with_source": 0, "answered": len(items),
              "questions": len(questions), "empty2": 0})
    for it in items.values():
        for mode in MODES:
            v = (it.get(mode) or {}).get("verdict")
            if v in VERDICTS:
                t[mode][v] += 1
        if it.get("chapter_hit") is not None:
            t["with_source"] += 1
            t["chapter_hit"] += int(it["chapter_hit"])
            t["chapter_hit2"] += int(bool(it.get("chapter_hit2")))
        if "rag2" in it and not (it["rag2"].get("sources") or []):
            t["empty2"] += 1
    return t


EVAL_JOB = Job()
LABELS = {"plain": "без RAG", "rag": "RAG", "rag2": "RAG+rerank"}


def _run_eval(model, judge_model, force):
    log = EVAL_JOB.add
    questions = [q for q in load_questions() if q["verified"]]
    prev = (load_results(model) or {}) if not force else {}
    same = prev.get("judge") == judge_model and prev.get("params2") == rerank.params()
    items = dict(prev.get("items", {})) if same else {}
    if prev.get("judge") == judge_model and not same:  # сменились настройки воронки — пересчитываем только rag2
        items = {k: {m: v for m, v in it.items() if m != "rag2"} for k, it in prev.get("items", {}).items()}
    log("eval", "модель {}, судья {}, вопросов {}, воронка: {}".format(model, judge_model, len(questions),
                                                                     json.dumps(rerank.params(), ensure_ascii=False)))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def save():
        result_path(model).write_text(json.dumps({
            "model": model, "judge": judge_model, "strategy": STRATEGY, "k": TOP_K, "params2": rerank.params(),
            "finished": time.time(), "items": items, "totals": totals(items, questions),
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    for i, q in enumerate(questions, 1):
        item = dict(items.get(q["id"]) or {"q": q["q"]})
        todo = [m for m in MODES if m not in item]
        if not todo:
            log("skip", "{}/{} {}: уже оценён".format(i, len(questions), q["id"]), progress=(i, len(questions)))
            continue
        r = ask(q["q"], model, modes=todo)
        for mode in todo:
            a = r[mode]
            if "error" in a:
                item[mode] = {"error": a["error"], "verdict": None}
                log("error", "{} {}: {}".format(q["id"], mode, a["error"]))
                continue
            j = judge(q["q"], q["expected"], a["text"], judge_model)
            item[mode] = {"text": a["text"], "latency_ms": a["latency_ms"], "usage": a["usage"], **j}
            if "sources" in a:
                item[mode]["sources"] = [{k: s.get(k) for k in ("n", "chunk_id", "section", "chapters", "similarity", "rel")}
                                         for s in a["sources"]]
                item[mode]["cited"] = a["cited"]
            if mode == "rag":
                item["chapter_hit"] = _hit(a["sources"], q["chapters"])
            if mode == "rag2":
                item["chapter_hit2"] = _hit(a["sources"], q["chapters"])
                item["rag2"]["query"] = a["funnel"]["query"]
                item["rag2"]["counts"] = a["funnel"]["counts"]
        items[q["id"]] = item
        save()
        log("judge", "{}/{} {}: {}".format(i, len(questions), q["id"], ", ".join(
            "{} — {}".format(LABELS[m], (item.get(m) or {}).get("verdict")) for m in MODES)), progress=(i, len(questions)))
    save()
    t = totals(items, questions)
    log("done", "верно: без RAG {}, RAG {}, RAG+rerank {} из {}; глава в отрывках {} → {} из {}".format(
        t["plain"]["верно"], t["rag"]["верно"], t["rag2"]["верно"], len(questions),
        t["chapter_hit"], t["chapter_hit2"], t["with_source"]))
    return t


def start_eval(model, judge_model=None, force=False):
    judge_model = judge_model or model
    llm.split_model(model)
    llm.split_model(judge_model)
    with EVAL_JOB.lock:
        if EVAL_JOB.state == "running":
            return False
        EVAL_JOB.state, EVAL_JOB.log, EVAL_JOB.progress, EVAL_JOB.result = "running", [], None, None
        EVAL_JOB.started, EVAL_JOB.finished = time.time(), None

    def worker():
        try:
            res = _run_eval(model, judge_model, force)
            with EVAL_JOB.lock:
                EVAL_JOB.state, EVAL_JOB.result = "done", {"model": model, "totals": res}
        except Exception as e:
            EVAL_JOB.add("error", str(e))
            with EVAL_JOB.lock:
                EVAL_JOB.state = "error"
        finally:
            EVAL_JOB.finished = time.time()

    threading.Thread(target=worker, name="rag-eval", daemon=True).start()
    return True
