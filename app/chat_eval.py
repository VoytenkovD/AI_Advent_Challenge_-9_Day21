# -*- coding: utf-8 -*-
"""Проверка мини-чата на длинных сценариях (День 25).

Каждый сценарий (eval/chat_scenarios.json) прогоняется в новом чате сообщение за сообщением. На каждом ходе:
  • тип реплики распознан правильно (служебная / вопрос / итог);
  • ЦЕЛЬ НЕ ПОТЕРЯНА — с момента постановки в памяти задачи остаётся цель с ключевыми словами сценария;
  • ограничения на месте — диапазон глав и ключевые ограничения сохраняются до конца;
  • поиск соблюдает диапазон глав — все отрывки контекста внутри диапазона;
  • ИСТОЧНИКИ ВСЕГДА — на вопрос ассистент отвечает с подтверждёнными источниками либо явно говорит «не знаю»
    и перечисляет проверенные отрывки; ответа без источников быть не должно;
  • нужная глава нашлась (для вопросов с известной главой);
  • итог ссылается на цель и содержит источники.
"""
import json
import re
import threading
import time
from pathlib import Path

import chat
from indexer import Job

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "eval" / "chat_scenarios.json"
RESULTS_DIR = ROOT / "data" / "chat-eval"
JOB = Job()


def load_scenarios():
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def _has(text, kw):
    return kw.lower()[:5] in (text or "").lower()  # корень слова: «Квикег» ~ «Квикега»


def check_turn(sc, turn, r, goal_set):
    st, meta = r["state"], r["meta"]
    goal = (st.get("goal") or {}).get("text") or ""
    c = {}
    c["kind"] = meta["kind"] == turn["kind"]
    if goal_set:
        c["goal_kept"] = all(_has(goal, k) for k in sc["goal_keywords"])
    if sc.get("chapter_range") and r.get("_range_set"):
        c["range_kept"] = st.get("chapter_range") == sc["chapter_range"]
        if meta.get("context_chapters"):
            lo, hi = sc["chapter_range"]
            c["range_respected"] = all(lo <= n <= hi for n in meta["context_chapters"])
    if turn["kind"] == "question":
        answered = meta["status"] in ("answered", "quotes")
        c["sources_always"] = (answered and bool(meta["sources"])) or (meta["status"] == "unknown" and bool(meta.get("sources_note")))
        c["answered_with_sources"] = answered and bool(meta["sources"])
        if turn.get("chapters"):
            c["chapter_found"] = bool(set(turn["chapters"]) & set(meta.get("context_chapters") or []))
        if turn.get("expect_unknown"):
            c["honest_unknown"] = meta["status"] == "unknown"
    if turn["kind"] == "summary":
        c["summary_sources"] = bool(meta["sources"])
        c["summary_on_goal"] = any(_has(r["reply"], k) for k in sc["goal_keywords"])
    return c


def run_scenario(sc, model, log):
    cid = chat.create_chat(sc["title"])["id"]
    turns, goal_set, range_set = [], False, False
    for i, t in enumerate(sc["turns"], 1):
        r = chat.send(cid, t["msg"], model)
        goal_set = goal_set or bool((r["state"].get("goal") or {}).get("text"))
        range_set = range_set or bool(r["state"].get("chapter_range"))
        r["_range_set"] = range_set
        checks = check_turn(sc, t, r, goal_set)
        m = r["meta"]
        turns.append({"n": i, "msg": t["msg"], "expected_kind": t["kind"], "reply": r["reply"], "kind": m["kind"],
                      "status": m["status"], "by": m.get("by"), "search_query": m.get("search_query"),
                      "sources": [{"section": s["section"], "chunk_id": s["chunk_id"]} for s in m.get("sources") or []],
                      "sources_note": m.get("sources_note"), "context_chapters": m.get("context_chapters"),
                      "state_changes": m.get("state_changes"), "goal": (r["state"].get("goal") or {}).get("text"),
                      "chapter_range": r["state"].get("chapter_range"), "latency_ms": m["latency_ms"], "checks": checks})
        fails = [k for k, v in checks.items() if not v]
        log("turn", "{} {}/{} [{}→{}] {}{}".format(sc["id"], i, len(sc["turns"]), t["kind"], m["status"],
                                                   "все проверки ✓" if not fails else "✗ " + ", ".join(fails),
                                                   " · {:.0f} с".format(m["latency_ms"] / 1000)), progress=(i, len(sc["turns"])))
    final = chat.get_chat(cid)["state"]
    return {"id": sc["id"], "title": sc["title"], "chat_id": cid, "turns": turns, "final_state": final,
            "totals": totals(turns, sc, final)}


def totals(turns, sc, final):
    def rate(key):
        vals = [t["checks"][key] for t in turns if key in t["checks"]]
        return {"ok": sum(vals), "n": len(vals)}
    t = {k: rate(k) for k in ("kind", "goal_kept", "range_kept", "range_respected", "sources_always",
                              "answered_with_sources", "chapter_found", "honest_unknown", "summary_sources", "summary_on_goal")}
    t["final_terms"] = [x["term"] for x in final["terms"]]
    t["terms_kept"] = all(any(_has(x, k) for x in t["final_terms"]) for k in sc.get("terms", []))
    t["constraints_kept"] = all(any(_has(x["text"], k) for x in final["constraints"]) for k in sc.get("constraint_keywords", []))
    t["messages"] = len(turns)
    return t


def _run(model):
    log = JOB.add
    out = {"model": model, "started": time.time(), "scenarios": []}
    for sc in load_scenarios():
        log("scenario", "«{}»: {} сообщений".format(sc["title"], len(sc["turns"])))
        out["scenarios"].append(run_scenario(sc, model, log))
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "{}.json".format(re.sub(r"[^\w.-]+", "_", model))).write_text(
            json.dumps(dict(out, finished=time.time()), ensure_ascii=False, indent=1), encoding="utf-8")
    for s in out["scenarios"]:
        t = s["totals"]
        log("done", "«{}»: цель сохранена {}/{}, источники всегда {}/{}, ответов с источниками {}/{}, диапазон соблюдён {}/{}".format(
            s["title"], t["goal_kept"]["ok"], t["goal_kept"]["n"], t["sources_always"]["ok"], t["sources_always"]["n"],
            t["answered_with_sources"]["ok"], t["answered_with_sources"]["n"], t["range_respected"]["ok"], t["range_respected"]["n"]))
    return out


def load_results(model):
    p = RESULTS_DIR / "{}.json".format(re.sub(r"[^\w.-]+", "_", model))
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def start(model):
    with JOB.lock:
        if JOB.state == "running":
            return False
        JOB.state, JOB.log, JOB.progress, JOB.result = "running", [], None, None
        JOB.started, JOB.finished = time.time(), None

    def worker():
        try:
            _run(model)
            with JOB.lock:
                JOB.state = "done"
        except Exception as e:
            JOB.add("error", str(e))
            with JOB.lock:
                JOB.state = "error"
        finally:
            JOB.finished = time.time()

    threading.Thread(target=worker, name="chat-eval", daemon=True).start()
    return True


if __name__ == "__main__":
    import sys
    _run(sys.argv[1] if len(sys.argv) > 1 else "ollama:qwen2.5:3b")
