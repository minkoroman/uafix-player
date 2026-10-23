"""Відео з серверів, що не віддають його за кордон (zetvideo — озвучка UAFlix), — через Tor
з українським виходом. Локальний HTTP-сервер на 127.0.0.1: плеєр бере в нього плейлист і
шматки відео, а сервер — у відеосервера через Tor (core.abroad_get); посилання в плейлистах
переписуються на себе. Пропускає лише сервери, які застосунок визнав недоступними звідси.
"""
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urljoin, urlparse

_state = {"port": None}
_lock = threading.Lock()


def wrap(url, referer):
    """Адреса потоку через локальний сервер."""
    return (f"http://127.0.0.1:{_start()}/v?u={quote(url, safe='')}"
            f"&r={quote(referer or '', safe='')}")


def _rewrite(text, base, referer):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            line = wrap(urljoin(base, s), referer)
        elif 'URI="' in s:  # ключі, субтитри, аудіодоріжки
            line = re.sub(r'URI="([^"]+)"',
                          lambda m: f'URI="{wrap(urljoin(base, m.group(1)), referer)}"', line)
        out.append(line)
    return "\n".join(out) + "\n"


def _allowed(url):
    import core
    host = urlparse(url).hostname or ""
    blocked = [h for h, ok in core.GEO["cdn"].items() if not ok]
    return url.startswith("https://") and \
        any(host == h or host.endswith("." + h) for h in blocked)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        import core
        q = parse_qs(urlparse(self.path).query)
        url, referer = q.get("u", [""])[0], q.get("r", [""])[0]
        if not _allowed(url):
            self.send_error(403)
            return
        try:
            r = core.abroad_get(url, referer, self.headers.get("Range"))
        except Exception:
            self.send_error(502)
            return
        ctype = r.headers.get("content-type") or "application/octet-stream"
        body = r.content
        if ".m3u8" in urlparse(url).path or "mpegurl" in ctype.lower():
            body = _rewrite(r.text, url, referer).encode("utf-8")
        try:
            self.send_response(r.status_code)
            self.send_header("Content-Type", ctype)
            if r.headers.get("content-range"):
                self.send_header("Content-Range", r.headers["content-range"])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # плеєр закрив з'єднання (перемотка, вихід)


def _start():
    with _lock:
        if _state["port"] is None:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            _state["port"] = server.server_address[1]
        return _state["port"]
