"""Обхід регіональних обмежень: сторінки з плеєром (кілька КБ) — через мережу Tor із
вихідними вузлами лише в Україні; саме відео йде напряму.

- Windows / macOS: офіційний Tor Expert Bundle завантажується з torproject.org при першій
  потребі (перевірка SHA-256), запускається окремим процесом із SOCKS на 127.0.0.1;
- Android: Tor вбудовано в застосунок (бібліотека tor-android, див. tools/android_template.py),
  його запускає MainActivity за посиланням uafixupdate://tor — тут лише чекаємо на порт.

Вихідні вузли — за відбитками з Onionoo (довідник мережі Tor), без бази GeoIP.
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time

import requests

TOR_VERSION = "15.0.26"
ARCHIVE = "https://archive.torproject.org/tor-package-archive/torbrowser/{v}/{name}"
BUNDLES = {
    ("windows", "x86_64"): ("tor-expert-bundle-windows-x86_64-{v}.tar.gz",
                            "c1988c701de2f24f7d098d1d6d7f507b58f27740d097f29c246e0524d639b252"),
    ("macos", "aarch64"): ("tor-expert-bundle-macos-aarch64-{v}.tar.gz",
                           "a3febb6179152b0b4f56a0f5cc57bf5cc8ecef7db35e7c3e9d5a044c8158c26c"),
    ("macos", "x86_64"): ("tor-expert-bundle-macos-x86_64-{v}.tar.gz",
                          "e1ae4d4a734d28bb590839188511deb3cebc26fcbce9df66d01b2c452060fe95"),
}
ONIONOO = ("https://onionoo.torproject.org/details?search=country:ua%20flag:exit"
           "&running=true&fields=fingerprint")
ANDROID_PORT = 19050  # той самий порт пише в torrc MainActivity

_state = {"proxy": None, "proc": None, "error": "", "exits": []}
_lock = threading.Lock()


def _base_dir():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~/.uafix")
    path = os.path.join(base, "tor")
    os.makedirs(path, exist_ok=True)
    return path


def _platform_key():
    if sys.platform == "win32":
        return "windows", "x86_64"
    if sys.platform == "darwin":
        return "macos", "aarch64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    return None


def last_error():
    return _state["error"]


def ua_exits():
    """Відбитки вихідних вузлів Tor в Україні (кеш на годину)."""
    if _state["exits"] and time.time() - _state.get("exits_at", 0) < 3600:
        return _state["exits"]
    r = requests.get(ONIONOO, timeout=20)
    r.raise_for_status()
    fps = [x["fingerprint"] for x in r.json().get("relays", []) if x.get("fingerprint")]
    if fps:
        _state["exits"], _state["exits_at"] = fps, time.time()
    return fps


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ensure_binary():
    """Шлях до tor (завантажити й розпакувати Expert Bundle, якщо ще немає)."""
    key = _platform_key()
    if key not in BUNDLES:
        raise RuntimeError("Tor для цієї системи не підтримується")
    name, sha = BUNDLES[key]
    name = name.format(v=TOR_VERSION)
    root = os.path.join(_base_dir(), TOR_VERSION)
    exe = os.path.join(root, "tor", "tor.exe" if key[0] == "windows" else "tor")
    if os.path.isfile(exe):
        return exe
    import hashlib
    os.makedirs(root, exist_ok=True)
    tmp = os.path.join(root, name + ".part")
    digest = hashlib.sha256()
    with requests.get(ARCHIVE.format(v=TOR_VERSION, name=name), stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
                digest.update(chunk)
    if digest.hexdigest() != sha:
        os.remove(tmp)
        raise RuntimeError("Завантажений Tor не пройшов перевірку")
    with tarfile.open(tmp, "r:gz") as tar:
        for m in tar.getmembers():  # лише звичайні файли й теки, без виходу за межі теки
            if m.name.startswith(("/", "..")) or ".." in m.name.split("/") or \
                    not (m.isfile() or m.isdir()):
                raise RuntimeError("Неочікуваний вміст архіву Tor")
        tar.extractall(root)
    os.remove(tmp)
    if key[0] != "windows":
        os.chmod(exe, 0o755)
    if key[0] == "macos":
        # бінарники в архіві не підписані — на Apple Silicon macOS їх одразу зупиняє;
        # локальний (ad-hoc) підпис це дозволяє
        folder = os.path.dirname(exe)
        for f in os.listdir(folder):
            if f == "tor" or f.endswith(".dylib"):
                subprocess.run(["/usr/bin/codesign", "-s", "-", "--force",
                                os.path.join(folder, f)], capture_output=True)
    return exe


def _clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("FLET_")}


def _start_desktop():
    exe = _ensure_binary()
    exits = ua_exits()
    if not exits:
        raise RuntimeError("Немає доступних вихідних вузлів")
    port = _free_port()
    data = os.path.join(_base_dir(), "data")
    os.makedirs(data, exist_ok=True)
    torrc = os.path.join(_base_dir(), "torrc")
    with open(torrc, "w", encoding="utf-8") as f:
        f.write(f"SocksPort 127.0.0.1:{port}\nDataDirectory {data}\n"
                f"ExitNodes {','.join('$' + x for x in exits)}\nStrictNodes 1\n"
                "ClientOnly 1\nAvoidDiskWrites 1\n")
    kw = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
          "env": _clean_env(), "cwd": os.path.dirname(exe)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen([exe, "-f", torrc], **kw)
    _state["proc"] = proc
    import atexit
    atexit.register(stop)  # Tor не лишається працювати після закриття застосунку
    deadline = time.time() + 120
    for line in proc.stdout:
        if "Bootstrapped 100%" in line:
            threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
            return f"socks5h://127.0.0.1:{port}"
        if time.time() > deadline:
            break
    proc.kill()
    raise RuntimeError("Tor не підключився")


def _start_android(launch):
    """launch(fingerprints) — попросити MainActivity запустити вбудований Tor."""
    if not _port_open(ANDROID_PORT):
        launch(ua_exits())
    deadline = time.time() + 120
    while time.time() < deadline:
        if _port_open(ANDROID_PORT):
            # порт відкрито раніше, ніж Tor збудує ланцюжок, — перевіряємо реальним запитом
            try:
                requests.get("https://check.torproject.org/api/ip", timeout=20, proxies={
                    "http": f"socks5h://127.0.0.1:{ANDROID_PORT}",
                    "https": f"socks5h://127.0.0.1:{ANDROID_PORT}"})
                return f"socks5h://127.0.0.1:{ANDROID_PORT}"
            except Exception:
                pass
        time.sleep(2)
    raise RuntimeError("Tor не підключився")


def proxy(android_launch=None):
    """Адреса SOCKS-проксі Tor (запуск при першому виклику; блокує — з потоку) або None."""
    with _lock:
        if _state["proxy"]:
            proc = _state["proc"]
            if proc is None or proc.poll() is None:
                return _state["proxy"]
            _state["proxy"] = None  # процес Tor завершився — запустимо знову
        try:
            _state["proxy"] = _start_android(android_launch) if android_launch \
                else _start_desktop()
            _state["error"] = ""
        except Exception as ex:
            _state["error"] = str(ex)
            return None
        return _state["proxy"]


def restart():
    """Інший ланцюжок (поточний вихідний вузол перестав пускати)."""
    stop()
    _state["exits"] = []


def stop():
    proc = _state.get("proc")
    if proc is not None and proc.poll() is None:
        proc.kill()
    _state["proc"], _state["proxy"] = None, None


def cleanup_old():
    """Прибрати старі версії Tor після оновлення."""
    base = _base_dir()
    for d in os.listdir(base):
        if d[:1].isdigit() and d != TOR_VERSION:
            shutil.rmtree(os.path.join(base, d), ignore_errors=True)
