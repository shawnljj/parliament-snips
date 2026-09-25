"""Verify the server's error path returns a 500 rather than an empty reply.

The old behaviour: an exception in a handler printed a traceback and closed the connection with NO
response. curl reported exit 52 and a browser showed a network error, making a code bug look like a
server outage. This forces the same class of failure and asserts a real status code comes back.

Method: temporarily point the DATABASE at something that will break a query. Done by monkeypatching
the module in-process (no file edits), so the live server is untouched.
"""
import json
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, '/Users/shawnlin/parsnips/rag')
import read_server as RS
from http.server import ThreadingHTTPServer

PORT = 8455

# a database whose summary tables are absent -> render_index() raises OperationalError,
# the exact class of failure that produced the empty reply.
import os
import sqlite3
import tempfile

tmp = os.path.join(tempfile.mkdtemp(), 'broken.db')
b = sqlite3.connect(tmp)
b.execute("CREATE TABLE summary_item_sitting(item_id TEXT, date TEXT)")
b.execute("CREATE TABLE summary_item(item_id TEXT PRIMARY KEY, title TEXT)")
b.execute("CREATE TABLE report(report_id TEXT PRIMARY KEY, date TEXT, n_turns INT)")
b.execute("CREATE TABLE turn(key TEXT PRIMARY KEY, report_id TEXT, text TEXT)")
b.commit()
b.close()

_orig = RS.connect


def broken():
    db = sqlite3.connect(tmp)
    db.row_factory = sqlite3.Row
    return db


RS.connect = broken

srv = ThreadingHTTPServer(('127.0.0.1', PORT), RS.Handler)
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()

print("=== forcing a handler exception ===")
try:
    r = urllib.request.urlopen(f'http://127.0.0.1:{PORT}/', timeout=20)
    code = r.status
    body = r.read().decode()
    print(f"  status: {code}")
    print(f"  body contains 'Internal error': {'Internal error' in body}")
    print(f"  body length: {len(body)}")
    ok = code == 500 and 'Internal error' in body
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"  status: {e.code}")
    print(f"  body contains 'Internal error': {'Internal error' in body}")
    ok = e.code == 500 and 'Internal error' in body
except Exception as e:
    print(f"  NO RESPONSE AT ALL: {type(e).__name__}: {e}")
    ok = False

print()
print("  healthy route still works:", end=' ')
try:
    r = urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health', timeout=20)
    print(f"{r.status} {r.read().decode()}")
except Exception as e:
    print(f"FAILED {e}")
    ok = False

srv.shutdown()
RS.connect = _orig
print()
print("RESULT:", "ERROR PATH RETURNS A STATUS" if ok else "ERROR PATH STILL BLIND")
sys.exit(0 if ok else 1)
