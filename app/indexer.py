# -*- coding: utf-8 -*-
"""Пайплайн индексации: книга → чанки (2 стратегии) → эмбеддинги (Ollama) → SQLite.

Запускается в фоновом потоке; ход работы пишется в журнал, который веб-интерфейс
читает в реальном времени. Повторный запуск сверяет «отпечаток» варианта
(sha256 текста + параметры нарезки + модель + калибровка + формат текста для
эмбеддинга) и пересчитывает только то, что изменилось.
"""
import hashlib
import json
import os
import threading
import time
import traceback

import book as book_mod
import chunking
import embed
import store

MAX_CHAPTERS = int(os.getenv("MAX_CHAPTERS", "40"))
STRATEGIES = ["fixed", "structure"]
EMBED_FORMAT_VERSION = 1  # меняется при изменении chunking.embed_text


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"     # idle | running | done | error
        self.log = []           # [{i, t, stage, msg}]
        self.progress = None    # {stage, done, total}
        self.started = self.finished = None
        self.result = None

    def add(self, stage, msg, progress=None):
        with self.lock:
            self.log.append({"i": len(self.log), "t": time.time(), "stage": stage, "msg": msg})
            if progress:
                self.progress = {"stage": stage, "done": progress[0], "total": progress[1]}
        print("[{}] {}".format(stage, msg), flush=True)

    def snapshot(self, after=0):
        with self.lock:
            return {"state": self.state, "progress": self.progress, "started": self.started,
                    "finished": self.finished, "result": self.result, "log": self.log[after:]}


JOB = Job()


def fingerprint(strategy, bk, cpt):
    payload = json.dumps({"text": bk["sha256"], "strategy": strategy, "params": chunking.PARAMS[strategy],
                          "version": chunking.VERSION[strategy], "model": embed.MODEL, "cpt": cpt, "format": EMBED_FORMAT_VERSION}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _run(rebuild):
    job = JOB
    log = job.add
    t_all = time.time()
    store.init()
    if rebuild:
        store.clear()
        store.set_meta("cpt", None)
        log("rebuild", "индекс очищен, считаю всё заново")

    st = embed.status()
    if not st["ok"]:
        raise embed.EmbedError(st.get("error", "Ollama недоступна"))
    log("ollama", "Ollama {} на связи: {}".format(st["version"], st["url"]))
    embed.pull(log)

    bk = book_mod.load(MAX_CHAPTERS, log=log)
    pages = round(len(bk["text"]) / 2500)
    log("parse", "{}: главы 1–{}, {} абзацев, {} тыс. символов (~{} стр.)".format(
        bk["short"], len(bk["chapters"]), len(bk["paragraphs"]), len(bk["text"]) // 1000, pages))
    store.save_document(bk)

    cpt = store.get_meta("cpt")
    if not cpt:
        text = bk["text"]
        step = max(1, len(text) // 20)
        samples = [text[i:i + 1500] for i in range(0, len(text) - 1500, step)][:20]
        cpt = embed.calibrate(samples, log)
        store.set_meta("cpt", cpt)
    else:
        log("calibrate", "калибровка из индекса: {} символа на токен".format(cpt))
    embed.gpu_check(log)
    tok = chunking.Tok(cpt)

    summary = {}
    for strategy in STRATEGIES:
        title = chunking.STRATEGIES[strategy]["title"]
        fp = fingerprint(strategy, bk, cpt)
        old = store.get_variant(strategy)
        if old and old["fingerprint"] == fp:
            log("skip", "{}: без изменений ({} чанков)".format(title, old["chunks"]))
            summary[strategy] = "без изменений"
            continue

        t0 = time.time()
        chunks = chunking.run(strategy, bk, tok)
        tokens = [c["tokens"] for c in chunks]
        log("chunk", "{}: {} чанков, токенов от {} до {}, в среднем {}".format(
            title, len(chunks), min(tokens), max(tokens), round(sum(tokens) / len(tokens))))

        texts = [chunking.embed_text(c, bk) for c in chunks]
        vectors, model_tokens, sec = embed.embed_batches(texts, log, label=title + ": ")
        store.replace_chunks(strategy, chunks, vectors, {
            "fingerprint": fp, "model": embed.MODEL, "params": chunking.PARAMS[strategy],
            "tokens": sum(tokens), "model_tokens": model_tokens, "dim": len(vectors[0]),
            "seconds": round(time.time() - t0, 1)})
        log("store", "{}: записано в индекс, вектор {} чисел, {:.1f} с".format(title, len(vectors[0]), time.time() - t0))
        summary[strategy] = "посчитано"

    done = sum(v == "посчитано" for v in summary.values())
    log("done", "готово за {:.1f} с: посчитано {}, без изменений {}".format(
        time.time() - t_all, done, len(summary) - done))
    return summary


def start(rebuild=False):
    """Запускает индексацию в фоне. Возвращает False, если она уже идёт."""
    with JOB.lock:
        if JOB.state == "running":
            return False
        JOB.state, JOB.log, JOB.progress, JOB.result = "running", [], None, None
        JOB.started, JOB.finished = time.time(), None

    def worker():
        try:
            res = _run(rebuild)
            with JOB.lock:
                JOB.state, JOB.result = "done", res
        except Exception as e:
            traceback.print_exc()
            JOB.add("error", str(e))
            with JOB.lock:
                JOB.state = "error"
        finally:
            JOB.finished = time.time()

    threading.Thread(target=worker, name="indexer", daemon=True).start()
    return True


if __name__ == "__main__":  # индексация из терминала: python app/indexer.py [--rebuild]
    import sys
    store.init()
    _run("--rebuild" in sys.argv)
