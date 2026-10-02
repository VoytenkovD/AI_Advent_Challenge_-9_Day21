# -*- coding: utf-8 -*-
"""Подбор порогов и сравнение режимов второго этапа (День 23).

Для каждого вопроса и каждого режима запроса (raw / en / hyde) берутся top-20 кандидатов
векторного поиска, а реранкер оценивает каждого (оценки кэшируются). По этим данным —
без новых обращений к моделям — считаются метрики любых настроек воронки:

  на вопросах с ответом (29, глава известна):
    hit@1     — первый отрывок контекста из нужной главы
    hit@K     — нужная глава есть среди отрывков, ушедших в модель
    precision — доля отрывков контекста из нужной главы (сколько «мусора» видит модель)
    пустой    — доля вопросов, где фильтр отсёк всё (плохо: модель останется без ответа)
  на вопросах без ответа в индексе (5):
    пустой    — доля вопросов, где фильтр отсёк всё (хорошо: модель честно скажет «нет»)
    отрывков  — сколько нерелевантных отрывков всё же ушло в модель

Отчёт с сырыми данными сохраняется в data/rerank-report.json; вкладка «Реранкинг»
пересчитывает метрики в браузере при движении ползунков.
"""
import json
import time
from pathlib import Path

import analysis
import rag
import rerank
from indexer import Job

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "rerank-report.json"
K_POOL = 20

JOB = Job()


def question_set():
    qs = []
    for q in analysis.load_questions():
        if q.get("verified", True):
            qs.append({"id": "idx-" + q["id"], "q": q["q"], "chapters": q["chapters"], "lang": q["lang"], "set": "index"})
    for q in rag.load_questions():
        if q["chapters"] and q["verified"]:
            qs.append({"id": "rag-" + q["id"], "q": q["q"], "chapters": q["chapters"], "lang": "ru", "set": "rag"})
    for q in json.loads((ROOT / "eval" / "noanswer.json").read_text(encoding="utf-8")):
        qs.append({"id": "no-" + q["id"], "q": q["q"], "chapters": [], "lang": "ru", "set": "noanswer", "why": q["why"]})
    return qs


# ═════════════════════════════ метрики по сырым данным ═════════════════════════════

def apply(cands, cfg):
    """Повторяет воронку rerank.funnel на сохранённых кандидатах. cands — по убыванию косинуса."""
    pool = cands[:cfg["k_before"]]
    pool = [c for c in pool if c["cos"] >= cfg["sim_min"]]
    if cfg["rerank"]:
        pool = [c for c in pool if c["rel"] >= cfg["rel_min"]]
        pool = sorted(pool, key=lambda c: c["rel"], reverse=True)
    return pool[:cfg["k_after"]]


def metrics(data, cfg):
    ans = [d for d in data if d["chapters"]]
    no = [d for d in data if not d["chapters"]]
    m = {"hit1": 0, "hitk": 0, "prec": 0.0, "kept": 0, "empty": 0}
    for d in ans:
        kept = apply(d["modes"][cfg["query"]], cfg)
        good = [c for c in kept if set(c["chapters"]) & set(d["chapters"])]
        m["hit1"] += bool(kept) and bool(set(kept[0]["chapters"]) & set(d["chapters"]))
        m["hitk"] += bool(good)
        m["prec"] += len(good) / len(kept) if kept else 0
        m["kept"] += len(kept)
        m["empty"] += not kept
    n = len(ans) or 1
    out = {k: round(v / n, 3) for k, v in m.items()}
    nk = [len(apply(d["modes"][cfg["query"]], cfg)) for d in no]
    out["no_empty"] = round(sum(1 for x in nk if x == 0) / (len(nk) or 1), 3)
    out["no_kept"] = round(sum(nk) / (len(nk) or 1), 2)
    return out


CONFIGS = [
    ("base", "Базовый (День 22): вопрос как есть, top-5", {"query": "raw", "rerank": False}),
    ("filter", "Фильтр + реранкер: вопрос как есть", {"query": "raw", "rerank": True}),
    ("rw-en", "Rewrite (перевод на английский), top-5", {"query": "en", "rerank": False}),
    ("rw-hyde", "Rewrite (HyDE), top-5", {"query": "hyde", "rerank": False}),
    ("en-filter", "Перевод + фильтр + реранкер", {"query": "en", "rerank": True}),
    ("hyde-filter", "HyDE + фильтр + реранкер", {"query": "hyde", "rerank": True}),
]


def summarize(data, sim_min, rel_min, k_after=5):
    rows = []
    for cid, title, c in CONFIGS:
        cfg = {"query": c["query"], "rerank": c["rerank"], "k_after": k_after,
               "k_before": K_POOL if c["rerank"] else k_after,
               "sim_min": sim_min if c["rerank"] else -1.0, "rel_min": rel_min if c["rerank"] else 0.0}
        rows.append({"id": cid, "title": title, "cfg": cfg, **metrics(data, cfg)})
    # отдельные вклады: только порог косинуса и только реранкер, на вопросе как есть
    for cid, title, cfg in [
        ("sim-only", "Только порог косинуса (без реранкера)",
         {"query": "raw", "rerank": False, "k_before": K_POOL, "sim_min": sim_min, "rel_min": 0.0, "k_after": k_after}),
        ("rerank-only", "Только реранкер (без порога косинуса)",
         {"query": "raw", "rerank": True, "k_before": K_POOL, "sim_min": -1.0, "rel_min": rel_min, "k_after": k_after})]:
        rows.insert(1, {"id": cid, "title": title, "cfg": cfg, **metrics(data, cfg)})
    return rows


def finalize(report):
    """Рекомендация, сводка и кривые по сырым данным отчёта (без обращений к моделям)."""
    data = report["questions"]
    rec = recommend(data)
    report["recommended"] = rec
    report["summary"] = summarize(data, rec["sim_min"], rec["rel_min"])
    report["sweeps"] = {m: sweep(data, m) for m in ("raw", "en", "hyde")}
    return report


def sweep(data, query):
    out = []
    for i in range(0, 20):
        t = round(i * 0.05, 2)
        cfg = {"query": query, "rerank": True, "k_before": K_POOL, "sim_min": -1.0, "rel_min": t, "k_after": 5}
        out.append({"rel_min": t, **metrics(data, cfg)})
    return out


SIM_GRID = [-1.0] + [round(0.30 + i * 0.01, 2) for i in range(21)]  # «без порога» и 0.30…0.50


def recommend(data):
    """Сетка по режиму запроса, порогу косинуса и порогу реранкера. Цель: hit@1 + hit@K + точность
    контекста + ½·доля честных отказов на вопросах без ответа; при этом hit@K хуже лучшего
    не больше чем на один вопрос и ни один вопрос с ответом не остаётся с пустым контекстом."""
    best = None
    tol = 1.0 / max(1, sum(1 for d in data if d["chapters"])) + 1e-6
    for query in ("raw", "en", "hyde"):
        grid = []
        for sim in SIM_GRID:
            for i in range(20):
                cfg = {"query": query, "rerank": True, "k_before": K_POOL, "sim_min": sim, "rel_min": round(i * 0.05, 2), "k_after": 5}
                grid.append((cfg, metrics(data, cfg)))
        top_hit = max(m["hitk"] for _, m in grid)
        for cfg, m in grid:
            if m["hitk"] < top_hit - tol or m["empty"] > 0:
                continue
            score = m["hit1"] + m["hitk"] + m["prec"] + 0.5 * m["no_empty"]
            if not best or score > best[0] + 1e-9:
                best = (score, cfg, m)
    cfg = best[1]
    return {"query": cfg["query"], "sim_min": cfg["sim_min"], "rel_min": cfg["rel_min"], "metrics": best[2]}


# ═════════════════════════════ прогон ═════════════════════════════

def _run(model):
    log = JOB.add
    qs = question_set()
    log("experiment", "вопросов {} (с ответом {}, без ответа {}), кандидатов на вопрос {}, реранкер {}, rewrite — {}".format(
        len(qs), sum(1 for q in qs if q["chapters"]), sum(1 for q in qs if not q["chapters"]), K_POOL, rerank.RERANK_MODEL, model))
    # Три фазы, а не «вопрос за вопросом»: LLM, bge-m3 и реранкер вместе не помещаются в 4 ГБ видеопамяти,
    # и Ollama перезагружала бы модели на каждом шаге. По фазам в памяти нужна одна модель за раз.
    data, t0 = [], time.time()
    total = len(qs) * 2
    for i, q in enumerate(qs, 1):
        for mode in ("en", "hyde"):
            rerank.rewrite(q["q"], mode, model)
        log("rewrite", "{}/{} {}: запросы en и hyde готовы".format(i, len(qs), q["id"]), progress=(i, len(qs)))
    log("rewrite", "переписывание готово за {} с".format(round(time.time() - t0)))

    t1 = time.time()
    for q in qs:
        entry = {k: q[k] for k in ("id", "q", "chapters", "set", "lang")}
        entry["queries"], entry["modes"], entry["_hits"] = {}, {}, {}
        for mode in ("raw", "en", "hyde"):
            query, _ = rerank.rewrite(q["q"], mode, model)
            entry["queries"][mode] = query
            entry["_hits"][mode] = analysis.rank(query, rerank.STRATEGY, K_POOL)
        data.append(entry)
    log("search", "векторный поиск: {} вопросов × 3 запроса за {} с".format(len(qs), round(time.time() - t1)))

    t2 = time.time()
    for i, entry in enumerate(data, 1):
        pool = {}
        for hits in entry["_hits"].values():  # один и тот же чанк оценивается один раз на вопрос
            for _, c in hits:
                pool[c["chunk_id"]] = c
        rels = dict(zip(pool, rerank.score(entry["q"], list(pool.values()))[0]))
        for mode, hits in entry.pop("_hits").items():
            entry["modes"][mode] = [{"chunk_id": c["chunk_id"], "section": c["section"], "chapters": c["chapters"],
                                     "cos": round(cos, 4), "rel": rels[c["chunk_id"]]} for cos, c in hits]
        log("rerank", "{}/{} {}: оценено {} уникальных кандидатов".format(i, len(data), entry["id"], len(pool)),
            progress=(i, len(data)))
    log("rerank", "реранкинг готов за {} с".format(round(time.time() - t2)))
    report = finalize({"finished": time.time(), "seconds": round(time.time() - t0), "rewrite_model": model,
                       "rerank_model": rerank.RERANK_MODEL, "k_pool": K_POOL, "questions": data})
    rec = report["recommended"]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    log("done", "готово за {} с; рекомендовано: запрос {}, порог косинуса {}, порог реранкера {}".format(
        report["seconds"], rec["query"], rec["sim_min"], rec["rel_min"]))
    return rec


def start(model):
    import threading
    with JOB.lock:
        if JOB.state == "running":
            return False
        JOB.state, JOB.log, JOB.progress, JOB.result = "running", [], None, None
        JOB.started, JOB.finished = time.time(), None

    def worker():
        try:
            res = _run(model)
            with JOB.lock:
                JOB.state, JOB.result = "done", res
        except Exception as e:
            JOB.add("error", str(e))
            with JOB.lock:
                JOB.state = "error"
        finally:
            JOB.finished = time.time()

    threading.Thread(target=worker, name="experiment", daemon=True).start()
    return True


def load_report():
    return json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.is_file() else None


if __name__ == "__main__":
    import sys
    if "--refinalize" in sys.argv:  # пересчитать рекомендацию и сводку по уже собранным данным
        r = finalize(load_report())
        REPORT.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(r["recommended"], ensure_ascii=False))
    else:
        _run(sys.argv[1] if len(sys.argv) > 1 else "ollama:qwen2.5:3b")
