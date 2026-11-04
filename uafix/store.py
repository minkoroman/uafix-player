"""Дані користувача: налаштування, історія, обране, вибір озвучки.

Ключі й формат — ті самі, що й у попередній версії (та сама історія після оновлення,
та сама синхронізація між пристроями). Позиція й тривалість в історії — у мілісекундах.
"""
import json
import time

import flet as ft

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

    def changed(self):
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
        self.changed()

    def remove(self, url):
        """Прибрати з «Продовжити» (позначка removed — щоб синхронізація не повернула)."""
        old = next((h for h in self.history if h["url"] == url), None)
        if not old:
            return
        self.history[:] = [{"url": url, "title": old.get("title", ""), "ts": int(time.time()),
                            "removed": True}] + [h for h in self.history if h["url"] != url]
        self._save("history", self.history)
        self.changed()

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
        self.changed()
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
