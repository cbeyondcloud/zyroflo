"""Local review server for the video deck.

    python serve.py <root> <port>

Same as `python -m http.server`, except every response carries Cache-Control:
no-store. Without it the browser applies heuristic freshness to files that have a
Last-Modified date and no cache header, and keeps showing a slide as it was before
the last edit, which is exactly wrong for a review loop.
"""
import functools
import http.server
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "."
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8793


class NoStore(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                functools.partial(NoStore, directory=root)).serve_forever()
