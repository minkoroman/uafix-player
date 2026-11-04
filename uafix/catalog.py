"""Каталог розділу, пошук, обране: сітка афіш."""
import asyncio

import flet as ft

import core
from . import kit, theme as T


class Grid:
    """Сітка афіш з кнопкою «Показати ще»."""

    def __init__(self, app, title):
        self.app = app
        self.card_w = 168 if app.mobile else (300 if app.tv else 250)
        self.title = T.caps(title, size=T.S_HEAD + 8 if not app.mobile else T.S_HEAD + 4)
        self.status = T.text("", size=T.S_SMALL, color=T.DIM)
        self.grid = ft.Row(wrap=True, spacing=4, run_spacing=10)
        self.more = kit.plate(kit.label("Показати ще"), on_click=lambda e: self.next_page())
        self.more.visible = False
        self.page_no = 0
        self.busy = False
        self.extra = []  # що показати над сіткою (поле пошуку)
        self.body = ft.ListView(expand=True, padding=ft.Padding.only(
            left=app.side - 6, right=app.side - 6, top=28, bottom=40), spacing=14)

    def view(self):
        self.body.controls = [ft.Container(self.title, padding=ft.Padding.only(left=6)),
                              *self.extra, ft.Container(self.status, padding=ft.Padding.only(left=6)),
                              self.grid, ft.Container(self.more, padding=ft.Padding.only(left=6))]
        return self.body

    def fetch_page(self, n):
        raise NotImplementedError

    def reset(self):
        self.grid.controls = []
        self.page_no = 0
        self.more.visible = False

    def next_page(self):
        if not self.busy:
            self.app.page.run_task(self.load_next)

    async def load_next(self):
        self.busy = True
        self.status.value = "Завантажую…"
        self.more.visible = False
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
            card = kit.poster_card(self.app.page, it, self.app.open_title, self.card_w,
                                   meta=self.app.store.settings.get("card_meta", True),
                                   autofocus=self.app.tv and not self.grid.controls)
            card.data = it["url"]
            self.grid.controls.append(card)
        self.more.visible = len(items) >= 10
        self.busy = False
        try:
            self.body.update()
        except Exception:
            pass


class Catalog(Grid):
    def __init__(self, app, name, path):
        super().__init__(app, name)
        self.path = path

    def fetch_page(self, n):
        return core.parse_catalog(core.fetch(core.catalog_url(self.path, n)))

    def shown(self):
        if not self.page_no and not self.busy:
            self.next_page()


class Search(Grid):
    def __init__(self, app):
        super().__init__(app, "Пошук")
        self.query = ""
        self.field = ft.TextField(
            hint_text="Назва фільму чи серіалу", autofocus=True, text_size=T.S_BODY + 2,
            bgcolor=T.WALL_2, border_color=T.LINE, focused_border_color=T.SIGNAL,
            border_radius=3, cursor_color=T.SIGNAL, color=T.INK,
            prefix_icon=ft.Icons.SEARCH_ROUNDED, on_submit=lambda e: self.run(e.control.value))
        self.extra = [ft.Container(self.field, padding=ft.Padding.only(left=6, right=6))]

    def run(self, q):
        kit.quiet()
        q = (q or "").strip()
        if not q:
            return
        self.query = q
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
            card = kit.poster_card(self.app.page, it, self.app.open_title, self.card_w,
                                   meta=self.app.store.settings.get("card_meta", True),
                                   autofocus=self.app.tv and i == 0)
            self.grid.controls.append(card)
        try:
            self.body.update()
        except Exception:
            pass
