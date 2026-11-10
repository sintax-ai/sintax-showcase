"""A tiny stand-in for Supabase's inquiries API, for tests only. All data is FICTIONAL.

It keeps rows in memory, honours `resolution=ignore-duplicates` on
(business_id, source_id) the way the real database does, and can be told to
fail the next requests with chosen status codes.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class FakeSupabase:
    def __init__(self, secret="sb_secret_FICTIONAL"):
        self.secret = secret
        self.rows = []  # dicts as stored
        self.calls = []
        self.audio = {}
        self.call_fail_with = []
        self.upload_fail_with = []
        self.requests = []  # (method, path, headers, body)
        self.fail_with = []  # status codes to return for the next POSTs
        self.last_prefer = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorised(self):
                if self.headers.get("apikey") != fake.secret:
                    self._send(401, {"message": "Invalid API key"})
                    return False
                return True

            def do_GET(self):
                url = urlparse(self.path)
                fake.requests.append(("GET", self.path, dict(self.headers), None))
                if not self._authorised():
                    return
                q = parse_qs(url.query)
                business = q.get("business_id", ["eq."])[0].removeprefix("eq.")
                self._send(200, [r for r in fake.rows if r["business_id"] == business])

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                is_audio = self.path.startswith("/storage/v1/object/")
                body = raw if is_audio else json.loads(raw)
                fake.requests.append(("POST", self.path, dict(self.headers), body))
                if is_audio:
                    if not self._authorised():
                        return
                    if fake.upload_fail_with:
                        return self._send(
                            fake.upload_fail_with.pop(0), {"code": "failure"}
                        )
                    fake.audio[self.path] = body
                    return self._send(200, {"Key": self.path})
                if self.path.startswith("/rest/v1/calls"):
                    if not self._authorised():
                        return
                    if fake.call_fail_with:
                        return self._send(fake.call_fail_with.pop(0), {"code": "23503"})
                    if not any(r["id"] == body["id"] for r in fake.calls):
                        fake.calls.append(dict(body))
                    return self._send(201, [])
                if not self._authorised():
                    return
                if fake.fail_with:
                    status = fake.fail_with.pop(0)
                    return self._send(
                        status,
                        {
                            "code": "23514" if status == 400 else "XX000",
                            "message": "FICTIONAL failure",
                        },
                    )
                fake.last_prefer = self.headers.get("Prefer", "")
                key = (body["business_id"], body["source_id"])
                if any((r["business_id"], r["source_id"]) == key for r in fake.rows):
                    if "ignore-duplicates" in fake.last_prefer:
                        return self._send(201, [])
                    return self._send(
                        409, {"code": "23505", "message": "duplicate key"}
                    )
                fake.rows.append(dict(body))
                self._send(201, [body])

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(("PATCH", self.path, dict(self.headers), body))
                if not self._authorised():
                    return
                if fake.fail_with:
                    status = fake.fail_with.pop(0)
                    return self._send(
                        status, {"code": "XX000", "message": "FICTIONAL failure"}
                    )
                q = parse_qs(urlparse(self.path).query)
                business = q.get("business_id", ["eq."])[0].removeprefix("eq.")
                if self.path.startswith("/rest/v1/calls"):
                    call_id = q["id"][0].removeprefix("eq.")
                    for row in fake.calls:
                        if (row["business_id"], row["id"]) == (business, call_id):
                            if (
                                q.get("recording_status") == ["neq.available"]
                                and row.get("recording_status") == "available"
                            ):
                                continue
                            row.update(body)
                    return self._send(200, [])
                source = q.get("source_id", ["eq."])[0].removeprefix("eq.")
                only_empty = q.get("transcript", [""])[0] == "is.null"
                for r in fake.rows:
                    if (r["business_id"], r["source_id"]) != (business, source):
                        continue
                    if q.get("call_id") == ["is.null"] and r.get("call_id") is not None:
                        continue
                    if only_empty and r.get("transcript") is not None:
                        continue  # PostgREST filter: the row is simply not matched
                    r.update(body)
                # return=minimal: no body
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    def patches(self):
        return [r for r in self.requests if r[0] == "PATCH"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
