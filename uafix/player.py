"""Плеєр: відео на весь екран, панель знизу (ховається сама), пульт — стрілками й OK."""
import asyncio
import time

import flet as ft
import flet_video as fv

import core
from . import kit, theme as T
from .links import CTRL
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
        # субтитри — як обирали востаннє для такого типу доріжки (оригінал / українська)
        self.sub_key = "sub_orig" if core.is_original(self.voice) else "sub_ua"
        self.sub_lang = app.store.settings.get(self.sub_key) or None
        self.alive = True
        self.shown_at = 0.0
        self.dur = 0
        self.last = (0, 0)  # остання відома (позиція, тривалість) — зберегти при виході
        self.ended = False
        self.reopening = False
        self.mini_on = False      # ПК: маленьке вікно поверх інших
        self.mini_saved = None
        self.pip_on = None        # Android: плаваюче вікно при згортанні (лише поки грає)

    # ── побудова
    def open(self, mini=False):
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
        # якості потоку (1080 / 720 / 480) уже запитано у фоні (TitleScreen.play)
        url = core.quality_url(self.entry["url"], settings.get("quality") or "1080")
        base = 20 if app.mobile else 24 if app.tv else 28
        try:
            base = round(base * float(settings.get("sub_scale") or 1.0))
        except (TypeError, ValueError):
            pass
        self.video = fv.Video(
            expand=True, autoplay=True, fill_color="#000000", fit=self.fit_mode(),
            # свої вбудовані кнопки плеєра — геть: керування наше (панель), а невидимі вбудовані
            # перехоплювали фокус пульта («вправо» з кнопок панелі губилось у них)
            controls=None,
            title=self.title.item["title"],
            playlist=[fv.VideoMedia(url, http_headers=core.stream_headers(self.entry))],
            playlist_mode=fv.PlaylistMode.NONE,
            configuration=fv.VideoConfiguration(mpv_properties=props, **kw),
            subtitle_track=self.track(),
            subtitle_configuration=fv.VideoSubtitleConfiguration(text_style=ft.TextStyle(
                size=base, color="#FFFFFF", weight=ft.FontWeight.W_600, bgcolor="#99000000")),
        )
        season = self.title.seasons[self.si]["name"]
        what = self.ep["label"] if len(self.title.all_episodes()) > 1 else ""
        self.time_text = T.text("0:00", size=T.S_SMALL + 1, color="#F1E6CC",
                                weight=ft.FontWeight.W_600)
        self.scrubbing = False
        if self.app.tv:  # пульт: перемотка кнопками ±10 / +30 с, смужка — лише показ
            self.bar = ft.ProgressBar(value=0, color=T.SIGNAL, bgcolor="#33F1E6CC", bar_height=4)
            bar_box = self.bar
        else:  # мишка / палець: клік чи перетягування по смужці — перемотка
            self.bar = ft.Slider(value=0, min=0, max=1, active_color=T.SIGNAL,
                                 inactive_color="#33F1E6CC", thumb_color="#F1E6CC",
                                 overlay_color=ft.Colors.TRANSPARENT, padding=0,
                                 mouse_cursor=ft.MouseCursor.CLICK,
                                 on_change_start=self.scrub_start, on_change=self.scrub_move,
                                 on_change_end=self.scrub_end)
            bar_box = ft.Container(self.bar, height=18)
        self.play_btn = kit.plate(ft.Icon(ft.Icons.PAUSE_ROUNDED, size=28),
                                  on_click=lambda e: self.app.page.run_task(self.toggle),
                                  kind="glass", pad=(18, 12))
        buttons = [
            kit.plate(ft.Icon(ft.Icons.REPLAY_10_ROUNDED, size=26),
                      on_click=lambda e: self.app.page.run_task(self.seek_by, -10), pad=(14, 12),
                      kind="glass"),
            self.play_btn,
            kit.plate(ft.Icon(ft.Icons.FORWARD_30_ROUNDED, size=26),
                      on_click=lambda e: self.app.page.run_task(self.seek_by, 30), pad=(14, 12),
                      kind="glass"),
        ]
        if self.has_next():
            buttons.append(kit.plate(kit.label("Наступна", ft.Icons.SKIP_NEXT_ROUNDED),
                                     on_click=lambda e: self.app.page.run_task(self.next_episode),
                                     kind="glass"))
        options = self.option_buttons()
        heading = ft.Column([
            kit.kicker(" · ".join(x for x in (season if what else "", what, self.voice) if x),
                       color="#B8AC95"),
            ft.Text(self.title.info.get("title") or self.title.item["title"], size=T.S_HEAD + 6,
                    weight=ft.FontWeight.W_800, color="#F1E6CC", max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS)], spacing=4, tight=True, expand=True)
        back = kit.plate(ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=24),
                         on_click=lambda e: self.app.back(), pad=(12, 10), kind="glass")
        top = ft.Container(ft.Row([back, heading], spacing=16), padding=ft.Padding.only(
            left=self.app.side, right=self.app.side, top=22, bottom=40),
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                       end=ft.Alignment.BOTTOM_CENTER,
                                       colors=["#CC000000", "#00000000"]))
        bottom = ft.Container(ft.Column([
            bar_box,
            # телефон: кнопки переносяться рядками (вертикально всі в один ряд не влазили)
            ft.Row([ft.Row(buttons, spacing=10, wrap=self.app.mobile, run_spacing=8),
                    ft.Row(options + [self.time_text], spacing=10, wrap=self.app.mobile,
                           run_spacing=8)],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=self.app.mobile,
                   run_spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)], spacing=14),
            padding=ft.Padding.only(left=self.app.side, right=self.app.side, top=50, bottom=26),
            gradient=ft.LinearGradient(begin=ft.Alignment.BOTTOM_CENTER,
                                       end=ft.Alignment.TOP_CENTER,
                                       colors=["#E6000000", "#00000000"]))
        # середина: тап — показати панель, а коли вона видна — пауза / продовження.
        # Майже прозорий фон обов'язковий: без нього дотики «провалювались» у відео.
        # велика ▶ посередині — лише на паузі: під час гри там спінер завантаження плеєра
        self.center_icon = ft.Container(
            ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=48, color="#F1E6CC"), width=96, height=96,
            border_radius=48, bgcolor="#99000000", alignment=ft.Alignment.CENTER, visible=False)
        self.paused = False
        middle = ft.GestureDetector(
            ft.Container(self.center_icon, expand=True, bgcolor="#01000000",
                         alignment=ft.Alignment.CENTER),
            on_tap=lambda e: (kit.quiet(), self.app.page.run_task(self.tap_center)),
            on_hover=lambda e: (kit.quiet(), self.show_overlay(False)), expand=True)
        self.top, self.bottom = top, bottom
        self.panel_focus = False
        if self.app.tv:
            self._track_panel_focus(top)
            self._track_panel_focus(bottom)
        # шари стопки — прив'язані до країв (без цього панель не мала розміру й не малювалась)
        self.overlay = ft.Container(ft.Column([top, middle, bottom], spacing=0),
                                    left=0, top=0, right=0, bottom=0)
        self.overlay.opacity = 1
        # міні-плеєр: вікно тягнеться за будь-яке місце кадру; маленька кругла «розгорнути»
        self.mini_drag = ft.WindowDragArea(ft.Container(bgcolor="#01000000"), left=0, top=0,
                                           right=0, bottom=0, visible=False)
        self.mini_btn = ft.Container(
            ft.IconButton(ft.Icons.OPEN_IN_FULL_ROUNDED, icon_size=16, icon_color="#F1E6CC",
                          tooltip="Розгорнути", width=34, height=34,
                          on_click=lambda e: (kit.quiet(), self.app.page.run_task(self.toggle_mini)),
                          style=ft.ButtonStyle(bgcolor="#99000000", padding=0,
                                               shape=ft.CircleBorder())),
            right=10, top=10, visible=False)
        stack = ft.Stack([self.video, self.overlay, self.mini_drag, self.mini_btn], expand=True)
        self.view = self.app.push("/play", stack, bgcolor="#000000")
        self.view.on_close = self.close
        self.app.key_handler = self.on_key
        self.app.player = self
        self.app.page.run_task(self.after_start)
        self.app.page.run_task(self.ticker)
        if self.app.mobile:
            self.immersive(self.landscape())
        self.show_overlay(False)
        if mini:  # перезапуск з міні-плеєра (інша озвучка / якість) — лишитись маленьким
            self.mini_on = True
            self.mini_btn.visible = self.mini_drag.visible = True
            self.video.fit = ft.BoxFit.COVER
            self.top.visible = self.bottom.visible = False

    # ── перемотка мишкою по смужці
    def scrub_start(self, e):
        kit.quiet()
        self.scrubbing = True
        self.show_overlay(False)

    def scrub_move(self, e):
        kit.quiet()
        self.shown_at = time.monotonic()
        if self.dur:
            pos = int(float(e.control.value) * self.dur)
            self.time_text.value = f"{fmt_time(pos)} / {fmt_time(self.dur)}"
            self.time_text.update()

    def scrub_end(self, e):
        kit.quiet()
        if self.dur:
            pos = int(float(e.control.value) * self.dur)
            self.app.page.run_task(self.seek_to, pos)
        self.scrubbing = False

    async def seek_to(self, ms):
        try:
            await self.video.seek(max(0, int(ms)))
        except Exception:
            pass

    # ── масштаб кадру: кнопка в плеєрі перемикає по колу й запам'ятовує
    FITS = [("contain", "Вписати", ft.Icons.FIT_SCREEN_ROUNDED),
            ("cover", "Заповнити", ft.Icons.CROP_FREE_ROUNDED),
            ("fill", "Розтягнути", ft.Icons.OPEN_IN_FULL_ROUNDED)]

    # ── телефон: поворот горизонтально — на весь екран (без системних панелей)
    def landscape(self):
        p = self.app.page
        return bool(p.width and p.height and p.width > p.height)

    def immersive(self, on):
        if self.app.mobile and self.app.android and getattr(self, "_immersive", None) != on:
            self._immersive = on
            self.app.page.run_task(ft.UrlLauncher().launch_url,
                                   f"{CTRL}://immersive?on={1 if on else 0}",
                                   mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    def on_resize(self):
        """Повернули телефон: горизонтально — повний екран і обраний масштаб, вертикально —
        системні панелі назад і кадр цілком."""
        if not self.app.mobile or not self.alive:
            return
        land = self.landscape()
        self.immersive(land)
        if not self.mini_on:
            self.video.fit = self.fit_mode()
            try:
                self.video.update()
            except Exception:
                pass

    def phone_fullscreen(self, e=None):
        """Кнопка «на весь екран» на телефоні: повернути горизонтально (і назад)."""
        kit.quiet()
        page = self.app.page
        self.forced_land = not getattr(self, "forced_land", False)
        orient = [ft.DeviceOrientation.LANDSCAPE_LEFT, ft.DeviceOrientation.LANDSCAPE_RIGHT] \
            if self.forced_land else [ft.DeviceOrientation.PORTRAIT_UP]
        page.run_task(page.set_allowed_device_orientations, orient)

    def fit_key(self):
        mode = self.app.store.settings.get("fit") or "auto"
        if mode not in [k for k, _, _ in self.FITS]:  # «авто» зі старих налаштувань
            mode = "cover" if self.app.mobile else "contain"
        return mode

    def cycle_fit(self, e=None):
        kit.quiet()
        keys = [k for k, _, _ in self.FITS]
        mode = keys[(keys.index(self.fit_key()) + 1) % len(keys)]
        self.app.store.set("fit", mode)
        if not self.mini_on:
            self.video.fit = self.fit_mode()
            self.video.update()
        self.refresh_fit_btn()
        self.show_overlay(False)

    def refresh_fit_btn(self):
        _, name, icon = next(f for f in self.FITS if f[0] == self.fit_key())
        self.fit_btn.content.controls[0].icon = icon
        self.fit_btn.content.controls[1].value = name.upper()
        self.fit_btn.tooltip = f"Масштаб: {name.lower()}"
        try:
            self.fit_btn.update()
        except Exception:
            pass

    def fit_mode(self):
        """Масштаб кадру з налаштувань; «Авто» — на телефоні заповнити екран, деінде вписати."""
        if self.app.mobile and not self.landscape():
            return ft.BoxFit.CONTAIN  # телефон вертикально — кадр цілком (як у 1.x)
        mode = self.fit_key()
        return {"cover": ft.BoxFit.COVER, "fill": ft.BoxFit.FILL}.get(mode, ft.BoxFit.CONTAIN)

    # ── панель
    def show_overlay(self, focus=True):
        """Панель видна: кнопки на місці (схована — їх немає зовсім, щоб не натиснути
        невидиму). overlay.opacity — лише позначка «видно / ні»."""
        self.shown_at = time.monotonic()
        if self.overlay.opacity != 1:
            self.overlay.opacity = 1
            self.top.visible = self.bottom.visible = not self.mini_on
            self.overlay.update()
            if focus and self.app.tv:
                self.app.page.run_task(self.play_btn.focus)

    def hide_overlay(self):
        if self.overlay.opacity != 0:
            self.overlay.opacity = 0
            self.top.visible = self.bottom.visible = False
            self.panel_focus = False  # схована кнопка фокус уже не тримає
            self.overlay.update()

    def _track_panel_focus(self, control):
        """TV: чи стоїть фокус пульта на кнопці панелі (тоді стрілки ходять по кнопках,
        інакше вліво / вправо — перемотка)."""
        if isinstance(control, ft.Button):
            old_focus, old_blur = control.on_focus, control.on_blur

            def focus(e, f=old_focus):
                self.panel_focus = True
                if f:
                    f(e)

            def blur(e, b=old_blur):
                self.panel_focus = False
                if b:
                    b(e)
            control.on_focus, control.on_blur = focus, blur
            return
        for attr in ("content", "controls"):
            child = getattr(control, attr, None)
            if isinstance(child, list):
                for c in child:
                    self._track_panel_focus(c)
            elif isinstance(child, ft.Control):
                self._track_panel_focus(child)

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
            self.pip(playing)  # сам відсіює повтори; раніше вмикався лише після паузи
            if self.paused == playing:
                self.set_play_icon(playing)
            if self.overlay.opacity == 1:
                if not self.scrubbing:
                    self.time_text.value = (f"{fmt_time(pos)} / {fmt_time(dur)}" if dur
                                            else fmt_time(pos))
                    self.bar.value = min(1, pos / dur) if dur else 0
                    self.time_text.update()
                    self.bar.update()
                self.set_play_icon(playing)
                # пультом по кнопках панелі ходять довше — тоді й ховаємо пізніше
                if playing and time.monotonic() - self.shown_at > (8 if self.panel_focus else 4):
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

    # ── озвучка, субтитри, якість
    def subs(self):
        return self.entry.get("subs") or []

    def track(self):
        subs = self.subs()
        sub = next((x for x in subs if x["lang"] == self.sub_lang), None) if self.sub_lang else None
        if not sub:
            return fv.VideoSubtitleTrack.none()
        return fv.VideoSubtitleTrack(src=sub["url"], title=sub["title"], language=sub["lang"])

    def voices(self):
        return [s["entry"].get("voice") for s in self.ep["sources"] if s["entry"].get("voice")]

    def option_buttons(self):
        """Плашки праворуч: натискання — наступне значення."""
        out = []
        if not self.app.tv:  # телевізор сам і є екраном — транслювати з нього нікуди
            from .casting import Casting
            self.casting = Casting(self)
            out.append(kit.plate(ft.Icon(ft.Icons.CAST_ROUNDED, size=24),
                                 on_click=lambda e: self.app.page.run_task(self.casting.open),
                                 kind="glass", pad=(14, 12), tooltip="Транслювати на телевізор"))
        if len(set(self.voices())) > 1:
            out.append(kit.plate(kit.label(self.voice, ft.Icons.MIC_ROUNDED),
                                 on_click=lambda e: self.choose_voice(), kind="glass",
                                 pad=(14, 12)))
        if self.subs():
            sub = next((x for x in self.subs() if x["lang"] == self.sub_lang), None)
            self.sub_btn = kit.plate(kit.label(sub["title"] if sub else "Без субтитрів",
                                               ft.Icons.SUBTITLES_ROUNDED),
                                     on_click=lambda e: self.choose_subs(), kind="glass",
                                     pad=(14, 12))
            out.append(self.sub_btn)
        _, name, icon = next(f for f in self.FITS if f[0] == self.fit_key())
        self.fit_btn = kit.plate(kit.label(name, icon), on_click=self.cycle_fit, kind="glass",
                                 pad=(14, 12), tooltip=f"Масштаб: {name.lower()}")
        out.append(self.fit_btn)
        variants = core._VARIANTS.get(self.entry["url"]) or {}
        if len(variants) > 1:
            q = core.pick_quality(variants, self.app.store.settings.get("quality") or "1080")
            out.append(kit.plate(kit.label(f"{q}p", ft.Icons.HD_ROUNDED),
                                 on_click=lambda e: self.choose_quality(), kind="glass",
                                 pad=(14, 12)))
        if self.app.mobile:  # телефон: на весь екран — горизонтально, без системних панелей
            out.append(kit.plate(ft.Icon(ft.Icons.FULLSCREEN_ROUNDED, size=24),
                                 on_click=self.phone_fullscreen, kind="glass", pad=(14, 12),
                                 tooltip="На весь екран"))
        if not self.app.tv and not self.app.mobile:  # ПК: міні-плеєр і повний екран (F)
            out.append(kit.plate(ft.Icon(ft.Icons.PICTURE_IN_PICTURE_ALT_ROUNDED, size=24),
                                 on_click=lambda e: self.app.page.run_task(self.toggle_mini),
                                 kind="glass", pad=(14, 12), tooltip="Міні-плеєр"))
            out.append(kit.plate(ft.Icon(ft.Icons.FULLSCREEN_ROUNDED, size=24),
                                 on_click=lambda e: self.fullscreen(), kind="glass",
                                 pad=(14, 12), tooltip="На весь екран (F)"))
        return out

    def choose(self, title, options, current, on_pick):
        """Вікно з варіантами (плашками): вибір — явно, одним натисканням."""
        def pick(value):
            self.app.page.pop_dialog()
            if value != current:
                on_pick(value)

        self.app.page.show_dialog(ft.AlertDialog(
            bgcolor="#1C1815", shape=ft.RoundedRectangleBorder(radius=4),
            title=T.caps(title, size=T.S_HEAD, color="#F1E6CC"),
            content=ft.Column([self.choice_plate(name, lambda e, v=v: pick(v), v == current)
                               for v, name in options], tight=True, spacing=8, width=320)))

    @staticmethod
    def choice_plate(name, on_click, on):
        """Рядок у вікні вибору: на всю ширину, звичайної висоти; вибраний — з ✓."""
        btn = kit.plate(ft.Row([ft.Text(name.upper(), size=T.S_SMALL + 1, weight=ft.FontWeight.W_700,
                                        style=ft.TextStyle(letter_spacing=1.2), expand=True),
                                ft.Icon(ft.Icons.CHECK_ROUNDED, size=20, visible=on)]),
                        on_click=on_click, kind="glass", autofocus=on, pad=(18, 14))
        if on:  # вибране — світла плашка (на темному вікні «lit» зливався з тлом)
            btn.style.bgcolor = {kit.CTRL.FOCUSED: "#F1E6CC", kit.CTRL.HOVERED: "#F1E6CC",
                                 kit.CTRL.DEFAULT: "#F1E6CC"}
            btn.style.color = btn.style.icon_color = {kit.CTRL.DEFAULT: "#1A1714"}
        btn.width = 320
        return btn

    def choose_voice(self):
        voices = list(dict.fromkeys(self.voices()))

        def apply(v):
            self.title.voice = v
            self.app.store.remember_voice(self.title.item["url"], v)
            self.title.render_voices()
            self.app.page.run_task(self.reopen)

        self.choose("Озвучка", [(v, v) for v in voices], self.voice, apply)

    def choose_subs(self):
        def apply(lang):
            self.sub_lang = lang
            self.app.store.set(self.sub_key, lang or "")
            self.video.subtitle_track = self.track()
            self.video.update()
            sub = next((x for x in self.subs() if x["lang"] == lang), None)
            self.sub_btn.content = kit.label(sub["title"] if sub else "Без субтитрів",
                                             ft.Icons.SUBTITLES_ROUNDED)
            self.sub_btn.update()

        self.choose("Субтитри", [(None, "Без субтитрів")] +
                    [(x["lang"], x["title"]) for x in self.subs()], self.sub_lang, apply)

    def choose_quality(self):
        variants = core._VARIANTS.get(self.entry["url"]) or {}
        keys = sorted(variants, key=int, reverse=True)
        cur = core.pick_quality(variants, self.app.store.settings.get("quality") or "1080")

        def apply(q):
            self.app.store.set("quality", q)
            self.app.page.run_task(self.reopen)

        self.choose("Якість", [(k, f"{k}p") for k in keys], cur, apply)

    async def reopen(self):
        """Та сама серія з того ж місця (інша озвучка чи якість). Лише один раз:
        повторний перезапуск, поки триває перший, закривав і сторінку фільму."""
        if self.reopening or not self.alive:
            return
        self.reopening = True
        pos = self.last[0] or self.start
        mini = self.mini_on
        self.app.back()
        await asyncio.sleep(0.1)
        nxt = Player(self.app, self.title, self.si, self.ei, pos)
        nxt.mini_saved = self.mini_saved  # куди повернути вікно з міні-плеєра
        nxt.open(mini=mini)

    def set_play_icon(self, playing):
        icon = ft.Icons.PAUSE_ROUNDED if playing else ft.Icons.PLAY_ARROW_ROUNDED
        self.paused = not playing
        show_center = self.paused and not self.app.tv
        if self.play_btn.content.icon != icon or self.center_icon.visible != show_center:
            self.play_btn.content.icon = icon
            self.center_icon.visible = show_center
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

    async def pause(self):
        """Пауза, якщо грає (застосунок пішов у фон)."""
        try:
            if await self.video.is_playing():
                await self.video.pause()
                self.set_play_icon(False)
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

    async def toggle_mini(self):
        """ПК: вікно 16:9 поверх інших у правому верхньому куті екрана; ще раз — назад."""
        import screen
        w = self.app.page.window
        if not self.mini_on:
            if w.full_screen:
                w.full_screen = False
                self.app.page.update()
                await asyncio.sleep(1.0)  # macOS: вихід з повного екрана — анімація
            self.mini_saved = dict(width=w.width, height=w.height, left=w.left, top=w.top,
                                   min_width=w.min_width, min_height=w.min_height)
            cx = (w.left or 0) + (w.width or 0) / 2
            cy = (w.top or 0) + (w.height or 0) / 2
            area = await asyncio.to_thread(screen.work_area, cx, cy) or \
                (w.left or 0, w.top or 0, w.width or 960, w.height or 540)
            ax, ay, aw, ah = area
            mw = max(480, int(aw / 3))
            w.title_bar_hidden = True          # без смуги заголовка — лише кадр
            w.title_bar_buttons_hidden = True
            self.app.page.update()             # спершу без смуги — тоді розмір рівно 16:9
            w.min_width, w.min_height = 320, 180
            w.width, w.height = mw, int(mw * 9 / 16)
            w.left, w.top = ax + aw - mw - 16, ay + 16
            w.always_on_top = True
            self.video.fit = ft.BoxFit.COVER   # кадр заповнює віконце, без чорних полів
            self.mini_on = True
        else:
            sv = self.mini_saved or {}
            w.always_on_top = False
            w.title_bar_hidden = False
            w.title_bar_buttons_hidden = False
            self.video.fit = self.fit_mode()
            for k, v in sv.items():
                if v is not None:
                    setattr(w, k, v)
            self.mini_on = False
        self.mini_btn.visible = self.mini_drag.visible = self.mini_on
        self.top.visible = self.bottom.visible = not self.mini_on and self.overlay.opacity == 1
        self.app.page.update()

    def pip(self, playing):
        """Android-телефон: «картинка в картинці» при згортанні — лише поки відео грає."""
        if not (self.app.mobile and self.app.android) or playing == self.pip_on:
            return
        self.pip_on = playing
        self.app.page.run_task(ft.UrlLauncher().launch_url,
                               f"{CTRL}://pip?on={1 if playing else 0}",
                               mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    def fullscreen(self, on=None):
        w = self.app.page.window
        w.full_screen = (not w.full_screen) if on is None else on
        self.app.page.update()

    async def on_key(self, key):
        if key in BACK_KEYS:
            if not self.app.tv and not self.app.mobile and self.app.page.window.full_screen:
                self.fullscreen(False)  # Escape — спершу вийти з повного екрана
                return True
            self.app.back()
            return True
        if key.upper() == "F" and not self.app.tv and not self.app.mobile:
            self.fullscreen()
            return True
        if key in ("Media Fast Forward", "Media Rewind"):
            await self.seek_by(30 if key == "Media Fast Forward" else -10)
            return True
        if key in ("Media Play", "Media Pause", "Media Play Pause"):
            await self.toggle()
            return True
        if self.overlay.opacity == 1 and (self.panel_focus or not self.app.tv):
            self.shown_at = time.monotonic()
            return False  # фокус на кнопках панелі — стрілки ходять по них
        if key in ("Arrow Left", "Arrow Right"):
            await self.seek_by(-10 if key == "Arrow Left" else 10)
            return True
        if key in OK_KEYS:
            await self.toggle()
            return True
        if key in ("Arrow Up", "Arrow Down"):
            # панель з фокусом на ▶ — далі стрілками до субтитрів, масштабу, якості…
            self.overlay.opacity = 0  # щоб show_overlay точно поставив фокус
            self.show_overlay(True)
            return True
        return False

    def close(self):
        """Вихід з плеєра: зберегти позицію й зупинити відео."""
        self.alive = False
        self.app.key_handler = None
        if self.app.player is self:
            self.app.player = None
        if not self.app.tv and not self.app.mobile and self.app.page.window.full_screen:
            self.fullscreen(False)
        if self.mini_on and not self.reopening:
            self.app.page.run_task(self.toggle_mini)
        if self.pip_on:
            self.pip(False)
        if self.app.mobile:
            self.immersive(False)
            if getattr(self, "forced_land", None) is not None and not self.reopening:
                self.app.page.run_task(self.app.page.set_allowed_device_orientations, [])

        if self.last[1]:
            self.save(*self.last)
