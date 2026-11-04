"""Дані користувача: налаштування, історія, обране, вибір озвучки.

Ключі й формат — ті самі, що й у попередній версії (та сама історія після оновлення,
та сама синхронізація між пристроями). Позиція й тривалість в історії — у мілісекундах.
"""
import asyncio
import json
import time

import flet as ft

import sync

HISTORY_MAX = 40

DEFAULT_SETTINGS = {
    "quality": "1080", "keep_history": True, "card_meta": True, "abroad": True,
    "hwdec": "auto", "volume_boost": "6", "skip_intro": True, "sync_code": "",
    "history_cleared": 0, "player": "ask", "auto_updates": True, "skipped_version": "",
}

ITEM_KEYS = ("url", "title", "orig", "poster", "age")


def item_of(d):
    return {k: d.get(k, "") for k in ITEM_KEYS}


class Store:
    def __init__(self, page: ft.Page):
        self.page = page
        self.prefs = ft.SharedPreferences()
        self.settings = dict(DEFAULT_SETTINGS)
        self.history = []        # новіше першим
        self.favorites = []      # новіше першим; прибране — з позначкою removed
        self.voice_titles = {}   # адреса тайтлу → обрана озвучка
        self.voice_recent = []   # студії, які обирали останніми
        self.listeners = []      # викликаються, коли змінилась історія чи обране
        self.sync_dirty = False  # є свої зміни, яких ще нема на сервері
        self.sync_busy = False
        self.sync_last = 0.0

    async def load(self):
        for key in self.settings:
            try:
                value = await self.prefs.get(key)
            except Exception:
                value = None
            if value is not None:
                self.settings[key] = value
        for key, attr in (("history", "history"), ("favorites", "favorites"),
                          ("voice_recent", "voice_recent")):
            try:
                raw = await self.prefs.get(key)
                if raw:
                    setattr(self, attr, json.loads(raw))
            except Exception:
                pass
        try:
            raw = await self.prefs.get("voice_titles")
            if raw:
                self.voice_titles.update(json.loads(raw))
        except Exception:
            pass

    def _save(self, key, value):
        self.page.run_task(self.prefs.set, key, value if isinstance(value, str)
                           else json.dumps(value, ensure_ascii=False))

    def changed(self, push=None):
        """Історія / обране змінились: оновити екрани; push — одразу на сервер (True),
        з найближчою синхронізацією (False) чи ні (None — дані прийшли із сервера)."""
        if push is not None and self.settings.get("sync_code"):
            self.sync_dirty = True
            if push:
                self.page.run_task(self.sync_now)
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:
                pass

    # ── налаштування
    def set(self, key, value):
        self.settings[key] = value
        self.page.run_task(self.prefs.set, key, value)

    # ── історія
    def entry(self, url):
        return next((h for h in self.history if h["url"] == url and not h.get("removed")), None)

    @staticmethod
    def watched(h):
        return bool(h.get("dur")) and h["pos"] >= 0.95 * h["dur"]

    def continuing(self):
        return [h for h in self.history if not h.get("removed") and not self.watched(h)]

    def record(self, item, season, label, si, ei, voice, pos, dur):
        """Де зупинились: серія й мілісекунда."""
        if not self.settings.get("keep_history", True):
            return
        old = self.entry(item["url"]) or {}
        seen = dict(old.get("seen") or {})
        if dur:
            seen[f"{si}:{ei}"] = round(min(1, pos / dur), 3)
        h = dict(item_of(item), season=season, label=label, si=si, ei=ei, voice=voice,
                 pos=int(pos), dur=int(dur), ts=int(time.time()))
        if seen:
            h["seen"] = dict(list(seen.items())[-150:])
        self.history[:] = [h] + [x for x in self.history if x["url"] != item["url"]]
        del self.history[HISTORY_MAX:]
        self._save("history", self.history)
        self.changed(push=False)  # позиція — раз на хвилину, не щоразу

    def remove(self, url):
        """Прибрати з «Продовжити» (позначка removed — щоб синхронізація не повернула)."""
        old = next((h for h in self.history if h["url"] == url), None)
        if not old:
            return
        self.history[:] = [{"url": url, "title": old.get("title", ""), "ts": int(time.time()),
                            "removed": True}] + [h for h in self.history if h["url"] != url]
        self._save("history", self.history)
        self.changed(push=True)

    # ── обране
    def is_favorite(self, url):
        return any(f["url"] == url and not f.get("removed") for f in self.favorites)

    def toggle_favorite(self, item):
        added = not self.is_favorite(item["url"])
        entry = dict(item_of(item), ts=int(time.time()))
        if not added:
            entry["removed"] = True
        self.favorites[:] = [entry] + [f for f in self.favorites if f["url"] != item["url"]]
        self._save("favorites", self.favorites)
        self.changed(push=True)
        return added

    def active_favorites(self):
        return [f for f in self.favorites if not f.get("removed")]

    # ── озвучка
    def remember_voice(self, url, voice):
        if not voice:
            return
        self.voice_titles[url] = voice
        if not voice.startswith("Оригінал") and voice not in ("Українська",
                                                              "Українська (багатоголоса)"):
            self.voice_recent[:] = [voice] + [v for v in self.voice_recent if v != voice][:19]
            self._save("voice_recent", self.voice_recent)
        if len(self.voice_titles) > 300:
            for k in list(self.voice_titles)[:len(self.voice_titles) - 300]:
                self.voice_titles.pop(k, None)
        self._save("voice_titles", self.voice_titles)

    def preferred_voice(self, url, voices):
        if not voices:
            return ""
        h = self.entry(url) or {}
        for v in (self.voice_titles.get(url), h.get("voice"), *self.voice_recent):
            if v in voices:
                return v
        return voices[0]

    def clear_history(self):
        """Очистити історію — позначка часу, щоб очистилась і на інших пристроях."""
        self.history.clear()
        self.set("history_cleared", int(time.time()))
        self._save("history", self.history)
        self.changed(push=True)

    # ── синхронізація між пристроями (сервер — sync_worker/, злиття — sync.merge)
    def sync_doc(self):
        return {"history": self.history, "favorites": self.favorites,
                "history_cleared": self.settings.get("history_cleared", 0) or 0}

    def _signature(self):
        return ([(h["url"], h.get("pos"), h.get("label")) for h in self.continuing()][:15],
                [f["url"] for f in self.active_favorites()])

    def apply_sync(self, doc):
        """Злиті дані з усіх пристроїв → сюди (екрани — лише якщо щось справді змінилось)."""
        if json.dumps(doc, sort_keys=True) == json.dumps(self.sync_doc(), sort_keys=True):
            return
        before = self._signature()
        self.history[:] = doc.get("history") or []
        self.favorites[:] = doc.get("favorites") or []
        if doc.get("history_cleared", 0) != self.settings.get("history_cleared", 0):
            self.set("history_cleared", doc.get("history_cleared", 0))
        self._save("history", self.history)
        self._save("favorites", self.favorites)
        if self._signature() != before:
            self.changed()

    async def sync_now(self, manual=False):
        """→ None, якщо все гаразд, або текст помилки."""
        code = self.settings.get("sync_code")
        if not code or self.sync_busy:
            return None
        self.sync_busy = True
        try:
            if self.sync_dirty or manual:
                self.sync_dirty = False
                merged = await asyncio.to_thread(sync.push, code, self.sync_doc())
            else:  # свого нового нема — лише забрати зміни інших пристроїв
                merged = sync.merge(self.sync_doc(), await asyncio.to_thread(sync.pull, code))
            self.apply_sync(merged)
            self.sync_last = time.time()
            return None
        except Exception as ex:
            self.sync_dirty = True  # повториться наступного разу
            import core
            return core.friendly_error(ex, "синхронізацію")
        finally:
            self.sync_busy = False

    async def sync_loop(self):
        """Одразу на старті, далі — раз на хвилину."""
        while True:
            await self.sync_now()
            await asyncio.sleep(60)
