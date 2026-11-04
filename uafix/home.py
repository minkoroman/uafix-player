"""Головна: табло «Сьогодні в залі» (продовжити перегляд) і ряди афіш."""
import asyncio
import json
import os

import flet as ft

import core
from . import kit, theme as T

# ряди афіш: (назва, розділ, блок головної сайту — звідти найсвіжіше)
SHELVES = [
    ("Озвучка від UAflix", "/dubbing/", "Наша озвучка"),
    ("Нові серії", "/serials/new_uaserial/", "Серіали"),
    ("Новинки кіно", "/films/new_netflix_ua/", "Фільми"),
    ("Серіали 2026", "/serials/serial_2026_online/", None),
    ("Аніме", "/anime/", "Аніме"),
    ("Мультфільми", "/cartoons/", "Мультфільми"),
]


def left_text(h):
    """«Лишилось 34 хв» — як час до кінця сеансу (позиція й тривалість у мс)."""
    if not h.get("dur"):
        return "Почато"
    m = max(1, int((h["dur"] - h["pos"]) // 60000))
    return f"Лишилось {m // 60} г {m % 60:02d} хв" if m >= 60 else f"Лишилось {m} хв"


def ep_text(h, short=False):
    """«Сезон 1 · Серія 3» (short — «С1 · Е3», для компактного табло)."""
    parts = [h["season"]] if h.get("season") else []
    if h.get("label") and h["label"] != "Фільм":
        parts.append(h["label"].split(" — ")[0])
    text = " · ".join(parts) or "Фільм"
    if short:
        text = text.replace("Сезон ", "С").replace("Серія ", "Е")
    return text


HOME_CACHE = {}  # розділ → картки з минулого запуску (показати одразу, поки вантажиться свіже)


def _cache_path():
    base = os.environ.get("FLET_APP_STORAGE_DATA") or os.path.expanduser("~")
    return os.path.join(base, ".uafix2_home.json")


def load_home_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            HOME_CACHE.update(json.load(f))
    except Exception:
        pass


def save_home_cache():
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(HOME_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


class Home:
    def __init__(self, app):
        self.app = app
        self.card_w = 112 if app.mobile else (118 if app.tv else 160)
        self.board_box = ft.Column(spacing=0)
        self.board_section = ft.Container(
            self.board_box, padding=ft.Padding.only(left=app.side, right=app.side, top=28),
            visible=False)
        self.fav_new = ft.Container(padding=ft.Padding.only(left=app.side, right=app.side, top=24),
                                    visible=False)
        load_home_cache()
        self.shelves = [self.shelf(*s) for s in SHELVES]
        # телефон: назва й налаштування зверху (у нижньому меню — лише головні розділи)
        top = [ft.Container(ft.Row([
            T.caps("UAFix", size=22, weight=ft.FontWeight.W_900, spacing=0.06),
            kit.plate(ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=20),
                      on_click=lambda e: app.show("settings", "Налаштування"), pad=(10, 10))],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.Padding.only(left=app.side, right=app.side, top=14))] if app.mobile else []
        if app.tv:  # назва застосунку — у боковому меню; тут лише «стінка» на всю ширину:
            # зайве «вгору» з першого ряду лишає фокус на місці, а не виводить його в меню
            top = [app.focus_wall(lambda: self.last_focus, width=4000)]
        self.last_focus = None
        self.list = ft.ListView(
            top + [self.board_section, self.fav_new] + [col for col, _ in self.shelves] +
            [ft.Container(height=400 if app.tv else 40)],  # TV: щоб і нижні ряди ставали вгору
            expand=True, spacing=0, build_controls_on_demand=not app.tv)
        self.loaded = False
        self.continue_urls = None
        app.store.listeners.append(self.refresh_board)

    def view(self):
        return self.list

    def shown(self):
        self.refresh_board(focus=True)
        if not self.loaded:
            self.loaded = True
            for _, load in self.shelves:
                self.app.page.run_task(load)
            self.app.page.run_task(self.check_fav_new)

    # ── табло «Продовжити перегляд»
    def refresh_board(self, focus=False):
        store = self.app.store
        items = store.continuing()[:4 if self.app.mobile else 5]
        urls = [(h["url"], h.get("pos"), h.get("voice")) for h in items]
        if urls == self.continue_urls and not focus:
            return
        self.continue_urls = urls
        rows = []
        for i, h in enumerate(items):
            item = {k: h.get(k, "") for k in ("url", "title", "orig", "poster", "age")}
            if self.app.mobile or self.app.tv:  # компактно: назва, під нею решта
                cells = [(h.get("title"), 1, "main"), (ep_text(h, True), 1, "dim"),
                         (left_text(h), 1, "signal"), (h.get("voice") or "", 1, "dim")]
            else:
                cells = [(h.get("title"), 7, "main"), (ep_text(h), 4, "dim"),
                         (left_text(h), 4, "signal"), (h.get("voice") or "", 3, "dim")]
            remove = kit.board_icon(ft.Icons.CLOSE_ROUNDED,
                                    lambda e, u=h["url"]: self.app.store.remove(u),
                                    tooltip="Прибрати з історії")
            rows.append(kit.board_row(
                cells, on_click=lambda e, it=item: self.app.open_title(it),
                last=i == len(items) - 1, trailing=remove,
                on_focus=(lambda i=i: self.focused(None, rows[i].focus_target))
                if self.app.tv else None))
        self.board_box.controls = [kit.board(rows, "Сьогодні в залі · Продовжити перегляд")] \
            if rows else []
        self.board_section.visible = bool(rows)
        try:
            self.board_section.update()
        except Exception:
            return
        if focus and self.app.tv and rows and len(self.app.page.views) == 1:
            self.app.page.run_task(self.focus_later, rows[0].focus_target)

    async def focus_later(self, control):
        await asyncio.sleep(0.3)
        try:
            await control.focus()
        except Exception:
            pass

    # ── ряд афіш
    def shelf(self, name, path, block):
        w = self.card_w
        row = ft.ListView(kit.placeholder_row(8, w), horizontal=True, spacing=6,
                          height=w / kit.RATIO + 70,
                          padding=ft.Padding.symmetric(horizontal=self.app.side - 6))
        more = kit.plate(kit.label("Усі"), on_click=lambda e: self.app.show(path, name),
                         pad=(14, 8)) if not self.app.tv else ft.Container()
        head = ft.Container(ft.Row([T.caps(name, size=T.S_HEAD), ft.Container(expand=True), more],
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=ft.Padding.only(left=self.app.side, right=self.app.side,
                                                    top=34, bottom=6))

        def fill(items):
            items = items[:14] if self.app.tv else items[:24]
            # TV: фокус на афіші — показати весь ряд разом із заголовком (інакше список
            # прокручувався рівно до картки, і підпис ряду ховався вгорі)
            cards = []
            for it in items:
                box = []
                cards.append(kit.poster_card(
                    self.app.page, it, self.app.open_title, w,
                    meta=self.app.store.settings.get("card_meta", True),
                    on_focus=(lambda f, k=f"shelf-{path}", b=box: f and self.focused(k, b[0]))
                    if self.app.tv else None))
                box.append(cards[-1])
            if self.app.tv and cards:  # замість «Усі» в заголовку — остання плашка в ряду
                box = []
                cards.append(ft.Container(kit.plate(
                    ft.Container(kit.label("Усі", ft.Icons.ARROW_FORWARD_ROUNDED),
                                 height=w / kit.RATIO - 28, width=w - 36,  # як постер
                                 alignment=ft.Alignment.CENTER),
                    on_click=lambda e: self.app.show(path, name),
                    on_focus=lambda f, k=f"shelf-{path}", b=box: f and self.focused(k, b[0])),
                    padding=ft.Padding.only(left=6, right=6, top=6)))
                box.append(cards[-1].content)
            row.controls = cards or [ft.Container(T.text("Не вдалося завантажити", color=T.DIM),
                                                  padding=20)]
            try:
                row.update()
            except Exception:
                pass

        async def load():
            # спершу — ряд з минулого запуску (миттєво), потім свіже з сайту, якщо змінилось
            cached = HOME_CACHE.get(path) or []
            if cached:
                fill(cached)
            try:
                items = await asyncio.to_thread(core.fresh_catalog, path, block)
            except Exception:
                items = []
            if items and [i["url"] for i in items[:24]] != [i["url"] for i in cached[:24]]:
                fill(items)
                HOME_CACHE[path] = items[:24]
                save_home_cache()
            elif not items and not cached:
                fill([])

        col = ft.Column([head, row], spacing=0, key=ft.ScrollKey(f"shelf-{path}"))
        return col, load

    async def check_fav_new(self):
        """Серіали з «Обраного», у яких щойно вийшла нова серія (розділ сайту «Нові серії»)."""
        favs = {f["url"] for f in self.app.store.active_favorites()}
        if not favs:
            return
        fresh = []
        for n in (1, 2):
            try:
                items = await asyncio.to_thread(lambda n=n: core.parse_catalog(
                    core.fetch(core.catalog_url("/serials/new_uaserial/", n))))
            except Exception:
                break
            fresh += [it for it in items if it["url"] in favs and it not in fresh]
        if not fresh:
            return
        rows = [kit.board_row([(it["title"], 3, "main"), ("Нова серія", 1, "signal")],
                              on_click=lambda e, it=it: self.app.open_title(it),
                              last=i == len(fresh[:6]) - 1)
                for i, it in enumerate(fresh[:6])]
        self.fav_new.content = kit.board(rows, "Нові серії ваших серіалів")
        self.fav_new.visible = True
        try:
            self.fav_new.update()
        except Exception:
            pass

    def focused(self, key, control):
        self.last_focus = control
        self.reveal_row(key)

    def reveal_row(self, key):
        """TV: ряд у фокусі — разом із заголовком. Табло (key=None) і перший ряд — на самий
        верх списку. Докручуємо ПІСЛЯ власного автоскролу Flutter (той підтягує лише картку,
        і заголовок ряду ховався) — на кожен фокус, останній виграє."""
        self._reveal_n = getattr(self, "_reveal_n", 0) + 1
        self.app.page.run_task(self._reveal, key, self._reveal_n)

    async def _reveal(self, key, n):
        await asyncio.sleep(0.12)
        if n != self._reveal_n:
            return  # уже є новіший фокус
        first = f"shelf-{SHELVES[0][1]}"
        try:
            if key is None or (key == first and not self.board_section.visible):
                await self.list.scroll_to(offset=0, duration=160)
            else:
                await self.list.scroll_to(scroll_key=ft.ScrollKey(key), duration=160)
        except Exception:
            pass
