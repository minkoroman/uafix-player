"""Каталог розділу, пошук, обране: сітка афіш."""
import asyncio

import flet as ft

import core
from . import kit, theme as T


class Grid:
    """Сітка афіш з кнопкою «Показати ще»."""

    def __init__(self, app, title):
        self.app = app
        self.card_w = 104 if app.mobile else (118 if app.tv else 160)
        self.title = T.caps(title, size=T.S_HEAD + 8 if not app.mobile else T.S_HEAD + 4)
        self.status = T.text("", size=T.S_SMALL, color=T.DIM)
        self.grid = ft.Row(wrap=True, spacing=4, run_spacing=10)
        self.page_no = 0
        self.busy = False
        self.extra = []  # що показати над сіткою (поле пошуку)
        self.has_more = False
        # наступна сторінка — сама, коли до кінця лишається приблизно екран
        self.body = ft.ListView(expand=True, padding=ft.Padding.only(
            left=app.side - 6, right=app.side - 6, top=28, bottom=40), spacing=14,
            scroll_interval=200, on_scroll=self.on_scroll)

    def view(self):
        # TV: «стінка» на всю ширину над усім — «вгору» з верхнього ряду не виводить у меню
        top = [self.app.focus_wall(self.bounce, width=4000)] if self.app.tv else []
        self.body.controls = top + [ft.Container(self.title, padding=ft.Padding.only(left=6)),
                                    *self.extra, ft.Container(self.status, padding=ft.Padding.only(left=6)),
                                    self.grid]
        return self.body

    _rebuild = None  # як перебудувати елемент у фокусі (для «стінки» вгорі)

    def bounce(self):
        """Фокус влучив у «стінку» — назад на той самий елемент, але свіжий: Flutter пам'ятає
        шлях фокуса, і зі старим елементом наступне «вниз» лише «поверталось» би на нього ж."""
        return self._rebuild() if self._rebuild else None

    def make_card(self, it, i, autofocus=False):
        card = kit.poster_card(self.app.page, it, self.app.open_title, self.card_w,
                               meta=self.app.store.settings.get("card_meta", True),
                               autofocus=autofocus,
                               on_focus=lambda f, i=i: f and self.card_focused(i))
        card.data = it["url"]
        card.item_ = it
        return card

    def card_focused(self, i):
        self._rebuild = lambda: self.rebuild_card(i)
        self.app.content_focused(self.bounce)
        self.near_end(i)

    def rebuild_card(self, i):
        if i >= len(self.grid.controls):
            return None
        fresh = self.make_card(self.grid.controls[i].item_, i)
        self.grid.controls[i] = fresh
        try:
            self.grid.update()
        except Exception:
            return None
        return fresh

    def fetch_page(self, n):
        raise NotImplementedError

    def reset(self):
        self.grid.controls = []
        self.page_no = 0
        self.has_more = False

    def on_scroll(self, e: ft.OnScrollEvent):
        kit.quiet()
        # extent_after — скільки ще прокручувати до кінця
        if self.has_more and not self.busy and e.extent_after is not None and \
                e.extent_after < 900:
            self.next_page()

    def next_page(self):
        if not self.busy:
            self.app.page.run_task(self.load_next)

    async def load_next(self):
        self.busy = True
        self.status.value = "Завантажую…" if not self.grid.controls else ""
        try:
            self.body.update()
        except Exception:
            pass
        try:
            items = await asyncio.to_thread(self.fetch_page, self.page_no + 1)
            self.page_no += 1
            self.status.value = "" if (items or self.grid.controls) else "Нічого не знайдено"
        except Exception as ex:
            items = []
            self.status.value = core.friendly_error(ex, "розділ")
        known = {getattr(c, "data", None) for c in self.grid.controls}
        for it in items:
            if it["url"] in known:
                continue
            self.grid.controls.append(self.make_card(
                it, len(self.grid.controls), autofocus=self.app.tv and not self.grid.controls))
        self.has_more = len(items) >= 10
        self.busy = False
        try:
            self.body.update()
        except Exception:
            pass
        # сайт дає по 12 на сторінку: поки екран не заповнений (прокрутки нема — й події
        # прокрутки нема), довантажуємо одразу
        if self.has_more and len(self.grid.controls) < self.MIN_ITEMS:
            self.next_page()

    MIN_ITEMS = 36

    def near_end(self, n):
        """Фокус пульта: перший ряд — докрутити до самого верху (видно назву розділу);
        останні два ряди — наступна сторінка, не чекаючи прокрутки."""
        if self.app.tv and n < self.per_row():
            self._top_n = getattr(self, "_top_n", 0) + 1
            self.app.page.run_task(self._to_top, self._top_n)
        if self.has_more and not self.busy and n >= len(self.grid.controls) - 16:
            self.next_page()

    def per_row(self):
        """Скільки карток уміщається в ряд (ширина вікна мінус меню й поля)."""
        from .app import RAIL_TV_MIN
        width = (self.app.page.width or 960) - (RAIL_TV_MIN if self.app.tv else 0) \
            - 2 * (self.app.side - 6)
        return max(1, int((width + 4) // (self.card_w + 6 + 8 + 4)))

    async def _to_top(self, n):
        await asyncio.sleep(0.12)  # після власного автоскролу Flutter (той лише «підтягує» картку)
        if n != self._top_n:
            return
        try:
            await self.body.scroll_to(offset=0, duration=160)
        except Exception:
            pass


# тематичні добірки розділу — їх немає серед жанрів фільтра сайту (окремі сторінки)
COLLECTIONS = {
    "/films/": [("Про кохання", "/films/love_films/"), ("Для підлітків", "/films/teen_films/"),
                ("Про школу", "/films/school_films/")],
    "/serials/": [("Про кохання", "/serials/pro_love/"), ("Про підлітків", "/serials/pro_pidlitkiv/"),
                  ("Про школу", "/serials/school/")],
}


class Catalog(Grid):
    """Розділ сайту з фільтрами (жанр, країна, сортування… — як на сайті) і добірками."""

    def __init__(self, app, name, path):
        super().__init__(app, name)
        self.root = path    # сам розділ; path — він же або відкрита добірка
        self.path = path
        self.filters = []   # [{field, title, options}]
        self.chosen = {}    # field → value
        self.filter_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self.extra = [ft.Container(self.filter_row, padding=ft.Padding.only(left=6, right=6))]

    def fetch_page(self, n):
        if self.chosen:
            return core.catalog_filtered(self.path, n, dict(self.chosen))
        items = core.parse_catalog(core.fetch(core.catalog_url(self.path, n)))
        if items:  # наступну сторінку — наперед: прокрутка далі без очікування
            import threading
            threading.Thread(target=core.prefetch, args=(core.catalog_url(self.path, n + 1),),
                             daemon=True).start()
        return items

    def shown(self):
        if not self.page_no and not self.busy:
            self.next_page()
            self.app.page.run_task(self.load_filters)

    async def load_filters(self):
        try:
            self.filters = await asyncio.to_thread(core.section_filters, self.path)
        except Exception:
            self.filters = []
        self.render_filters()

    def render_filters(self):
        plates = []
        foc = lambda i: (lambda f: f and self.filter_focused(i))  # noqa: E731
        if self.root in COLLECTIONS:
            name = next((n for n, p in COLLECTIONS[self.root] if p == self.path), None)
            plates.append(kit.plate(kit.label(f"Добірка: {name or 'усі'}"),
                                    on_click=lambda e: self.pick_collection(),
                                    kind="lit" if name else "plain", pad=(14, 10),
                                    on_focus=foc(0)))
        for f in self.filters:
            value = self.chosen.get(f["field"])
            name = dict(f["options"]).get(value, "Усі") if value else "Усі"
            plates.append(kit.plate(kit.label(f"{f['title']}: {name}"),
                                    on_click=lambda e, f=f: self.pick(f),
                                    kind="lit" if value else "plain", pad=(14, 10),
                                    on_focus=foc(len(plates))))
        if self.chosen:
            plates.append(kit.plate(kit.label("Скинути", ft.Icons.CLOSE_ROUNDED),
                                    on_click=lambda e: self.apply({}), pad=(14, 10),
                                    on_focus=foc(len(plates))))
        self.filter_row.controls = plates
        try:
            self.filter_row.update()
        except Exception:
            pass

    def filter_focused(self, i):
        self._rebuild = lambda: self.rebuild_filter(i)
        self.app.content_focused(self.bounce)

    def rebuild_filter(self, i):
        self.render_filters()
        return self.filter_row.controls[i] if i < len(self.filter_row.controls) else None

    def pick(self, f):
        """Вікно з варіантами фільтра — плашками (зручно пультом)."""
        cur = self.chosen.get(f["field"])

        def choose(value):
            self.app.page.pop_dialog()
            chosen = dict(self.chosen)
            if value is None:
                chosen.pop(f["field"], None)
            else:
                chosen[f["field"]] = value
            self.apply(chosen)

        opts = [(None, "Усі")] + list(f["options"])
        plates = [kit.plate(kit.label(label), on_click=lambda e, v=v: choose(v),
                            kind="lit" if v == cur else "plain", pad=(14, 10),
                            autofocus=v == cur)
                  for v, label in opts]
        self.app.page.show_dialog(ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
            title=T.caps(f["title"], size=T.S_HEAD),
            content=ft.Container(ft.Row(plates, wrap=True, spacing=8, run_spacing=8),
                                 width=640)))

    def pick_collection(self):
        def choose(path):
            self.app.page.pop_dialog()
            self.path = path
            self.chosen = {}
            self.filters = []
            self.render_filters()
            self.reset()
            self.next_page()
            self.app.page.run_task(self.load_filters)

        opts = [(self.root, "Усі")] + [(p, n) for n, p in COLLECTIONS[self.root]]
        plates = [kit.plate(kit.label(label), on_click=lambda e, v=v: choose(v),
                            kind="lit" if v == self.path else "plain", pad=(14, 10),
                            autofocus=v == self.path)
                  for v, label in opts]
        self.app.page.show_dialog(ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
            title=T.caps("Добірка", size=T.S_HEAD),
            content=ft.Container(ft.Row(plates, wrap=True, spacing=8, run_spacing=8),
                                 width=640)))

    def apply(self, chosen):
        self.chosen = chosen
        self.render_filters()
        self.reset()
        self.next_page()


class Search(Grid):
    def __init__(self, app):
        super().__init__(app, "Пошук")
        self.query = ""
        self.field = ft.TextField(
            hint_text="Назва фільму чи серіалу", autofocus=True, text_size=T.S_BODY + 2,
            bgcolor=T.WALL_2, border_color=T.LINE, focused_border_color=T.SIGNAL,
            border_radius=3, cursor_color=T.SIGNAL, color=T.INK,
            prefix_icon=ft.Icons.SEARCH_ROUNDED, on_submit=lambda e: self.run(e.control.value),
            on_change=self.typed,
            on_focus=lambda e: (setattr(self, "_rebuild", lambda: self.field),
                                self.app.content_focused(self.bounce)))
        self.recent = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self.recent_box = ft.Container(
            ft.Column([kit.kicker("Нещодавні пошуки"), self.recent], spacing=10, tight=True),
            padding=ft.Padding.only(left=6, right=6, top=4))
        self.extra = [ft.Container(self.field, padding=ft.Padding.only(left=6, right=6)),
                      self.recent_box]
        self.fill_recent()

    # ── нещодавні пошуки: під полем, поки нічого не шукаємо (або що підходить до набраного)
    def fill_recent(self, typed=""):
        t = typed.strip().lower()
        items = [q for q in self.app.store.searches if t in q.lower()] if t else \
            list(self.app.store.searches)
        chips = []
        for q in items:
            chips.append(ft.Row([
                kit.plate(kit.label(q, ft.Icons.HISTORY_ROUNDED), pad=(14, 10),
                          on_click=lambda e, q=q: self.pick(q)),
                kit.plate(ft.Icon(ft.Icons.CLOSE_ROUNDED, size=16), pad=(10, 10),
                          tooltip="Прибрати", on_click=lambda e, q=q: self.forget(q))],
                spacing=2, tight=True))
        if items and not t:
            chips.append(kit.plate(kit.label("Очистити"), pad=(14, 10),
                                   on_click=lambda e: self.forget(None)))
        self.recent.controls = chips
        self.recent_box.visible = bool(items) and not (self.query and self.grid.controls
                                                         and t == self.query.lower())

    def refresh_recent(self, typed=""):
        self.fill_recent(typed)
        try:
            self.recent_box.update()
        except Exception:
            pass

    def typed(self, e):
        kit.quiet()
        self.refresh_recent(e.control.value or "")

    def pick(self, q):
        self.field.value = q
        try:
            self.field.update()
        except Exception:
            pass
        self.run(q)

    def forget(self, q):
        kit.quiet()
        self.app.store.forget_search(q)
        self.refresh_recent(self.field.value or "")

    def run(self, q):
        kit.quiet()
        q = (q or "").strip()
        if not q:
            return
        self.query = q
        self.app.store.remember_search(q)
        self.recent_box.visible = False
        try:
            self.recent_box.update()
        except Exception:
            pass
        self.reset()
        self.next_page()

    def fetch_page(self, n):
        return core.parse_catalog(core.fetch(core.search_url(self.query, n)))

    def shown(self):
        self.app.page.run_task(self.focus_field)

    async def focus_field(self):
        await asyncio.sleep(0.2)
        try:
            await self.field.focus()
        except Exception:
            pass


class Favorites(Grid):
    def __init__(self, app):
        super().__init__(app, "Обране")

    def shown(self):
        self.reset()
        favs = self.app.store.active_favorites()
        self.status.value = "" if favs else "Тут з'являться фільми, які ви додасте в обране"
        for i, it in enumerate(favs):
            self.grid.controls.append(self.make_card(it, i, autofocus=self.app.tv and i == 0))
        try:
            self.body.update()
        except Exception:
            pass


class History(Grid):
    """Усе переглянуте (і завершене) — новіше першим."""

    def __init__(self, app):
        super().__init__(app, "Історія переглядів")

    def shown(self):
        self.reset()
        items = [h for h in self.app.store.history if not h.get("removed") and h.get("url")]
        self.status.value = "" if items else "Тут з'являться фільми й серіали, які ви дивились"
        for i, h in enumerate(items):
            it = {k: h.get(k, "") for k in ("url", "title", "orig", "poster", "age")}
            self.grid.controls.append(self.make_card(it, i, autofocus=self.app.tv and i == 0))
        try:
            self.body.update()
        except Exception:
            pass
