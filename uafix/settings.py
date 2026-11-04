"""Налаштування: рядок — плашка; натискання перемикає значення по колу (зручно з пульта)."""
import time

import flet as ft

import appearance
import core
import sync
from . import kit, theme as T

QUALITY = [("1080", "1080p"), ("720", "720p"), ("480", "480p")]
BOOST = [("0", "Без підсилення"), ("6", "+6 дБ"), ("10", "+10 дБ")]
HWDEC = [("auto", "Апаратне"), ("copy", "Апаратне сумісне"), ("off", "Програмне")]
PLAYER = [("ask", "Запитувати"), ("builtin", "Вбудований"), ("external", "Інша програма")]
THEMES = [("dark", "Темна"), ("light", "Світла")]


class Settings:
    def __init__(self, app):
        self.app = app
        self.body = ft.ListView(expand=True, spacing=8, padding=ft.Padding.only(
            left=app.side, right=app.side, top=28, bottom=60))

    def view(self):
        self.render()
        return self.body

    def shown(self):
        pass

    # ── рядки
    def row(self, title, value, on_click, hint=None):
        left = ft.Column([ft.Text(title, size=T.S_BODY, weight=ft.FontWeight.W_700),
                          ft.Text(hint, size=T.S_SMALL, opacity=0.75)] if hint else
                         [ft.Text(title, size=T.S_BODY, weight=ft.FontWeight.W_700)],
                         spacing=2, tight=True, expand=True)
        right = ft.Text((value or "").upper(), size=T.S_SMALL, weight=ft.FontWeight.W_800,
                        style=ft.TextStyle(letter_spacing=1.2))
        return kit.plate(ft.Row([left, right], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         on_click=on_click, pad=(20, 16), expand=True)

    def choice(self, title, key, options, hint=None, after=None):
        """Значення з переліку; натискання — наступне."""
        cur = str(self.app.store.settings.get(key))
        keys = [k for k, _ in options]
        name = dict(options).get(cur, options[0][1])

        def next_value(e):
            i = (keys.index(cur) + 1) % len(keys) if cur in keys else 0
            self.app.store.set(key, keys[i])
            if after:
                after(keys[i])
            self.render()

        return self.row(title, name, next_value, hint)

    def toggle(self, title, key, hint=None, after=None):
        on = bool(self.app.store.settings.get(key))

        def flip(e):
            self.app.store.set(key, not on)
            if after:
                after(not on)
            self.render()

        return self.row(title, "Увімкнено" if on else "Вимкнено", flip, hint)

    def section(self, name):
        return ft.Container(kit.kicker(name), padding=ft.Padding.only(top=22, bottom=2))

    # ── екран
    def render(self):
        app, store = self.app, self.app.store
        rows = [T.caps("Налаштування", size=T.S_HEAD + 8)]
        rows += [self.section("Вигляд"),
                 self.row("Тема", dict(THEMES).get(appearance.CURRENT.get("theme"), "Темна"),
                          self.switch_theme, "Діє після перезапуску застосунку"),
                 self.toggle("Рейтинг і жанр на картках", "card_meta")]
        rows += [self.section("Перегляд"),
                 self.choice("Якість відео", "quality", QUALITY),
                 self.toggle("Пропускати заставку UAFlix", "skip_intro"),
                 self.choice("Гучність", "volume_boost", BOOST,
                             "Підсилення тихих фільмів"),
                 self.choice("Декодування відео", "hwdec", HWDEC,
                             "Якщо картинка смикається — спробуйте інше")]
        if app.android:
            rows.append(self.choice("Плеєр", "player", PLAYER))
        rows.append(self.toggle(
            "Обхід регіональних обмежень", "abroad",
            "Якщо фільм недоступний у вашій країні",
            after=lambda v: core.ABROAD.update(enabled=bool(v))))
        rows += [self.section("Історія"),
                 self.toggle("Зберігати історію переглядів", "keep_history"),
                 self.row("Очистити історію", "", self.ask_clear)]
        rows += [self.section("Синхронізація між пристроями")] + self.sync_rows()
        rows += [self.section("Про застосунок"),
                 ft.Container(T.text(f"UAFix Player {self.version()} · дані про фільми: TMDB",
                                     size=T.S_SMALL, color=T.DIM), padding=6)]
        self.body.controls = rows
        try:
            self.body.update()
        except Exception:
            pass

    @staticmethod
    def version():
        try:
            from app_version import VERSION
            return VERSION
        except Exception:
            return "2.0 (розробка)"

    def switch_theme(self, e):
        new = "light" if appearance.CURRENT.get("theme") != "light" else "dark"
        appearance.save("theme", new)
        appearance.CURRENT["theme"] = new
        self.app.toast("Тему змінено — перезапустіть застосунок")
        self.render()

    def ask_clear(self, e):
        def yes(ev):
            self.app.page.pop_dialog()
            self.app.store.clear_history()
            self.app.toast("Історію очищено")

        self.app.page.show_dialog(ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
            title=T.caps("Очистити історію?", size=T.S_HEAD),
            content=T.text("«Продовжити перегляд» стане порожнім на всіх пов'язаних пристроях.",
                           color=T.DIM),
            actions=[kit.plate(kit.label("Скасувати"), lambda ev: self.app.page.pop_dialog()),
                     kit.plate(kit.label("Очистити"), yes, kind="board", autofocus=True)]))

    # ── синхронізація
    def sync_rows(self):
        store = self.app.store
        code = store.settings.get("sync_code")
        if not code:
            return [T.text("Історія й обране — однакові на телефоні, телевізорі й комп'ютері. "
                           "Створіть код тут і введіть його на інших пристроях.",
                           size=T.S_SMALL, color=T.DIM),
                    self.row("Створити код", "", self.create_code),
                    self.row("Ввести код з іншого пристрою", "", self.enter_code)]
        last = time.strftime("%H:%M", time.localtime(store.sync_last)) if store.sync_last else "ще ні"
        return [self.row("Код синхронізації", sync.pretty(code), None,
                         "Введіть його на іншому пристрої"),
                self.row("Синхронізувати зараз", f"Востаннє: {last}", self.sync_now),
                self.row("Відʼєднати цей пристрій", "", self.disconnect)]

    def create_code(self, e):
        self.app.store.set("sync_code", sync.new_code())
        self.app.store.sync_dirty = True
        self.app.page.run_task(self._sync, False)
        self.render()

    def enter_code(self, e):
        field = ft.TextField(hint_text="XXXX-XXXX-XXXX", autofocus=True, text_size=T.S_BODY + 4,
                             bgcolor=T.WALL_2, border_color=T.LINE, focused_border_color=T.SIGNAL,
                             capitalization=ft.TextCapitalization.CHARACTERS, color=T.INK)
        err = T.text("", size=T.S_SMALL, color=T.SIGNAL)

        def ok(ev):
            code = sync.normalize(field.value)
            if not code:
                err.value = "Код — 12 літер і цифр, як на іншому пристрої"
                err.update()
                return
            self.app.page.pop_dialog()
            self.app.store.set("sync_code", code)
            self.app.store.sync_dirty = True
            self.app.page.run_task(self._sync, True)
            self.render()

        field.on_submit = ok
        self.app.page.show_dialog(ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
            title=T.caps("Код синхронізації", size=T.S_HEAD),
            content=ft.Column([field, err], tight=True, spacing=8),
            actions=[kit.plate(kit.label("Скасувати"), lambda ev: self.app.page.pop_dialog()),
                     kit.plate(kit.label("Під'єднати"), ok, kind="board")]))

    def sync_now(self, e):
        self.app.page.run_task(self._sync, True)

    async def _sync(self, notify):
        err = await self.app.store.sync_now(manual=True)
        if notify:
            self.app.toast(err or "Синхронізовано", error=bool(err))
        self.render()

    def disconnect(self, e):
        self.app.store.set("sync_code", "")
        self.app.toast("Пристрій відʼєднано: історія лишилась тут, але більше не синхронізується")
        self.render()
