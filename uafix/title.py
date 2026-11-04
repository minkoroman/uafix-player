"""Сторінка фільму чи серіалу: кадр, озвучка до перегляду, «Продовжити», серії табло."""
import asyncio

import flet as ft

import core
import tmdb
from . import kit, theme as T
from .links import CTRL
from .store import item_of


def fmt_time(ms):
    sec = int(ms // 1000)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TitleScreen:
    def __init__(self, app, item):
        self.app = app
        self.item = item_of(item)
        self.info = {}
        self.seasons = []
        self.season = 0
        self.voices = []
        self.voice = ""
        self.view = None

    # ── побудова
    async def open(self, autoplay=False):
        app = self.app
        # кадр — лише з TMDB (картинки сайту не вантажимо); знайдений раніше — одразу
        known, still = tmdb.cached(self.item["url"], "w1280")
        self.backdrop = ft.Container(kit.cover(still), left=0, top=0, right=0,
                                     bottom=0, bgcolor=T.WALL)
        shade = ft.Container(left=0, top=0, right=0, bottom=0, gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
            colors=[ft.Colors.with_opacity(0.0, T.FACADE), ft.Colors.with_opacity(0.35, T.FACADE),
                    T.FACADE], stops=[0.25, 0.55, 1.0] if app.tv else [0.5, 0.78, 1.0]))
        side_shade = ft.Container(left=0, top=0, right=0, bottom=0, gradient=ft.LinearGradient(
            begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
            colors=[ft.Colors.with_opacity(0.85, T.FACADE), ft.Colors.with_opacity(0, T.FACADE)],
            stops=[0.0, 0.6]))
        self.kick = kit.kicker("")
        self.title = ft.Text(self.item["title"], size=T.S_TITLE, weight=ft.FontWeight.W_800,
                             color=T.INK, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                             style=ft.TextStyle(height=1.05, letter_spacing=-0.5))
        self.orig = T.text(self.item.get("orig") or "", size=T.S_BODY, color=T.DIM)
        self.meta = T.text("", size=T.S_BODY, color=T.INK, weight=ft.FontWeight.W_600)
        heading = ft.Container(ft.Column([self.kick, self.title, self.orig, self.meta],
                                         spacing=6, tight=True),
                               left=app.side, right=app.side, bottom=10)
        back = ft.Container(kit.plate(ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=22),
                                      on_click=lambda e: app.back(), pad=(12, 10)),
                            left=app.side - 8, top=18, visible=not app.tv)
        # кадр — повністю, у своїх пропорціях 16:9 (без обрізання); назва лягає на його низ
        hero = ft.Container(ft.Stack([self.backdrop, side_shade, shade, heading, back]),
                            aspect_ratio=16 / 9)

        self.status = ft.Row([kit.spinner(18), T.text("Шукаю відео…", size=T.S_SMALL, color=T.DIM)],
                             spacing=10)
        self.voice_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self.voice_box = ft.Column([kit.kicker("Озвучка"), self.voice_row], spacing=8, visible=False)
        self.actions = ft.Row(wrap=True, spacing=10, run_spacing=10)
        self.fav_btn = kit.plate(self.fav_label(), on_click=lambda e: self.toggle_fav())
        self.description = T.text("", size=T.S_BODY, color=T.INK, max_lines=6 if not app.mobile else 8,
                                  overflow=ft.TextOverflow.ELLIPSIS)
        self.cast_line = T.text("", size=T.S_SMALL, color=T.DIM, visible=False)
        self.season_row = ft.Row(wrap=True, spacing=8, run_spacing=8)
        self.episodes = ft.Column(spacing=0)
        self.episodes_box = ft.Column([kit.kicker("Серії"), self.season_row, self.episodes],
                                      spacing=12, visible=False)
        self.saga_box = ft.Column(spacing=10, visible=False)
        self.people_box = ft.Column(spacing=10, visible=False)
        self.reviews_box = ft.Column(spacing=10, visible=False)
        if app.tv:
            # TV (як Google TV / Apple TV): кадр на весь екран, озвучка й кнопки — поверх
            # нього внизу; інакше «Дивитись» опинялась за краєм екрана
            heading.content.controls += [ft.Container(height=10), self.status,
                                         self.voice_box, self.actions]
            heading.bottom = 44
            top_part = []
        else:
            top_part = [self.status, self.voice_box, self.actions]
        body = ft.Container(ft.Column([
            *top_part, self.description, self.cast_line,
            self.episodes_box, self.saga_box, self.people_box, self.reviews_box], spacing=22),
            padding=ft.Padding.only(left=app.side, right=app.side, top=18, bottom=60),
            width=1100 if not app.mobile else None)
        self.scroller = ft.ListView([hero, body], expand=True, spacing=0)
        self.view = app.push("/title", self.scroller)
        self.view.on_return = self.on_return
        page = app.page

        # ── опис зі сторінки сайту
        try:
            html = await asyncio.to_thread(core.fetch, self.item["url"])
            info = await asyncio.to_thread(core.parse_title, html, self.item["url"])
        except Exception as ex:
            self.status.controls = [T.text(core.friendly_error(ex, "сторінку"), color=T.SIGNAL)]
            self.update()
            return
        self.info = info
        self.fill_info()
        page.run_task(self.load_backdrop)
        page.run_task(self.load_saga)
        page.run_task(self.load_people)
        self.show_reviews(info.get("comments") or [])

        # ── серії: плеєри на сторінці (фільм / серіал з плейлистом) або окремі сторінки серій
        try:
            found = await asyncio.to_thread(core.page_streams, self.item["url"], html)
            if found:
                self.seasons = core.group_streams(found)
            else:
                episodes, locked = await asyncio.to_thread(core.list_episodes, self.item["url"], html)
                self.seasons = core.group_episode_pages(episodes, locked)
        except Exception as ex:
            self.status.controls = [T.text(core.friendly_error(ex, "серії"), color=T.SIGNAL)]
            self.update()
            return
        if not any(s["episodes"] for s in self.seasons):
            blocked = core.GEO["site"] or core.GEO["blocked"]
            self.status.controls = [T.text(
                "Недоступно у вашому регіоні" if blocked else "Відео ще не додали на сайт",
                color=T.SIGNAL)]
            self.update()
            return
        self.status.visible = False
        h = self.app.store.entry(self.item["url"])
        self.season = h["si"] if h and h.get("si") is not None and \
            h["si"] < len(self.seasons) else next(
                (i for i, s in enumerate(self.seasons) if s["episodes"]), 0)
        self.collect_voices()
        if not self.voices:
            page.run_task(self.detect_voices)
        self.render()
        if autoplay:
            await self.play_resume()

    def update(self):
        try:
            self.view.update()
        except Exception:
            pass

    def fill_info(self):
        info = self.info
        kind = "Серіал" if "/serials/" in self.item["url"] or "/dorama" in self.item["url"] \
            else "Фільм"
        self.kick.value = " · ".join(x for x in (kind, info.get("country") or "") if x).upper()
        self.title.value = info.get("title") or self.item["title"]
        self.orig.value = info.get("orig") or self.item.get("orig") or ""
        bits = []
        if info.get("imdb"):
            bits.append(f"★ {info['imdb']}")
        bits += [info[k] for k in ("year", "duration", "age") if info.get(k)]
        if info.get("genres"):
            bits.append(", ".join(g.capitalize() for g in info["genres"][:3]))
        self.meta.value = "   ·   ".join(bits)
        self.description.value = info.get("description") or ""
        if info.get("actors"):
            self.cast_line.value = "У ролях: " + ", ".join(info["actors"][:8])
            self.cast_line.visible = True
        self.render_actions()
        self.update()

    async def load_backdrop(self):
        """Широкий кадр з TMDB: пошук з роком сайту (знайдене для картки без року —
        перевіряється й за потреби шукається заново)."""
        try:
            url = await asyncio.to_thread(
                tmdb.backdrop, self.item["url"], self.info.get("title") or self.item["title"],
                self.info.get("orig") or self.item.get("orig", ""), self.info.get("year", ""))
        except Exception:
            url = None
        if url:
            self.backdrop.content = kit.cover(url)
            self.update()

    # ── дії
    def fav_label(self):
        on = self.app.store.is_favorite(self.item["url"])
        return kit.label("В обраному" if on else "В обране",
                         ft.Icons.BOOKMARK_ROUNDED if on else ft.Icons.BOOKMARK_BORDER_ROUNDED)

    def toggle_fav(self):
        added = self.app.store.toggle_favorite(self.item)
        self.fav_btn.content = self.fav_label()
        self.fav_btn.update()
        self.app.toast("Додано в обране" if added else "Прибрано з обраного")

    def resume_spot(self):
        """(si, ei, позиція) з історії, якщо цей тайтл почали й не додивились."""
        h = self.app.store.entry(self.item["url"])
        if not h or self.app.store.watched(h) or h.get("si") is None:
            return None
        si, ei = h["si"], h.get("ei", 0)
        if si < len(self.seasons) and ei < len(self.seasons[si]["episodes"]):
            return si, ei, h.get("pos", 0)
        return None

    def render_actions(self):
        buttons = []
        spot = self.resume_spot() if self.seasons else None
        if spot:
            si, ei, pos = spot
            ep = self.seasons[si]["episodes"][ei]
            what = "" if len(self.all_episodes()) <= 1 else \
                f"{self.seasons[si]['name'].replace('Сезон ', 'С')} · {ep['label'].split(' — ')[0]} · "
            buttons.append(kit.plate(kit.label(f"Продовжити · {what}{fmt_time(pos)}",
                                               ft.Icons.PLAY_ARROW_ROUNDED),
                                     on_click=lambda e: self.app.page.run_task(self.play_resume),
                                     kind="board", autofocus=self.app.tv))
            buttons.append(kit.plate(kit.label("Спочатку", ft.Icons.REPLAY_ROUNDED),
                                     on_click=lambda e: self.app.page.run_task(
                                         self.play, self.season, 0, 0)))
        elif self.seasons:
            buttons.append(kit.plate(kit.label("Дивитись", ft.Icons.PLAY_ARROW_ROUNDED),
                                     on_click=lambda e: self.app.page.run_task(
                                         self.play, self.season, 0, 0),
                                     kind="board", autofocus=self.app.tv))
        buttons.append(self.fav_btn)
        if self.info.get("trailer"):
            buttons.append(kit.plate(kit.label("Трейлер", ft.Icons.SMART_DISPLAY_ROUNDED),
                                     on_click=lambda e: self.app.page.run_task(
                                         ft.UrlLauncher().launch_url, self.info["trailer"])))
        self.actions.controls = buttons

    def all_episodes(self):
        return [e for s in self.seasons for e in s["episodes"]]

    # ── озвучки
    def collect_voices(self):
        voices = []
        for ep in self.all_episodes():
            for src in ep["sources"] or []:
                v = src["entry"].get("voice") or ""
                if v and v not in voices:
                    voices.append(v)
        for v in core.VOICE_STATS.get(core.series_url(self.item["url"]), {}):
            if v and v not in voices:
                voices.append(v)
        self.voices = sorted(voices, key=core.is_original)
        if self.voice not in self.voices:
            self.voice = self.app.store.preferred_voice(self.item["url"], self.voices)

    async def detect_voices(self):
        """Серії зі своїми сторінками: набір озвучок — з першої серії."""
        ep = next(iter(self.all_episodes()), None)
        if ep is None:
            return
        try:
            await self.ensure_sources(ep)
        except Exception:
            return
        self.collect_voices()
        self.render_voices()
        self.update()

    def render_voices(self):
        counts = core.VOICE_STATS.get(core.series_url(self.item["url"])) or {}
        many = len(self.voices) > 1
        chips = []
        for v in self.voices:
            n = counts.get(v)
            text = v + (f" · {n}" if n and many else "")
            icon = ft.Icons.SUBTITLES_ROUNDED if core.is_original(v) else ft.Icons.MIC_ROUNDED
            chips.append(kit.plate(kit.label(text, icon), kind="lit" if v == self.voice else "plain",
                                   on_click=(lambda e, v=v: self.pick_voice(v)) if many else None,
                                   pad=(14, 10)))
        self.voice_row.controls = chips
        self.voice_box.visible = bool(chips)

    def pick_voice(self, v):
        self.voice = v
        self.app.store.remember_voice(self.item["url"], v)
        self.render_voices()
        self.voice_box.update()

    # ── серії
    def render(self):
        self.render_voices()
        self.render_actions()
        episodes = self.all_episodes()
        if len(episodes) <= 1 and len(self.seasons) <= 1:
            self.episodes_box.visible = False
            self.update()
            return
        self.episodes_box.visible = True
        self.season_row.controls = [
            kit.plate(kit.label(s["name"] + (" · недоступний" if s.get("locked") else "")),
                      kind="lit" if i == self.season else "plain", pad=(14, 10),
                      on_click=lambda e, i=i: self.pick_season(i))
            for i, s in enumerate(self.seasons)] if len(self.seasons) > 1 else []
        self.render_episodes()
        self.update()

    def pick_season(self, i):
        self.season = i
        self.render()

    def render_episodes(self):
        si = self.season
        eps = self.seasons[si]["episodes"] if self.seasons else []
        h = self.app.store.entry(self.item["url"]) or {}
        seen = h.get("seen") or {}
        rows = []
        for ei, ep in enumerate(eps):
            part = seen.get(f"{si}:{ei}", 0)
            if h.get("si") == si and h.get("ei") == ei and not self.app.store.watched(h):
                state, kind = f"Зупинились на {fmt_time(h.get('pos', 0))}", "signal"
            elif part >= 0.9:
                state, kind = "Переглянуто", "dim"
            else:
                state, kind = "", "dim"
            rows.append(kit.board_row(
                [(ep["label"], 3, "main"), (state, 2 if not self.app.mobile else 1, kind)],
                on_click=lambda e, ei=ei: self.app.page.run_task(self.play, si, ei, None),
                last=ei == len(eps) - 1, height=52))
        if not eps and self.seasons and self.seasons[si].get("locked"):
            rows = [ft.Container(T.text("Сайт показує цей сезон лише зареєстрованим",
                                        color=T.LETTER), padding=20)]
        self.episodes.controls = [kit.board(rows)] if rows else []

    def on_return(self):
        """Повернулись з плеєра: нові позначки серій і «Продовжити»."""
        self.render()

    # ── перегляд
    async def ensure_sources(self, ep):
        if ep["sources"] is None:
            ep["sources"] = await asyncio.to_thread(core.episode_sources, ep["page"])
        return ep["sources"]

    async def play_resume(self):
        spot = self.resume_spot()
        if spot:
            await self.play(*spot)
        else:
            await self.play(self.season, 0, 0)

    async def play(self, si, ei, pos):
        """pos=None — з місця зупинки, якщо саме цю серію почали; 0 — спочатку."""
        ep = self.seasons[si]["episodes"][ei]
        if ep["sources"] is None:
            self.app.toast("Завантажую серію…")
        try:
            sources = await self.ensure_sources(ep)
        except Exception as ex:
            self.app.toast(core.friendly_error(ex, "серію"), error=True)
            return
        if not sources:
            blocked = core.GEO["site"] or core.GEO["blocked"]
            self.app.toast("Недоступно у вашому регіоні" if blocked else
                           "Для цієї серії відео не знайдено", error=True)
            return
        if pos is None:
            h = self.app.store.entry(self.item["url"]) or {}
            pos = h.get("pos", 0) if h.get("si") == si and h.get("ei") == ei and \
                not self.app.store.watched(h) else 0
        self.collect_voices()
        if self.app.android:
            mode = self.app.store.settings.get("player") or "ask"
            if mode == "ask":
                mode = await self.ask_player()
                if not mode:
                    return
            if mode == "external":
                await self.play_external(ep)
                return
        try:  # якості потоку — у фоні, щоб плеєр узяв обрану (мережа)
            await asyncio.wait_for(asyncio.to_thread(core.hls_variants, self.pick_source(ep)), 12)
        except Exception:
            pass
        from .player import Player
        Player(self.app, self, si, ei, pos).open()

    async def ask_player(self):
        """Android: яким плеєром дивитись (запам'ятовується; змінити — у налаштуваннях)."""
        done = asyncio.get_running_loop().create_future()

        def pick(mode):
            self.app.store.set("player", mode)
            self.app.page.pop_dialog()
            if not done.done():
                done.set_result(mode)

        dlg = ft.AlertDialog(
            bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
            title=T.caps("Яким плеєром дивитись?", size=T.S_HEAD),
            content=T.text("Вбудований пам'ятає, де ви зупинились, пропускає заставку й вмикає "
                           "наступну серію. Інша програма — VLC, MX Player тощо. "
                           "Змінити можна в налаштуваннях.", color=T.DIM),
            actions=[kit.plate(kit.label("Інша програма"), lambda e: pick("external")),
                     kit.plate(kit.label("Вбудований"), lambda e: pick("builtin"), kind="board",
                               autofocus=True)],
            on_dismiss=lambda e: (None if done.done() else done.set_result(None)))
        self.app.page.show_dialog(dlg)
        return await done

    async def play_external(self, ep):
        """Потік обраної якості — у сторонній програмі (вибір показує MainActivity)."""
        from urllib.parse import urlencode
        entry = self.pick_source(ep)
        try:
            await asyncio.to_thread(core.hls_variants, entry)
            url = core.quality_url(entry["url"], self.app.store.settings.get("quality") or "1080")
        except Exception:
            url = entry["url"]
        name = (self.info.get("title") or self.item["title"]) + \
            (f" · {ep['label']}" if len(self.all_episodes()) > 1 else "")
        await ft.UrlLauncher().launch_url(
            f"{CTRL}://external?" + urlencode({"url": url, "title": name}),
            mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    def pick_source(self, ep):
        by_voice = {s["entry"].get("voice"): s["entry"] for s in ep["sources"]}
        for v in [self.voice] + self.app.store.voice_recent:
            if v in by_voice:
                return by_voice[v]
        return next((s["entry"] for s in ep["sources"]
                     if not core.is_original(s["entry"].get("voice") or "")),
                    ep["sources"][0]["entry"])

    # ── франшиза: табло частин у порядку виходу
    async def load_saga(self):
        info = self.info
        try:
            saga = await asyncio.to_thread(
                tmdb.collection, self.item["url"], info.get("title") or self.item["title"],
                info.get("orig") or self.item.get("orig", ""), info.get("year", ""))
        except Exception:
            return
        if not saga:
            return

        def lookup(p):
            if not p["released"]:
                return None
            try:
                return core.find_title(p["orig"], p["title"], p["year"])
            except Exception:
                return None
        found = await asyncio.gather(*[asyncio.to_thread(lookup, p) for p in saga["parts"]])
        if not any(found):
            return
        rows = []
        for i, (p, site) in enumerate(zip(saga["parts"], found)):
            here = p["id"] == saga["current"] or (site or {}).get("url") == self.item["url"]
            if here:
                note, kind = "Ви тут", "signal"
            elif site:
                note, kind = "", "dim"
            elif not p["released"]:
                note, kind = "Скоро", "dim"
            else:
                note, kind = "Немає на сайті", "dim"
            name = (site or {}).get("title") or p["title"]
            cells = [(f"{i + 1}.  {name}", 5, "main"), (p["year"], 1, "dim"), (note, 2, kind)]
            go = (lambda e, it=site: self.app.open_title(it)) if site and not here else None
            rows.append(kit.board_row(cells, on_click=go, last=i == len(saga["parts"]) - 1,
                                      height=50))
        name = saga["name"]
        self.saga_box.controls = [kit.board(rows, f"Франшиза{' · ' + name if name else ''}"
                                                  " · по порядку перегляду")]
        self.saga_box.visible = True
        self.update()

    # ── актори (TMDB)
    async def load_people(self):
        info = self.info
        try:
            people = await asyncio.to_thread(
                tmdb.cast, self.item["url"], info.get("title") or self.item["title"],
                info.get("orig") or self.item.get("orig", ""), info.get("year", ""), 12)
        except Exception:
            return
        if not people:
            return
        size = 64 if self.app.tv else 76 if not self.app.mobile else 64

        def person(c):
            face = ft.Container(
                ft.Image(src=c["photo"], fit=ft.BoxFit.COVER, width=size, height=size)
                if c["photo"] else T.caps((c["name"][:1] or "?"), size=size // 3, color=T.DIM),
                width=size, height=size, border_radius=size / 2, bgcolor=T.WALL_2,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS, alignment=ft.Alignment.CENTER)
            return ft.Column([face,
                              ft.Text(c["name"], size=T.S_SMALL, weight=ft.FontWeight.W_700,
                                      color=T.INK, max_lines=2, text_align=ft.TextAlign.CENTER,
                                      width=size + 30),
                              ft.Text(c["character"], size=T.S_SMALL - 1, color=T.DIM,
                                      max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                      text_align=ft.TextAlign.CENTER, width=size + 30)],
                             spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                             width=size + 30)

        self.people_box.controls = [kit.kicker("У ролях"),
                                    ft.Row([person(c) for c in people], spacing=14,
                                           scroll=ft.ScrollMode.AUTO)]
        self.people_box.visible = True
        self.cast_line.visible = False  # імена — уже з фото
        self.update()

    # ── відгуки з сайту: найпопулярніші першими
    def show_reviews(self, comments, limit=4):
        if not comments:
            return
        ranked = sorted(comments, key=lambda c: -c["likes"])

        def card(c):
            staff = "адмін" in (c["role"] or "").lower()
            head = ft.Row([
                ft.Text(c["author"] + (" · сайт" if staff else ""), size=T.S_SMALL,
                        weight=ft.FontWeight.W_800, color=T.SIGNAL if staff else T.INK),
                ft.Text(c["date"], size=T.S_SMALL - 1, color=T.DIM),
                *([ft.Text(f"♥ {c['likes']}", size=T.S_SMALL - 1, color=T.DIM)]
                  if c["likes"] else [])], spacing=12, wrap=True)
            body = ft.Column([head, ft.Text(c["text"], size=T.S_BODY, color=T.INK)],
                             spacing=6, tight=True)
            if self.app.tv:  # пульт «доходить» лише до того, що фокусується
                return kit.plate(body, on_click=lambda e: None, pad=(18, 14), expand=True)
            return ft.Container(body, padding=ft.Padding.symmetric(horizontal=18, vertical=14),
                                bgcolor=T.WALL_2, border=ft.Border.all(1, T.LINE), border_radius=3)

        def fill(n):
            items = [card(c) for c in ranked[:n]]
            if len(ranked) > n:
                items.append(kit.plate(kit.label(f"Показати всі · {len(ranked)}"),
                                       on_click=lambda e: (fill(len(ranked)), self.update())))
            self.reviews_box.controls = [kit.kicker(f"Відгуки глядачів · {len(comments)}"),
                                         *items]

        fill(limit)
        self.reviews_box.visible = True
