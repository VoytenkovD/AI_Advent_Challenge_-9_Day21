# -*- coding: utf-8 -*-
"""День 28: полностью локальный RAG против облачной генерации.

Во всех режимах ОДИНАКОВЫЙ локальный поиск: индекс Недели 6 (SQLite, bge-m3 в Ollama) → воронка Дня 23
(косинус + реранкер Qwen3-Reranker в Ollama) → порог «не знаю» → ответ JSON с цитатами → проверка цитат кодом →
проверка смысла (Дни 24). Меняется только модель, которая пишет ответ и проверяет смысл:

  ollama:qwen2.5:3b, ollama:llama3.2:3b  — всё на этом компьютере, 100% локально;
  ai-public:openai/gpt-4.1               — генерация в облаке (поиск всё равно локальный).

Верность ответа по эталону для ВСЕХ режимов оценивает один и тот же сильный судья (JUDGE) — это инструмент
измерения, а не часть RAG-системы. 10 контрольных вопросов × RUNS прогонов × модели.
"""
import json
import re
import statistics
import threading
import time
from pathlib import Path

import grounded
import llm
import rag
from indexer import Job

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "data" / "local-vs-cloud.json"
MODELS = ["ollama:qwen2.5:3b", "ollama:llama3.2:3b", "ai-public:openai/gpt-4.1"]
JUDGE = "ai-public:openai/gpt-4.1"
RUNS = 3
JOB = Job()


def is_local(model):
    return model.startswith("ollama:")


def _words(t):
    return set(w.lower() for w in re.findall(r"\w{3,}", t or ""))


def _jaccard(a, b):
    a, b = _words(a), _words(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def run_one(q, model, gen="v1"):   # День 28 сравнивает модели на одном промпте v1
    t0 = time.time()
    try:
        a = grounded.answer(q["q"], model, None, grounded.GATE_MIN, judge_model=model, gen=gen)
    except Exception as e:
        return {"status": "error", "error": "{}: {}".format(type(e).__name__, str(e)[:200]),
                "latency_ms": round((time.time() - t0) * 1000)}
    t = a.get("timing", {})
    gen_tok = (a.get("usage") or {}).get("completion", 0)
    r = {"status": a["status"], "by": a.get("by"), "answer": a["answer"], "unverified": a.get("unverified_answer"),
         "sources": [s["section"] for s in a["sources"]], "quotes": len(a["quotes"]), "rejected": len(a["rejected"]),
         "quote_texts": [x["quote"][:200] for x in a["quotes"]], "json_ok": a.get("json_ok"),
         "meaning": (a.get("judge_support") or {}).get("verdict"), "latency_ms": a["latency_ms"],
         "retrieval_ms": t.get("retrieval_ms", 0), "generate_ms": t.get("generate_ms", 0),
         "gen_tokens": gen_tok, "tok_s": round(gen_tok / (t["generate_ms"] / 1000), 1) if t.get("generate_ms") else None,
         "prompt_tokens": (a.get("usage") or {}).get("prompt", 0), "llm_stats": a.get("llm_stats"), "gen": a.get("gen")}
    # верность по эталону: «не знаю» оценивает код, остальное — общий сильный судья
    if a["status"] == "unknown":
        r["verdict"] = "верно" if not q["chapters"] else "неверно"
        r["reason"] = "честный отказ на ловушке" if not q["chapters"] else "«не знаю» на вопросе с ответом"
    else:
        text = a["answer"] if a["status"] == "answered" else "Цитаты из книги: " + " / ".join(r["quote_texts"])
        try:
            j = rag.judge(q["q"], q["expected"], text, JUDGE)
            r["verdict"], r["reason"] = j["verdict"], j["reason"]
        except Exception as e:
            r["verdict"], r["reason"] = None, "судья недоступен: {}".format(e)
    return r


def _pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))] if vals else None


def summarize(model, by_q, questions):
    runs = [r for q in questions for r in by_q.get(q["id"], [])]
    ok = [r for r in runs if r["status"] != "error"]
    n = len(runs) or 1
    verdicts = [r.get("verdict") for r in ok]
    # большинство по прогонам на каждый вопрос
    majority = {}
    for q in questions:
        vs = [r.get("verdict") for r in by_q.get(q["id"], []) if r.get("verdict")]
        majority[q["id"]] = max(set(vs), key=vs.count) if vs else None
    consistent = sum(1 for q in questions if len({r.get("verdict") for r in by_q.get(q["id"], [])}) == 1
                     and len(by_q.get(q["id"], [])) >= 2)
    status_consistent = sum(1 for q in questions if len({r["status"] for r in by_q.get(q["id"], [])}) == 1
                            and len(by_q.get(q["id"], [])) >= 2)
    sims = []
    for q in questions:
        ans = [r["answer"] for r in by_q.get(q["id"], []) if r["status"] in ("answered", "quotes")]
        for i in range(len(ans)):
            for k in range(i + 1, len(ans)):
                sims.append(_jaccard(ans[i], ans[k]))
    lat = [r["latency_ms"] for r in ok]
    gen = [r["generate_ms"] for r in ok if r.get("generate_ms")]
    tps = [r["tok_s"] for r in ok if r.get("tok_s")]
    answered = [r for r in ok if r["status"] in ("answered", "quotes")]
    trap = [r for q in questions if not q["chapters"] for r in by_q.get(q["id"], [])]
    return {
        "model": model, "local": is_local(model), "runs": len(runs),
        "quality": {
            "correct": verdicts.count("верно") / n, "partial": verdicts.count("частично") / n,
            "wrong": verdicts.count("неверно") / n,
            "majority_correct": sum(1 for v in majority.values() if v == "верно"),
            "majority_partial": sum(1 for v in majority.values() if v == "частично"),
            "answered": len(answered) / n,
            "with_sources": sum(1 for r in answered if r["sources"]) / (len(answered) or 1),
            "quotes_verified": sum(r.get("quotes", 0) for r in ok) / (sum(r.get("quotes", 0) + r.get("rejected", 0) for r in ok) or 1),
            "trap_ok": sum(1 for r in trap if r["status"] == "unknown") / (len(trap) or 1),
        },
        "speed": {
            "median_ms": statistics.median(lat) if lat else None, "p90_ms": _pct(lat, 0.9), "max_ms": max(lat) if lat else None,
            "median_generate_ms": statistics.median(gen) if gen else None,
            "median_retrieval_ms": statistics.median([r["retrieval_ms"] for r in ok]) if ok else None,
            "median_tok_s": statistics.median(tps) if tps else None,
        },
        "stability": {
            "verdict_consistent": consistent, "status_consistent": status_consistent, "questions": len(questions),
            "answer_similarity": round(statistics.mean(sims), 3) if sims else None,
            "errors": len(runs) - len(ok), "json_ok": sum(1 for r in ok if r.get("json_ok") is not False) / (len(ok) or 1),
            "latency_cv": round(statistics.pstdev(lat) / statistics.mean(lat), 2) if len(lat) > 1 else None,
        },
        "majority": majority,
    }


def _save(data):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load():
    return json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else None


def _run(models, runs, fresh):
    log = JOB.add
    questions = [q for q in rag.load_questions() if q["verified"]]
    data = (None if fresh else load()) or {"questions": [{k: q[k] for k in ("id", "q", "expected", "chapters", "sources")}
                                                        for q in questions], "results": {}, "judge": JUDGE}
    data.update(started=time.time(), runs=runs, judge=JUDGE)
    total = len(models) * runs * len(questions)
    done = 0
    for model in models:          # модель — внешний цикл: меньше перезагрузок моделей в 4 ГБ видеопамяти
        by_q = data["results"].setdefault(model, {"runs": {}})["runs"]
        log("model", "{} — {}".format(model, "локально (Ollama)" if is_local(model) else "облако, поиск локальный"))
        for run in range(1, runs + 1):
            for q in questions:
                done += 1
                have = by_q.setdefault(q["id"], [])
                if len(have) >= run:   # продолжение прерванного прогона
                    continue
                r = run_one(q, model)
                r["run"] = run
                have.append(r)
                data["results"][model]["summary"] = summarize(model, by_q, questions)
                data["updated"] = time.time()
                _save(data)
                log("answer", "{} · прогон {} · {}: {} → {} · {:.1f} с".format(
                    model, run, q["id"], r["status"], r.get("verdict") or r.get("error", ""), r["latency_ms"] / 1000),
                    progress=(done, total))
        s = data["results"][model]["summary"]
        log("summary", "{}: верно {:.0%} (по большинству {}/{}), медиана {:.1f} с, стабильность вердикта {}/{}".format(
            model, s["quality"]["correct"], s["quality"]["majority_correct"], len(questions),
            (s["speed"]["median_ms"] or 0) / 1000, s["stability"]["verdict_consistent"], len(questions)))
    data["finished"] = time.time()
    _save(data)
    log("done", "готово: {} ответов".format(total))
    return data


def start(models=None, runs=RUNS, fresh=False):
    models = [m for m in (models or MODELS) if m]
    for m in models:
        llm.split_model(m)
    with JOB.lock:
        if JOB.state == "running":
            return False
        JOB.state, JOB.log, JOB.progress, JOB.result = "running", [], None, None
        JOB.started, JOB.finished = time.time(), None

    def worker():
        try:
            _run(models, int(runs), fresh)
            with JOB.lock:
                JOB.state = "done"
        except Exception as e:
            JOB.add("error", str(e))
            with JOB.lock:
                JOB.state = "error"
        finally:
            JOB.finished = time.time()

    threading.Thread(target=worker, name="local-vs-cloud", daemon=True).start()
    return True


if __name__ == "__main__":
    import sys
    ms = [a for a in sys.argv[1:] if ":" in a] or MODELS
    _run(ms, RUNS, "--fresh" in sys.argv)
