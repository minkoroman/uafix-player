"""Оновлення з GitHub Releases (updater.py): перевірка, «Що нового», завантаження й встановлення."""
import asyncio
import os
import time

import flet as ft

import core
import updater
from . import kit, theme as T
from .links import CTRL

CHECK_EVERY = 6 * 3600  # автоматично — не частіше ніж раз на 6 годин


class Updates:
    def __init__(self, app):
        self.app = app
        page = app.page
        self.platform = {ft.PagePlatform.ANDROID: "android", ft.PagePlatform.ANDROID_TV: "android",
                         ft.PagePlatform.MACOS: "macos",
                         ft.PagePlatform.WINDOWS: "windows"}.get(page.platform)

    def dialog(self, title, content, actions):
        self.app.page.show_dialog(ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4), scrollable=True,
            title=T.caps(title, size=T.S_HEAD), content=content, actions=actions))

    @staticmethod
    def notes_view(notes):
        return ft.Container(ft.Markdown(notes or "—", selectable=False), width=520)

    # ── старт
    async def on_start(self):
        updater.mark_started()  # для скрипта оновлення ПК: нова версія запустилась
        prefs = self.app.store.prefs
        try:
            last = await prefs.get("last_version")
            if last != updater.VERSION:
                await prefs.set("last_version", updater.VERSION)
                if last:
                    await self.whats_new()
        except Exception:
            pass
        await self.check()

    async def whats_new(self, manual=False):
        try:
            notes = await asyncio.to_thread(updater.release_notes, updater.VERSION)
        except Exception:
            if manual:
                self.app.toast("Не вдалося завантажити список змін", error=True)
            return
        self.dialog(f"Що нового у версії {updater.VERSION}", self.notes_view(notes),
                    [kit.plate(kit.label("Чудово"), lambda e: self.app.page.pop_dialog(),
                               kind="board", autofocus=True)])

    async def check(self, manual=False):
        store = self.app.store
        if updater.VERSION == "dev":
            if manual:
                self.app.toast("Це версія для розробки — оновлення не перевіряються")
            return
        now = time.time()
        if not manual:
            if not store.settings.get("auto_updates", True):
                return
            try:
                if now - float(await store.prefs.get("update_checked_at") or 0) < CHECK_EVERY:
                    return
            except Exception:
                pass
        try:
            rel = await asyncio.to_thread(updater.latest_release)
        except Exception:
            if manual:
                self.app.toast("Не вдалося перевірити оновлення", error=True)
            return
        await store.prefs.set("update_checked_at", now)
        if updater.is_newer(rel["version"]) and (
                manual or rel["version"] != store.settings.get("skipped_version")):
            self.offer(rel)
        elif manual:
            self.app.toast(f"У вас найновіша версія {updater.VERSION}")

    # ── пропозиція й встановлення
    def offer(self, rel):
        url = updater.asset_for(rel, self.platform, self.app.tv) or rel["url"]

        def update(e):
            self.app.page.pop_dialog()
            exe = url.lower().endswith(".exe")
            desktop = self.platform in ("macos", "windows") and url != rel["url"] and (
                (exe and os.environ.get("UAFIX_LAUNCHER")) or
                (not exe and updater.desktop_target(self.platform)))
            if desktop or (self.platform == "android" and self.update_dir() and url != rel["url"]):
                self.app.page.run_task(self.download_and_install, url, rel["version"])
                return
            self.app.page.run_task(ft.UrlLauncher().launch_url, url,
                                   mode=ft.LaunchMode.EXTERNAL_APPLICATION)

        def skip(e):
            self.app.store.set("skipped_version", rel["version"])
            self.app.page.pop_dialog()

        self.dialog(f"Доступна версія {rel['version']}", ft.Column([
            T.text(f"У вас версія {updater.VERSION}. Що нового:", size=T.S_SMALL, color=T.DIM),
            self.notes_view(rel["notes"])], tight=True, spacing=10),
            [kit.plate(kit.label("Пропустити"), skip),
             kit.plate(kit.label("Пізніше"), lambda e: self.app.page.pop_dialog()),
             kit.plate(kit.label("Оновити", ft.Icons.DOWNLOAD_ROUNDED), update, kind="board",
                       autofocus=True)])

    @staticmethod
    def update_dir():
        """Кеш застосунку: на Android звідти MainActivity бере update.apk."""
        for key in ("FLET_APP_STORAGE_CACHE", "FLET_APP_STORAGE_TEMP", "FLET_APP_STORAGE_DATA"):
            if os.environ.get(key):
                return os.environ[key]
        return None

    async def download_and_install(self, url, version):
        page = self.app.page
        desktop = self.platform in ("macos", "windows")
        if desktop:
            import tempfile
            dest = os.path.join(tempfile.gettempdir(), "uafix-update.exe"
                                if url.lower().endswith(".exe") else "uafix-update.zip")
        else:
            dest = os.path.join(self.update_dir(), "update.apk")
        bar = ft.ProgressBar(value=None, color=T.SIGNAL, bgcolor=T.WALL_2, bar_height=6, width=420)
        label = T.text("Підключаюсь…", size=T.S_SMALL, color=T.DIM)
        self.dialog(f"Завантаження {version}", ft.Column(
            [label, bar, T.text("Можна сховати — завантаження триватиме у фоні.",
                                size=T.S_SMALL, color=T.DIM)], tight=True, spacing=12),
            [kit.plate(kit.label("Сховати"), lambda e: page.pop_dialog())])
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] < 0.3 and done != total:
                return
            last[0] = now
            mb = 1024 * 1024
            bar.value = done / total if total else None
            label.value = (f"{done / mb:.1f} з {total / mb:.1f} МБ · {done * 100 // total}%"
                           if total else f"{done / mb:.1f} МБ")
            try:
                bar.update()
                label.update()
            except Exception:
                pass

        try:
            await asyncio.to_thread(updater.download, url, dest, progress)
        except Exception as ex:
            try:
                page.pop_dialog()
            except Exception:
                pass
            self.app.toast(core.friendly_error(ex, "оновлення"), error=True)
            return
        try:
            page.pop_dialog()
        except Exception:
            pass
        if desktop:
            await self.install_desktop(dest, url)
            return
        # Android: системне вікно «Встановити» (MainActivity, uafixupdate://install)
        self.dialog(f"Версія {version} завантажена",
                    T.text("Натисніть «Встановити». Якщо Android попросить дозволити встановлення "
                           "з цього джерела — дозвольте, поверніться й натисніть ще раз."),
                    [kit.plate(kit.label("Пізніше"), lambda e: page.pop_dialog()),
                     kit.plate(kit.label("Встановити"), lambda e: page.run_task(self.launch_installer),
                               kind="board", autofocus=True)])
        await self.launch_installer()

    @staticmethod
    async def launch_installer():
        await ft.UrlLauncher().launch_url(f"{CTRL}://install",
                                          mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    async def install_desktop(self, path, url):
        page = self.app.page
        self.dialog("Встановлюю оновлення", ft.Row(
            [kit.spinner(20), T.text("Застосунок закриється й відкриється знову вже оновленим.",
                                     expand=True)], spacing=14, width=420), [])
        try:
            await asyncio.to_thread(updater.install_desktop, path, self.platform)
        except Exception as ex:
            page.pop_dialog()
            self.app.toast(f"Не вдалося встановити ({ex}) — відкриваю завантаження", error=True)
            await ft.UrlLauncher().launch_url(url, mode=ft.LaunchMode.EXTERNAL_APPLICATION)
            return
        await asyncio.sleep(0.8)
        try:
            await page.window.destroy()
        finally:
            os._exit(0)  # скрипт заміни чекає на завершення процесу

    async def restart(self):
        """Перезапуск (зміна теми): Android — MainActivity, ПК — скрипт перезапуску."""
        if self.platform == "android":
            await ft.UrlLauncher().launch_url(f"{CTRL}://restart",
                                              mode=ft.LaunchMode.EXTERNAL_APPLICATION)
            return
        if self.platform in ("macos", "windows") and updater.desktop_target(self.platform):
            try:
                updater.relaunch_desktop(self.platform)
            except Exception:
                pass
            else:
                await asyncio.sleep(0.3)
                try:
                    await self.app.page.window.destroy()
                finally:
                    os._exit(0)
        self.app.toast("Закрийте й знову відкрийте застосунок, щоб застосувати тему")
