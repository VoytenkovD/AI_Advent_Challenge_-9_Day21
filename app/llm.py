# -*- coding: utf-8 -*-
"""Клиент LLM: один OpenAI-совместимый Chat Completions для трёх провайдеров.

  ollama    — локальные модели Ollama (http://127.0.0.1:11434/v1), без ключа
  ai-public — https://ai-public.a101.ru/api, ключ ~/.secrets/ai-public или AI_PUBLIC_API_KEY
  nvidia    — https://integrate.api.nvidia.com/v1, ключ ~/.secrets/nvidia или NVIDIA_API_KEY

Ключи читаются только на сервере и никогда не уходят в браузер.
"""
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request

import embed

TIMEOUT = 300

PROVIDERS = {
    "ollama": {"base_url": embed.OLLAMA_URL + "/v1", "key_file": None, "env_var": None},
    "ai-public": {"base_url": "https://ai-public.a101.ru/api",
                  "key_file": pathlib.Path.home() / ".secrets" / "ai-public", "env_var": "AI_PUBLIC_API_KEY"},
    "nvidia": {"base_url": "https://integrate.api.nvidia.com/v1",
               "key_file": pathlib.Path.home() / ".secrets" / "nvidia", "env_var": "NVIDIA_API_KEY"},
}
DEFAULT_MODEL = os.getenv("RAG_MODEL", "ollama:qwen2.5:3b")
EMBED_ONLY = ("bge-m3", "nomic-embed", "mxbai-embed", "embed")  # эти модели в Ollama не умеют чат


class LlmError(RuntimeError):
    pass


def _key(provider):
    p = PROVIDERS[provider]
    if not p["key_file"]:
        return None
    if p["key_file"].is_file():
        k = p["key_file"].read_text(encoding="utf-8-sig").strip()
        if k:
            return k
    k = (os.getenv(p["env_var"]) or "").strip()
    if k:
        return k
    raise LlmError("Нет ключа для {}: файл {} или переменная {}".format(provider, p["key_file"], p["env_var"]))


def _request(provider, path, body=None, timeout=TIMEOUT):
    if provider not in PROVIDERS:
        raise LlmError("Неизвестный провайдер: {}".format(provider))
    headers = {"Content-Type": "application/json"}
    key = _key(provider)
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(PROVIDERS[provider]["base_url"] + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LlmError("{} вернул {}: {}".format(provider, e.code, e.read().decode("utf-8", "replace")[:300]))
    except urllib.error.URLError as e:
        raise LlmError("{} недоступен: {}".format(provider, e.reason))


def split_model(full):
    """'ollama:qwen2.5:3b' → ('ollama', 'qwen2.5:3b')."""
    provider, _, model = (full or DEFAULT_MODEL).partition(":")
    if provider not in PROVIDERS or not model:
        raise LlmError("Модель задаётся как провайдер:модель, например ollama:qwen2.5:3b")
    return provider, model


def catalog():
    """Модели всех провайдеров: {provider: {ok, models | error}}."""
    out = {}
    for p in PROVIDERS:
        try:
            if p == "ollama":
                tags = json.loads(urllib.request.urlopen(embed.OLLAMA_URL + "/api/tags", timeout=5).read())
                models = [m["name"] for m in tags.get("models", [])
                          if not any(x in m["name"] for x in EMBED_ONLY)]
            else:
                models = sorted(m["id"] for m in _request(p, "/models", timeout=15).get("data", []))
            out[p] = {"ok": True, "models": models}
        except Exception as e:
            out[p] = {"ok": False, "error": str(e)[:200], "models": []}
    return {"providers": out, "default": DEFAULT_MODEL}


THINK_RE = re.compile(r"^.*?</think>\s*|<think>.*?</think>\s*", re.S)  # блок рассуждений, даже без открывающего тега


NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))  # 5 отрывков ≈ 1500–2000 токенов; больше — не влезет в 4 ГБ видеопамяти


def _chat_ollama(model, messages, temperature, max_tokens, schema=None):
    """Нативный /api/chat: в отличие от /v1 позволяет задать окно контекста (num_ctx).
    Через /v1 Ollama молча обрезает длинный промпт с отрывками до окна по умолчанию."""
    body = {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": max_tokens}}
    if schema:
        body["format"] = schema  # structured outputs: Ollama гарантирует JSON по схеме
    if model.startswith(("qwen3", "deepseek-r1")):
        # без рассуждений: быстрее, и ответ не съедает лимит токенов. Параметр think понимают не все
        # сборки модели, поэтому для Qwen3 дублируем мягким переключателем /no_think в последнем сообщении.
        body["think"] = False
        if model.startswith("qwen3"):
            body["messages"] = messages[:-1] + [dict(messages[-1], content=messages[-1]["content"] + " /no_think")]
    req = urllib.request.Request(embed.OLLAMA_URL + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LlmError("ollama вернула {}: {}".format(e.code, e.read().decode("utf-8", "replace")[:300]))
    except urllib.error.URLError as e:
        raise LlmError("ollama недоступна: {}".format(e.reason))
    return data["message"].get("content") or "", data.get("prompt_eval_count", 0), data.get("eval_count", 0)


def chat(full_model, messages, temperature=0.2, max_tokens=900, schema=None):
    """Один вызов модели. Возвращает {text, usage, latency_ms, model}.
    schema — JSON Schema ответа: у Ollama строгий формат, у остальных провайдеров — json_object
    (если провайдер его не поддерживает, повторяем запрос без него; схема тогда описана в промпте)."""
    provider, model = split_model(full_model)
    t0 = time.time()
    if provider == "ollama":
        text, p_tok, c_tok = _chat_ollama(model, messages, temperature, max_tokens, schema)
        return {"text": THINK_RE.sub("", text).strip(), "usage": {"prompt": p_tok, "completion": c_tok},
                "latency_ms": round((time.time() - t0) * 1000), "model": full_model}
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    if schema:
        try:
            data = _request(provider, "/chat/completions", dict(body, response_format={"type": "json_object"}))
        except LlmError as e:
            if " 400" not in str(e) and " 422" not in str(e):
                raise
            data = _request(provider, "/chat/completions", body)
    else:
        data = _request(provider, "/chat/completions", body)
    try:
        text = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError):
        raise LlmError("Неожиданный ответ {}: {}".format(provider, str(data)[:200]))
    u = data.get("usage") or {}
    return {
        "text": THINK_RE.sub("", text).strip(),
        "usage": {"prompt": u.get("prompt_tokens", 0), "completion": u.get("completion_tokens", 0)},
        "latency_ms": round((time.time() - t0) * 1000),
        "model": provider + ":" + model,
    }
