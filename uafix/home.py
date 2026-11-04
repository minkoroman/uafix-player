"""Головна: табло «Сьогодні в залі» (продовжити перегляд) і ряди афіш."""
import asyncio

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


def ep_text(h):
    parts = [h["season"]] if h.get("season") else []
    if h.get("label") and h["label"] != "Фільм":
        parts.append(h["label"].split(" — ")[0])
    return " · ".join(parts) or "Фільм"


class Home:
    def __init__(self, app):
        self.app = app
        self.card_w = 200 if app.mobile else (300 if app.tv else 250)
        self.board_box = ft.Column(spacing=0)
        self.board_section = ft.Container(
            self.board_box, padding=ft.Padding.only(left=app.side, right=app.side, top=28),
            visible=False)
        self.shelves = [self.shelf(*s) for s in SHELVES]
        self.list = ft.ListView(
            [self.board_section] + [col for col, _ in self.shelves] + [ft.Container(height=40)],
            expand=True, spacing=0)
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
            if self.app.mobile:
                cells = [(h.get("title"), 3, "main"), (left_text(h), 2, "signal")]
            else:
                cells = [(h.get("title"), 7, "main"), (ep_text(h), 4, "dim"),
                         (left_text(h), 4, "signal"), (h.get("voice") or "", 3, "dim")]
            remove = kit.board_icon(ft.Icons.CLOSE_ROUNDED,
                                    lambda e, u=h["url"]: self.app.store.remove(u),
                                    tooltip="Прибрати з історії")
            rows.append(kit.board_row(
                cells, on_click=lambda e, it=item: self.app.open_title(it),
                last=i == len(items) - 1, trailing=remove))
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
        row = ft.ListView(kit.placeholder_row(6, w), horizontal=True, spacing=8,
                          height=w / (16 / 9) + 84,
                          padding=ft.Padding.symmetric(horizontal=self.app.side - 6))
        more = kit.plate(kit.label("Усі"), on_click=lambda e: self.app.show(path, name),
                         pad=(14, 8)) if not self.app.tv else ft.Container()
        head = ft.Container(ft.Row([T.caps(name, size=T.S_HEAD), ft.Container(expand=True), more],
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=ft.Padding.only(left=self.app.side, right=self.app.side,
                                                    top=34, bottom=6))

        async def load():
            try:
                items = await asyncio.to_thread(core.fresh_catalog, path, block)
            except Exception:
                items = []
            items = items[:14] if self.app.tv else items[:24]
            cards = [kit.poster_card(self.app.page, it, self.app.open_title, w,
                                     meta=self.app.store.settings.get("card_meta", True))
                     for it in items]
            if self.app.tv and cards:  # замість «Усі» в заголовку — остання плашка в ряду
                cards.append(ft.Container(kit.plate(
                    ft.Container(kit.label("Усі", ft.Icons.ARROW_FORWARD_ROUNDED),
                                 height=w / (16 / 9), alignment=ft.Alignment.CENTER),
                    on_click=lambda e: self.app.show(path, name)), padding=6))
            row.controls = cards or [ft.Container(T.text("Не вдалося завантажити", color=T.DIM),
                                                  padding=20)]
            try:
                row.update()
            except Exception:
                pass

        return ft.Column([head, row], spacing=0), load
