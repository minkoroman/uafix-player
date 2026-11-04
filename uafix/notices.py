"""Сповіщення (дзвіночок): нова версія застосунку й нові серії серіалів з «Обраного».
Червона крапка на дзвіночку — є що подивитись."""
import asyncio

import flet as ft

import updater
from . import kit, theme as T

QUIET_EVERY = 3 * 3600  # тихо (без вікон) дізнатись про нову версію — для крапки


class Notices:
    def __init__(self, app):
        self.app = app
        self.rel = None     # знайдена новіша версія
        self.fresh = []     # нові серії обраного [{url, title, …}]
        self.dots = []

    # ── дзвіночок
    def dot(self):
        d = ft.Container(width=9, height=9, border_radius=5, bgcolor=T.SIGNAL,
                         border=ft.Border.all(1.5, T.WALL), visible=self.has_news(),
                         right=-2, top=-2)
        self.dots.append(d)
        return d

    def bell_icon(self, size):
        """Іконка дзвіночка з крапкою."""
        return ft.Stack([ft.Icon(ft.Icons.NOTIFICATIONS_ROUNDED, size=size), self.dot()],
                        clip_behavior=ft.ClipBehavior.NONE)

    def has_news(self):
        return bool(self.rel or self.fresh)

    def refresh(self):
        on = self.has_news()
        for d in self.dots:
            if d.visible != on:
                d.visible = on
                try:
                    d.update()
                except Exception:
                    pass

    def set_update(self, rel):
        self.rel = rel
        self.refresh()

    def set_fresh(self, items):
        self.fresh = list(items)
        self.refresh()

    async def loop(self):
        if updater.VERSION == "dev":
            return
        await asyncio.sleep(60)  # не заважати першому завантаженню (оновлення й так перевіряє Updates)
        while True:
            try:
                rel = await asyncio.to_thread(updater.latest_release)
                self.set_update(rel if updater.is_newer(rel["version"]) else None)
            except Exception:
                pass
            await asyncio.sleep(QUIET_EVERY)

    # ── вікно
    def open(self):
        kit.quiet()
        app, page = self.app, self.app.page
        items = []  # (клітинки, дія)
        if self.rel:
            items.append(([(f"Доступна версія {self.rel['version']}", 3, "main"),
                           ("Оновити", 1, "signal")],
                          lambda e: (page.pop_dialog(), app.updates.offer(self.rel))))
        for it in self.fresh[:8]:
            items.append(([(it["title"], 3, "main"), ("Нова серія", 1, "signal")],
                          lambda e, it=it: (page.pop_dialog(), app.open_title(it))))
        rows = [kit.board_row(cells, on_click=go, last=i == len(items) - 1, autofocus=i == 0)
                for i, (cells, go) in enumerate(items)]
        body = kit.board(rows) if rows else ft.Container(
            T.text("Нових сповіщень немає", color=T.DIM), padding=6)

        def check(e):
            page.pop_dialog()
            page.run_task(app.updates.check, True)

        def whats_new(e):
            page.pop_dialog()
            page.run_task(app.updates.whats_new, True)

        page.show_dialog(ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4), scrollable=True,
            title=T.caps("Сповіщення", size=T.S_HEAD),
            content=ft.Container(body, width=520),
            actions=[kit.plate(kit.label("Перевірити оновлення"), check, pad=(14, 10)),
                     kit.plate(kit.label(f"Що нового в {updater.VERSION}"), whats_new,
                               pad=(14, 10)),
                     kit.plate(kit.label("Закрити"), lambda e: page.pop_dialog(), pad=(14, 10),
                               autofocus=not rows)]))
