"""Мережа і парсинг uafix.net (спільне ядро для всіх платформ)."""
import json
import re
import time
from urllib.parse import urljoin, urlparse, quote_plus

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as http
    IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # Android / коли curl_cffi недоступний
    import requests as http
    IMPERSONATE = {}

BASE = "https://uafix.net"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8"}

CATEGORIES = [
    ("Серіали — всі", "/serials/"),
    ("Серіали — нові", "/serials/new_uaserial/"),
    ("Серіали 2026", "/serials/serial_2026_online/"),
    ("Серіали 2025", "/serials/serial_2025_online/"),
    ("Фільми — всі", "/films/"),
    ("Фільми — новинки", "/films/new_netflix_ua/"),
    ("Фільми 2026", "/films/uafilms_2026/"),
    ("Мультфільми", "/cartoons/"),
    ("Мультсеріали", "/serials/multseial/"),
    ("Аніме", "/anime/"),
    ("Дорами", "/dorama/"),
    ("Наша озвучка", "/dubbing/"),
]

_session = http.Session() if hasattr(http, "Session") else http


# Перегляд з-за кордону: плеєр ashdi показує «Змініть країну перегляду» для не-українських
# адрес (саме відео при цьому доступне звідусіль). Тоді сторінку плеєра (кілька КБ) беремо
# через безкоштовний український проксі з публічних списків — застосунок сам знаходить
# робочий. З'єднання з плеєром — HTTPS-тунель, тож проксі вмісту не бачить і не змінює.
# Каталог і відео йдуть напряму. GEO — чи траплялось таке блокування.
GEO = {"blocked": False}
GEO_TEXT = "Змініть країну перегляду"
ABROAD = {"enabled": True, "proxy": None, "bad": set(), "checked": 0.0}
GEO_TEST = "https://ashdi.vip/vod/296470"
PROXY_LISTS = [
    "https://proxylist.geonode.com/api/proxy-list?country=UA&limit=100&page=1"
    "&sort_by=lastChecked&sort_type=desc",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&country=UA"
    "&timeout=10000",
]


def _proxy_candidates():
    found = []
    for src in PROXY_LISTS:
        try:
            r = _session.get(src, timeout=15, headers=HEADERS)
            if src.startswith("https://proxylist.geonode"):
                found += [f"http://{p['ip']}:{p['port']}" for p in r.json().get("data", [])
                          if set(p.get("protocols", [])) & {"http", "https"}]
            else:
                found += ["http://" + x for x in r.text.split() if ":" in x]
        except Exception:
            continue
    return [p for p in dict.fromkeys(found) if p not in ABROAD["bad"]]


def _proxy_works(proxy):
    try:
        r = _session.get(GEO_TEST, timeout=10, headers=dict(HEADERS, Referer=BASE + "/"),
                         proxies={"http": proxy, "https": proxy}, **IMPERSONATE)
        return r.status_code == 200 and GEO_TEXT not in r.text and "file" in r.text
    except Exception:
        return False


def abroad_proxy(refresh=False):
    """Робочий український проксі (або None). Перевіряємо кандидатів паралельно — перший,
    що відкрив плеєр, і береться; запам'ятовується до першого збою."""
    import concurrent.futures as cf
    if ABROAD["proxy"] and not refresh:
        return ABROAD["proxy"]
    if ABROAD["proxy"]:
        ABROAD["bad"].add(ABROAD["proxy"])
        ABROAD["proxy"] = None
    cands = _proxy_candidates()[:40]
    if not cands:
        return None
    ex = cf.ThreadPoolExecutor(12)
    futures = {ex.submit(_proxy_works, p): p for p in cands}
    try:
        for f in cf.as_completed(futures, timeout=25):
            if f.result():
                ABROAD["proxy"] = futures[f]
                break
            ABROAD["bad"].add(futures[f])
    except Exception:
        pass
    ex.shutdown(wait=False, cancel_futures=True)
    return ABROAD["proxy"]


def fetch(url, referer=None, proxy=None):
    h = dict(HEADERS)
    kw = dict(IMPERSONATE)
    if referer:
        h["Referer"] = referer
        p = urlparse(referer)
        h["Origin"] = f"{p.scheme}://{p.netloc}"
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    r = _session.get(url, headers=h, timeout=20, **kw)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {url}")
    return r.text


# ───────── фільтри розділу (плагін сайту xfsort) ─────────
# Сайт тримає вибрані фільтри в сесії (cookie): POST xf_sort=get по одному полю, далі всі
# сторінки розділу — вже відфільтровані. Тому для кожного набору фільтрів — своя сесія, щоб
# фільтри не впливали на решту застосунку.

FILTER_TITLES = {"default": "Сортування"}  # назви кнопок замість «За замовчуванням»
_filter_sessions = {}


def section_filters(path):
    """Фільтри розділу з його сторінки: [{field, title, options: [(value, label)]}]."""
    soup = BeautifulSoup(fetch(catalog_url(path, 1)), "html.parser")
    out = []
    for sel in soup.select(".xfsort-block .xfsort-select"):
        ul = sel.select_one(".xfsort-ul")
        if not ul or not ul.get("data-field") or ul["data-field"].startswith("-"):
            continue  # «Приховати аніме» — не фільтр, а виняток
        items = [(li.get("data-val", ""), li.get_text(strip=True)) for li in ul.select("li")]
        opts = [(v, t) for v, t in items if v]
        if opts:
            title = FILTER_TITLES.get(ul["data-field"]) or (items[0][1] if items else "")
            out.append({"field": ul["data-field"], "title": title, "options": opts})
    return out


def catalog_filtered(path, page, filters):
    """Сторінка розділу з фільтрами {field: value} → картки (як parse_catalog)."""
    key = (path, tuple(sorted(filters.items())))
    sess = _filter_sessions.get(key)
    url1 = catalog_url(path, 1)
    if sess is None:
        sess = http.Session()  # нова сесія — фільтрів у ній ще немає, «скинути» не треба
        last = None
        for field, value in filters.items():
            last = sess.post(url1, data={"xf_sort": "get", "xf_field": field, "xf_value": value},
                             headers=HEADERS, timeout=20, **IMPERSONATE)
        if len(_filter_sessions) > 20:
            _filter_sessions.clear()
        _filter_sessions[key] = sess
        if page == 1 and last is not None and last.status_code == 200:
            return parse_catalog(last.text)  # відповідь на фільтр — це вже перша сторінка
    r = sess.get(catalog_url(path, page), headers=HEADERS, timeout=20, **IMPERSONATE)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {path}")
    return parse_catalog(r.text)


ITEM_PATH_RE = re.compile(r"^/(serials|films|film|cartoons|anime|dorama)/[^/]+/?$")
TITLE_ATTR_RE = re.compile(r"^\s*(Смотреть|Дивитись|Дивитися)\s+(.*?)\s+онлайн\s*$", re.I)


AGE_RE = re.compile(r"^\s*\d{1,2}\+\s*")
BADGE_RE = re.compile(r"^\s*(нов(а|і|ий|е)\s+(серія|серії|серіал|сезон|мультсеріал|мультфільм|фільм)|"
                      r"останн\w+\s+серія|фінал\w*)\s*", re.I)
# розділи меню, що мають ту саму форму шляху /serials/<щось>/
CATEGORY_SLUGS = {"action", "melodrama", "fantastics", "comedy", "detective", "school",
                  "documental", "multseial", "serials", "films", "film", "page"}


def _clean_name(raw):
    s = re.sub(r"\s+", " ", raw).strip()
    s = BADGE_RE.sub("", s)
    s = AGE_RE.sub("", s)
    s = BADGE_RE.sub("", s)
    return s


def parse_catalog(html, base=BASE):
    """Картки тайтлів на сторінці каталогу / пошуку:
    [{title, orig, url, poster, age}]."""
    soup = BeautifulSoup(html, "html.parser")
    found = {}  # url -> картка
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"]).split("#")[0].split("?")[0]
        if not url.endswith("/"):
            url += "/"
        path = urlparse(url).path
        if not ITEM_PATH_RE.match(path):
            continue
        slug = path.strip("/").split("/")[-1]
        if "_" in slug or slug in CATEGORY_SLUGS:
            continue
        text = a.get_text(" ", strip=True)
        img = a.find("img")
        m = TITLE_ATTR_RE.match(a.get("title", ""))
        is_card = bool(m or img or AGE_RE.match(text) or BADGE_RE.match(text))
        if not is_card:
            continue
        if m:
            name = m.group(2)
        elif img and img.get("alt", "").strip():
            name = img["alt"]
        else:
            name = text
        name = _clean_name(name)
        poster = ""
        if img:
            src = img.get("data-src") or img.get("src") or ""
            if src and not src.startswith("data:"):
                poster = urljoin(base, src)
        age_el = a.select_one(".age")
        age = age_el.get_text(strip=True) if age_el else ""
        card = found.setdefault(url, {"name": "", "poster": "", "age": ""})
        card["name"] = card["name"] or name
        card["poster"] = card["poster"] or poster
        card["age"] = card["age"] or age
    items = []
    for url, c in found.items():
        parts = [p.strip() for p in c["name"].split(" / ") if p.strip()]
        short = parts[0] if parts else urlparse(url).path.strip("/").split("/")[-1]
        # оригінальна (латинська) назва; у тексті картки після неї ще повторюється укр. — обрізаємо
        extra = ""
        for p in parts[1:]:
            if re.search(r"[A-Za-z]", p):
                extra = re.split(r"\s+(?=[А-ЯҐЄІЇа-яґєії])", p, maxsplit=1)[0].strip()
                break
        items.append({"title": short, "orig": extra, "url": url,
                      "poster": c["poster"], "age": c["age"]})
    return items


def parse_title(html, url=BASE):
    """Інформація зі сторінки тайтлу: постер, рік, жанри, IMDb, опис…"""
    soup = BeautifulSoup(html, "html.parser")
    info = {"title": "", "orig": "", "poster": "", "year": "", "genres": [], "country": "",
            "imdb": "", "age": "", "duration": "", "description": "", "actors": [], "trailer": ""}
    el = soup.select_one("#ftitle [itemprop=name]") or soup.select_one("h1")
    if el:
        title = re.sub(r"\s+дивит\w+\s+онлайн.*$", "", el.get_text(" ", strip=True), flags=re.I)
        info["title"] = title.split(" / ")[0].strip()
    og = soup.find("meta", property="og:image")
    img = soup.select_one("img[itemprop=image]") or soup.select_one(".fposter2 img")
    src = (img.get("data-src") or img.get("src")) if img else ""
    if og and og.get("content") and (not src or "lazy" in src):
        src = og["content"]
    info["poster"] = urljoin(url, src) if src else ""
    for li in soup.select("#finfo li, .finfo li"):
        label_el = li.find("span")
        label = label_el.get_text(strip=True).rstrip(":").lower() if label_el else ""
        value = li.get_text(" ", strip=True)
        if label_el:
            value = value[len(label_el.get_text(" ", strip=True)):].strip()
        value = re.sub(r"\s+,", ",", value)
        if "рік" in label:
            parts = [x.strip() for x in value.split("/")]
            info["year"] = parts[0]
            for x in parts[1:]:
                if re.fullmatch(r"\d{1,2}\+", x):
                    info["age"] = x
                elif "хв" in x:
                    info["duration"] = x
        elif "ориг" in label:
            orig, _, age = value.partition(" / ")
            info["orig"] = orig.strip()
            if re.fullmatch(r"\d{1,2}\+", age.strip()):
                info["age"] = info["age"] or age.strip()
        elif "жанр" in label:
            info["genres"] = [g.strip() for g in value.split(",") if g.strip()]
        elif "країна" in label:
            info["country"] = value
        elif "ролях" in label:
            info["actors"] = [x.get_text(strip=True) for x in li.select("[itemprop=actor]")]
    r = soup.select_one(".rat-imdb")
    if r:
        info["imdb"] = r.get_text(strip=True)
    d = soup.select_one(".full-text") or soup.select_one(".fdesc")
    if d:
        info["description"] = d.get_text(" ", strip=True)
    else:
        m = soup.find("meta", attrs={"name": "description"})
        info["description"] = m["content"] if m and m.get("content") else ""
    t = soup.select_one(".to-trailer[data-src]")
    if t:
        info["trailer"] = t["data-src"].replace("/embed/", "/watch?v=")
    info["comments"] = parse_comments(soup)
    return info


def site_meta(url):
    """(рейтинг IMDb або None, [жанри]) зі сторінки тайтлу — для карток, коли TMDB не знає."""
    info = parse_title(fetch(url), url)
    try:
        rating = float(info["imdb"].replace(",", ".")) or None
    except ValueError:
        rating = None
    return rating, [g.capitalize() for g in info["genres"][:3]]


def parse_comments(soup):
    """Відгуки глядачів зі сторінки тайтлу: [{author, role, date, text, likes}],
    найновіші першими (як на сайті)."""
    out, seen = [], set()
    for box in soup.select("[id^=comment-id-]"):
        cid = box["id"].rsplit("-", 1)[-1]
        item = box.select_one(".comm-item")
        text_el = box.select_one(f"#comm-id-{cid}")
        if cid in seen or not item or not text_el:
            continue
        seen.add(cid)
        for q in text_el.select(".quote, .title_quote"):  # цитати інших — не дублюємо
            q.decompose()
        text = text_el.get_text("\n", strip=True)
        if not text:
            continue
        spans = [sp.get_text(" ", strip=True) for sp in item.select(".comm-one > span")]
        author = re.sub(r"^Гість\s+", "", spans[0]) if spans else ""
        likes = item.select_one(".ratingtypeplus")
        out.append({"author": author or "Гість", "role": spans[1] if len(spans) > 1 else "",
                    "date": spans[2] if len(spans) > 2 else "", "text": text,
                    "likes": int(likes.get_text(strip=True) or 0)
                    if likes and likes.get_text(strip=True).lstrip("-").isdigit() else 0})
    return out


_RESOLVED = {}


def resolve_category(path):
    """Фактична адреса розділу. Сайт перейменовує розділи й перенаправляє зі старих адрес
    (serial_2026_online → serials_2026_online), але сторінки /page/N/ за старою адресою
    віддають знову першу сторінку — тож пагінацію будуємо від адреси після перенаправлення."""
    if path not in _RESOLVED:
        try:
            r = _session.get(BASE + path, headers=HEADERS, timeout=20, **IMPERSONATE)
            final = urlparse(str(r.url)).path if r.status_code == 200 else path
        except Exception:
            final = path
        _RESOLVED[path] = final if final.endswith("/") else final + "/"
    return _RESOLVED[path]


_HOME = {"at": 0.0, "blocks": {}}


def home_blocks(max_age=300):
    """Блоки головної сторінки сайту: {назва: [картки]} — «Наша озвучка», «Фільми»,
    «Серіали»… Тут найсвіжіше (що щойно додали чи оновили): у каталогах розділів нові
    надходження часто опиняються на другій сторінці. Кеш — кілька хвилин на всі полички."""
    if time.time() - _HOME["at"] < max_age and _HOME["blocks"]:
        return _HOME["blocks"]
    soup = BeautifulSoup(fetch(BASE + "/"), "html.parser")
    blocks = {}
    for sect in soup.select("main div.sect"):
        title = sect.select_one(".sect-title")
        items = parse_catalog(str(sect))
        if title and items:
            blocks[title.get_text(" ", strip=True)] = items
    _HOME.update(at=time.time(), blocks=blocks)
    return blocks


def fresh_catalog(path, block):
    """Поличка головної: спершу свіже з блоку головної сайту, далі — перша сторінка розділу."""
    try:
        first = home_blocks().get(block, []) if block else []
    except Exception:
        first = []
    try:
        rest = parse_catalog(fetch(catalog_url(path, 1)))
    except Exception:
        if not first:
            raise
        rest = []
    seen = {it["url"] for it in first}
    return first + [it for it in rest if it["url"] not in seen]


def catalog_url(path, page):
    path = path if path.endswith("/") else path + "/"
    if page > 1:
        path = resolve_category(path)
    return BASE + path + (f"page/{page}/" if page > 1 else "")


def search_url(query, page):
    return (f"{BASE}/index.php?do=search&subaction=search&full_search=0"
            f"&search_start={page}&result_from={(page - 1) * 20 + 1}&story={quote_plus(query)}")


def _norm_title(s):
    return re.sub(r"[^\w]+", " ", (s or "").lower()).strip()


def find_title(orig, title=""):
    """Тайтл на сайті за назвою з TMDB: спершу шукаємо за оригінальною (переклад назви на
    сайті й у TMDB часто різний), збіг — за точною назвою. → картка або None."""
    for q, key in ((orig, "orig"), (title, "title")):
        if not q:
            continue
        want = _norm_title(q)
        try:
            items = parse_catalog(fetch(search_url(q, 1)))
        except Exception:
            continue
        for it in items:
            if "/serials/" not in it["url"] and _norm_title(it[key]) == want:
                return it
    return None


M3U8_RE = re.compile(r'https?:[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', re.I)
FILE_RE = re.compile(r'''\bfile\s*:\s*(['"])(.*?)(?<!\\)\1''', re.S)
EPISODE_LINK_RE = re.compile(r'(season|sezon|episode|seriya|seria|seriia|epizod)[-_]?\d+', re.I)


def find_iframes(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for fr in soup.find_all("iframe"):
        src = fr.get("src") or fr.get("data-src") or fr.get("data-lazy-src")
        if src and not src.startswith(("about:", "javascript:")):
            out.append(urljoin(base, src))
    for m in re.finditer(r'''["'](//[^"']+/(?:embed|vod|serial|video)/[^"']+)["']''', html):
        out.append(urljoin(base, m.group(1)))
    return list(dict.fromkeys(out))


def best_quality(v):
    parts = re.findall(r'\[(\d+)p?\]([^,\[]+)', v)
    if parts:
        return max(parts, key=lambda x: int(x[0]))[1].strip()
    return v.split(" or ")[0].strip()


SUB_RE = re.compile(r'''\bsubtitle\s*:\s*(['"])(.*?)(?<!\\)\1''', re.S)


def parse_subtitles(value):
    """'[Українські (UA)]https://…_ua.vtt,[Оригинал (ENG)]https://…_en.vtt' →
    [{title, url, lang}]"""
    subs = []
    for name, link in re.findall(r"\[([^\]]*)\]\s*(https?://[^,\s\[]+)", value or ""):
        n = name.lower()
        lang = "en" if ("eng" in n or "_en." in link) else "uk" if ("ua" in n or "укр" in n) else ""
        title = {"uk": "Українські", "en": "Англійські"}.get(lang, name.strip())
        subs.append({"title": title, "url": link, "lang": lang})
    return subs


_SUB_CHECKS = {}
MIN_SUBTITLE_CUES = 5


def useful_subtitles(subs):
    """Лишає лише ті субтитри, що реально існують і мають текст: сайт інколи віддає
    посилання на неіснуючий файл (404) або файл з 2–3 службовими написами."""
    good = []
    for x in subs:
        ok = _SUB_CHECKS.get(x["url"])
        if ok is None:
            try:
                r = _session.get(x["url"], headers=HEADERS, timeout=10, **IMPERSONATE)
                ok = r.status_code == 200 and r.text.count("-->") >= MIN_SUBTITLE_CUES
            except Exception:
                ok = False
            _SUB_CHECKS[x["url"]] = ok
        if ok:
            good.append(x)
    return good


def walk_playlist(items, path=(), subs=()):
    for it in items:
        title = re.sub(r"<[^>]+>", "", (it.get("title") or it.get("comment") or "")).strip()
        if isinstance(it.get("folder"), list):
            yield from walk_playlist(it["folder"], path + (title,), subs)
        elif it.get("file"):
            yield path + (title,), best_quality(it["file"]), parse_subtitles(it.get("subtitle")) or list(subs)


def parse_player(html):
    """[(path, url_потоку, субтитри)] зі сторінки плеєра."""
    m = SUB_RE.search(html)
    subs = parse_subtitles(m.group(2).replace("\\/", "/")) if m else []
    res = []
    for _, raw in FILE_RE.findall(html):
        val = raw.replace("\\/", "/").replace('\\"', '"').replace("\\'", "'")
        if val.lstrip().startswith("["):
            try:
                res.extend(walk_playlist(json.loads(val), subs=subs))
                continue
            except json.JSONDecodeError:
                pass
        if ".m3u8" in val or val.startswith("http"):
            res.append(((), best_quality(val), subs))
    if not res:
        res = [((), u, subs) for u in dict.fromkeys(M3U8_RE.findall(html))]
    return res


def voice_name(tab, url):
    """Назва озвучки за підписом вкладки плеєра («Плеєр 2 (субтитри)») та іменем файлу."""
    t, u = (tab or "").lower(), url.lower()
    # «_eng_» у назві файлу — оригінал, але лише якщо там немає й української:
    # «…_ukr_eng_…» чи «…_2xukr_eng_…» (дві українські доріжки) — файл із кількома
    # доріжками, і грає в ньому саме українська
    eng_file = re.search(r"[._]eng[._]", u) and \
        not re.search(r"[._](\d+x)?(ukr|ua|uk)[._]", u)
    if "субтитр" in t or "оригінал" in t or "original" in t or eng_file:
        return "Оригінал (ENG)"
    if t and not re.match(r"^(плеєр|player|дивитись)", t):
        return tab.strip()  # власна назва студії / озвучки
    if ".mvo" in u:
        return "Українська (багатоголоса)"
    return "Українська"


def _fetch_abroad(url, referer):
    """Сторінка плеєра, недоступна з-за кордону, — через український проксі (дві спроби:
    збережений, потім свіжознайдений)."""
    if not ABROAD["enabled"]:
        return None
    for refresh in (False, True):
        proxy = abroad_proxy(refresh)
        if not proxy:
            return None
        try:
            html = fetch(url, referer=referer, proxy=proxy)
        except Exception:
            continue
        if GEO_TEXT not in html:
            return html
    return None


def _page_streams(url, label=(), html=None):
    html = html if html is not None else fetch(url)
    # вкладки плеєрів на сторінці («Плеєр 1», «Плеєр 2 (субтитри)») — у тому ж порядку, що й iframe
    tabs = [t.get_text(" ", strip=True) for t in BeautifulSoup(html, "html.parser").select(".tabs-link")]
    found = []
    for i, fr in enumerate(find_iframes(html, url)):
        try:
            fhtml = fetch(fr, referer=url)
        except Exception:
            continue
        if GEO_TEXT in fhtml:
            GEO["blocked"] = True
            fhtml = _fetch_abroad(fr, url)
            if fhtml is None:
                continue
        tab = tabs[i] if i < len(tabs) else ""
        for path, link, subs in parse_player(fhtml):
            voice = voice_name(tab, link)
            if not is_original(voice):
                # до української доріжки «субтитри» — лише переклад написів на екрані
                subs = [dict(x, title="Написи на екрані") if x["lang"] == "uk" else x for x in subs]
            found.append({"path": label + path if path else label, "url": link, "referer": fr,
                          "voice": voice, "subs": useful_subtitles(subs)})
    if not found:
        found = [{"path": label + p if p else label, "url": l, "referer": url,
                  "voice": voice_name("", l), "subs": subs}
                 for p, l, subs in parse_player(html)]
    return html, found


def get_streams(url, progress=lambda done, total: None, html=None):
    """[{path: ('Сезон 1','Серія 2'), url, referer}] для сторінки тайтлу."""
    html, found = _page_streams(url, html=html)
    if found:
        return dedupe(found)
    soup = BeautifulSoup(html, "html.parser")
    base_path = urlparse(url).path.rstrip("/")
    eps = []
    for a in soup.find_all("a", href=True):
        u = urljoin(url, a["href"])
        p = urlparse(u).path
        if p.startswith(base_path + "/") and EPISODE_LINK_RE.search(p):
            eps.append((a.get_text(" ", strip=True), u))
    eps = list(dict.fromkeys(eps))
    for i, (text, ep) in enumerate(eps, 1):
        progress(i, len(eps))
        try:
            _, f = _page_streams(ep, label=(text,))
            found.extend(f)
        except Exception:
            pass
        time.sleep(0.5)
    return dedupe(found)


def dedupe(items):
    seen, out = set(), []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out


def stream_headers(entry):
    """HTTP-заголовки, з якими треба запитувати потік."""
    p = urlparse(entry["referer"])
    return {"Referer": entry["referer"], "Origin": f"{p.scheme}://{p.netloc}", "User-Agent": UA}


_VARIANTS = {}  # посилання на потік → {"1080": посилання на цю якість, …}


def hls_variants(entry):
    """Якості HLS-потоку серії (з головного плейлиста сервера): {"1080": url, "720": url, …}.
    Порожньо, якщо сервер віддає одну якість. Результат кешується."""
    url = entry["url"]
    if url in _VARIANTS:
        return _VARIANTS[url]
    variants = {}
    try:
        r = _session.get(url, headers=stream_headers(entry), timeout=10, **IMPERSONATE)
        lines = r.text.splitlines() if r.status_code == 200 else []
        for i, line in enumerate(lines):
            m = re.search(r"RESOLUTION=\d+x(\d+)", line)
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                link = urljoin(url, lines[i + 1].strip())
                # назва якості — з адреси (…/720/index.m3u8), інакше з висоти кадру
                name = re.search(r"/(\d{3,4})p?/[^/]*$", link)
                key = name.group(1) if name else (m.group(1) if m else None)
                if key:
                    variants[key] = link
    except Exception:
        return {}  # не кешуємо: можливо, тимчасова помилка мережі
    _VARIANTS[url] = variants if len(variants) > 1 else {}
    return _VARIANTS[url]


def pick_quality(variants, quality):
    """Наявна якість, найближча до бажаної: така сама, нижча або (якщо нижчих нема) найнижча."""
    if quality in variants:
        return quality
    keys = sorted(variants, key=int, reverse=True)
    lower = [k for k in keys if int(k) <= int(quality)]
    return lower[0] if lower else keys[-1]


def quality_url(url, quality):
    """Посилання на потік потрібної якості; якщо сервер віддає одну якість — як є."""
    variants = _VARIANTS.get(url)
    if not variants:
        return url
    return variants[pick_quality(variants, quality)]


SE_URL_RE = re.compile(r"s(\d{1,2})e(\d{1,3})", re.I)
EP_ONLY_RE = re.compile(r"^(\d+)\s*сері[яї]\s*(.*)$|^сері[яї]\s*(\d+)\s*(.*)$", re.I)


def _season_episode(e):
    """(сезон, серія, назва серії) з підпису або з імені файлу (…s02e08…)."""
    label = " ".join(e["path"]).strip()
    m = re.search(r"сезон\s*(\d+)\s*сері[яї]\s*(\d+)\s*(.*)", label, re.I)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3).strip()
    u = SE_URL_RE.search(e["url"])
    m = EP_ONLY_RE.match(label)
    name = ((m.group(2) or m.group(4) or "") if m else "").strip()
    if u:
        return int(u.group(1)), int(u.group(2)), name
    if m:
        return 1, int(m.group(1) or m.group(3)), name
    return None


def _source_labels(entries):
    """Підписи джерел однієї серії: назва озвучки, а якщо збігаються — ще якість / номер."""
    labels = [e.get("voice") or "" for e in entries]
    for n, (e, lab) in enumerate(zip(entries, labels), 1):
        if not lab or labels.count(lab) > 1:
            q = re.search(r"(2160|1080|720|480)p", e["url"])
            extra = q.group(0) if q else f"джерело {n}"
            labels[n - 1] = f"{lab} · {extra}" if lab else extra.capitalize()
    return labels


def is_original(voice):
    return voice.startswith("Оригінал")


def group_streams(streams):
    """Плоский список потоків → сезони з серіями, у кожної серії — одне чи кілька джерел.

    [{"name": "Сезон 1", "episodes": [{"num": 1, "name": "Ціль", "label": "Серія 1 — Ціль",
                                       "sources": [{"label": "1080p", "entry": {...}}]}]}]
    """
    if not streams:
        return []
    flat = all(len(e["path"]) <= 1 for e in streams)
    parsed = [_season_episode(e) for e in streams] if flat else [None]
    seasons = {}
    if flat and all(parsed):
        # підписи з окремих сторінок серій: «Сезон 2 Серія 8 Стріп», «1 серія» + s01e01 в url
        for (sn, ep, name), e in zip(parsed, streams):
            season = seasons.setdefault(f"Сезон {sn}", {})
            item = season.setdefault(ep, {"num": ep, "name": "", "sources": []})
            item["name"] = item["name"] or name
            item["sources"].append(e)
        ordered = sorted(seasons.items(), key=lambda kv: int(kv[0].split()[-1]))
        result = [{"name": k, "episodes": [v[ep] for ep in sorted(v)]} for k, v in ordered]
    else:
        # дерево з плеєра (озвучка / сезон / серія) або фільм
        for i, e in enumerate(streams):
            path = e["path"] or ("",)
            season = seasons.setdefault(" · ".join(p for p in path[:-1] if p) or "Відео", {})
            # без назви — це той самий фільм у різних озвучках (окремі плеєри на сторінці)
            same_film = all(not x["path"] for x in streams) and \
                len({x.get("voice") for x in streams}) == len(streams)
            label = path[-1] or ("Фільм" if same_film or len(streams) == 1 else f"Відео {i + 1}")
            m = re.match(r"^\s*(?:серія\s*)?(\d+)(?:\s*серія)?\s*[.:—-]?\s*(.*)$", label, re.I)
            item = season.setdefault(label, {"num": int(m.group(1)) if m else None,
                                             "name": (m.group(2) if m else label).strip(),
                                             "sources": []})
            item["sources"].append(e)
        result = [{"name": k, "episodes": list(v.values())} for k, v in seasons.items()]
    for season in result:
        for item in season["episodes"]:
            item["label"] = episode_label(item["num"], item["name"])
            item["sources"] = make_sources(item["sources"])
    return result


def episode_label(num, name):
    return (f"Серія {num}" + (f" — {name}" if name else "")) if num is not None else name


def make_sources(entries):
    """Потоки однієї серії → [{label, entry}], українська озвучка першою."""
    srcs = sorted(dedupe(entries), key=lambda e: is_original(e.get("voice") or ""))
    return [{"label": lab, "entry": e} for lab, e in zip(_source_labels(srcs), srcs)]


# ───────── серіали, де кожна серія — окрема сторінка ─────────

EP_PATH_RE = re.compile(r"(?:season|sezon)[-_]?(\d+)[-_/]+(?:episode|seriya|seria|seriia|epizod)[-_]?(\d+)", re.I)
SEASON_PATH_RE = re.compile(r"/(?:sezon|season)[-_]?(\d+)/?$", re.I)


def page_streams(url, html):
    """Потоки з плеєрів, вбудованих прямо в сторінку (фільм або серіал з плейлистом)."""
    return dedupe(_page_streams(url, html=html)[1])


def list_episodes(url, html, workers=4):
    """Список серій без завантаження відео: з головної сторінки та сторінок усіх сезонів
    (головна показує лише останні ~20 серій).
    → ([{season, num, name, page}] за порядком, {номери сезонів без доступних серій})
    Сезони без серій — це ті, що сайт показує лише зареєстрованим користувачам."""
    base_path = urlparse(url).path.rstrip("/")
    episodes = {}   # page_url -> {season, num, name}
    seasons = set()

    def collect(page_html, page_url):
        soup = BeautifulSoup(page_html, "html.parser")
        for a in soup.find_all("a", href=True):
            u = urljoin(page_url, a["href"]).split("#")[0].split("?")[0]
            path = urlparse(u).path
            if not path.startswith(base_path + "/"):
                continue
            if SEASON_PATH_RE.search(path):
                seasons.add(u if u.endswith("/") else u + "/")
                continue
            m = EP_PATH_RE.search(path)
            if not m:
                continue
            text = a.get_text(" ", strip=True)
            t = re.match(r"^\s*сезон\s*\d+\s*сері[яї]\s*\d+\s*(.*)$", text, re.I)
            name = t.group(1).strip() if t else ""
            item = episodes.setdefault(u, {"season": int(m.group(1)), "num": int(m.group(2)),
                                           "name": "", "page": u})
            item["name"] = item["name"] or name

    collect(html, url)
    if seasons:
        from concurrent.futures import ThreadPoolExecutor

        def load(season_url):
            try:
                return season_url, fetch(season_url, referer=url)
            except Exception:
                return season_url, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for season_url, season_html in pool.map(load, sorted(seasons)):
                if season_html:
                    collect(season_html, season_url)
    found = {e["season"] for e in episodes.values()}
    locked = set()
    for season_url in seasons:
        m = SEASON_PATH_RE.search(urlparse(season_url).path)
        if m and int(m.group(1)) not in found:
            locked.add(int(m.group(1)))
    return sorted(episodes.values(), key=lambda e: (e["season"], e["num"])), locked


def group_episode_pages(episodes, locked=()):
    """[{season, num, name, page}] → структура як у group_streams, але sources=None
    (потоки серії завантажуються, коли її вмикають)."""
    seasons = {}
    for e in episodes:
        seasons.setdefault(e["season"], []).append({
            "num": e["num"], "name": e["name"], "label": episode_label(e["num"], e["name"]),
            "sources": None, "page": e["page"]})
    for n in locked:
        seasons.setdefault(n, [])
    return [{"name": f"Сезон {n}", "episodes": eps, "locked": not eps}
            for n, eps in sorted(seasons.items())]


def episode_sources(page_url):
    """Потоки однієї серії (зі сторінки серії)."""
    return make_sources(_page_streams(page_url)[1])


# ───────── зрозумілі повідомлення про помилки ─────────

def is_online(timeout=2.5):
    """Чи є інтернет: пробуємо TCP-з'єднання з кількома надійними вузлами."""
    import socket
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("uafix.net", 443)):
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


NETWORK_HINTS = ("resolve", "connect", "timed out", "timeout", "network", "unreachable",
                 "connection", "failed to open", "ssl", "reset by peer", "name or service")


def friendly_error(err, what="дані"):
    """Технічну помилку (виняток або текст від плеєра) → коротке пояснення українською."""
    text = str(err).lower()
    m = re.search(r"http (\d{3})", text)
    if m:
        code = int(m.group(1))
        if code == 403:
            return "Сайт показує це лише зареєстрованим користувачам"
        if code == 404:
            return f"Не вдалося знайти {what} на сайті — можливо, сторінку прибрали"
        return "Сайт тимчасово недоступний. Спробуйте пізніше"
    if isinstance(err, OSError) or any(h in text for h in NETWORK_HINTS):
        if not is_online():
            return "Немає підключення до інтернету. Перевірте мережу й спробуйте ще раз"
        return "Сайт або відеосервер не відповідає. Спробуйте пізніше"
    return f"Не вдалося завантажити {what}. Спробуйте ще раз"


# ───────── головна: добірка для банера ─────────

FEATURED_SOURCES = ("/films/new_netflix_ua/",)  # банер — новинки фільмів


def featured(count=7, workers=7):
    """Новинки для банера (з описом, роком, IMDb і жанрами); кілька джерел — упереміш."""
    from concurrent.futures import ThreadPoolExecutor

    lists = []
    for path in FEATURED_SOURCES:
        try:
            lists.append(parse_catalog(fetch(catalog_url(path, 1))))
        except Exception:
            lists.append([])
    mixed, seen = [], set()
    for pair in zip(*lists) if all(lists) else [(x,) for lst in lists for x in lst]:
        for it in pair:
            if it["url"] not in seen and it["poster"]:
                seen.add(it["url"])
                mixed.append(it)
    mixed = mixed[:count]

    def enrich(item):
        try:
            info = parse_title(fetch(item["url"]), item["url"])
        except Exception:
            return dict(item, info=None)
        import tmdb  # офіційний широкий кадр замість картинки сайту (800×450, з написами)
        wide = tmdb.backdrop(item["url"], info.get("title") or item["title"], info.get("orig", ""),
                             info.get("year", ""))
        return dict(item, info=info, backdrop=wide)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(enrich, mixed))
