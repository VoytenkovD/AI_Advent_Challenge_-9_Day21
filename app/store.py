# -*- coding: utf-8 -*-
"""Индекс в SQLite: книга, чанки с метаданными и векторами (float32 BLOB), состояние вариантов."""
import json
import sqlite3
import threading
import time
from array import array
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "index.db"
_lock = threading.Lock()
_cache = {}  # strategy -> (version, [chunks с векторами]) — для поиска в памяти

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents (
    source TEXT PRIMARY KEY, title TEXT, author TEXT, url TEXT,
    text TEXT, chapters TEXT, sha256 TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL, seq INTEGER NOT NULL,
    source TEXT, title TEXT, author TEXT, section TEXT, chapters TEXT,
    start INTEGER, body_start INTEGER, "end" INTEGER,
    chars INTEGER, tokens INTEGER, overlap_tokens INTEGER,
    starts_mid_sentence INTEGER, ends_mid_sentence INTEGER,
    text TEXT, dim INTEGER, vector BLOB
);
CREATE INDEX IF NOT EXISTS ix_chunks_strategy ON chunks(strategy, seq);
CREATE TABLE IF NOT EXISTS variants (
    strategy TEXT PRIMARY KEY, fingerprint TEXT, model TEXT, params TEXT,
    chunks INTEGER, tokens INTEGER, model_tokens INTEGER, dim INTEGER,
    seconds REAL, built_at REAL
);
"""

CHUNK_FIELDS = ["chunk_id", "strategy", "seq", "source", "title", "author", "section", "chapters", "start",
                "body_start", "end", "chars", "tokens", "overlap_tokens", "starts_mid_sentence",
                "ends_mid_sentence", "text", "dim"]


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init():
    with closing(connect()) as con:
        con.executescript(SCHEMA)
        con.commit()


def get_meta(key, default=None):
    with closing(connect()) as con:
        r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_meta(key, value):
    with _lock, closing(connect()) as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, json.dumps(value)))
        con.commit()


def save_document(book):
    with _lock, closing(connect()) as con:
        con.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?)", (
            book["source"], book["title"], book["author"], book["url"], book["text"],
            json.dumps(book["chapters"]), book["sha256"]))
        con.commit()


def get_document():
    with closing(connect()) as con:
        r = con.execute("SELECT * FROM documents LIMIT 1").fetchone()
    if not r:
        return None
    d = dict(r)
    d["chapters"] = json.loads(d["chapters"])
    return d


def get_variant(strategy):
    with closing(connect()) as con:
        r = con.execute("SELECT * FROM variants WHERE strategy=?", (strategy,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["params"] = json.loads(d["params"] or "{}")
    return d


def replace_chunks(strategy, chunks, vectors, variant):
    """Атомарно заменяет все чанки стратегии и запись о варианте."""
    with _lock, closing(connect()) as con:
        con.execute("DELETE FROM chunks WHERE strategy=?", (strategy,))
        rows = []
        for c, v in zip(chunks, vectors):
            rows.append(tuple(
                json.dumps(c["chapters"]) if f == "chapters" else
                int(c[f]) if f in ("starts_mid_sentence", "ends_mid_sentence") else
                len(v) if f == "dim" else c[f]
                for f in CHUNK_FIELDS) + (array("f", v).tobytes(),))
        con.executemany('INSERT INTO chunks ({}, vector) VALUES ({})'.format(
            ", ".join('"end"' if f == "end" else f for f in CHUNK_FIELDS), ", ".join("?" * (len(CHUNK_FIELDS) + 1))), rows)
        con.execute("INSERT OR REPLACE INTO variants VALUES (?,?,?,?,?,?,?,?,?,?)", (
            strategy, variant["fingerprint"], variant["model"], json.dumps(variant["params"]),
            len(chunks), variant["tokens"], variant["model_tokens"], variant["dim"], variant["seconds"], time.time()))
        con.commit()
    _cache.pop(strategy, None)


def clear():
    with _lock, closing(connect()) as con:
        con.execute("DELETE FROM chunks")
        con.execute("DELETE FROM variants")
        con.commit()
    _cache.clear()


def _row(r, with_vector=False):
    d = {k: r[k] for k in r.keys() if k != "vector"}
    d["chapters"] = json.loads(d["chapters"])
    d["starts_mid_sentence"] = bool(d["starts_mid_sentence"])
    d["ends_mid_sentence"] = bool(d["ends_mid_sentence"])
    if with_vector:
        d["vector"] = array("f", r["vector"]).tolist()
    return d


def list_chunks(strategy, with_vector=False, chapter=None):
    with closing(connect()) as con:
        rows = con.execute("SELECT * FROM chunks WHERE strategy=? ORDER BY seq", (strategy,)).fetchall()
    out = [_row(r, with_vector) for r in rows]
    if chapter is not None:
        out = [c for c in out if chapter in c["chapters"]]
    return out


def get_chunk(chunk_id, with_vector=False):
    with closing(connect()) as con:
        r = con.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    return _row(r, with_vector) if r else None


def vectors(strategy):
    """Чанки стратегии с векторами, закэшированные в памяти до следующей переиндексации."""
    v = get_variant(strategy)
    key = v and v["built_at"]
    cached = _cache.get(strategy)
    if cached and cached[0] == key:
        return cached[1]
    data = list_chunks(strategy, with_vector=True) if v else []
    _cache[strategy] = (key, data)
    return data
