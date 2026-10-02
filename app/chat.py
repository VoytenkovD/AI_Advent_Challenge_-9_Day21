# -*- coding: utf-8 -*-
"""Мини-чат с RAG и памятью задачи (День 25).

На каждое сообщение пользователя:
  1. ПАМЯТЬ ЗАДАЧИ (task state) обновляется: LLM извлекает изменения по JSON-схеме, а код решает, что принять.
     • цель диалога — ставится один раз и меняется только по явной просьбе (защита от «уплывания»);
     • что пользователь уже уточнил, ограничения, термины — накапливаются без дублей;
     • диапазон глав («только главы 1–20») распознаёт код и превращает в фильтр поиска.
  2. Если это вопрос о книге — он переписывается в самостоятельный поисковый запрос с учётом цели и предыдущих
     реплик («а кем был его отец?» → «Кем был отец Квикега?»).
  3. RAG по конвейеру Дня 24: воронка → порог «не знаю» → JSON-ответ → проверка цитат → проверка смысла.
     В LLM уходят память задачи, последние реплики и отрывки.
  4. Ответ ВСЕГДА с блоком источников: подтверждённые источники, либо явное «источников не найдено» со списком
     проверенных отрывков, либо пометка, что это служебная реплика (уточнение задачи, без поиска).
  «Подведи итог» — сводка по цели из уже подтверждённых ответов диалога с объединённым списком источников.
"""
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

import grounded
import llm

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "chat.db"
HISTORY_TURNS = 3         # сколько последних обменов репликами видит модель
_lock = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, title TEXT, created REAL, updated REAL, state TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT, meta TEXT, created REAL
);
CREATE INDEX IF NOT EXISTS ix_messages_chat ON messages(chat_id, id);
"""


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    return con


def empty_state():
    return {"goal": None, "goal_history": [], "clarified": [], "constraints": [], "terms": [],
            "chapter_range": None, "turns": 0}


# ═════════════════════════════ хранилище ═════════════════════════════

def create_chat(title=None):
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock, closing(_connect()) as con:
        con.execute("INSERT INTO chats VALUES (?,?,?,?,?)", (cid, title or "Новый чат", now, now, json.dumps(empty_state())))
        con.commit()
    return get_chat(cid)


def list_chats():
    with closing(_connect()) as con:
        rows = con.execute("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.chat_id=c.id) AS n "
                           "FROM chats c ORDER BY updated DESC").fetchall()
    return [{"id": r["id"], "title": r["title"], "updated": r["updated"], "messages": r["n"],
             "goal": (json.loads(r["state"]).get("goal") or {}).get("text")} for r in rows]


def get_chat(cid):
    with closing(_connect()) as con:
        c = con.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
        if not c:
            return None
        msgs = con.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY id", (cid,)).fetchall()
    return {"id": c["id"], "title": c["title"], "state": json.loads(c["state"]),
            "messages": [{"id": m["id"], "role": m["role"], "content": m["content"], "meta": json.loads(m["meta"] or "{}"),
                          "created": m["created"]} for m in msgs]}


def delete_chat(cid):
    with _lock, closing(_connect()) as con:
        con.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
        con.execute("DELETE FROM chats WHERE id=?", (cid,))
        con.commit()


def _save(cid, state, user_msg, reply, meta, title=None):
    now = time.time()
    with _lock, closing(_connect()) as con:
        con.execute("INSERT INTO messages(chat_id, role, content, meta, created) VALUES (?,?,?,?,?)",
                    (cid, "user", user_msg, "{}", now))
        con.execute("INSERT INTO messages(chat_id, role, content, meta, created) VALUES (?,?,?,?,?)",
                    (cid, "assistant", reply, json.dumps(meta, ensure_ascii=False), now))
        if title:
            con.execute("UPDATE chats SET state=?, updated=?, title=? WHERE id=?", (json.dumps(state, ensure_ascii=False), now, title, cid))
        else:
            con.execute("UPDATE chats SET state=?, updated=? WHERE id=?", (json.dumps(state, ensure_ascii=False), now, cid))
        con.commit()


def set_state(cid, state):
    with _lock, closing(_connect()) as con:
        con.execute("UPDATE chats SET state=?, updated=? WHERE id=?", (json.dumps(state, ensure_ascii=False), time.time(), cid))
        con.commit()


# ═════════════════════════════ память задачи ═════════════════════════════

STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["question", "setup", "summary"]},
        "goal": {"type": "string"},
        "goal_changed": {"type": "boolean"},
        "clarified": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "terms": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"}, "meaning": {"type": "string"}}, "required": ["term", "meaning"]}},
    },
    "required": ["kind", "goal", "goal_changed", "clarified", "constraints", "terms"],
}

STATE_PROMPT = (
    "Ты ведёшь память задачи в диалоге о романе «Моби Дик». По НОВОМУ сообщению пользователя заполни JSON:\n"
    "- kind: question — пользователь спрашивает что-то о книге; setup — только ставит цель, ограничения, термины "
    "или уточнения, вопроса о книге нет; summary — просит подвести итог / резюме диалога.\n"
    "- goal: цель всего диалога, если пользователь её назвал в этом сообщении (иначе пустая строка).\n"
    "- goal_changed: true, только если пользователь ЯВНО меняет цель («новая цель», «теперь давай о другом»).\n"
    "- clarified: что пользователь уточнил о своём запросе (например, «интересуют только офицеры»).\n"
    "- constraints: ограничения на ответы (объём, стиль, какие главы, на чём основываться).\n"
    "- terms: термины, которые пользователь определил: term — слово, meaning — что оно значит.\n"
    "Записывай только то, что есть в НОВОМ сообщении; пустые списки, если ничего нет. Пиши по-русски, коротко."
)

CHAPTERS_RE = re.compile(r"глав\w*\s*(?:с\s*)?(\d{1,3})\s*(?:[-–—]|по|до)\s*(\d{1,3})|с\s*(\d{1,3})\s*(?:-?й)?\s*(?:по|до)\s*(\d{1,3})\s*-?\w*\s*глав", re.I)
RESET_CHAPTERS_RE = re.compile(r"(без ограничени\w* по глав|сними ограничени\w* по глав|по всем глав)", re.I)
GOAL_RE = re.compile(r"(?:^|[.!?]\s*)цель\s*[—:-]\s*([^.!?\n]+)", re.I)
TERM_RE = re.compile(r"(?:термин|слово|под словом)\s*«([^»]+)»[^—–-]*[—–-]\s*(?:это\s+)?([^.\n]+)|"
                     r"когда я (?:пишу|говорю)\s*«([^»]+)»,?\s*я имею в виду\s*([^.\n]+)", re.I)
ASK_RE = re.compile(r"(что|кто|как|где|когда|почему|зачем|какой|какая|какие|каков|чем|откуда|куда|сколько|из чего|"
                    r"расскажи|опиши|объясни|перечисли|назови|покажи|найди|сравни)\b", re.I)
CONSTRAINT_RE = re.compile(r"\b(отвечай|пиши|используй|опирайся|только|кратко|коротко|подробно|без\s|не\s+(?:выдумывай|добавляй))", re.I)
SUMMARY_RE = re.compile(r"(подведи|подвести|подводя)\s+итог|резюм|итог\s+по\s+(?:нашей\s+)?цели", re.I)


def _add_unique(lst, items, turn, key="text"):
    seen = {re.sub(r"\W+", " ", x[key]).strip().lower() for x in lst}
    added = []
    for it in items:
        text = (it if isinstance(it, str) else it.get(key, "")).strip()
        norm = re.sub(r"\W+", " ", text).strip().lower()
        if len(norm) < 3 or norm in seen:
            continue
        seen.add(norm)
        entry = {key: text, "turn": turn} if isinstance(it, str) else dict(it, turn=turn)
        lst.append(entry)
        added.append(entry)
    return added


def update_state(state, message, model):
    """Возвращает (новое состояние, что изменилось, kind сообщения)."""
    st = json.loads(json.dumps(state))
    turn = st["turns"] = st.get("turns", 0) + 1
    changes = {}
    try:
        res = llm.chat(model, [{"role": "system", "content": STATE_PROMPT},
                               {"role": "user", "content": "Текущая цель: {}\n\nНОВОЕ сообщение: {}".format(
                                   (st["goal"] or {}).get("text") or "не задана", message)}],
                       temperature=0.0, max_tokens=400, schema=STATE_SCHEMA)
        delta = grounded._parse(res["text"]) or {}
    except llm.LlmError:
        delta = {}
    # цель: явная формула «Цель — …» надёжнее модели; иначе — от модели, но только если цели нет или её явно меняют
    explicit = GOAL_RE.search(message)
    new_goal = (explicit.group(1).strip() if explicit else (delta.get("goal") or "").strip())
    if explicit and re.search(r"\b(его|её|ее|их|него|неё)\b", new_goal, re.I):
        before = message[:explicit.start(1)]  # «его портрет» → добавляем предложение, где назван герой
        prev = [s.strip() for s in re.split(r"[.!?]", before) if len(s.strip()) > 10 and not re.match(r"цель", s.strip(), re.I)]
        if prev:
            new_goal = "{} ({})".format(new_goal, prev[-1])
    if new_goal and (not st["goal"] or (delta.get("goal_changed") and (explicit or "цел" in message.lower()))):
        if st["goal"]:
            st["goal_history"].append(st["goal"])
        st["goal"] = {"text": new_goal, "turn": turn}
        changes["goal"] = new_goal
    elif new_goal and st["goal"] and delta.get("goal_changed"):
        changes["goal_kept"] = "модель предложила сменить цель без явной просьбы — цель сохранена"

    # из обычного вопроса память задачи не пополняем: маленькая модель «находит» там термины и ограничения
    is_question = ("?" in message or ASK_RE.match(message.strip())) and not TERM_RE.search(message)
    if is_question:
        delta = dict(delta, clarified=[], constraints=[], terms=[])
    goal_text = ((st.get("goal") or {}).get("text") or "").lower()
    m = CHAPTERS_RE.search(message)
    term_words = {(t.get("term") or "").strip().lower() for t in (delta.get("terms") or [])}
    if TERM_RE.search(message):
        tm = TERM_RE.search(message)
        term_words.add((tm.group(1) or tm.group(3) or "").lower())
    for field in ("clarified", "constraints"):
        items = [x for x in (delta.get(field) or []) if x.strip().lower() not in goal_text
                 and x.strip().strip("«»\"").lower() not in term_words]
        if m:  # диапазон глав записывает код в одной канонической формулировке
            items = [x for x in items if not re.search(r"\d+\s*[-–—]\s*\d+|глав", x, re.I)]
        added = _add_unique(st[field], items, turn)
        if added:
            changes[field] = [a["text"] for a in added]
    added = _add_unique(st["terms"], [t for t in (delta.get("terms") or []) if t.get("term") and t.get("meaning")], turn, key="term")
    if not added:  # термин в явной формулировке надёжнее распознать шаблоном
        tm = TERM_RE.search(message)
        if tm:
            term = tm.group(1) or tm.group(3)
            meaning = (tm.group(2) or tm.group(4) or "").strip().rstrip(".,; ")
            meaning = re.sub(r",?\s*держи в уме.*$", "", meaning, flags=re.I).strip()
            added = _add_unique(st["terms"], [{"term": term, "meaning": meaning}], turn, key="term")
    if added:
        changes["terms"] = ["{} — {}".format(a["term"], a["meaning"]) for a in added]

    # диапазон глав — структурное ограничение, распознаёт код
    if m:
        a, b = [int(x) for x in m.groups() if x][:2]
        lo, hi = max(1, min(a, b)), min(40, max(a, b))
        st["chapter_range"] = [lo, hi]
        changes["chapter_range"] = [lo, hi]
        _add_unique(st["constraints"], ["только главы {}–{}".format(lo, hi)], turn)
    elif RESET_CHAPTERS_RE.search(message):
        st["chapter_range"] = None
        changes["chapter_range"] = None
    # тип реплики: правила надёжнее маленькой модели
    if SUMMARY_RE.search(message):
        kind = "summary"
    elif is_question:
        kind = "question"
    else:
        kind = "setup"
        # служебная реплика об ограничениях, из которой модель ничего не извлекла, — записываем как есть
        if not changes and CONSTRAINT_RE.search(message):
            added = _add_unique(st["constraints"], [message.strip().rstrip(".")], turn)
            if added:
                changes["constraints"] = [a["text"] for a in added]
    return st, changes, kind


def memory_block(st):
    lines = ["## Память задачи (учитывай в каждом ответе)"]
    lines.append("Цель диалога: " + ((st.get("goal") or {}).get("text") or "не задана"))
    if st["clarified"]:
        lines.append("Пользователь уже уточнил:\n" + "\n".join("- " + x["text"] for x in st["clarified"]))
    if st["constraints"]:
        lines.append("Ограничения:\n" + "\n".join("- " + x["text"] for x in st["constraints"]))
    if st["terms"]:
        lines.append("Термины пользователя:\n" + "\n".join("- {} — {}".format(x["term"], x["meaning"]) for x in st["terms"]))
    lines.append("Отвечай так, чтобы ответ продвигал пользователя к цели, и соблюдай ограничения.")
    return "\n".join(lines)


# ═════════════════════════════ поисковый запрос ═════════════════════════════

CONDENSE_PROMPT = (
    "Перепиши последний вопрос пользователя о романе «Моби Дик» в самостоятельный вопрос, понятный без истории диалога: "
    "замени местоимения («он», «его», «там») на имена из диалога, раскрой термины пользователя. "
    "Если вопрос уже самостоятельный — верни его как есть. Верни только вопрос по-русски, одной строкой."
)


ANAPHORA_RE = re.compile(r"\b(он|его|ему|им|нём|она|её|ее|ей|они|их|им|ними|там|тот|та|те|этот|эта|эти|к ним|у него|у них)\b", re.I)
STOP = set("что кто как где когда почему зачем какой какая какие каков чем откуда куда сколько из для про при над под это был была были было "
           "такой такая такое такие только тоже также ещё еще уже вот кстати вернёмся вернемся нашей теме".split())


def _content_words(text):
    return [w.lower() for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text) if w.lower() not in STOP]


def expand_terms(text, st):
    """Термины пользователя → их значения прямо в поисковом запросе («гарпунщик» → «Квикега»)."""
    for t in st["terms"]:
        root = t["term"].lower()[:max(4, len(t["term"]) - 2)]
        meaning = re.sub(r"^(именно|это|то есть)\s+", "", t["meaning"].strip(), flags=re.I)
        text = re.sub(r"\b" + re.escape(root) + r"\w*", meaning, text, flags=re.I)
    return text


def condense(message, st, history, model):
    """Самостоятельный поисковый запрос. Переписываем только реплики, которые без истории непонятны
    (местоимения, «А Стабб?»), и проверяем, что модель не подменила вопрос: все значимые слова исходного
    вопроса должны остаться. Иначе — исходный вопрос + предыдущий вопрос пользователя как контекст."""
    expanded = expand_terms(message, st)
    prev_q = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    words = _content_words(message)
    follow_up = bool(ANAPHORA_RE.search(message)) or len(words) <= 1 or re.match(r"^\s*(а|и|ну)\s", message, re.I)
    if not prev_q or not follow_up:
        return expanded
    # сначала детерминированные правила — маленькая модель при переписывании присочиняет имена и детали
    prev = expand_terms(prev_q, st)
    names_prev, names_now = _names(prev), _names(expanded)
    if names_now and names_prev and len(words) <= 2:          # «А Стабб?» → «Кто такой Стабб?»
        return re.sub(re.escape(names_prev[-1]), names_now[-1], prev)
    recent = [expand_terms(m["content"], st) for m in history if m["role"] == "user"][-3:]
    last = next((n for t in reversed(recent) for n in reversed(_names(t))), None) or \
        next(iter(reversed(_names((st.get("goal") or {}).get("text") or ""))), None)
    if ANAPHORA_RE.search(expanded) and last:                 # «Какому идолу он…» → «Какому идолу Квикег…»
        return ANAPHORA_RE.sub(last, expanded, count=1)
    try:
        res = llm.chat(model, [{"role": "system", "content": CONDENSE_PROMPT}, {"role": "user", "content":
                       "Цель диалога: {}\nПредыдущий вопрос пользователя: {}\n\nПоследний вопрос: {}".format(
                           (st.get("goal") or {}).get("text") or "—", expand_terms(prev_q, st), expanded)}],
                       temperature=0.0, max_tokens=120)
        q = res["text"].strip().strip('"«»').split("\n")[0].strip()
    except llm.LlmError:
        q = ""
    # модель — только когда правила не нашли имени; её вариант проверяем: все слова исходного вопроса на месте,
    # один вопрос, не длиннее исходного больше чем на 30 символов, без новых имён, которых нет в диалоге
    known = " ".join(recent + [prev, (st.get("goal") or {}).get("text") or ""]).lower()
    kept = all(any(w[:5] in c for c in _content_words(q)) for w in _content_words(expanded))
    single = q.count("?") <= 1 and len(q) <= len(expanded) + 30
    no_new_names = all(n.lower()[:5] in known or n.lower()[:5] in expanded.lower() for n in _names(q))
    if q and 5 <= len(q) <= 300 and kept and single and no_new_names and q.strip(" ?").lower() != expanded.strip(" ?").lower():
        return q
    return "{} {}".format(prev, expanded)


def _names(text):
    """Имена собственные: слова с заглавной буквы, кроме первого слова предложения."""
    out = []
    for sent in re.split(r"[.!?]\s*", text):
        toks = re.findall(r"[A-Za-zА-Яа-яЁё’'-]+", sent)
        out += [t for t in toks[1:] if t[0].isupper() and len(t) > 2]
    return out


def _dialog(history):
    """Последние HISTORY_TURNS обменов репликами для модели (ответы ассистента укорочены)."""
    out = []
    for m in history[-2 * HISTORY_TURNS:]:
        content = m["content"] if m["role"] == "user" else m["content"][:400]
        out.append({"role": m["role"], "content": content})
    return out


# ═════════════════════════════ сводка по цели ═════════════════════════════

SUMMARY_PROMPT = (
    "Подведи итог диалога о романе «Моби Дик» с точки зрения цели пользователя. Используй ТОЛЬКО подтверждённые ответы "
    "ниже, ничего не добавляй. По-русски, 3–7 пунктов, в каждом пункте ссылки на источники в квадратных скобках как в "
    "ответах, например [Chapter 12]. В конце одной строкой — что ещё осталось выяснить для цели."
)


def summarize(chat, st, model):
    facts, sources = [], {}
    for m in chat["messages"]:
        meta = m.get("meta") or {}
        if m["role"] == "assistant" and meta.get("status") in ("answered", "quotes") and meta.get("sources"):
            secs = sorted({s["section"].split(".")[0] for s in meta["sources"]})
            facts.append("- {} [{}]".format(m["content"][:300].replace("\n", " "), "; ".join(secs)))
            for s in meta["sources"]:
                sources[s["chunk_id"]] = s
    if not facts:
        return {"status": "unknown", "text": "Подводить пока нечего: в диалоге ещё нет подтверждённых ответов с источниками.",
                "sources": []}
    res = llm.chat(model, [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content":
                   "Цель: {}\nОграничения: {}\n\nПодтверждённые ответы:\n{}".format(
                       (st.get("goal") or {}).get("text") or "не задана",
                       "; ".join(x["text"] for x in st["constraints"]) or "—", "\n".join(facts))}],
                   temperature=0.1, max_tokens=600)
    return {"status": "summary", "text": res["text"].strip(), "sources": sorted(sources.values(), key=lambda s: s["chunk_id"]),
            "facts_used": len(facts)}


# ═════════════════════════════ один ход диалога ═════════════════════════════

def send(cid, message, model, params=None, gate_min=None, judge_model=None):
    t0 = time.time()
    chat = get_chat(cid)
    if not chat:
        raise ValueError("Чат не найден")
    message = message.strip()
    history = [{"role": m["role"], "content": m["content"]} for m in chat["messages"]]
    st, changes, kind = update_state(chat["state"], message, model)
    meta = {"kind": kind, "state_changes": changes, "goal": (st.get("goal") or {}).get("text"),
            "chapter_range": st.get("chapter_range"), "turn": st["turns"]}

    if kind == "setup":
        parts = []
        if changes.get("goal"):
            parts.append("цель диалога: «{}»".format(changes["goal"]))
        for label, key in (("уточнение", "clarified"), ("ограничение", "constraints"), ("термин", "terms")):
            for x in changes.get(key, []):
                parts.append("{}: {}".format(label, x))
        if "chapter_range" in changes:
            parts.append("поиск: " + ("главы {}–{}".format(*changes["chapter_range"]) if changes["chapter_range"] else "все главы"))
        reply = ("Записал в память задачи — " + "; ".join(parts) + ". Задавайте вопрос." if parts
                 else "Понял. Задайте вопрос о книге — отвечу по тексту с источниками.")
        meta.update(status="setup", sources=[], sources_note="служебная реплика: уточнение задачи, поиск по книге не нужен")

    elif kind == "summary":
        s = summarize(chat, st, model)
        reply = s["text"]
        meta.update(status=s["status"], sources=s["sources"], facts_used=s.get("facts_used", 0),
                    sources_note=None if s["sources"] else "источников нет — в диалоге ещё нет подтверждённых ответов")

    else:
        query = condense(message, st, history, model)
        a = grounded.answer(message, model, params, gate_min if gate_min is not None else grounded.GATE_MIN,
                            judge_model or model, search_query=query,
                            chapter_range=tuple(st["chapter_range"]) if st.get("chapter_range") else None,
                            dialog=_dialog(history), memory=memory_block(st))
        reply = a["answer"]
        if a["status"] == "quotes":
            reply += "\n" + "\n".join("«{}» [{}]".format(q["quote"], q["source"]) for q in a["quotes"])
        checked = [{"chunk_id": c["chunk_id"], "section": c["section"], "rel": c.get("rel")} for c in a["context"]]
        meta.update(status=a["status"], by=a.get("by"), search_query=query, sources=a["sources"], quotes=a["quotes"],
                    rejected=len(a["rejected"]), best_rel=a["best_rel"], judge_support=a.get("judge_support"),
                    unverified_answer=a.get("unverified_answer"), clarify=a.get("clarify"), clarify_hints=a.get("clarify_hints"),
                    checked=checked, context_chapters=sorted({n for c in a["context"] for n in c["chapters"]}),
                    sources_note=(
                        "источники найдены, но ответ модели ими не подтверждён — показаны отрывки, на которые она ссылалась"
                        if a["sources"] and a["status"] == "unknown" else None if a["sources"] else
                        "подтверждённых источников нет — проверено отрывков: {}".format(len(checked)) if checked
                        else "в книге (в пределах ограничений) не найдено ни одного подходящего отрывка"))

    meta["latency_ms"] = round((time.time() - t0) * 1000)
    title = None
    if chat["title"] == "Новый чат":
        title = ((st.get("goal") or {}).get("text") or message)[:60]
    _save(cid, st, message, reply, meta, title)
    return {"reply": reply, "meta": meta, "state": st}
