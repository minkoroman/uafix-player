"""Каркас застосунку: меню розділів, перехід між екранами, «Назад», клавіші пульта."""
import asyncio
import os

import flet as ft

import core
import tor_runner
from urllib.parse import urlencode

from . import kit, theme as T
from .store import Store

# розділи меню: (назва, ключ / шлях розділу сайту, іконка)
NAV = [
    ("Головна", "home", ft.Icons.HOME_ROUNDED),
    ("Пошук", "search", ft.Icons.SEARCH_ROUNDED),
    ("Фільми", "/films/", ft.Icons.THEATERS_ROUNDED),
    ("Серіали", "/serials/", ft.Icons.LIVE_TV_ROUNDED),
    ("Мультфільми", "/cartoons/", ft.Icons.CHILD_CARE_ROUNDED),
    ("Аніме", "/anime/", ft.Icons.AUTO_AWESOME_ROUNDED),
    ("Обране", "favorites", ft.Icons.BOOKMARK_ROUNDED),
]
MOBILE_NAV = ("home", "search", "/films/", "/serials/", "favorites")
BACK_KEYS = ("Escape", "Go Back", "Browser Back")


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.store = Store(page)
        self.tv = T.TV
        self.mobile = (page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
                       and not T.TV) or os.environ.get("UAFIX_MOBILE") == "1"
        self.android = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.ANDROID_TV)
        T.configure(self.mobile)  # розміри шрифтів — під телефон / TV / ПК
        self.side = 18 if self.mobile else 44
        self.key_handler = None   # клавіші для поточного екрана (плеєр)
        self.section = "home"
        self.screens = {}

    # ── запуск
    async def start(self):
        page = self.page
        page.title = "UAFix Player"
        page.fonts = {T.FONT: T.FONT_FILE}
        page.theme = page.dark_theme = T.theme()
        page.theme_mode = ft.ThemeMode.LIGHT if T.LIGHT else ft.ThemeMode.DARK
        page.bgcolor = T.FACADE
        page.padding = 0
        if page.platform in (ft.PagePlatform.MACOS, ft.PagePlatform.WINDOWS):
            page.window.width, page.window.height = 1280, 820
            page.window.min_width, page.window.min_height = 760, 540
            page.window.bgcolor = T.FACADE
            page.update()
        page.on_view_pop = lambda e: self.back()
        page.on_keyboard_event = self.on_key
        await self.store.load()
        core.ABROAD["enabled"] = bool(self.store.settings.get("abroad", True))
        core.ABROAD["get_proxy"] = self.tor_proxy

        from .home import Home
        from .catalog import Catalog, Search, Favorites
        self.screens = {"home": Home(self), "search": Search(self), "favorites": Favorites(self)}
        self.Catalog = Catalog
        self.content = ft.Container(expand=True)
        root = ft.Row([self.rail(), ft.VerticalDivider(width=1, color=T.LINE), self.content],
                      spacing=0, expand=True) if not self.mobile else \
            ft.Column([self.content, self.bottom_bar()], spacing=0, expand=True)
        page.views.clear()
        page.views.append(ft.View(route="/", controls=[ft.SafeArea(root, expand=True)
                                                       if self.mobile else root],
                                  padding=0, bgcolor=T.FACADE))
        page.update()  # без цього на телефоні до згортання/розгортання був сірий екран
        self.show("home")

    def tor_proxy(self):
        def android_launch(exits):
            self.page.run_task(ft.UrlLauncher().launch_url,
                               "uafixupdate://tor?" + urlencode({"exits": ",".join(exits[:40])}),
                               mode=ft.LaunchMode.EXTERNAL_APPLICATION)
        return tor_runner.proxy(android_launch if self.android else None)

    # ── меню
    def rail(self):
        self.rail_items = {}
        items = []
        for name, key, icon in NAV:
            btn = kit.plate(kit.label(name, icon, size=T.S_SMALL + (2 if self.tv else 0)),
                            on_click=lambda e, k=key, n=name: self.show(k, n),
                            pad=(16, 16 if self.tv else 12))
            self.rail_items[key] = btn
            items.append(ft.Container(btn, width=float("inf")))
        mark = ft.Column([T.caps("UAFix", size=26, weight=ft.FontWeight.W_900, spacing=0.06),
                          kit.kicker("кінотеатр українською")], spacing=2, tight=True)
        return ft.Container(
            ft.Column([mark, ft.Container(height=22)] + items, spacing=6,
                      scroll=ft.ScrollMode.AUTO),
            width=250 if self.tv else 220, padding=ft.Padding.only(left=20, right=16, top=30),
            bgcolor=T.WALL)

    def bottom_bar(self):
        dests = [(n, k, i) for n, k, i in NAV if k in MOBILE_NAV]
        self.mobile_keys = [k for _, k, _ in dests]
        return ft.NavigationBar(
            destinations=[ft.NavigationBarDestination(
                icon=ft.Icon(i, color=T.INK), selected_icon=ft.Icon(i, color=T.LETTER), label=n)
                for n, _, i in dests],
            bgcolor=T.WALL, indicator_color=T.BOARD, height=68,
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            on_change=lambda e: self.show(self.mobile_keys[e.control.selected_index]))

    def mark_rail(self):
        if self.mobile:
            return
        for key, btn in self.rail_items.items():
            on = key == self.section
            btn.style.bgcolor = ({kit.CTRL.FOCUSED: T.SIGNAL, kit.CTRL.HOVERED: T.SIGNAL,
                                  kit.CTRL.DEFAULT: T.BOARD} if on else
                                 {kit.CTRL.FOCUSED: T.BOARD, kit.CTRL.HOVERED: T.BOARD,
                                  kit.CTRL.DEFAULT: ft.Colors.TRANSPARENT})
            btn.style.color = btn.style.icon_color = (
                {kit.CTRL.FOCUSED: "#FFFFFF", kit.CTRL.HOVERED: "#FFFFFF",
                 kit.CTRL.DEFAULT: T.LETTER} if on else
                {kit.CTRL.FOCUSED: T.LETTER, kit.CTRL.HOVERED: T.LETTER, kit.CTRL.DEFAULT: T.INK})
            btn.style.side = {kit.CTRL.FOCUSED: ft.BorderSide(3, T.SIGNAL),
                              kit.CTRL.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
            try:
                btn.update()
            except Exception:
                pass

    def show(self, key, name=None):
        """Розділ у правій частині (головна, пошук, каталог, обране)."""
        kit.quiet()
        self.section = key
        if key not in self.screens:
            self.screens[key] = self.Catalog(self, name or key, key)
        screen = self.screens[key]
        self.content.content = screen.view()
        self.mark_rail()
        try:
            self.content.update()
        except Exception:
            pass
        screen.shown()

    # ── екрани поверх (тайтл, плеєр)
    def push(self, route, control, bgcolor=None):
        view = ft.View(route=route, controls=[ft.SafeArea(control, expand=True)
                                              if self.mobile else control],
                       padding=0, bgcolor=bgcolor or T.FACADE)
        self.page.views.append(view)
        self.page.update()
        return view

    def back(self):
        kit.quiet()
        if len(self.page.views) <= 1:
            if self.section != "home":
                self.show("home")
            return
        view = self.page.views.pop()
        closer = getattr(view, "on_close", None)
        if closer:
            closer()
        if self.page.views[-1].route != "/play":
            self.key_handler = None
        self.page.update()
        top = self.page.views[-1]
        if getattr(top, "on_return", None):
            top.on_return()
        elif len(self.page.views) == 1:
            self.screens[self.section].shown()

    def open_title(self, item, autoplay=False):
        from .title import TitleScreen
        self.page.run_task(TitleScreen(self, item).open, autoplay)

    def toast(self, message, error=False):
        self.page.show_dialog(ft.SnackBar(
            ft.Text(message, color=T.LETTER, weight=ft.FontWeight.W_600),
            bgcolor=T.SIGNAL if error else T.BOARD, duration=ft.Duration(seconds=4)))

    async def on_key(self, e: ft.KeyboardEvent):
        kit.quiet()
        if self.key_handler and await self.key_handler(e.key):
            return
        if e.key in BACK_KEYS and not self.mobile:
            self.back()


def run(page: ft.Page):
    app = App(page)
    page.run_task(app.start)
