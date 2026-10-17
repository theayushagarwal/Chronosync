"""A tiny local site that changes on demand, so you can watch detection work in seconds.

Run:  python scripts/demo_site.py      (serves http://localhost:8001)

  /          product page: stable text, a live clock and view counter (noise), a price
  /bump      changes the price, then redirects to /   (a REAL change)
  /flaky     different on every load                   (should never alert)
  /captcha   200 OK but a bot-check page               (should be 'blocked')
  /down      503                                       (should be 'error')
"""
import random
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

state = {"price": 19.99, "lock": threading.Lock()}


def page(body: str) -> bytes:
    return f"<!doctype html><html><head><title>Demo shop</title></head><body>{body}</body></html>".encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            views = random.randint(1000, 9999)
            self._send(200, page(f"""
              <header><h1>Aurora Desk Lamp</h1><p id="clock">Server time: {time.strftime('%H:%M:%S')}</p></header>
              <main>
                <p>A warm, dimmable desk lamp with a solid aluminium base and a three-year warranty.</p>
                <p id="price">Price: ${state['price']:.2f}</p>
                <p>Free delivery on orders over $50. Returns accepted within 30 days of purchase.</p>
                <p class="views">{views:,} views</p>
                <p><a href="/bump">Change the price (demo)</a></p>
              </main>"""))
        elif path == "/bump":
            with state["lock"]:
                state["price"] = round(state["price"] + random.choice([-3, -2, 4, 5]) + 0.5, 2)
            self._send(302, b"", extra={"Location": "/"})
        elif path == "/flaky":
            junk = " ".join("".join(random.choices(string.ascii_lowercase, k=7)) for _ in range(8))
            self._send(200, page(f"<h1>Flaky page</h1><p>{junk}</p><p>This text differs on every load.</p>"))
        elif path == "/captcha":
            self._send(200, page("<h1>Just a moment...</h1><p>Please verify you are human to continue.</p>"))
        elif path == "/down":
            self._send(503, page("<h1>Service unavailable</h1>"))
        elif path == "/robots.txt":
            self._send(200, b"User-agent: *\nAllow: /\n", "text/plain")
        else:
            self._send(404, page("<h1>Not found</h1>"))


if __name__ == "__main__":
    print("Demo site on http://localhost:8001  (visit /bump to change the price)")
    ThreadingHTTPServer(("127.0.0.1", 8001), Handler).serve_forever()
