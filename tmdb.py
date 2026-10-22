"""Офіційні широкі кадри (backdrop 16:9) з TMDB замість картинок сайту (800×450, з написами).

Ключ — «API Read Access Token»: у збірці його вшиває CI (секрет TMDB_TOKEN → app_version.py),
у версії для розробки — файл ~/.uafix_tmdb_token. Без ключа все працює як раніше.
Знайдене (і не знайдене) запам'ятовується на диску — той самий фільм удруге не шукаємо.
Дані: TMDB (умови TMDB вимагають цієї згадки в застосунку)."""
import json
import os
import re
import threading

import requests

try:
    from app_version import TMDB_TOKEN
except Exception:
    TMDB_TOKEN = ""
if not TMDB_TOKEN:
    try:
        with open(os.path.expanduser("~/.uafix_tmdb_token"), encoding="utf-8") as f:
            TMDB_TOKEN = f.read().strip()
    except OSError:
        pass

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
_session = requests.Session()
_lock = threading.Lock()
CACHE_V = 2  # версія кешу: 2 — пошук спершу за оригінальною назвою, з перевіркою року
_cache = None  # url сторінки на сайті → шлях кадру в TMDB ("" — не знайдено)
_ids = {}  # url сторінки → (kind, id) у TMDB (у тому ж файлі під ключем "__ids__")
_meta = {}  # url сторінки → {"r": рейтинг або None, "g": [id жанрів]} ("__meta__")
_genres = {}  # id жанру → назва українською ("__genres__")


def enabled():
    return bool(TMDB_TOKEN)


def _cache_file():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    return os.path.join(base, ".uafix_tmdb.json")


def _load():
    global _cache
    if _cache is None:
        try:
            with open(_cache_file(), encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
        if _cache.pop("__v__", 1) < CACHE_V:  # старий пошук міг знайти не той тайтл
            _cache = {}
        _ids.update({k: tuple(v) for k, v in _cache.pop("__ids__", {}).items()})
        _meta.update(_cache.pop("__meta__", {}))
        _genres.update({int(k): v for k, v in _cache.pop("__genres__", {}).items()})
    return _cache


def _save():
    try:
        with open(_cache_file(), "w", encoding="utf-8") as f:
            json.dump(dict(_cache, __ids__=_ids, __meta__=_meta, __genres__=_genres,
                           __v__=CACHE_V), f, ensure_ascii=False)
    except Exception:
        pass


def _get(path, **params):
    r = _session.get(API + path, params=params, timeout=8,
                     headers={"Authorization": f"Bearer {TMDB_TOKEN}",
                              "accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _norm(s):
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


def _search(kind, query, year):
    """Найкращий збіг: спершу з роком, потім без нього (у серіалів рік сайту — часто рік
    сезону). Серед результатів — той, чия назва збігається, інакше найпопулярніший.
    Фільм з іншим роком (±1) відкидаємо: «Буря» 2026 інакше ставала «Бурею» 1988-го."""
    key_year = "primary_release_year" if kind == "movie" else "first_air_date_year"
    for params in ({key_year: year} if year else None, {}):
        if params is None:
            continue
        res = _get(f"/search/{kind}", query=query, language="uk-UA", include_adult="false",
                   **params).get("results", [])
        if kind == "movie" and year and not params:
            res = [r for r in res if not r.get("release_date") or
                   abs(int(r["release_date"][:4]) - int(year)) <= 1]
        if not res:
            continue
        q = _norm(query)
        same = [r for r in res
                if q in (_norm(r.get("title") or r.get("name")),
                         _norm(r.get("original_title") or r.get("original_name")))]
        return (same or res)[0]
    return None


def _queries(title, orig):
    """Спершу оригінальна (латиницею) назва — вона однозначніша: українських «Бур» кілька."""
    if orig and re.search(r"[A-Za-z]", orig):
        return [q for q in (orig, title) if q]
    return [q for q in (title, orig) if q]


def _best_backdrop(kind, tmdb_id, fallback):
    """Кадр без тексту (мова «null») з найвищою оцінкою; інакше — основний кадр TMDB."""
    try:
        imgs = _get(f"/{kind}/{tmdb_id}/images", include_image_language="null").get("backdrops", [])
    except Exception:
        imgs = []
    imgs = [i for i in imgs if i.get("aspect_ratio", 0) > 1.6]
    if imgs:
        imgs.sort(key=lambda i: (i.get("vote_average", 0), i.get("width", 0)), reverse=True)
        return imgs[0]["file_path"]
    return fallback or ""


def cached(url, size="w1280"):
    """(чи вже шукали, URL або None) — без мережі, щоб одразу показати знайдене раніше."""
    if not TMDB_TOKEN:
        return True, None
    with _lock:
        cache = _load()
        if url not in cache:
            return False, None
        return True, IMG + size + cache[url] if cache[url] else None


def backdrop(url, title, orig="", year="", size="w1280"):
    """URL офіційного широкого кадру для тайтлу сайту або None. Блокує (мережа) —
    викликати з потоку."""
    if not TMDB_TOKEN or not title:
        return None
    with _lock:
        cache = _load()
        if url in cache:
            path = cache[url]
            return IMG + size + path if path else None
    kinds = ("movie", "tv") if "/films/" in url else ("tv", "movie")
    path = ""
    try:
        for kind in kinds:
            hit = None
            for q in _queries(title, orig):
                hit = _search(kind, q, str(year or "")[:4])
                if hit:
                    break
            if hit:
                path = _best_backdrop(kind, hit["id"], hit.get("backdrop_path"))
                _ids[url] = (kind, hit["id"])
                _meta[url] = _meta_of(hit)
                break
    except Exception:
        return None  # мережа/ліміт — не запам'ятовуємо, спробуємо наступного разу
    with _lock:
        _cache[url] = path
        _save()
    return IMG + size + path if path else None


# ───────── рейтинг і жанри для карток ─────────

def _meta_of(d):
    """Рейтинг TMDB і жанри — з результату пошуку чи деталей."""
    rating = round(d.get("vote_average") or 0, 1) if d.get("vote_count") else None
    ids = d.get("genre_ids") or [g["id"] for g in d.get("genres", [])]
    for g in d.get("genres", []):  # з деталей — одразу з назвами
        _genres.setdefault(g["id"], g.get("name") or "")
    return {"r": rating or None, "g": ids[:3], "p": d.get("poster_path") or "", "v": META_V}


META_V = 3  # версія запису: 3 — з вертикальним постером ("p")


def _genre_names(ids):
    if any(i not in _genres for i in ids):
        for kind in ("movie", "tv"):
            try:
                for g in _get(f"/genre/{kind}/list", language="uk-UA").get("genres", []):
                    _genres[g["id"]] = g.get("name") or ""
            except Exception:
                pass
    return [(_genres.get(i) or "").capitalize() for i in ids if _genres.get(i)]


def meta_cached(url):
    """(чи відомо, {"rating", "genres"}) — без мережі."""
    if not TMDB_TOKEN:
        return True, None
    with _lock:
        _load()
        m = _meta.get(url)
        if m is None or m.get("v") != META_V:
            return False, None  # ще не шукали / старий запис
        if "names" in m:  # із сайту (TMDB не знайшов)
            return True, {"rating": m["r"], "genres": m["names"]}
        if any(i not in _genres for i in m["g"]):
            return False, None
        return True, {"rating": m["r"], "genres": [(_genres[i] or "").capitalize()
                                                   for i in m["g"] if _genres.get(i)]}


def poster_cached(url, size="w342"):
    """(чи відомо, URL вертикального постера або None) — без мережі."""
    if not TMDB_TOKEN:
        return True, None
    with _lock:
        _load()
        m = _meta.get(url)
        if m is None or m.get("v") != META_V:
            return False, None
        return True, (IMG + size + m["p"]) if m.get("p") else None


def poster(url, title, orig="", year="", size="w342"):
    """URL вертикального постера (TMDB) або None. Блокує — з потоку."""
    meta(url, title, orig, year)
    return poster_cached(url, size)[1]


def meta(url, title, orig="", year="", site=None):
    """Рейтинг і жанри тайтлу для картки. Блокує (мережа) — з потоку.
    site(url) → (рейтинг, [жанри]) — запасне джерело (сторінка тайтлу на сайті), коли TMDB
    тайтл не знайшов або в нього немає оцінки чи жанрів."""
    if not TMDB_TOKEN:
        return None
    with _lock:
        _load()
        m, found = _meta.get(url), _ids.get(url)
    if m is None or m.get("v") != META_V:
        m = None
        if found:  # кадр шукали раніше — лише деталі
            m = _meta_of(_get(f"/{found[0]}/{found[1]}", language="uk-UA"))
        else:
            backdrop(url, title, orig, year)
            with _lock:
                m = _meta.get(url)
                if m is not None and m.get("v") != META_V:
                    m = None
    names = _genre_names(m["g"]) if m else []
    rating = m["r"] if m else None
    if site and (not rating or not names):
        try:
            s_rating, s_genres = site(url)
        except Exception:
            s_rating, s_genres = None, []
        rating = rating or s_rating
        names = names or s_genres
        m = {"r": rating, "g": [], "names": names, "p": (m or {}).get("p", ""), "v": META_V}
    if m is None:
        return None
    with _lock:
        _meta[url] = m
        _save()
    return {"rating": rating, "genres": names}


# ───────── актори ─────────

def _find(url, title, orig, year):
    """(kind, id) тайтлу в TMDB — із запам'ятованого або пошуком."""
    with _lock:
        _load()
        if url in _ids:
            return _ids[url]
    for kind in (("movie", "tv") if "/films/" in url else ("tv", "movie")):
        for q in _queries(title, orig):
            hit = _search(kind, q, str(year or "")[:4])
            if hit:
                with _lock:
                    _ids[url] = (kind, hit["id"])
                    _save()
                return kind, hit["id"]
    return None


def cast(url, title, orig="", year="", limit=15):
    """Актори тайтлу: [{id, name, character, photo}] (імена — українською, якщо є)."""
    if not TMDB_TOKEN:
        return []
    found = _find(url, title, orig, year)
    if not found:
        return []
    kind, tid = found
    path = f"/{kind}/{tid}/aggregate_credits" if kind == "tv" else f"/{kind}/{tid}/credits"
    data = _get(path, language="uk-UA")
    out = []
    for c in data.get("cast", [])[:limit]:
        role = c.get("character") or ", ".join(
            r.get("character", "") for r in c.get("roles", [])[:1])
        out.append({"id": c["id"], "name": c.get("name") or "", "character": role or "",
                    "photo": IMG + "w185" + c["profile_path"] if c.get("profile_path") else None})
    return out


def person(pid):
    """Актор: {name, photo, bio, works: [{title, orig, year, poster, kind}]} — відомі роботи."""
    d = _get(f"/person/{pid}", language="uk-UA", append_to_response="combined_credits")
    bio = d.get("biography") or ""
    if not bio:
        try:
            bio = _get(f"/person/{pid}", language="en-US").get("biography") or ""
        except Exception:
            pass
    seen, works = set(), []
    credits = sorted(d.get("combined_credits", {}).get("cast", []),
                     key=lambda c: c.get("vote_count") or 0, reverse=True)
    for c in credits:
        key = (c.get("media_type"), c.get("id"))
        if key in seen or c.get("media_type") not in ("movie", "tv"):
            continue
        seen.add(key)
        date = c.get("release_date") or c.get("first_air_date") or ""
        works.append({"title": c.get("title") or c.get("name") or "",
                      "orig": c.get("original_title") or c.get("original_name") or "",
                      "year": date[:4], "kind": c["media_type"],
                      "poster": IMG + "w342" + c["poster_path"] if c.get("poster_path") else None})
        if len(works) >= 24:
            break
    return {"name": d.get("name") or "", "bio": bio,
            "photo": IMG + "w342" + d["profile_path"] if d.get("profile_path") else None,
            "works": works}


# ───────── франшизи ─────────

_collections = {}  # id фільму в TMDB → франшиза (у пам'яті, на час роботи)


def collection(url, title, orig="", year=""):
    """Франшиза фільму по порядку виходу: {name, current, parts: [{id, title, orig, year,
    poster, released}]} або None (не фільм, не частина франшизи). Блокує — з потоку."""
    if not TMDB_TOKEN:
        return None
    found = _find(url, title, orig, year)
    if found and found[0] != "movie" and "/serials/" not in url:
        # мультфільм / аніме: _find міг спершу знайти однойменний серіал
        for q in _queries(title, orig):
            hit = _search("movie", q, str(year or "")[:4])
            if hit:
                found = ("movie", hit["id"])
                break
    if not found or found[0] != "movie":
        return None
    tid = found[1]
    if tid in _collections:
        res = _collections[tid]
        return dict(res, current=tid) if res else None
    col = (_get(f"/movie/{tid}", language="uk-UA").get("belongs_to_collection") or {})
    res = None
    if col.get("id"):
        data = _get(f"/collection/{col['id']}", language="uk-UA")
        parts = sorted(data.get("parts", []), key=lambda p: p.get("release_date") or "9999")
        if len(parts) > 1:
            name = re.sub(r"\s*[|(-]?\s*(Колекція|Collection)\)?\s*$", "",
                          data.get("name") or col.get("name") or "", flags=re.I).strip(" |-")
            res = {"name": name, "parts": [
                {"id": p["id"], "title": p.get("title") or "", "orig": p.get("original_title") or "",
                 "year": (p.get("release_date") or "")[:4],
                 "released": bool(p.get("release_date")) and p["release_date"] <= _today(),
                 "poster": IMG + "w342" + p["poster_path"] if p.get("poster_path") else None}
                for p in parts]}
    _collections[tid] = res
    for p in (res or {}).get("parts", []):  # інші частини — та сама франшиза
        _collections.setdefault(p["id"], res)
    return dict(res, current=tid) if res else None


def _today():
    import datetime
    return datetime.date.today().isoformat()


# ───────── топ тижня ─────────

def trending_week(limit=20):
    """Найпопулярніше за тиждень (фільми й серіали): [{title, orig, year, kind}]."""
    if not TMDB_TOKEN:
        return []
    out = []
    for r in _get("/trending/all/week", language="uk-UA").get("results", []):
        if r.get("media_type") not in ("movie", "tv"):
            continue
        date = r.get("release_date") or r.get("first_air_date") or ""
        out.append({"title": r.get("title") or r.get("name") or "",
                    "orig": r.get("original_title") or r.get("original_name") or "",
                    "year": date[:4], "kind": r["media_type"]})
        if len(out) >= limit:
            break
    return out
