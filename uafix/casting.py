"""Трансляція з плеєра на телевізор: Chromecast / Google TV, DLNA і AirPlay (cast.py, dlna.py,
airplay.py). Телевізор сам грає потік; тут — пошук, керування й повернення «дивитись тут»."""
import asyncio
import json

import flet as ft

import airplay
import cast as uafix_cast
import core
import dlna
import tmdb
from . import kit, theme as T
from .title import fmt_time

BETA = {"dlna": " · DLNA (бета)", "airplay": " · AirPlay (бета)"}


class Casting:
    def __init__(self, player):
        self.player = player
        self.app = player.app
        self.sess = None
        self.state = (0.0, 0.0, "BUFFERING")  # позиція с, тривалість с, стан
        self.ui = {}

    def dialog(self, title, content, actions, on_dismiss=None):
        dlg = ft.AlertDialog(bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
                             title=T.caps(title, size=T.S_HEAD), content=content, actions=actions,
                             on_dismiss=on_dismiss)
        self.app.page.show_dialog(dlg)
        return dlg

    # ── пошук пристроїв
    async def open(self):
        if self.sess:
            self.panel()
            return
        box = ft.Column([ft.Row([kit.spinner(18), T.text("Шукаю телевізори в мережі…", color=T.DIM)],
                                spacing=12)], tight=True, spacing=8, width=460)
        dlg = self.dialog("Транслювати на", box,
                          [kit.plate(kit.label("Скасувати"), lambda e: self.app.page.pop_dialog())])
        try:
            devices = await asyncio.to_thread(uafix_cast.discover)
        except Exception:
            devices = []
        if not devices:
            box.controls = [
                T.text("Пристроїв не знайдено.", weight=ft.FontWeight.W_700),
                T.text("Телефон і телевізор (Chromecast, Google TV, Android TV або з DLNA — "
                       "Hisense VIDAA, Samsung, LG) мають бути в одній Wi-Fi мережі, а "
                       "телевізор — увімкнений.", size=T.S_SMALL, color=T.DIM),
                T.text("Hisense VIDAA: «Налаштування → Мережа → Content Sharing».",
                       size=T.S_SMALL, color=T.DIM),
                kit.plate(kit.label("Шукати ще раз", ft.Icons.REFRESH_ROUNDED),
                          lambda e: (self.app.page.pop_dialog(), self.app.page.run_task(self.open)))]
        else:
            box.controls = [kit.plate(kit.label(d["name"] + BETA.get(d.get("kind"), ""),
                                                ft.Icons.TV_ROUNDED),
                                      lambda e, d=d: self.app.page.run_task(self.start, d),
                                      expand=True) for d in devices]
        try:
            dlg.update()
        except Exception:
            pass

    # ── початок трансляції
    async def start(self, device):
        p = self.player
        self.app.page.pop_dialog()
        start = (p.last[0] or p.start) / 1000
        try:
            await p.video.pause()
        except Exception:
            pass
        entry = p.entry
        sub = next((x for x in p.subs() if x["lang"] == p.sub_lang), None)
        name = (p.title.info.get("title") or p.title.item["title"]) + \
            (f" · {p.ep['label']}" if len(p.title.all_episodes()) > 1 else "")
        _, still = tmdb.cached(p.title.item["url"], "w780")
        self.app.toast(f"Підключаюсь до «{device['name']}»…")
        quality = self.app.store.settings.get("quality") or "1080"

        def connect():
            sess = uafix_cast.open_session(device, core.stream_headers(entry)
                                           if entry.get("referer") else None)
            sess.play(core.quality_url(entry["url"], quality), name, still, start,
                      sub["url"] if sub else None)
            return sess

        try:
            sess = await asyncio.to_thread(connect)
        except airplay.PairingRequired:
            await self.pair(device)  # один раз: PIN-код з екрана телевізора
            return
        except Exception as ex:
            why = f": {ex}" if device.get("kind") in ("dlna", "airplay") and str(ex) else ""
            self.app.toast(f"Не вдалося почати трансляцію на «{device['name']}»{why}", error=True)
            return
        self.sess = sess
        self.state = (start, 0.0, "BUFFERING")
        self.panel()
        self.app.page.run_task(self.watch, sess)

    async def pair(self, device):
        pairing = airplay.Pairing(device["host"])
        try:
            await asyncio.to_thread(pairing.start)
        except Exception as ex:
            self.app.toast(f"Телевізор не почав спарювання: {ex}", error=True)
            return
        field = ft.TextField(hint_text="PIN-код", autofocus=True, text_size=22, max_length=8,
                             keyboard_type=ft.KeyboardType.NUMBER, bgcolor=T.WALL_2,
                             border_color=T.LINE, focused_border_color=T.SIGNAL, color=T.INK)
        msg = T.text("Введіть код, який зараз показує телевізор. Це потрібно лише один раз.",
                     size=T.S_SMALL, color=T.DIM)

        async def done(e=None):
            pin = (field.value or "").strip()
            if not pin:
                return
            msg.value = "Спарюю…"
            msg.update()
            try:
                creds = await asyncio.to_thread(pairing.finish, pin)
            except Exception as ex:
                msg.value = f"Не вдалося: {ex}"
                msg.update()
                return
            airplay.CREDENTIALS[device["host"]] = creds
            self.app.page.run_task(self.app.store.prefs.set, "airplay_creds",
                                   json.dumps(airplay.CREDENTIALS))
            self.app.page.pop_dialog()
            await self.start(device)

        field.on_submit = lambda e: self.app.page.run_task(done)
        self.dialog(f"Спарювання з «{device['name']}»", ft.Column([msg, field], tight=True,
                                                                     spacing=12, width=420),
                    [kit.plate(kit.label("Скасувати"), lambda e: self.app.page.pop_dialog()),
                     kit.plate(kit.label("Спарити"), lambda e: self.app.page.run_task(done),
                               kind="board")])

    # ── керування
    def panel(self):
        if not self.sess:
            return
        p = self.player
        pos_text = T.text("", size=T.S_SMALL, color=T.DIM)
        bar = ft.ProgressBar(value=0, color=T.SIGNAL, bgcolor=T.WALL_2, bar_height=6, width=420)
        toggle = kit.plate(ft.Icon(ft.Icons.PAUSE_ROUNDED, size=30),
                           lambda e: self.app.page.run_task(self.do, "toggle"), kind="board",
                           pad=(20, 12))
        self.ui = {"pos": pos_text, "bar": bar, "toggle": toggle}
        name = (p.title.info.get("title") or p.title.item["title"]) + \
            (f" · {p.ep['label']}" if len(p.title.all_episodes()) > 1 else "")
        self.dlg = self.dialog(self.sess.name, ft.Column([
            T.text(name, weight=ft.FontWeight.W_700), bar, pos_text,
            ft.Row([kit.plate(ft.Icon(ft.Icons.REPLAY_10_ROUNDED, size=26),
                              lambda e: self.app.page.run_task(self.do, -10), pad=(14, 12)),
                    toggle,
                    kit.plate(ft.Icon(ft.Icons.FORWARD_10_ROUNDED, size=26),
                              lambda e: self.app.page.run_task(self.do, 10), pad=(14, 12))],
                   alignment=ft.MainAxisAlignment.CENTER, spacing=12)],
            tight=True, spacing=12, width=420),
            [kit.plate(kit.label("Сховати"), lambda e: self.app.page.pop_dialog()),
             kit.plate(kit.label("Дивитись тут"), lambda e: self.app.page.run_task(self.stop, True),
                       kind="board")],
            on_dismiss=lambda e: self.ui.clear())
        self.render()

    def render(self):
        if not self.ui:
            return
        pos, dur, state = self.state
        self.ui["pos"].value = (f"{fmt_time(pos * 1000)} / {fmt_time(dur * 1000)}" if dur
                                else fmt_time(pos * 1000)) + \
            ("  ·  завантаження…" if state == "BUFFERING" else "")
        self.ui["bar"].value = pos / dur if dur else None
        self.ui["toggle"].content.icon = (ft.Icons.PLAY_ARROW_ROUNDED if state == "PAUSED"
                                          else ft.Icons.PAUSE_ROUNDED)
        try:
            self.ui["pos"].update()
            self.ui["bar"].update()
            self.ui["toggle"].update()
        except Exception:
            self.ui.clear()

    async def do(self, action):
        if not self.sess:
            return
        try:
            if action == "toggle":
                await asyncio.to_thread(self.sess.toggle)
            else:
                await asyncio.to_thread(self.sess.seek, self.state[0] + action)
        except Exception:
            self.app.toast("Телевізор не відповідає", error=True)

    async def watch(self, sess):
        """Стан на телевізорі раз на секунду; позиція — в історію (як при перегляді тут)."""
        fails, saved = 0, 0
        while self.sess is sess:
            if not self.player.alive:
                await self.stop(False)
                return
            try:
                pos, dur, state, reason = await asyncio.to_thread(sess.status)
                fails = 0
            except Exception:
                fails += 1
                if fails >= 5:
                    self.app.toast("Зв'язок із телевізором втрачено", error=True)
                    await self.stop(True, quit_tv=False)
                    return
                await asyncio.sleep(1)
                continue
            if state == "IDLE" and reason in ("FINISHED", "CANCELLED", "INTERRUPTED", "OTHER_APP"):
                await self.stop(False, quit_tv=False)
                return
            self.state = (pos, dur, state)
            self.render()
            saved += 1
            if dur and pos > 1 and saved % 10 == 0:
                self.player.last = (int(pos * 1000), int(dur * 1000))
                self.player.save(*self.player.last)
            await asyncio.sleep(1)

    async def stop(self, resume_here, quit_tv=True):
        sess, self.sess = self.sess, None
        if not sess:
            return
        pos = self.state[0]
        self.ui.clear()
        try:
            self.app.page.pop_dialog()
        except Exception:
            pass
        if quit_tv:
            try:
                await asyncio.to_thread(sess.stop)
            except Exception:
                pass
        p = self.player
        if not p.alive:
            return
        if pos > 5:
            await p.video.seek(int(pos * 1000))
        if resume_here:
            await p.video.play()
