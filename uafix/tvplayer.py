"""Панель плеєра на телевізорі — як у 1.x.

Знизу: назва й серія, смужка часу (її можна вибрати пультом: ←/→ — перемотка, що довше
тиснути, то більший крок) і ряд круглих кнопок: попередня, ▶/⏸, наступна, озвучка,
субтитри, якість, масштаб, таймер сну. За 15 с до кінця серії — картка «Наступна серія».
Панель ховається через 5 с без натискань; будь-яка кнопка пульта показує її, «Назад» —
спершу ховає.
"""
import asyncio
import time

import flet as ft

from . import kit, theme as T

FOCUS_BG = {ft.ControlState.FOCUSED: "#D92C3039", ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT}
FOCUS_SIDE = {ft.ControlState.FOCUSED: ft.BorderSide(4, "#FFFFFF"),
              ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
QUIET_KEYS = ("Audio Volume Up", "Audio Volume Down", "Audio Volume Mute", "Volume Up",
              "Volume Down", "Volume Mute")
NEXT_LEAD = 15  # с до кінця серії — картка «Наступна серія»
HIDE_AFTER = 5


def fmt(sec):
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TVPanel:
    def __init__(self, player, season, what):
        self.p = player
        self.app = player.app
        self.season, self.what = season, what
        self.shown = True
        self.hide_token = 0
        self.timeline_focus = False
        self.row_focus = False
        self.seek_target = None   # с — куди перемотаємо, коли перестануть натискати
        self.seek_token = 0
        self.seek_streak = 0
        self.seek_last = 0.0
        self.pos = 0.0
        self.dur = 0.0
        self.paused = False
        self.dialog = False
        self.next_shown = False
        self.next_dismissed = False
        self.sleep_at = None      # time.monotonic(), коли зупинити
        self.sleep_after = False  # після цієї серії

    # ── побудова
    def btn(self, icon, tip, action, autofocus=False):
        return ft.IconButton(icon, icon_size=34, icon_color="#FFFFFF", tooltip=tip,
                             autofocus=autofocus, on_click=lambda e: (kit.quiet(), action()),
                             on_focus=lambda e: (kit.quiet(), setattr(self, "row_focus", True)),
                             on_blur=lambda e: (kit.quiet(), setattr(self, "row_focus", False)),
                             style=ft.ButtonStyle(bgcolor=FOCUS_BG, side=FOCUS_SIDE,
                                                  shape=ft.CircleBorder(), padding=10))

    def build(self):
        p, run = self.p, self.app.page.run_task
        self.play_btn = self.btn(ft.Icons.PAUSE_ROUNDED, "Пауза / відтворення",
                                 lambda: run(self.toggle), autofocus=True)
        self.bar = ft.ProgressBar(value=0, color=T.SIGNAL, bgcolor="#40FFFFFF", bar_height=8,
                                  border_radius=4, expand=True)
        self.time = ft.Text("", size=18, color="#FFFFFF", weight=ft.FontWeight.W_600)
        self.hint = ft.Text("", size=16, color=T.SIGNAL, weight=ft.FontWeight.W_700)
        self.timeline_box = ft.Container(
            ft.Column([ft.Row([self.bar]), ft.Row([self.time, self.hint], spacing=16)],
                      spacing=10, tight=True),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12), border_radius=14,
            border=ft.Border.all(3, ft.Colors.TRANSPARENT))
        self.timeline = timeline = ft.Button(
            content=self.timeline_box, on_click=lambda e: (kit.quiet(), run(self.toggle)),
            on_focus=lambda e: (kit.quiet(), self.on_timeline(True)),
            on_blur=lambda e: (kit.quiet(), self.on_timeline(False)),
            style=ft.ButtonStyle(padding=0, elevation=0, bgcolor=ft.Colors.TRANSPARENT,
                                 overlay_color=ft.Colors.TRANSPARENT,
                                 shadow_color=ft.Colors.TRANSPARENT, animation_duration=0,
                                 shape=ft.RoundedRectangleBorder(radius=14)))
        row = []
        if p.has_prev():
            row.append(self.btn(ft.Icons.SKIP_PREVIOUS_ROUNDED, "Попередня серія",
                                lambda: run(p.prev_episode)))
        row.append(self.play_btn)
        if p.has_next():
            row.append(self.btn(ft.Icons.SKIP_NEXT_ROUNDED, "Наступна серія",
                                lambda: run(p.next_episode)))
        if len(set(p.voices())) > 1:
            row.append(self.btn(ft.Icons.RECORD_VOICE_OVER_ROUNDED, "Озвучка",
                                lambda: self.modal(p.choose_voice)))
        row.append(self.btn(ft.Icons.SUBTITLES_ROUNDED, "Субтитри",
                            lambda: self.modal(p.choose_subs) if p.subs() else
                            self.app.toast("Субтитрів для цього відео немає")))
        row.append(self.btn(ft.Icons.HD_ROUNDED, "Якість", lambda: self.modal(p.choose_quality)))
        self.fit_btn = self.btn(self.fit_icon(), "Масштаб", self.cycle_fit)
        self.sleep_btn = self.btn(ft.Icons.BEDTIME_OUTLINED, "Таймер сну",
                                  lambda: self.modal(self.choose_sleep))
        row += [self.fit_btn, self.sleep_btn]

        title = p.title.info.get("title") or p.title.item["title"]
        now = " · ".join(x for x in (self.season, self.what, p.voice) if x)
        self.overlay = ft.Container(
            ft.Column([
                ft.Container(expand=True),
                ft.Container(ft.Row([
                    ft.Text(title, size=22, weight=ft.FontWeight.W_700, color="#FFFFFF",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(now, size=18, color="#C9CDD4", max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS, expand=True)], spacing=14),
                    padding=ft.Padding.only(left=16)),
                timeline,
                ft.Row(row, spacing=12),
            ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            left=0, right=0, top=0, bottom=0,
            padding=ft.Padding.only(left=40, right=40, bottom=28),
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                       end=ft.Alignment.BOTTOM_CENTER,
                                       colors=["#00000000", "#00000000", "#E6000000"],
                                       stops=[0, 0.5, 1]))

        # картка «Наступна серія»
        self.next_name = ft.Text("", size=18, weight=ft.FontWeight.W_700, color="#FFFFFF",
                                 max_lines=2)
        self.next_count = ft.Text("", size=14, color="#C9CDD4")
        self.next_now = ft.FilledButton(
            "Дивитись зараз", icon=ft.Icons.SKIP_NEXT_ROUNDED,
            on_click=lambda e: (kit.quiet(), run(self.play_next_now)),
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.FOCUSED: "#FFFFFF", ft.ControlState.DEFAULT: T.SIGNAL},
                color={ft.ControlState.FOCUSED: "#111214", ft.ControlState.DEFAULT: "#FFFFFF"},
                icon_color={ft.ControlState.FOCUSED: "#111214",
                            ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=18, vertical=14)))
        cancel = ft.TextButton(
            "Скасувати", on_click=lambda e: (kit.quiet(), self.cancel_next()),
            style=ft.ButtonStyle(color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=12),
                                 bgcolor={ft.ControlState.FOCUSED: "#33FFFFFF"},
                                 side=FOCUS_SIDE,
                                 padding=ft.Padding.symmetric(horizontal=18, vertical=14)))
        self.next_card = ft.Container(
            ft.Column([ft.Text("НАСТУПНА СЕРІЯ", size=11, weight=ft.FontWeight.W_700,
                               color=T.SIGNAL),
                       self.next_name, self.next_count,
                       ft.Row([self.next_now, cancel], spacing=8)], spacing=6, tight=True),
            right=28, bottom=110, width=360, visible=False, padding=16, border_radius=16,
            bgcolor="#E6141519", border=ft.Border.all(1, "#26FFFFFF"),
            shadow=ft.BoxShadow(blur_radius=24, color="#80000000"))
        run(self.poll)
        run(self.hide_later, 0)
        return [self.overlay, self.next_card]

    # ── показ / приховування панелі
    def activity(self, focus=True):
        if not self.shown:
            self.shown = True
            self.overlay.visible = True
            self.render_time()
            self.safe(self.overlay)
            if focus and not self.next_shown:
                self.app.page.run_task(self.focus_later, self.play_btn)
        self.hide_token += 1
        self.app.page.run_task(self.hide_later, self.hide_token)

    def hide(self):
        self.shown = False
        self.timeline_focus = False  # схована панель втрачає фокус без on_blur
        self.timeline_box.border = ft.Border.all(3, ft.Colors.TRANSPARENT)
        self.bar.bar_height, self.hint.value = 8, ""
        self.overlay.visible = False
        self.safe(self.overlay)

    async def hide_later(self, token):
        await asyncio.sleep(HIDE_AFTER)
        if (token != self.hide_token or self.paused or self.dialog or self.seek_target is not None
                or not self.shown or not self.p.alive):
            return
        self.hide()

    async def focus_later(self, control):
        await asyncio.sleep(0.05)
        try:
            await control.focus()
        except Exception:
            pass

    @staticmethod
    def safe(control):
        try:
            control.update()
        except Exception:
            pass

    # ── пауза
    async def toggle(self):
        self.paused = not self.paused
        self.play_btn.icon = ft.Icons.PLAY_ARROW_ROUNDED if self.paused else ft.Icons.PAUSE_ROUNDED
        self.safe(self.play_btn)
        try:
            await self.p.video.play_or_pause()
        except Exception:
            pass
        self.activity(False)

    async def pause(self):
        if not self.paused:
            await self.toggle()

    # ── смужка часу й перемотка
    def on_timeline(self, on):
        self.timeline_focus = on
        self.timeline_box.border = ft.Border.all(3, "#FFFFFF" if on else ft.Colors.TRANSPARENT)
        self.bar.bar_height = 12 if on else 8
        if self.seek_target is None:
            self.hint.value = "← → перемотка" if on else ""
        self.safe(self.timeline_box)

    def render_time(self):
        shown = self.seek_target if self.seek_target is not None else self.pos
        self.bar.value = shown / self.dur if self.dur else None
        self.time.value = f"{fmt(shown)} / {fmt(self.dur)}" if self.dur else fmt(shown)

    async def poll(self):
        while self.p.alive:
            try:
                self.pos = (await self.p.video.get_current_position()).in_milliseconds / 1000
                self.dur = (await self.p.video.get_duration()).in_milliseconds / 1000
            except Exception:
                pass
            if self.shown:
                self.render_time()
                self.safe(self.overlay)
            await self.watch_end()
            await asyncio.sleep(1)

    def seek_key(self, key):
        now = time.monotonic()
        self.seek_streak = self.seek_streak + 1 if now - self.seek_last < 0.6 else 0
        self.seek_last = now
        step = 10 if self.seek_streak < 4 else 30 if self.seek_streak < 10 else 60
        base = self.seek_target if self.seek_target is not None else self.pos
        target = base + (step if key == "Arrow Right" else -step)
        target = max(0.0, min(target, self.dur - 3 if self.dur else target))
        self.seek_target = target
        delta = target - self.pos
        self.hint.value = f"{'+' if delta >= 0 else '−'}{fmt(abs(delta))}"
        self.render_time()
        self.safe(self.overlay)
        self.seek_token += 1
        self.app.page.run_task(self.apply_seek, self.seek_token)

    async def apply_seek(self, token):
        await asyncio.sleep(0.8)  # чекаємо, поки перестануть натискати
        if token != self.seek_token or self.seek_target is None:
            return
        target, self.seek_target = self.seek_target, None
        self.pos = target
        self.hint.value = "← → перемотка" if self.timeline_focus else ""
        self.render_time()
        self.safe(self.overlay)
        await self.p.seek_to(int(target * 1000))
        self.next_dismissed = False if self.dur - target > NEXT_LEAD else self.next_dismissed

    # ── вікна вибору (поки відкрите — панель не ховається, «Назад» закриває вікно)
    def modal(self, opener):
        self.dialog = True
        opener()

    def dialog_closed(self):
        self.dialog = False
        self.activity(False)

    def fit_icon(self):
        return next(i for k, _, i in self.p.FITS if k == self.p.fit_key())

    def cycle_fit(self):
        self.p.cycle_fit()
        self.fit_btn.icon = self.fit_icon()
        self.safe(self.fit_btn)
        self.app.toast(f"Масштаб: {next(n for k, n, _ in self.p.FITS if k == self.p.fit_key())}")

    # ── таймер сну
    def choose_sleep(self):
        left = (self.sleep_at - time.monotonic()) / 60 if self.sleep_at else None
        opts = [(0, "Вимкнено")] + [(m, f"Через {m} хв") for m in (15, 30, 60, 90)] + \
               [(-1, "Після цієї серії" if self.p.has_next() or self.p.has_prev()
                 else "Після цього фільму")]
        cur = -1 if self.sleep_after else (min((m for m in (15, 30, 60, 90)),
                                              key=lambda m: abs(m - left)) if left else 0)
        if left is not None:
            opts.insert(1, (cur, f"Залишилось {max(1, round(left))} хв"))

        def apply(m):
            self.sleep_after = m == -1
            self.sleep_at = time.monotonic() + m * 60 if m and m > 0 else None
            on = bool(self.sleep_at or self.sleep_after)
            self.sleep_btn.icon_color = T.SIGNAL if on else "#FFFFFF"
            self.safe(self.sleep_btn)
            if on:
                self.app.toast("Таймер сну: " + ("після цієї серії" if self.sleep_after
                                                  else f"через {m} хв"))
        self.p.choose("Таймер сну", opts, cur, apply)

    async def stop_for_sleep(self):
        self.sleep_at, self.sleep_after = None, False
        self.sleep_btn.icon_color = "#FFFFFF"
        self.safe(self.sleep_btn)
        await self.pause()
        self.app.toast("Таймер сну: відтворення зупинено")

    # ── наступна серія
    async def watch_end(self):
        if self.sleep_at and time.monotonic() >= self.sleep_at:
            await self.stop_for_sleep()
        left = self.dur - self.pos
        if self.dur < 120 or left <= 0 or not self.p.has_next() or self.next_dismissed:
            if self.next_shown and (self.next_dismissed or left > NEXT_LEAD):
                self.hide_next()
            return
        if left <= NEXT_LEAD:
            self.next_name.value = self.p.next_label()
            self.next_count.value = f"Почнеться через {int(left)} с"
            first = not self.next_shown
            self.next_shown = True
            self.next_card.visible = True
            self.safe(self.next_card)
            if first:
                self.app.page.run_task(self.focus_later, self.next_now)
        elif self.next_shown:
            self.hide_next()  # перемотали назад

    def hide_next(self):
        if self.next_shown:
            self.next_shown = False
            self.next_card.visible = False
            self.safe(self.next_card)

    def cancel_next(self):
        self.next_dismissed = True
        self.p.no_autonext = True  # кінець серії — без переходу далі
        self.hide_next()
        self.activity()
        self.app.page.run_task(self.focus_later, self.play_btn)

    async def play_next_now(self):
        self.hide_next()
        await self.p.next_episode()

    def at_end(self):
        """Серія скінчилась: таймер «після цієї серії» — зупинитись, а не йти далі."""
        if self.sleep_after:
            self.app.page.run_task(self.stop_for_sleep)
            return False
        return not getattr(self.p, "no_autonext", False)

    # ── пульт
    async def on_key(self, key, back_keys, ok_keys):
        """True — оброблено тут."""
        if key in back_keys:
            if self.dialog:
                return True
            if self.next_shown:
                self.cancel_next()
                return True
            if self.shown:
                self.hide_token += 1
                self.hide()
                return True
            return False  # схована панель — «Назад» виходить з плеєра
        if key in QUIET_KEYS:
            return True
        was_hidden = not self.shown
        self.activity()
        if key in ("Media Play Pause", "Media Play", "Media Pause"):
            await self.toggle()
            return True
        if key in ("Media Fast Forward", "Media Rewind"):
            await self.p.seek_by(30 if key == "Media Fast Forward" else -10)
            return True
        if was_hidden:
            return True  # перше натискання лише показує панель (фокус — на ▶/⏸)
        if self.timeline_focus and key in ("Arrow Left", "Arrow Right"):
            self.seek_key(key)
            return True
        # між рядом кнопок і смужкою — явно (сам Flutter смужку «вгорі» не знаходив)
        if key == "Arrow Up" and self.row_focus:
            self.app.page.run_task(self.focus_later, self.timeline)
            return True
        if key == "Arrow Up" and self.timeline_focus:
            return True
        if key == "Arrow Down" and self.timeline_focus:
            self.app.page.run_task(self.focus_later, self.play_btn)
            return True
        if key == "Arrow Down" and self.row_focus:
            return True
        return False  # ←/→ по кнопках і OK — Flutter
