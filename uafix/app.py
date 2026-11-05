"""Каркас застосунку: меню розділів, перехід між екранами, «Назад», клавіші пульта."""
import asyncio
import os
import time

import flet as ft

import core
import tor_runner
from urllib.parse import urlencode

from . import kit, theme as T
from .links import CTRL
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
    ("Історія", "history", ft.Icons.HISTORY_ROUNDED),
    ("Сповіщення", "notices", ft.Icons.NOTIFICATIONS_ROUNDED),
    ("Налаштування", "settings", ft.Icons.SETTINGS_ROUNDED),
    ("Відгук", "report", ft.Icons.BUG_REPORT_ROUNDED),
]
MOBILE_NAV = ("home", "search", "/films/", "/serials/", "favorites")
RAIL_TV_MIN, RAIL_TV_MAX = 64, 220  # меню TV: згорнуте (іконки) / розгорнуте
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
        self.side = 18 if self.mobile else (32 if self.tv else 44)
        self.key_handler = None   # клавіші для поточного екрана (плеєр)
        self.player = None        # відкритий плеєр (для паузи, коли застосунок іде у фон)
        self.back_at = 0.0
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
            try:  # новий розмір — від старого лівого краю: вікно вилазило праворуч за екран
                await page.window.center()
            except Exception:
                pass
        page.on_view_pop = lambda e: self.back()
        page.on_route_change = lambda e: (kit.quiet(), self.handle_link(e.route))
        page.on_keyboard_event = self.on_key
        page.on_app_lifecycle_state_change = self.on_lifecycle
        page.on_resize = self.on_resize
        await self.store.load()
        core.ABROAD["enabled"] = bool(self.store.settings.get("abroad", True))
        core.ABROAD["get_proxy"] = self.tor_proxy
        if not self.android:  # старі версії Tor і запасні теки даних (як у 1.x)
            page.run_thread(tor_runner.cleanup_old)
        page.run_task(self.store.sync_loop)
        from .updates import Updates
        from .notices import Notices
        self.updates = Updates(self)
        self.notices = Notices(self)

        from .home import Home
        from .catalog import Catalog, Search, Favorites, History
        from .settings import Settings
        self.screens = {"home": Home(self), "search": Search(self), "favorites": Favorites(self),
                        "history": History(self),
                        "settings": Settings(self)}
        self.Catalog = Catalog
        self.content = ft.Container(expand=True)
        if self.mobile:
            root = ft.Column([self.content, self.bottom_bar()], spacing=0, expand=True)
        elif self.tv:
            # TV (як Google TV): меню — смужка з іконками; фокус у ньому (стрілка вліво від
            # крайньої картки) — розгортається поверх вмісту з підписами
            root = ft.Stack([ft.Container(self.content, left=RAIL_TV_MIN, top=0, right=0,
                                          bottom=0), self.rail()], expand=True)
        else:
            root = ft.Row([self.rail(), ft.VerticalDivider(width=1, color=T.LINE), self.content],
                          spacing=0, expand=True)
        page.views.clear()
        page.views.append(ft.View(route="/", controls=[ft.SafeArea(root, expand=True)
                                                       if self.mobile else root],
                                  padding=0, bgcolor=T.FACADE))
        page.update()  # без цього на телефоні до згортання/розгортання був сірий екран
        self.show("home")
        page.run_task(self.updates.on_start)
        page.run_task(self.notices.loop)
        self.handle_link(page.route)  # застосунок відкрили посиланням uafix://open?url=…

    def tor_proxy(self):
        def android_launch(exits):
            self.page.run_task(ft.UrlLauncher().launch_url,
                               f"{CTRL}://tor?" + urlencode({"exits": ",".join(exits[:40])}),
                               mode=ft.LaunchMode.EXTERNAL_APPLICATION)
        return tor_runner.proxy(android_launch if self.android else None)

    # ── меню
    def rail(self):
        self.rail_items, self.rail_texts = {}, []
        self.rail_focus = set()
        tv = self.tv
        items = []
        for name, key, icon in NAV:
            lab = kit.label(name, icon, size=T.S_SMALL, left=True)
            if key == "notices":  # дзвіночок з крапкою, коли є новини
                lab.controls[0] = self.notices.bell_icon(T.S_SMALL + 6)
            if tv:
                lab.controls[-1].visible = False  # згорнуте: лише іконка
                self.rail_texts.append(lab.controls[-1])
            btn = kit.plate(lab, on_click=lambda e, k=key, n=name: self.show(k, n),
                            pad=(14, 8 if tv else 12),
                            on_focus=(lambda f, k=key: self.rail_focused(k, f)) if tv else None)
            btn.width = (RAIL_TV_MAX if tv else 236) - 32  # на всю ширину меню, напис — ліворуч
            self.rail_items[key] = btn
            items.append(ft.Container(btn, width=float("inf")))
        self.rail_mark = ft.Column([T.caps("UAFix", size=22 if tv else 26, weight=ft.FontWeight.W_900,
                                           spacing=0.06),
                                    kit.kicker("кінотеатр українською")], spacing=2, tight=True,
                                   visible=not tv)
        if tv:
            # «стінки» над першим і під останнім пунктом: затиснуте вгору / вниз інакше
            # виводило фокус із меню на картки (Flutter шукав найближче вище / нижче)
            keys = list(self.rail_items)
            items = [self.rail_wall(keys[0])] + items + [self.rail_wall(keys[-1])]
        self.rail_box = ft.Container(
            ft.Column([self.rail_mark, ft.Container(height=22 if not tv else 8)] + items,
                      spacing=2 if tv else 6, scroll=ft.ScrollMode.AUTO),
            width=RAIL_TV_MIN if tv else 236,
            padding=ft.Padding.only(left=8 if tv else 16, right=8 if tv else 16, top=12 if tv else 26),
            bgcolor=T.WALL, clip_behavior=ft.ClipBehavior.HARD_EDGE)  # без анімації: підписи
            #                                   не встигали за шириною й ламались у два рядки
        if tv:
            self.rail_box.left, self.rail_box.top, self.rail_box.bottom = 0, 0, 0
            for b in self.rail_items.values():
                b.width = RAIL_TV_MIN - 16
        return self.rail_box

    async def keep_focus(self, control):
        await asyncio.sleep(0.01)
        try:
            await control.focus()
        except Exception:
            pass

    def rail_wall(self, edge_key):
        """Невидима кнопка-«стінка»: отримала фокус — одразу повертає його на край меню."""
        return self.focus_wall(lambda: self.rail_items[edge_key])

    def focus_wall(self, target, width=None):
        """Невидима «стінка» на краю списку: фокус, що на неї потрапив, повертається на target()."""
        def back(e):
            kit.quiet()
            t = target()
            if t is not None:
                self.page.run_task(self.keep_focus, t)
        return ft.Button(
            content=ft.Container(height=2), on_click=lambda e: None, height=2, on_focus=back,
            width=width,  # широка — щоб Flutter бачив її «над» картками, а не меню
            style=ft.ButtonStyle(bgcolor=ft.Colors.TRANSPARENT, elevation=0, padding=0,
                                 overlay_color=ft.Colors.TRANSPARENT,
                                 shadow_color=ft.Colors.TRANSPARENT, animation_duration=0))

    # ── меню відкривається лише стрілкою вліво: якщо фокус «упав» у меню зі вмісту стрілкою
    #    вгору / вниз (Flutter шукає найближче вище / нижче — а це пункти меню), його одразу
    #    повертає назад на той самий елемент вмісту
    def content_focused(self, rebuild):
        """Вміст отримав фокус. rebuild() → той самий елемент, свіжий (див. focus_wall:
        інакше Flutter пам'ятає шлях і наступна стрілка назад лише «поверталась» на нього)."""
        self.zone = "content"
        self.content_rebuild = rebuild

    def rail_focused(self, key, on):
        """TV: фокус зайшов у меню — розгорнути (підписи, назва); вийшов — згорнути."""
        if on:
            self.rail_focus.add(key)
        else:
            self.rail_focus.discard(key)
        self.page.run_task(self._rail_settle)

    async def _rail_settle(self):
        await asyncio.sleep(0.05)  # перехід між пунктами меню: blur одного, focus іншого
        if getattr(self, "zone", "") == "content" and self.rail_focus \
                and getattr(self, "content_rebuild", None) is not None:
            # фокус зі вмісту потрапив у меню. Пускаємо лише стрілку вліво. На ТВ подія
            # клавіші приходить ПІСЛЯ зміни фокусу — трохи чекаємо на неї
            since = time.monotonic() - 0.4
            for _ in range(8):
                if getattr(self, "last_arrow_at", 0) >= since:
                    break
                await asyncio.sleep(0.04)
            if getattr(self, "last_arrow", "") != "Arrow Left":
                self.last_arrow = ""
                target = self.content_rebuild()
                if target is not None:
                    await self.keep_focus(target)
                    return
        if self.rail_focus:
            self.zone = "rail"
        open_ = bool(self.rail_focus)
        if (self.rail_box.width == RAIL_TV_MAX) == open_:
            return
        self.rail_box.width = RAIL_TV_MAX if open_ else RAIL_TV_MIN
        self.rail_mark.visible = open_
        for t in self.rail_texts:
            t.visible = open_
        for b in self.rail_items.values():
            b.width = (RAIL_TV_MAX if open_ else RAIL_TV_MIN) - 16
        self.rail_box.shadow = ft.BoxShadow(blur_radius=40, color="#99000000") if open_ else None
        self.rail_box.update()
        cur = self.rail_items.get(self.section)
        if open_ and cur is not None and self.section not in self.rail_focus:
            await cur.focus()  # зайшли в меню — фокус на поточному розділі (як у Google TV)

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
        if key == "notices":  # не розділ — вікно поверх поточного
            self.notices.open()
            return
        if key == "report":  # не розділ — вікно поверх поточного
            from .report import open_report
            open_report(self)
            return
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

    def handle_link(self, route):
        """uafix://open?url=<сторінка тайтлу>[&play=1] — напр. «Продовжити» з головного
        екрана телевізора. Лише сторінки тайтлів самого сайту."""
        from urllib.parse import parse_qs, urlparse
        if "url=" not in (route or ""):
            return
        q = parse_qs(urlparse(route).query)
        url = (q.get("url") or [""])[0].split("?")[0].split("#")[0]
        if not url.startswith(core.BASE + "/") or not core.ITEM_PATH_RE.match(urlparse(url).path):
            return
        url += "" if url.endswith("/") else "/"
        store = self.store
        h = store.entry(url) or next((f for f in store.favorites if f["url"] == url), None) or {}
        item = {k: h.get(k, "") for k in ("title", "orig", "poster", "age")}
        item["url"] = url
        while len(self.page.views) > 1:
            self.back()
        self.open_title(item, autoplay=(q.get("play") or ["0"])[0] == "1" and bool(h))

    def toast(self, message, error=False):
        self.page.show_dialog(ft.SnackBar(
            ft.Text(message, color=T.LETTER, weight=ft.FontWeight.W_600),
            bgcolor=T.SIGNAL if error else T.BOARD, duration=ft.Duration(seconds=4)))

    def on_resize(self, e):
        """Ширина вікна змінилась — сітки розділів перекладають картки по рядах."""
        kit.quiet()
        screen = self.screens.get(self.section)
        if hasattr(screen, "relayout"):
            before = screen.per
            screen.relayout()
            if screen.per != before:
                screen.flush()

    def on_lifecycle(self, e):
        """Телефон: застосунок пішов у фон / закрили плаваюче вікно — пауза (у самому плаваючому
        вікні застосунок лише «неактивний», і відео грає далі). ТВ: заставка чи кнопка «Додому»
        — пауза, а коли повернулись — панель плеєра з фокусом на ▶."""
        kit.quiet()
        p = self.player
        if p is None or not p.alive or not (self.mobile or self.tv):
            return
        if e.state in (ft.AppLifecycleState.HIDE, ft.AppLifecycleState.PAUSE):
            p.suspended = True
            self.page.run_task(p.pause)
        elif e.state in (ft.AppLifecycleState.SHOW, ft.AppLifecycleState.RESUME) and self.tv \
                and getattr(p, "suspended", False):
            # лише після справжнього виходу у фон: індикатор гучності ТВ теж ненадовго
            # «забирає» застосунок
            p.suspended = False
            p.show_overlay(True)

    async def on_key(self, e: ft.KeyboardEvent):
        kit.quiet()
        if e.key.startswith("Arrow "):
            self.last_arrow, self.last_arrow_at = e.key, time.monotonic()
        if self.tv and self.rail_focus and e.key in ("Arrow Up", "Arrow Down") \
                and getattr(self, "zone", "") == "rail":
            # краї меню: Flutter шукає «найближче» вище / нижче й перескакував праворуч у вміст
            keys = list(self.rail_items)
            edge = keys[0] if e.key == "Arrow Up" else keys[-1]
            if edge in self.rail_focus:
                self.page.run_task(self.keep_focus, self.rail_items[edge])
                return
        if e.key in BACK_KEYS:
            # затиснута клавіша (чи повтор пульта) шле «Назад» сотнями — один раз на 0,4 с
            now = time.monotonic()
            if now - self.back_at < 0.4:
                self.back_at = now
                return
            self.back_at = now
        if self.key_handler and await self.key_handler(e.key):
            return
        if e.key in BACK_KEYS and not self.mobile:
            self.back()


def run(page: ft.Page):
    app = App(page)
    page.run_task(app.start)
