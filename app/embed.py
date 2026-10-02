# -*- coding: utf-8 -*-
"""Эмбеддинги через локальную Ollama (модель bge-m3 на видеокарте).

Все векторы нормализуются к единичной длине (L2): тогда косинусное сходство —
это просто скалярное произведение.
"""
import json
import math
import os
import time
import urllib.error
import urllib.request

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("EMBED_MODEL", "bge-m3")
BATCH = 16
TIMEOUT = 300


class EmbedError(RuntimeError):
    pass


def _call(path, body=None, timeout=TIMEOUT):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(OLLAMA_URL + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise EmbedError("Ollama {} вернула {}: {}".format(path, e.code, e.read().decode("utf-8", "replace")[:300]))
    except urllib.error.URLError as e:
        raise EmbedError("Ollama недоступна по адресу {}: {}".format(OLLAMA_URL, e.reason))


def normalize(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts):
    """Эмбеддинги списка текстов одним запросом. Возвращает (векторы, число токенов во входе)."""
    res = _call("/api/embed", {"model": MODEL, "input": texts, "truncate": True})
    return [normalize(v) for v in res["embeddings"]], res.get("prompt_eval_count", 0)


def embed_one(text):
    vecs, _ = embed([text])
    return vecs[0]


def status():
    """Состояние Ollama: версия, есть ли модель, сколько её в видеопамяти."""
    out = {"url": OLLAMA_URL, "model": MODEL, "ok": False}
    try:
        out["version"] = _call("/api/version", timeout=3)["version"]
        out["ok"] = True
        names = [m["name"] for m in _call("/api/tags", timeout=5).get("models", [])]
        out["model_pulled"] = any(n == MODEL or n.startswith(MODEL + ":") for n in names)
        for m in _call("/api/ps", timeout=5).get("models", []):
            if m["name"].startswith(MODEL):
                out["loaded"] = True
                out["size_mb"] = round(m.get("size", 0) / 2**20)
                out["vram_mb"] = round(m.get("size_vram", 0) / 2**20)
                out["on_gpu"] = m.get("size_vram", 0) >= m.get("size", 1) * 0.99
    except EmbedError as e:
        out["error"] = str(e)
    return out


def pull(log):
    """Скачивает модель, если её нет (один раз)."""
    st = status()
    if not st["ok"]:
        raise EmbedError(st.get("error", "Ollama недоступна"))
    if st.get("model_pulled"):
        return
    log("pull", "скачиваю модель {} (один раз)…".format(MODEL))
    _call("/api/pull", {"model": MODEL, "stream": False}, timeout=3600)
    log("pull", "модель {} готова".format(MODEL))


def calibrate(samples, log):
    """Сколько символов приходится на токен у модели: отправляем образцы по одному и
    смотрим prompt_eval_count. Из ответа вычитаем 2 служебных токена (<s>, </s>)."""
    chars = tokens = 0
    for s in samples:
        _, n = embed([s])
        chars += len(s)
        tokens += max(1, n - 2)
    cpt = round(chars / tokens, 3)
    log("calibrate", "{} образцов: {} символов → {} токенов, {} символа на токен".format(
        len(samples), chars, tokens, cpt))
    return cpt


def gpu_check(log):
    st = status()
    if st.get("loaded"):
        where = "целиком в видеопамяти" if st.get("on_gpu") else "НЕ целиком в видеопамяти (частично на CPU)"
        log("gpu", "{} {}: {} МБ из {} МБ".format(MODEL, where, st.get("vram_mb"), st.get("size_mb")))
    return st


def embed_batches(texts, log, label=""):
    """Эмбеддинги большими списками пачками по BATCH. Возвращает (векторы, токены, секунды)."""
    vecs, total_tokens, t0 = [], 0, time.time()
    batches = (len(texts) + BATCH - 1) // BATCH
    for i in range(0, len(texts), BATCH):
        v, n = embed(texts[i:i + BATCH])
        vecs.extend(v)
        total_tokens += n
        b = i // BATCH + 1
        if b == batches or b % 4 == 0:
            el = time.time() - t0
            log("embed", "{}пачка {}/{}, {:.1f} тыс. токенов/с".format(
                label, b, batches, total_tokens / el / 1000 if el else 0), progress=(len(vecs), len(texts)))
    return vecs, total_tokens, time.time() - t0
