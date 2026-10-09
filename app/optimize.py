# -*- coding: utf-8 -*-
"""День 29: оптимизация локальной LLM под конкретную задачу — ответы с цитатами по «Моби Дику».

Поиск (индекс, bge-m3, реранкер, порог «не знаю») во всех профилях одинаковый — меняется только генерация:

  base      qwen2.5:3b Q4_K_M · промпт Дня 24 (v1) · temperature 0.1 · max 700 токенов · окно 4096   ← «до»
  params    то же, но temperature 0 · max 400 · окно 3072
  prompt    + шаблон v2 под этот случай (сначала дословные цитаты, потом точный перевод, глоссарий, пример)
  q3/q5/q8  тот же профиль на других квантованиях: Q3_K_M, Q5_K_M, Q8_0
  …-fa      профиль после включения в Ollama flash attention + KV-кэша q8_0 (переменные среды Ollama)

Качество по эталону оценивает один судья (gpt-4.1, как в Дне 28), «не знаю» — код. Скорость и ресурсы —
по данным Ollama (время загрузки модели, чтения промпта, генерации) и по /api/ps + nvidia-smi (память).
Результаты — data/optimize.json. Запуск: python app/optimize.py [профили…] [--runs N] [--only id,id] [--fresh]
"""
import json
import os
import statistics
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import embed
import grounded
import llm
import local_vs_cloud as lvc
import rag
from indexer import Job

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(os.getenv("OPTIMIZE_RESULTS") or ROOT / "data" / "optimize.json")
RUNS = 3
JOB = Job()

Q4, Q3, Q5, Q8 = ("ollama:qwen2.5:3b", "ollama:qwen2.5:3b-instruct-q3_K_M",
                  "ollama:qwen2.5:3b-instruct-q5_K_M", "ollama:qwen2.5:3b-instruct-q8_0")
PROFILES = {
    "base":   {"label": "до: Q4_K_M · промпт v1 · t=0.1 · 700 ток · окно 4096", "model": Q4, "gen": "v1"},
    "params": {"label": "параметры: t=0 · 400 ток · окно 3072", "model": Q4, "gen": "v1-tuned"},
    "prompt": {"label": "+ промпт v2 под задачу", "model": Q4, "gen": "v2"},
    "q3":     {"label": "v2 · Q3_K_M", "model": Q3, "gen": "v2"},
    "q5":     {"label": "v2 · Q5_K_M", "model": Q5, "gen": "v2"},
    "q8":     {"label": "v2 · Q8_0", "model": Q8, "gen": "v2"},
}


def _get(path, timeout=10):
    return json.loads(urllib.request.urlopen(embed.OLLAMA_URL + path, timeout=timeout).read().decode("utf-8"))


def gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def load_model(model, num_ctx):
    """Загружает модель с тем же окном, что и при ответах (пустой запрос к /api/generate), — чтобы замерить память.
    Во время ответа модель могла быть уже выгружена реранкером: в 4 ГБ видеопамяти модели сменяют друг друга."""
    body = {"model": model.split(":", 1)[1], "options": {"num_ctx": num_ctx}}
    req = urllib.request.Request(embed.OLLAMA_URL + "/api/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=300).read()
    except Exception:
        pass


def resources(model):
    """Что сейчас загружено в Ollama: размер модели, доля в видеопамяти, окно; и общая занятость GPU."""
    name = model.split(":", 1)[1]
    try:
        ps = _get("/api/ps").get("models", [])
    except Exception:
        ps = []
    me = next((m for m in ps if m["name"] == name or m["name"] == name + ":latest"), None)
    return {"gpu_mb": gpu_used_mb(), "loaded": [m["name"] for m in ps],
            "size_mb": round(me["size"] / 2**20) if me else None,
            "vram_mb": round(me["size_vram"] / 2**20) if me else None,
            "ctx": me.get("context_length") if me else None}


def model_info(model):
    name = model.split(":", 1)[1]
    try:
        tags = {m["name"]: m for m in _get("/api/tags")["models"]}
        t = tags.get(name) or tags.get(name + ":latest") or {}
        d = t.get("details") or {}
        return {"quant": d.get("quantization_level"), "params": d.get("parameter_size"),
                "file_mb": round(t["size"] / 2**20) if t.get("size") else None}
    except Exception:
        return {}


def summarize(pid, by_q, questions, memory=None):
    s = lvc.summarize(PROFILES.get(pid.removesuffix("-fa"), {}).get("model", pid), by_q, questions)
    runs = [r for q in questions for r in by_q.get(q["id"], []) if r["status"] != "error"]
    st = [r["llm_stats"] for r in runs if r.get("llm_stats")]
    res = [r["res"] for r in runs if r.get("res")]
    med = lambda xs: statistics.median(xs) if xs else None
    s["speed"].update(
        median_load_ms=med([x["load_ms"] for x in st]),
        median_prompt_eval_ms=med([x["prompt_ms"] for x in st]),
        median_eval_ms=med([x["eval_ms"] for x in st]),
        eval_tok_s=med([r["gen_tokens"] / (r["llm_stats"]["eval_ms"] / 1000) for r in runs
                        if r.get("llm_stats") and r["llm_stats"]["eval_ms"]]),
        median_prompt_tokens=med([r["prompt_tokens"] for r in runs if r.get("prompt_tokens")]),
        median_gen_tokens=med([r["gen_tokens"] for r in runs if r.get("gen_tokens")]),
        truncated=sum(1 for x in st if x.get("done_reason") == "length"),
    )
    mem = memory or {}
    s["resources"] = {
        "model_mb": mem.get("size_mb") or max((x["size_mb"] for x in res if x.get("size_mb")), default=None),
        "model_vram_mb": mem.get("vram_mb") or max((x["vram_mb"] for x in res if x.get("vram_mb")), default=None),
        "gpu_peak_mb": max([x["gpu_mb"] for x in res if x.get("gpu_mb")] + [mem.get("gpu_mb") or 0]) or None,
        "ctx": mem.get("ctx") or next((x["ctx"] for x in res if x.get("ctx")), None),
    }
    return s


def _save(data):
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load():
    return json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else None


def ollama_env():
    """Настройки сервера Ollama, которые проверяем (переменные среды пользователя Windows)."""
    keys = ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE")
    out = {k: os.environ.get(k) for k in keys}
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                for name in keys:
                    try:
                        out[name] = winreg.QueryValueEx(k, name)[0]
                    except OSError:
                        pass
        except OSError:
            pass
    return out


def _run(pids, runs, fresh, only=None):
    log = JOB.add
    questions = [q for q in rag.load_questions() if q["verified"] and (not only or q["id"] in only)]
    data = (None if fresh else load()) or {"questions": [], "results": {}}
    data["questions"] = [{k: q[k] for k in ("id", "q", "expected", "chapters")} for q in rag.load_questions() if q["verified"]]
    data.update(judge=lvc.JUDGE, runs=runs, updated=time.time())
    total, done = len(pids) * runs * len(questions), 0
    for pid in pids:
        prof = PROFILES[pid.removesuffix("-fa")]
        model, gen = prof["model"], prof["gen"]
        env = ollama_env()
        entry = data["results"].setdefault(pid, {"runs": {}})
        entry.update(profile=dict(prof, label=prof["label"] + (" · flash attention + KV q8_0" if pid.endswith("-fa") else "")),
                     gen=grounded.gen_profile(model, gen) | {"system": None, "judge": None, "schema": None, "judge_schema": None},
                     info=model_info(model), env=env)
        by_q = entry["runs"]
        g = grounded.gen_profile(model, gen)
        load_model(model, g["num_ctx"] or llm.NUM_CTX)
        entry["memory"] = resources(model)   # модель только что загружена: её размер с KV-кэшем и доля в видеопамяти
        log("profile", "{} — {} ({})".format(pid, entry["profile"]["label"], model))
        for run in range(1, runs + 1):
            for q in questions:
                done += 1
                have = by_q.setdefault(q["id"], [])
                if len(have) >= run:
                    continue
                r = lvc.run_one(q, model, gen)
                r["run"], r["res"] = run, resources(model)
                have.append(r)
                entry["summary"] = summarize(pid, by_q, questions, entry.get("memory"))
                data["updated"] = time.time()
                _save(data)
                log("answer", "{} · прогон {} · {}: {} → {} · {:.1f} с".format(
                    pid, run, q["id"], r["status"], r.get("verdict") or r.get("error", ""), r["latency_ms"] / 1000),
                    progress=(done, total))
        s = entry["summary"]
        log("summary", "{}: верно {:.0%}, медиана {:.1f} с, генерация {:.1f} ток/с, память модели {} МБ".format(
            pid, s["quality"]["correct"], (s["speed"]["median_ms"] or 0) / 1000, s["speed"]["eval_tok_s"] or 0,
            s["resources"]["model_mb"]))
    data["finished"] = time.time()
    _save(data)
    log("done", "готово: {} ответов".format(total))
    return data


def start(pids=None, runs=RUNS, fresh=False):
    pids = [p for p in (pids or PROFILES) if p.removesuffix("-fa") in PROFILES]
    with JOB.lock:
        if JOB.state == "running":
            return False
        JOB.state, JOB.log, JOB.progress, JOB.result = "running", [], None, None
        JOB.started, JOB.finished = time.time(), None

    def worker():
        try:
            _run(pids, int(runs), fresh)
            with JOB.lock:
                JOB.state = "done"
        except Exception as e:
            JOB.add("error", str(e))
            with JOB.lock:
                JOB.state = "error"
        finally:
            JOB.finished = time.time()

    threading.Thread(target=worker, name="optimize", daemon=True).start()
    return True


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else RUNS
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
    pids = [a for a in args if a.removesuffix("-fa") in PROFILES] or list(PROFILES)
    _run(pids, runs, "--fresh" in args, only)
