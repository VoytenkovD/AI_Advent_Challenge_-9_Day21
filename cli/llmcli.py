# -*- coding: utf-8 -*-
"""llmcli — CLI для локальной LLM в Ollama (День 26). Только стандартная библиотека Python.

  python cli/llmcli.py ask [флаги] "вопрос"     ответ печатается по мере генерации
  echo текст | python cli/llmcli.py ask "задание к тексту"
  python cli/llmcli.py models                   локальные модели и что сейчас в видеопамяти
  python cli/llmcli.py bench                    запросы разной сложности с проверкой ответов и скоростью
  python cli/llmcli.py help                     справка, сведения о модели и её ограничения

Модель по умолчанию — qwen2.5:3b (переменная LLM_MODEL или флаг --model), Ollama — http://127.0.0.1:11434
(переменная OLLAMA_URL). Обращение идёт по HTTP API Ollama: /api/chat (стриминг NDJSON), /api/show, /api/tags,
/api/ps, /api/pull.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("LLM_MODEL", "qwen2.5:3b")

LIMITATIONS = """Ограничения:
  - маленькая модель (3B): уступает облачным, может выдумывать факты и ошибаться в счёте
  - нет доступа к интернету; знания заканчиваются датой обучения
  - каждый ask независим: модель не помнит прошлые вопросы
  - контекстное окно ограничено (--ctx); длинный ввод обрезается
  - на GTX 1050 Ti (4 ГБ) модель целиком в видеопамяти; модель больше 4 ГБ частично уйдёт на процессор и замедлится"""

# флаги ask → options Ollama
OPTIONS = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "repeat_penalty": "repeat_penalty",
           "ctx": "num_ctx", "max_tokens": "num_predict", "seed": "seed", "stop": "stop"}


class OllamaError(RuntimeError):
    pass


def _post(path, body, stream=False, timeout=600):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 404:
            raise OllamaError("not found: " + detail)
        raise OllamaError("Ollama вернула {}: {}".format(e.code, detail))
    except urllib.error.URLError as e:
        raise OllamaError("Ollama недоступна по адресу {} ({}). Запустите её: ollama serve".format(OLLAMA, e.reason))
    if stream:
        return resp
    with resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path, timeout=5):
    try:
        with urllib.request.urlopen(OLLAMA + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise OllamaError("Ollama недоступна по адресу {} ({})".format(OLLAMA, getattr(e, "reason", e)))


def show(model):
    return _post("/api/show", {"model": model}, timeout=10)


def ensure_model(model, err=sys.stderr):
    try:
        return show(model)
    except OllamaError as e:
        if not str(e).startswith("not found"):
            raise
    print("скачиваю {} (один раз)...".format(model), file=err, flush=True)
    resp = _post("/api/pull", {"model": model, "stream": True}, stream=True, timeout=3600)
    last = ""
    with resp:
        for line in resp:
            st = json.loads(line).get("status", "")
            if st != last and not st.startswith("pulling "):
                print("  " + st, file=err, flush=True)
            last = st
    return show(model)


def chat(model, messages, options=None, fmt=None, out=sys.stdout, echo=True):
    """Стриминговый /api/chat. Печатает токены по мере генерации; возвращает (текст, статистика)."""
    body = {"model": model, "messages": messages, "stream": True, "options": options or {}}
    if fmt is not None:
        body["format"] = fmt
    t0 = time.time()
    first = None
    parts, stats = [], {}
    with _post("/api/chat", body, stream=True) as resp:
        for line in resp:
            ev = json.loads(line)
            if ev.get("error"):
                raise OllamaError(ev["error"])
            piece = (ev.get("message") or {}).get("content", "")
            if piece:
                if first is None:
                    first = time.time() - t0
                parts.append(piece)
                if echo:
                    out.write(piece)
                    out.flush()
            if ev.get("done"):
                stats = ev
    total = time.time() - t0
    ev_n, ev_d = stats.get("eval_count", 0), stats.get("eval_duration", 0)
    return "".join(parts), {
        "prompt_tokens": stats.get("prompt_eval_count", 0), "answer_tokens": ev_n,
        "tokens_per_s": round(ev_n / (ev_d / 1e9), 1) if ev_d else 0.0,
        "ttft_s": round(first or 0, 2), "load_s": round(stats.get("load_duration", 0) / 1e9, 2),
        "total_s": round(total, 2), "done_reason": stats.get("done_reason"),
    }


# ═════════════════════════════ ask ═════════════════════════════

def cmd_ask(a):
    prompt = " ".join(a.prompt)
    if not sys.stdin.isatty():  # аргумент — инструкция, stdin — материал к ней
        data = sys.stdin.read()
        if data.strip():
            prompt = (prompt + "\n\n" + data).strip()
    if not prompt.strip():
        raise SystemExit('нет запроса: llmcli ask "вопрос" или echo вопрос | llmcli ask')
    ensure_model(a.model)
    messages = ([{"role": "system", "content": a.system}] if a.system else []) + [{"role": "user", "content": prompt}]
    options = {OPTIONS[k]: v for k, v in vars(a).items() if k in OPTIONS and v not in (None, [])}
    text, st = chat(a.model, messages, options, "json" if a.json else None)
    print(flush=True)
    if a.stats:
        print("[{}] токены: запрос {}, ответ {} · {} ток/с · первый токен {} с · загрузка модели {} с · всего {} с{}".format(
            a.model, st["prompt_tokens"], st["answer_tokens"], st["tokens_per_s"], st["ttft_s"], st["load_s"],
            st["total_s"], " · обрезано по --max-tokens" if st["done_reason"] == "length" else ""), file=sys.stderr)


# ═════════════════════════════ models / help ═════════════════════════════

def _gb(n):
    return "{:.1f} ГБ".format(n / 2**30)


def model_info(model):
    info = show(model)
    d = info.get("details", {})
    ctx = next((v for k, v in (info.get("model_info") or {}).items() if k.endswith(".context_length")), None)
    return {"family": d.get("family"), "params": d.get("parameter_size"), "quant": d.get("quantization_level"),
            "format": d.get("format"), "context": ctx}


def cmd_models(a):
    tags = _get("/api/tags").get("models", [])
    loaded = {m["name"]: m for m in _get("/api/ps").get("models", [])}
    print("{:42} {:>8} {:>8} {:>8}  {}".format("модель", "размер", "парам.", "квант.", "сейчас в памяти"))
    for m in sorted(tags, key=lambda m: m["name"]):
        d = m.get("details", {})
        ps = loaded.get(m["name"])
        where = ""
        if ps:
            gpu = ps.get("size_vram", 0) / max(1, ps.get("size", 1))
            where = "{} · {:.0f}% на видеокарте · контекст {}".format(_gb(ps["size"]), gpu * 100, ps.get("context_length", "?"))
        print("{:42} {:>8} {:>8} {:>8}  {}".format(m["name"], _gb(m["size"]), d.get("parameter_size", "?"),
                                                    d.get("quantization_level", "?"), where))
    print("\nOllama {} · {}".format(_get("/api/version").get("version"), OLLAMA))


def cmd_help(a, parser):
    parser.print_help()
    print("\nМодель: {}".format(a.model))
    try:
        i = model_info(a.model)
        print("  семейство {family}, параметров {params}, квантование {quant}, контекст модели {context} токенов".format(**i))
    except OllamaError as e:
        print("  " + ("ещё не скачана — скачается при первом ask" if str(e).startswith("not found")
                      else "Ollama недоступна — живые данные модели не получены"))
    print("\n" + LIMITATIONS)


# ═════════════════════════════ bench ═════════════════════════════

def _num(text):
    m = re.findall(r"-?\d+(?:[.,]\d+)?", text.replace(" ", " "))
    return float(m[-1].replace(",", ".")) if m else None


def _check_code(text):
    """Проверка сгенерированной функции: в отдельном процессе Python (-I), с таймаутом, только если в коде
    нет ничего, кроме определений функций (никаких импортов, вызовов и обращений к файлам на верхнем уровне)."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    code = (m.group(1) if m else text).strip()
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, "синтаксическая ошибка: {}".format(e.msg)
    if not tree.body or not all(isinstance(n, ast.FunctionDef) or
                                (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)) for n in tree.body):
        return False, "в коде есть что-то кроме определений функций — не запускаю"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "is_palindrome" for n in tree.body):
        return False, "нет функции is_palindrome"
    tests = ("cases = [('А роза упала на лапу Азора', True), ('Madam, I\\'m Adam', True), ('Привет', False), ('', True), ('ab', False)]\n"
             "bad = [c for c, want in cases if bool(is_palindrome(c)) != want]\n"
             "print('OK' if not bad else 'FAIL ' + repr(bad))\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code + "\n\n" + tests)
        try:
            r = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True, timeout=10, cwd=tmp,
                               encoding="utf-8")
        except subprocess.TimeoutExpired:
            return False, "превышено время выполнения"
    out = (r.stdout + r.stderr).strip()
    return out.startswith("OK"), out.splitlines()[-1] if out else "нет вывода"


def _check_json(text):
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return False, "не JSON"
    want = {"name": "Анна", "age": 34, "city": "Казань"}
    got = {k: d.get(k) for k in want}
    ok = str(got["name"]).strip() == "Анна" and str(got["age"]).strip() == "34" and "Казан" in str(got["city"])
    return ok, json.dumps(got, ensure_ascii=False)


BENCH = [
    {"id": "fact", "level": "простой", "title": "факт одним словом",
     "prompt": "Какая столица Франции? Ответь одним словом.",
     "check": lambda t: ("париж" in t.lower(), t.strip()[:40])},
    {"id": "arith", "level": "простой", "title": "арифметика",
     "prompt": "Сколько будет 17 * 23? Ответь только числом.",
     "check": lambda t: (_num(t) == 391, "ответ {}, нужно 391".format(_num(t)))},
    {"id": "word", "level": "средний", "title": "задача в несколько шагов",
     "prompt": ("В магазине было 120 яблок. Утром продали четверть, днём привезли ещё 45, вечером продали 30. "
                "Сколько яблок осталось? Реши по шагам, в последней строке напиши: Ответ: <число>."),
     "check": lambda t: (_num(t.split("Ответ")[-1]) == 105, "итог {}, нужно 105".format(_num(t.split("Ответ")[-1])))},
    {"id": "json", "level": "средний", "title": "извлечение в JSON (format=json)", "format": "json",
     "prompt": ("Извлеки из текста данные в JSON с ключами name, age (число), city: "
                "«Вчера в Казани Анна отметила свой 34-й день рождения»."),
     "check": _check_json},
    {"id": "code", "level": "сложный", "title": "код + автотесты",
     "prompt": ("Напиши на Python функцию is_palindrome(s: str) -> bool, которая проверяет, является ли строка палиндромом, "
                "игнорируя регистр, пробелы и знаки препинания. Пустая строка — палиндром. Не используй import. "
                "Верни только код функции в блоке ```python```."),
     "check": _check_code},
    {"id": "logic", "level": "сложный", "title": "логическая задача",
     "prompt": ("Три друга — Аня, Боря и Вика — выбрали разные напитки: чай, кофе и сок. Аня не пьёт кофе. "
                "Боря пьёт не чай и не кофе. Что пьёт Вика? Коротко поясни и в последней строке напиши: Ответ: <напиток>."),
     "check": lambda t: ("кофе" in t.split("Ответ")[-1].lower(), t.split("Ответ")[-1].strip(": \n")[:40])},
]


def cmd_bench(a):
    ensure_model(a.model)
    i = model_info(a.model)
    print("bench · {} ({}, {}, контекст {}) · temperature 0, seed 42\n".format(a.model, i["params"], i["quant"], i["context"]))
    rows = []
    for b in BENCH:
        if a.only and b["id"] not in a.only:
            continue
        print("── [{}] {} ── {}".format(b["level"], b["title"], b["prompt"]))
        text, st = chat(a.model, [{"role": "user", "content": b["prompt"]}],
                        {"temperature": 0, "seed": 42, "num_predict": 600}, b.get("format"), echo=a.verbose)
        if not a.verbose:
            print(text.strip()[:600] + ("…" if len(text.strip()) > 600 else ""))
        ok, note = b["check"](text)
        print("\n   {} {} · {} ток., {} ток/с, первый токен {} с, всего {} с\n".format(
            "✓" if ok else "✗", note, st["answer_tokens"], st["tokens_per_s"], st["ttft_s"], st["total_s"]))
        rows.append(dict(b, ok=ok, note=note, **st))
    print("{:9} {:30} {:>5} {:>7} {:>7} {:>7} {:>7}".format("уровень", "запрос", "итог", "токены", "ток/с", "1-й, с", "всего"))
    for r in rows:
        print("{:9} {:30} {:>5} {:>7} {:>7} {:>7} {:>7}".format(r["level"], r["title"], "✓" if r["ok"] else "✗",
                                                               r["answer_tokens"], r["tokens_per_s"], r["ttft_s"], r["total_s"]))
    print("\nверно {} из {}".format(sum(r["ok"] for r in rows), len(rows)))
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump({"model": a.model, "info": i, "rows": [{k: v for k, v in r.items() if k != "check"} for r in rows]},
                      f, ensure_ascii=False, indent=1)


# ═════════════════════════════ разбор аргументов ═════════════════════════════

def build_parser():
    p = argparse.ArgumentParser(prog="llmcli", description="CLI для локальной LLM в Ollama")
    p.add_argument("--model", default=MODEL, help="модель Ollama (по умолчанию {})".format(MODEL))
    sub = p.add_subparsers(dest="cmd")
    ask = sub.add_parser("ask", help="спросить модель; ответ печатается по мере генерации")
    ask.add_argument("prompt", nargs="*", help="вопрос; текст из конвейера добавляется к нему")
    ask.add_argument("--system", help="системный промпт")
    ask.add_argument("--temperature", type=float, help="случайность ответа, 0..2")
    ask.add_argument("--top-p", dest="top_p", type=float, help="nucleus sampling, 0..1")
    ask.add_argument("--top-k", dest="top_k", type=int, help="выбор из k самых вероятных токенов")
    ask.add_argument("--repeat-penalty", dest="repeat_penalty", type=float, help="штраф за повторы, напр. 1.1")
    ask.add_argument("--ctx", type=int, help="контекстное окно в токенах (num_ctx)")
    ask.add_argument("--max-tokens", dest="max_tokens", type=int, help="предел длины ответа (num_predict)")
    ask.add_argument("--seed", type=int, help="зерно случайности для воспроизводимых ответов")
    ask.add_argument("--stop", action="append", default=[], help="стоп-последовательность (можно повторять)")
    ask.add_argument("--json", action="store_true", help="ответ строго в JSON (format=json)")
    ask.add_argument("--stats", action="store_true", help="напечатать в stderr токены и скорость")
    sub.add_parser("models", help="локальные модели и что сейчас в видеопамяти")
    bench = sub.add_parser("bench", help="запросы разной сложности с проверкой ответов")
    bench.add_argument("--only", nargs="*", help="только эти id: " + ", ".join(b["id"] for b in BENCH))
    bench.add_argument("--verbose", action="store_true", help="печатать ответ по мере генерации")
    bench.add_argument("--save", help="сохранить результаты в JSON-файл")
    sub.add_parser("help", help="справка, сведения о модели и её ограничения")
    return p


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        if a.cmd == "ask":
            cmd_ask(a)
        elif a.cmd == "models":
            cmd_models(a)
        elif a.cmd == "bench":
            cmd_bench(a)
        else:
            cmd_help(a, parser)
    except OllamaError as e:
        print("ошибка: {}".format(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nпрервано", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
