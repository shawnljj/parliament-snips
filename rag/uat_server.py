#!/usr/bin/env python3
"""UAT server for the PARSNIPS RAG answer stage.

Why a server rather than a static page: the answer stage is Python (SQLite + a 157 MB vector
store) plus a local model call, so a self-contained HTML file cannot run it. This is the smallest
thing that serves the real pipeline to a phone: stdlib HTTP only, no framework, no build step.

  GET  /                -> the UAT page
  GET  /ask?q=...       -> the full answer result as JSON
  POST /verdict         -> record the owner's judgement for a question (JSONL append)

Run:
  cd ~/parsnips && <python-with-numpy> rag/uat_server.py --port 8441
Requires ollama running with nomic-embed-text (ranker) and the answer model.
"""
import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DB = os.path.join(HERE, "..", "pipeline", "hansard.db")
PAGE = os.path.join(HERE, "uat.html")
VERDICTS = os.path.join(HERE, "..", "pipeline", "uat_verdicts.jsonl")

_lock = threading.Lock()          # one question at a time: the ranker holds a big array
_db = None
_answer = None


def get_db():
    global _db
    if _db is None:
        _db = sqlite3.connect(DB, check_same_thread=False)
        _db.row_factory = sqlite3.Row
    return _db


def get_answer():
    global _answer
    if _answer is None:
        import answer as A
        _answer = A
    return _answer


def clean(res):
    """Strip the private/undersized fields and keep everything the owner needs to judge.

    Deliberately NOT a trimmed-down result: UAT is where a wrong answer must be diagnosable, so
    the payload carries what the system DID (pool size, constraints, passages offered, whether a
    passage was flagged as superseded) and not only what it said.
    """
    out = {
        "question": res.get("question"),
        "refused": res.get("refused"),
        "source": res.get("source"),
        "reason": res.get("reason"),
        "quotes": [],
        "related": [],
        "n_chunks": res.get("n_chunks"),
        "context_tokens": res.get("context_tokens"),
        "attempts": res.get("attempts"),
        "retrieval": res.get("retrieval"),
    }
    for q in (res.get("quotes") or []):
        out["quotes"].append({
            "quote": q.get("quote"),
            "citation": q.get("citation"),
            "chunk_id": q.get("chunk_id"),
            "speaker": q.get("speaker"),
            "cited_ok": q.get("cited_ok"),
        })
    for s in (res.get("related") or []):
        out["related"].append({
            "title": s.get("title"),
            "real_title": s.get("real_title"),
            "date": s.get("date"),
            "report_id": s.get("report_id"),
            "chunk_id": s.get("chunk_id"),
            "grounded": s.get("grounded"),
        })
    # what the model was shown -- so a bad answer can be attributed to retrieval vs generation
    passages = []
    for c in (res.get("_chunks") or []):
        passages.append({"id": c.get("id"), "turn": str(c.get("id") or "").rsplit("#c", 1)[0]})
    if res.get("_context_rows"):
        for p, r in zip(passages, res["_context_rows"]):
            t = r.get("cite_text") or ""
            p["excerpt"] = t[:400] + ("..." if len(t) > 400 else "")
    out["passages"] = passages
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "parsnips-uat"

    def log_message(self, format, *args):     # quieter, but keep errors
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                with open(PAGE, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, {"error": f"page missing: {e}"})
            return
        if u.path == "/health":
            self._send(200, {"ok": True, "db": os.path.exists(DB)})
            return
        if u.path == "/ask":
            q = (parse_qs(u.query).get("q") or [""])[0].strip()
            k = int((parse_qs(u.query).get("k") or ["10"])[0])
            if not q:
                self._send(400, {"error": "empty question"})
                return
            t0 = time.time()
            try:
                with _lock:
                    res = get_answer().answer(q, get_db(), k=k, ranker="vector")
                payload = clean(res)
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": f"{type(e).__name__}: {e}"})
                return
            payload["elapsed_s"] = round(time.time() - t0, 1)
            self._send(200, payload)
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/verdict":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError) as e:
            self._send(400, {"error": f"bad body: {e}"})
            return
        body["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(VERDICTS, "a") as fh:
            fh.write(json.dumps(body) + "\n")
        self._send(200, {"saved": True, "file": os.path.basename(VERDICTS)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8441)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--warm", action="store_true",
                    help="load the vector ranker before serving (first query is ~20s otherwise)")
    a = ap.parse_args()

    if a.warm:
        sys.stderr.write("warming the vector ranker...\n")
        get_answer().get_vr(get_db())
        sys.stderr.write("ready\n")

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    sys.stderr.write(f"UAT on http://{a.host}:{a.port}/   (db={DB})\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
