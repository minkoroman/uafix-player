"""Плеєр: відео на весь екран, панель знизу (ховається сама), пульт — стрілками й OK."""
import asyncio
import time

import flet as ft
import flet_video as fv

import core
from . import kit, theme as T
from .title import fmt_time

# запас для нестабільного відеосервера: без нього плеєр підвисав і розсинхронювався
MPV_STREAMING = {
    "cache": "yes", "cache-secs": 60, "demuxer-readahead-secs": 60,
    "demuxer-max-bytes": "150MiB", "demuxer-max-back-bytes": "50MiB",
    "cache-pause-initial": "yes", "cache-pause-wait": 3, "network-timeout": 20,
    "stream-lavf-o": "reconnect=1,reconnect_streamed=1,reconnect_on_network_error=1,"
                     "reconnect_delay_max=5",
}
SKIP_INTRO_MS = 16000  # заставка UAFLIX на початку файлів з «uaflix» у назві
OK_KEYS = ("Enter", "Select", "Space", " ", "Numpad Enter", "Media Play Pause")
BACK_KEYS = ("Escape", "Go Back", "Browser Back")


class Player:
    def __init__(self, app, title, si, ei, pos):
        self.app = app
        self.title = title
        self.si, self.ei, self.start = si, ei, pos or 0
        self.ep = title.seasons[si]["episodes"][ei]
        self.entry = title.pick_source(self.ep)
        self.voice = self.entry.get("voice") or title.voice
        self.alive = True
        self.shown_at = 0.0
        self.dur = 0
        self.last = (0, 0)  # остання відома (позиція, тривалість) — зберегти при виході
        self.ended = False

    # ── побудова
    def open(self):
        app = self.app
        settings = app.store.settings
        props = dict(MPV_STREAMING)
        boost = str(settings.get("volume_boost") or "0")
        if boost != "0":
            props.update({"replaygain": "track", "replaygain-fallback": boost})
        kw = {}
        if settings.get("hwdec") == "off":
            kw["enable_hardware_acceleration"] = False
        elif settings.get("hwdec") == "copy":
            kw["hardware_decoding_api"] = "mediacodec-copy" if app.android else "auto-copy"
        try:
            core.hls_variants(self.entry)  # якості — щоб узяти обрану (1080 / 720 / 480)
        except Exception:
            pass
        url = core.quality_url(self.entry["url"], settings.get("quality") or "1080")
        self.video = fv.Video(
            expand=True, autoplay=True, fill_color="#000000",
            title=self.title.item["title"],
            playlist=[fv.VideoMedia(url, http_headers=core.stream_headers(self.entry))],
            playlist_mode=fv.PlaylistMode.NONE,
            configuration=fv.VideoConfiguration(mpv_properties=props, **kw),
        )
        season = self.title.seasons[self.si]["name"]
        what = self.ep["label"] if len(self.title.all_episodes()) > 1 else ""
        self.time_text = T.text("0:00", size=T.S_SMALL + 1, color="#F1E6CC",
                                weight=ft.FontWeight.W_600)
        self.bar = ft.ProgressBar(value=0, color=T.SIGNAL, bgcolor="#33F1E6CC", bar_height=4)
        self.play_btn = kit.plate(ft.Icon(ft.Icons.PAUSE_ROUNDED, size=28),
                                  on_click=lambda e: self.app.page.run_task(self.toggle),
                                  kind="board", pad=(18, 12))
        buttons = [
            kit.plate(ft.Icon(ft.Icons.REPLAY_10_ROUNDED, size=26),
                      on_click=lambda e: self.app.page.run_task(self.seek_by, -10), pad=(14, 12)),
            self.play_btn,
            kit.plate(ft.Icon(ft.Icons.FORWARD_30_ROUNDED, size=26),
                      on_click=lambda e: self.app.page.run_task(self.seek_by, 30), pad=(14, 12)),
        ]
        if self.has_next():
            buttons.append(kit.plate(kit.label("Наступна", ft.Icons.SKIP_NEXT_ROUNDED),
                                     on_click=lambda e: self.app.page.run_task(self.next_episode)))
        heading = ft.Column([
            kit.kicker(" · ".join(x for x in (season if what else "", what, self.voice) if x),
                       color="#B8AC95"),
            ft.Text(self.title.info.get("title") or self.title.item["title"], size=T.S_HEAD + 6,
                    weight=ft.FontWeight.W_800, color="#F1E6CC", max_lines=1)], spacing=4, tight=True)
        back = kit.plate(ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=24),
                         on_click=lambda e: self.app.back(), pad=(12, 10))
        top = ft.Container(ft.Row([back, heading], spacing=16), padding=ft.Padding.only(
            left=self.app.side, right=self.app.side, top=22, bottom=40),
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                       end=ft.Alignment.BOTTOM_CENTER,
                                       colors=["#CC000000", "#00000000"]))
        bottom = ft.Container(ft.Column([
            self.bar,
            ft.Row([ft.Row(buttons, spacing=10), self.time_text],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER)], spacing=14),
            padding=ft.Padding.only(left=self.app.side, right=self.app.side, top=50, bottom=26),
            gradient=ft.LinearGradient(begin=ft.Alignment.BOTTOM_CENTER,
                                       end=ft.Alignment.TOP_CENTER,
                                       colors=["#E6000000", "#00000000"]))
        # середина: тап — показати панель, а коли вона видна — пауза / продовження.
        # Майже прозорий фон обов'язковий: без нього дотики «провалювались» у відео.
        self.center_icon = ft.Container(
            ft.Icon(ft.Icons.PAUSE_ROUNDED, size=44, color="#F1E6CC"), width=96, height=96,
            border_radius=48, bgcolor="#99000000", alignment=ft.Alignment.CENTER,
            visible=not self.app.tv)
        middle = ft.GestureDetector(
            ft.Container(self.center_icon, expand=True, bgcolor="#01000000",
                         alignment=ft.Alignment.CENTER),
            on_tap=lambda e: (kit.quiet(), self.app.page.run_task(self.tap_center)),
            on_hover=lambda e: (kit.quiet(), self.show_overlay(False)), expand=True)
        self.top, self.bottom = top, bottom
        # шари стопки — прив'язані до країв (без цього панель не мала розміру й не малювалась)
        self.overlay = ft.Container(ft.Column([top, middle, bottom], spacing=0),
                                    left=0, top=0, right=0, bottom=0)
        self.overlay.opacity = 1
        stack = ft.Stack([self.video, self.overlay], expand=True)
        self.view = self.app.push("/play", stack, bgcolor="#000000")
        self.view.on_close = self.close
        self.app.key_handler = self.on_key
        self.app.page.run_task(self.after_start)
        self.app.page.run_task(self.ticker)
        self.show_overlay(False)

    # ── панель
    def show_overlay(self, focus=True):
        """Панель видна: кнопки на місці (схована — їх немає зовсім, щоб не натиснути
        невидиму). overlay.opacity — лише позначка «видно / ні»."""
        self.shown_at = time.monotonic()
        if self.overlay.opacity != 1:
            self.overlay.opacity = 1
            self.top.visible = self.bottom.visible = True
            self.center_icon.visible = not self.app.tv
            self.overlay.update()
            if focus and self.app.tv:
                self.app.page.run_task(self.play_btn.focus)

    def hide_overlay(self):
        if self.overlay.opacity != 0:
            self.overlay.opacity = 0
            self.top.visible = self.bottom.visible = self.center_icon.visible = False
            self.overlay.update()

    async def tap_center(self):
        if self.overlay.opacity != 1:
            self.show_overlay(False)
        else:
            await self.toggle()

    async def ticker(self):
        """Час і смужка; сховати панель за 4 с; позиція в історію — раз на 10 с."""
        saved_at = 0
        while self.alive:
            await asyncio.sleep(1)
            if not self.alive:
                return
            try:
                pos = (await self.video.get_current_position()).in_milliseconds
                dur = (await self.video.get_duration()).in_milliseconds
                playing = await self.video.is_playing()
            except Exception:
                continue
            self.dur = dur or self.dur
            if dur and pos > 1000:
                self.last = (pos, dur)
            # кінець серії (подія on_complete у плеєрі спрацьовує й передчасно)
            if dur > 60000 and pos >= dur - 1500 and not playing and not self.ended:
                self.ended = True
                await self.next_episode()
                return
            if self.overlay.opacity == 1:
                self.time_text.value = f"{fmt_time(pos)} / {fmt_time(dur)}" if dur else fmt_time(pos)
                self.bar.value = pos / dur if dur else 0
                self.set_play_icon(playing)
                self.time_text.update()
                self.bar.update()
                if playing and time.monotonic() - self.shown_at > 4:
                    self.hide_overlay()
            if dur and pos > 1000 and time.monotonic() - saved_at > 10:
                saved_at = time.monotonic()
                self.save(pos, dur)

    def save(self, pos, dur):
        t = self.title
        t.app.store.record(t.item, t.seasons[self.si]["name"] if len(t.all_episodes()) > 1 else "",
                           self.ep["label"] if len(t.all_episodes()) > 1 else "Фільм",
                           self.si, self.ei, self.voice, pos, dur)

    async def after_start(self):
        """Коли відео почало грати — перейти на місце зупинки (або пропустити заставку)."""
        target = self.start
        if not target and self.app.store.settings.get("skip_intro", True) and \
                "uaflix" in self.entry["url"].lower():
            target = SKIP_INTRO_MS
        if target < 3000:
            return
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                if (await self.video.get_duration()).in_milliseconds > 0:
                    await self.video.seek(int(target))
                    return
            except Exception:
                pass

    def set_play_icon(self, playing):
        icon = ft.Icons.PAUSE_ROUNDED if playing else ft.Icons.PLAY_ARROW_ROUNDED
        if self.play_btn.content.icon != icon:
            self.play_btn.content.icon = icon
            self.center_icon.content.icon = icon
            self.play_btn.update()
            self.center_icon.update()

    # ── керування
    async def toggle(self):
        await self.video.play_or_pause()
        self.show_overlay(False)
        try:
            self.set_play_icon(await self.video.is_playing())
        except Exception:
            pass

    async def seek_by(self, seconds):
        try:
            pos = (await self.video.get_current_position()).in_milliseconds
        except Exception:
            return
        await self.video.seek(max(0, int(pos + seconds * 1000)))
        self.show_overlay(False)

    def has_next(self):
        eps = self.title.seasons[self.si]["episodes"]
        return self.ei + 1 < len(eps) or self.si + 1 < len(self.title.seasons)

    async def next_episode(self):
        if not self.has_next():
            self.app.back()
            return
        si, ei = (self.si, self.ei + 1) if self.ei + 1 < len(self.title.seasons[self.si]["episodes"]) \
            else (self.si + 1, 0)
        self.app.back()
        await self.title.play(si, ei, 0)

    async def on_key(self, key):
        if key in BACK_KEYS:
            self.app.back()
            return True
        if key in ("Media Fast Forward", "Media Rewind"):
            await self.seek_by(30 if key == "Media Fast Forward" else -10)
            return True
        if key in ("Media Play", "Media Pause", "Media Play Pause"):
            await self.toggle()
            return True
        if self.overlay.opacity == 1:
            self.shown_at = time.monotonic()
            return False  # панель видно — стрілки ходять по кнопках
        if key in ("Arrow Left", "Arrow Right"):
            await self.seek_by(-10 if key == "Arrow Left" else 10)
            return True
        if key in OK_KEYS:
            await self.toggle()
            return True
        if key in ("Arrow Up", "Arrow Down"):
            self.show_overlay()
            return True
        return False

    def close(self):
        """Вихід з плеєра: зберегти позицію й зупинити відео."""
        self.alive = False
        self.app.key_handler = None

        if self.last[1]:
            self.save(*self.last)
