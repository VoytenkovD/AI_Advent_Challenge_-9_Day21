# -*- coding: utf-8 -*-
"""HTTP-сервер Doc Index Lab: API индекса + статика web/. Только стандартная библиотека."""
import json
import mimetypes
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis
import chunking
import embed
import chat
import chat_eval
import experiment
import grounded
import indexer
import llm
import local_vs_cloud
import rag
import rerank
import store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PORT = int(os.getenv("PORT", "5210"))


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # на Windows иначе второй процесс молча займёт тот же порт
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _guard(self, fn):
        try:
            self._json(200, fn())
        except (embed.EmbedError, llm.LlmError) as e:
            self._json(503, {"error": str(e)})
        except (ValueError, KeyError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            self._json(500, {"error": "{}: {}".format(type(e).__name__, e)})

    # ───────────────────────────── GET ─────────────────────────────
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        arg = lambda k, d=None: qs.get(k, [d])[0]

        if path == "/api/overview":
            return self._guard(overview)
        if path == "/api/index/status":
            return self._guard(lambda: indexer.JOB.snapshot(int(arg("after", 0))))
        if path == "/api/chapter":
            return self._guard(lambda: chapter(int(arg("num", 1))))
        if path == "/api/chunk":
            return self._guard(lambda: chunk_detail(arg("id")))
        if path == "/api/compare":
            return self._guard(analysis.compare)
        if path == "/api/questions":
            return self._guard(lambda: {"questions": analysis.load_questions()})
        if path == "/api/llm/models":
            return self._guard(llm.catalog)
        if path == "/api/rag/questions":
            return self._guard(lambda: {"questions": rag.load_questions(), "strategy": rag.STRATEGY, "k": rag.TOP_K})
        if path == "/api/lvc/status":
            return self._guard(lambda: local_vs_cloud.JOB.snapshot(int(arg("after", 0))))
        if path == "/api/lvc/results":
            return self._guard(lambda: {"data": local_vs_cloud.load(), "models": local_vs_cloud.MODELS,
                                        "judge": local_vs_cloud.JUDGE, "runs": local_vs_cloud.RUNS})
        if path == "/api/chats":
            return self._guard(lambda: {"chats": chat.list_chats()})
        if path.startswith("/api/chats/"):
            cid = path[len("/api/chats/"):]
            def get():
                c = chat.get_chat(cid)
                if not c:
                    raise ValueError("Чат не найден")
                return {"chat": c}
            return self._guard(get)
        if path == "/api/chat-eval/status":
            return self._guard(lambda: chat_eval.JOB.snapshot(int(arg("after", 0))))
        if path == "/api/chat-eval/results":
            return self._guard(lambda: {"result": chat_eval.load_results(arg("model") or llm.DEFAULT_MODEL),
                                        "scenarios": chat_eval.load_scenarios()})
        if path == "/api/cite/config":
            return self._guard(lambda: {"gate_min": grounded.GATE_MIN, "support_min": grounded.SUPPORT_MIN,
                                        "fuzzy_min": grounded.FUZZY_MIN, "params": rerank.IMPROVED})
        if path == "/api/cite/eval/status":
            return self._guard(lambda: grounded.EVAL_JOB.snapshot(int(arg("after", 0))))
        if path == "/api/cite/results":
            model = arg("model")
            return self._guard(lambda: {"result": grounded.load_results(model)} if model else {"runs": grounded.list_results()})
        if path == "/api/rerank/config":
            return self._guard(lambda: {"base": rerank.BASE, "improved": rerank.IMPROVED, "limits": rerank.LIMITS,
                                        "query_modes": rerank.QUERY_MODES, "reranker": rerank.status()})
        if path == "/api/experiment/status":
            return self._guard(lambda: experiment.JOB.snapshot(int(arg("after", 0))))
        if path == "/api/experiment/report":
            return self._guard(lambda: {"report": experiment.load_report()})
        if path == "/api/rag/eval/status":
            return self._guard(lambda: rag.EVAL_JOB.snapshot(int(arg("after", 0))))
        if path == "/api/rag/results":
            model = arg("model")
            return self._guard(lambda: {"result": rag.load_results(model)} if model else {"runs": rag.list_results()})

        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")
        f = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(f).startswith(str(WEB_DIR.resolve())):
            return self._json(403, {"error": "forbidden"})
        self._file(f)

    def _file(self, f):
        if not f.is_file():
            return self._json(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    # ───────────────────────────── POST ─────────────────────────────
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/index":
            data = self._body()
            started = indexer.start(rebuild=bool(data.get("rebuild")))
            return self._json(200 if started else 409, {"started": started, "state": indexer.JOB.state})
        if path == "/api/search":
            data = self._body()
            q = (data.get("q") or "").strip()
            if not q:
                return self._json(400, {"error": "Пустой запрос"})
            return self._guard(lambda: analysis.search(q, int(data.get("k", 5)), data.get("strategies")))
        if path == "/api/ask":
            data = self._body()
            q = (data.get("q") or "").strip()
            if not q:
                return self._json(400, {"error": "Пустой вопрос"})
            modes = [m for m in (data.get("modes") or rag.MODES) if m in rag.MODES]
            return self._guard(lambda: rag.ask(q, data.get("model") or llm.DEFAULT_MODEL, int(data.get("k", rag.TOP_K)),
                                               params=data.get("params"), modes=modes))
        if path == "/api/lvc/run":
            data = self._body()
            return self._guard(lambda: {"started": local_vs_cloud.start(data.get("models"), data.get("runs") or local_vs_cloud.RUNS,
                                                                        bool(data.get("fresh"))), "state": local_vs_cloud.JOB.state})
        if path == "/api/chats":
            data = self._body()
            return self._guard(lambda: {"chat": chat.create_chat(data.get("title"))})
        if path.startswith("/api/chats/") and path.endswith("/send"):
            cid = path[len("/api/chats/"):-len("/send")]
            data = self._body()
            msg = (data.get("message") or "").strip()
            if not msg:
                return self._json(400, {"error": "Пустое сообщение"})
            return self._guard(lambda: chat.send(cid, msg, data.get("model") or llm.DEFAULT_MODEL, data.get("params"),
                                                 data.get("gate_min"), data.get("judge")))
        if path.startswith("/api/chats/") and path.endswith("/state"):
            cid = path[len("/api/chats/"):-len("/state")]
            data = self._body()
            def go():
                st = data.get("state") or chat.empty_state()
                chat.set_state(cid, st)
                return {"state": st}
            return self._guard(go)
        if path == "/api/chat-eval":
            data = self._body()
            return self._guard(lambda: {"started": chat_eval.start(data.get("model") or llm.DEFAULT_MODEL),
                                        "state": chat_eval.JOB.state})
        if path == "/api/cite/ask":
            data = self._body()
            q = (data.get("q") or "").strip()
            if not q:
                return self._json(400, {"error": "Пустой вопрос"})
            return self._guard(lambda: grounded.answer(q, data.get("model") or llm.DEFAULT_MODEL, data.get("params"),
                                                       float(data.get("gate_min", grounded.GATE_MIN)), data.get("judge")))
        if path == "/api/cite/eval":
            data = self._body()
            return self._guard(lambda: {"started": grounded.start_eval(
                data.get("model") or llm.DEFAULT_MODEL, data.get("judge"), data.get("params"),
                float(data.get("gate_min", grounded.GATE_MIN))), "state": grounded.EVAL_JOB.state})
        if path == "/api/rerank/funnel":
            data = self._body()
            q = (data.get("q") or "").strip()
            if not q:
                return self._json(400, {"error": "Пустой вопрос"})
            return self._guard(lambda: rerank.funnel(q, rerank.params(data.get("params")), data.get("model") or llm.DEFAULT_MODEL))
        if path == "/api/experiment":
            data = self._body()
            return self._guard(lambda: {"started": experiment.start(data.get("model") or llm.DEFAULT_MODEL),
                                        "state": experiment.JOB.state})
        if path == "/api/rag/eval":
            data = self._body()
            def go():
                started = rag.start_eval(data.get("model") or llm.DEFAULT_MODEL, data.get("judge"), bool(data.get("force")))
                return {"started": started, "state": rag.EVAL_JOB.state}
            return self._guard(go)
        self._json(404, {"error": "unknown endpoint"})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/chats/"):
            cid = path[len("/api/chats/"):]
            return self._guard(lambda: (chat.delete_chat(cid), {"deleted": cid})[1])
        self._json(404, {"error": "unknown endpoint"})

    def log_message(self, fmt, *args):
        pass


# ───────────────────────────── данные для вкладок ─────────────────────────────

def overview():
    store.init()
    doc = store.get_document()
    variants = {}
    for s in indexer.STRATEGIES:
        v = store.get_variant(s)
        variants[s] = dict(chunking.describe(s), built=bool(v), **({
            "chunks": v["chunks"], "tokens": v["tokens"], "model_tokens": v["model_tokens"], "dim": v["dim"],
            "seconds": v["seconds"], "built_at": v["built_at"], "model": v["model"], "fingerprint": v["fingerprint"],
        } if v else {}))
    sample = None
    built = [s for s in indexer.STRATEGIES if variants[s]["built"]]
    if built:
        chunks = store.list_chunks(built[-1])
        if chunks:
            c = store.get_chunk(chunks[min(12, len(chunks) - 1)]["chunk_id"], with_vector=True)
            c["vector_head"] = [round(x, 4) for x in c.pop("vector")[:12]]
            sample = c
    return {
        "document": doc and {k: doc[k] for k in ("source", "title", "author", "url", "sha256")} | {
            "chars": len(doc["text"]), "pages": round(len(doc["text"]) / 2500),
            "chapters": [{k: c[k] for k in ("num", "label", "start", "end")} for c in doc["chapters"]]},
        "max_chapters": indexer.MAX_CHAPTERS,
        "cpt": store.get_meta("cpt"),
        "ollama": embed.status(),
        "variants": variants,
        "sample": sample,
        "job": {"state": indexer.JOB.state},
    }


def chapter(num):
    doc = store.get_document()
    if not doc:
        raise ValueError("Индекс ещё не построен")
    ch = next((c for c in doc["chapters"] if c["num"] == num), None)
    if not ch:
        raise ValueError("Нет главы {}".format(num))
    out = {"chapter": ch, "text": doc["text"][ch["start"]:ch["end"]], "offset": ch["start"], "strategies": {}}
    for s in indexer.STRATEGIES:
        out["strategies"][s] = [
            {k: c[k] for k in ("chunk_id", "seq", "start", "body_start", "end", "tokens", "overlap_tokens",
                               "section", "chapters", "starts_mid_sentence", "ends_mid_sentence")}
            for c in store.list_chunks(s, chapter=num)]
    return out


def chunk_detail(chunk_id):
    c = store.get_chunk(chunk_id, with_vector=True)
    if not c:
        raise ValueError("Чанк не найден: {}".format(chunk_id))
    v = c.pop("vector")
    c["vector_head"] = [round(x, 4) for x in v[:16]]
    c["vector_norm"] = round(sum(x * x for x in v) ** 0.5, 4)
    return c


def main():
    store.init()
    srv = Server(("127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:{}".format(PORT)
    print("Doc Index Lab: {}".format(url), flush=True)
    if not os.getenv("NO_BROWSER"):
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
