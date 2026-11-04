"""Складові інтерфейсу «Табло кінотеатру».

Усе, що натискається, — ft.Button: лише кнопки отримують фокус з пульта. Колір тексту без
явного кольору дає сама кнопка залежно від стану (миттєво, без Python) — так вибране
перефарбовується навіть на слабкій ТВ-приставці.
"""
import asyncio

import flet as ft

import tmdb
from . import theme as T

CTRL = ft.ControlState


def quiet():
    """Без перевірки всього застосунку після події: обробник сам оновлює, що змінив."""
    ft.context.disable_auto_update()


def _click(handler):
    def run(e):
        quiet()
        return handler(e) if handler else None
    return run


def plate(content, on_click=None, kind="plain", pad=(18, 14), radius=3, autofocus=False,
          on_focus=None, expand=False, tooltip=None):
    """Плашка табло. kind: "plain" — тло стіни; "board" — плашка табло (головна дія);
    "lit" — вибране (як board, але без зміни при фокусі, лише рамка).
    У фокусі / під мишею: plain → плашка табло; board → червоний сигнал."""
    if kind == "board":
        bg = {CTRL.FOCUSED: T.SIGNAL, CTRL.HOVERED: T.SIGNAL, CTRL.DEFAULT: T.BOARD}
        fg = {CTRL.FOCUSED: "#FFFFFF", CTRL.HOVERED: "#FFFFFF", CTRL.DEFAULT: T.LETTER}
    elif kind == "glass":  # поверх відео: завжди темна, незалежно від теми
        bg = {CTRL.FOCUSED: "#F1E6CC", CTRL.HOVERED: "#F1E6CC", CTRL.DEFAULT: "#66000000"}
        fg = {CTRL.FOCUSED: "#1A1714", CTRL.HOVERED: "#1A1714", CTRL.DEFAULT: "#F1E6CC"}
    elif kind == "lit":
        bg = {CTRL.DEFAULT: T.BOARD}
        fg = {CTRL.DEFAULT: T.LETTER}
    else:
        bg = {CTRL.FOCUSED: T.BOARD, CTRL.HOVERED: T.BOARD, CTRL.DEFAULT: T.WALL_2}
        fg = {CTRL.FOCUSED: T.LETTER, CTRL.HOVERED: T.LETTER, CTRL.DEFAULT: T.INK}
    side = {CTRL.FOCUSED: ft.BorderSide(3, T.SIGNAL if kind != "board" else T.INK),
            CTRL.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
    if kind == "glass":
        side[CTRL.DEFAULT] = ft.BorderSide(1, "#40F1E6CC")
    if kind == "plain":
        side[CTRL.DEFAULT] = ft.BorderSide(1, T.LINE)
    extra = {"expand": True} if expand else {}  # expand=False у рядку з переносом ламає розмітку
    return ft.Button(
        content=content, on_click=_click(on_click), autofocus=autofocus, tooltip=tooltip, **extra,
        on_focus=(lambda e: (quiet(), on_focus(True))) if on_focus else None,
        on_blur=(lambda e: (quiet(), on_focus(False))) if on_focus else None,
        style=ft.ButtonStyle(
            bgcolor=bg, color=fg, icon_color=fg, side=side, elevation=0,
            overlay_color=ft.Colors.TRANSPARENT, shadow_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=radius), animation_duration=0,
            padding=ft.Padding.symmetric(horizontal=pad[0], vertical=pad[1])))


def label(text, icon=None, size=None, weight=ft.FontWeight.W_700, left=False):
    """Напис для плашки: великі літери, колір — від плашки. left — притиснути ліворуч
    (у пунктах меню на всю ширину), інакше — по центру плашки."""
    size = size or T.S_SMALL
    parts = [ft.Icon(icon, size=size + 6)] if icon else []
    parts.append(ft.Text(text.upper(), size=size, weight=weight,
                         style=ft.TextStyle(letter_spacing=size * 0.1)))
    return ft.Row(parts, spacing=10, tight=not left, alignment=ft.MainAxisAlignment.START,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)


def cover(src):
    """Кадр, що заповнює весь батьківський контейнер (обрізаючи зайве)."""
    if not src:
        return None
    return ft.Image(src=src, fit=ft.BoxFit.COVER, gapless_playback=True,
                    width=float("inf"), height=float("inf"))


def kicker(text, color=None):
    return T.caps(text, size=T.S_KICK, color=color or T.DIM, weight=ft.FontWeight.W_700,
                  spacing=0.14)


# ── постери, рейтинг і жанри для карток — лише з TMDB (картинки сайту не вантажимо)
# окрема черга потоків для того, на що чекає людина (сторінка фільму, пошук відео): у
# спільній черзі воно стояло за рядами головної й постерами (на ТВ-приставці потоків мало)
_FAST = None


async def run_fast(fn, *args):
    global _FAST
    if _FAST is None:
        from concurrent.futures import ThreadPoolExecutor
        _FAST = ThreadPoolExecutor(4, thread_name_prefix="uafix-fast")
    return await asyncio.get_running_loop().run_in_executor(_FAST, lambda: fn(*args))


_slots = None
POSTER = "w342"
RATIO = 2 / 3  # вертикальний постер


def _title_plate(title):
    """Замість постера (поки шукаємо чи якщо TMDB не знайшов): назва написом табло."""
    return ft.Container(
        ft.Text((title or "").upper(), size=T.S_SMALL, weight=ft.FontWeight.W_800,
                color=T.DIM, text_align=ft.TextAlign.CENTER, max_lines=4,
                overflow=ft.TextOverflow.ELLIPSIS, style=ft.TextStyle(letter_spacing=1.2)),
        alignment=ft.Alignment.CENTER, padding=10, bgcolor=T.WALL_2)


def tmdb_poster(page, item, width, on_meta=None, meta=True):
    """Вертикальний постер TMDB: з кешу одразу, інакше — один пошук (укр. назва, потім
    оригінальна), з якого заразом рейтинг і жанри (on_meta(rating, genres, late))."""
    global _slots
    h = width / RATIO
    box = ft.Container(_title_plate(item.get("title")), width=width, height=h,
                       bgcolor=T.WALL_2, border_radius=3, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)

    def put(src):
        if src:
            box.content = ft.Image(src=src, fit=ft.BoxFit.COVER, gapless_playback=True,
                                   width=width, height=h,
                                   error_content=_title_plate(item.get("title")))

    def meta_now():
        k, m = tmdb.meta_cached(item["url"])
        if k and m and (m["rating"] or m["genres"]):
            on_meta(m["rating"], m["genres"], False)
        return k

    known, src = tmdb.poster_cached(item["url"], POSTER) if item.get("url") else (True, None)
    if known:
        put(src)
        if not (on_meta and meta) or meta_now():
            return box
    if not tmdb.enabled() or not item.get("url"):
        return box
    if _slots is None:
        _slots = asyncio.Semaphore(3 if T.TV else 6)  # ТВ: не забирати всі потоки

    async def look():
        import core
        try:
            async with _slots:
                if not known:
                    url = await asyncio.wait_for(asyncio.to_thread(
                        tmdb.poster, item["url"], item.get("title", ""), item.get("orig", ""),
                        item.get("year", ""), POSTER), 15)
                    put(url)
                    try:
                        box.update()
                    except Exception:
                        return
                if on_meta and meta:  # рейтинг і жанри — з того ж пошуку (назви жанрів раз)
                    m = await asyncio.wait_for(asyncio.to_thread(
                        tmdb.meta, item["url"], item.get("title", ""), item.get("orig", ""),
                        item.get("year", ""), core.site_meta), 15)
                    if m and (m["rating"] or m["genres"]):
                        on_meta(m["rating"], m["genres"], True)
        except Exception:
            pass

    page.run_task(look)
    return box


def poster_card(page, item, on_open, width, meta=True, autofocus=False, on_focus=None):
    """Афіша: вертикальний постер TMDB, під ним назва й «★ 7.6 · Драма». Вибрана — у рамці."""
    title = ft.Text(item.get("title") or "", size=T.S_SMALL + 1, weight=ft.FontWeight.W_700,
                    color=T.INK, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    sub = ft.Text(item.get("orig") or " ", size=T.S_SMALL - 1, color=T.DIM, max_lines=1,
                  overflow=ft.TextOverflow.ELLIPSIS)
    age = ft.Container(
        ft.Text(item.get("age") or "", size=T.S_SMALL - 2, weight=ft.FontWeight.W_800,
                color=T.LETTER), bgcolor=T.BOARD,
        padding=ft.Padding.symmetric(horizontal=5, vertical=1),
        border_radius=2, visible=bool(item.get("age")), left=6, top=6)

    def show(rating, genres, late):
        bits = ([f"★ {rating:.1f}"] if rating else []) + list(genres[:1])
        sub.value = "  ·  ".join(bits) or sub.value
        if late:
            try:
                sub.update()
            except Exception:
                pass

    pic = ft.Stack([tmdb_poster(page, item, width, show, meta), age],
                   width=width, height=width / RATIO)
    body = ft.Column([pic, ft.Container(ft.Column([title, sub], spacing=1, tight=True),
                                        padding=ft.Padding.only(left=2, right=2, top=6))],
                     spacing=0, width=width, tight=True)
    return ft.Button(
        content=body, on_click=_click(lambda e: on_open(item)), autofocus=autofocus,
        on_focus=(lambda e: (quiet(), on_focus(True))) if on_focus else None,
        on_blur=(lambda e: (quiet(), on_focus(False))) if on_focus else None,
        style=ft.ButtonStyle(
            bgcolor=ft.Colors.TRANSPARENT, elevation=0, padding=5,
            overlay_color=ft.Colors.TRANSPARENT, shadow_color=ft.Colors.TRANSPARENT,
            side={CTRL.FOCUSED: ft.BorderSide(3, T.BOARD), CTRL.HOVERED: ft.BorderSide(2, T.BOARD),
                  CTRL.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)},
            shape=ft.RoundedRectangleBorder(radius=5), animation_duration=0))


def board(rows, label_text=None):
    """Табло: світла панель у рамці, рядки розділені лініями."""
    panel = ft.Container(ft.Column(rows, spacing=0), bgcolor=T.BOARD, border_radius=3,
                         border=ft.Border.all(5, T.BOARD_LINE), clip_behavior=ft.ClipBehavior.HARD_EDGE)
    if not label_text:
        return panel
    return ft.Column([kicker(label_text), panel], spacing=10, tight=True)


def board_row(cells, on_click=None, last=False, autofocus=False, height=None, trailing=None,
              on_focus=None):
    """Рядок табло. cells — [(текст, частка ширини, вид)], вид: "main" / "dim" / "signal".
    Колонки ділять ширину пропорційно (вузьке вікно не «з'їдає» назву).
    У фокусі / під мишею — інверсія: темна смуга, світлі літери."""
    size_main = T.S_BOARD
    texts, row_cells = [], []
    for value, share, kind in cells:
        color = {"main": T.LETTER, "dim": T.BOARD_DIM, "signal": T.BOARD_SIGNAL}[kind]
        size = size_main if kind == "main" else T.S_SMALL
        t = ft.Text((value or "").upper(), size=size, color=color,
                    max_lines=2 if (T.MOBILE and kind == "main") else 1,
                    weight=ft.FontWeight.W_800 if kind == "main" else ft.FontWeight.W_700,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    style=ft.TextStyle(letter_spacing=size * (0.04 if kind == "main" else 0.06)))
        texts.append((t, color))
        row_cells.append(ft.Container(t, expand=share or 1))
    if T.MOBILE or T.TV:
        # телефон і TV: назва — першим рядком, решта (серія, скільки лишилось) — під нею
        extra = [c.content for c in row_cells[1:] if c.content.value]
        body = ft.Column([row_cells[0].content,
                          ft.Row(extra, spacing=12, wrap=True, run_spacing=2)],
                         spacing=4, tight=True)
    else:
        body = ft.Row(row_cells, spacing=18, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def lit(on):
        for t, c in texts:
            t.color = T.BOARD if on else c
            try:  # рядок могли вже прибрати з екрана (перебудова табло) — тоді нічого
                t.update()
            except Exception:
                return

    btn = ft.Button(
        content=ft.Container(body, height=None if T.MOBILE else (height or (56 if T.TV else 56)),
                             padding=ft.Padding.symmetric(vertical=12) if T.MOBILE else None,
                             alignment=ft.Alignment.CENTER_LEFT),
        on_click=_click(on_click), autofocus=autofocus, expand=True,
        on_focus=lambda e: (quiet(), lit(True), on_focus and on_focus()),
        on_blur=lambda e: (quiet(), lit(False)),
        on_hover=lambda e: (quiet(), lit(e.data in (True, "true"))),
        style=ft.ButtonStyle(
            bgcolor={CTRL.FOCUSED: T.LETTER, CTRL.HOVERED: T.LETTER, CTRL.DEFAULT: T.BOARD},
            elevation=0, overlay_color=ft.Colors.TRANSPARENT, shadow_color=ft.Colors.TRANSPARENT,
            shape=ft.RoundedRectangleBorder(radius=0), animation_duration=0,
            padding=ft.Padding.symmetric(horizontal=16 if T.MOBILE else 24, vertical=0)))
    row = ft.Container(
        ft.Row([btn] + ([trailing] if trailing else []), spacing=0,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        border=None if last else ft.Border.only(bottom=ft.BorderSide(2, T.BOARD_LINE)),
        animate=T.FAST, clip_behavior=ft.ClipBehavior.HARD_EDGE)
    row.focus_target = btn
    row.texts = texts
    return row


def board_icon(icon, on_click, tooltip=None):
    """Кнопка-іконка на табло (✕ «Прибрати»)."""
    return ft.IconButton(icon, on_click=_click(on_click), tooltip=tooltip, icon_size=22,
                         style=ft.ButtonStyle(
                             bgcolor={CTRL.FOCUSED: T.LETTER, CTRL.HOVERED: T.LETTER,
                                      CTRL.DEFAULT: ft.Colors.TRANSPARENT},
                             icon_color={CTRL.FOCUSED: T.BOARD, CTRL.HOVERED: T.BOARD,
                                         CTRL.DEFAULT: T.BOARD_DIM},
                             shape=ft.RoundedRectangleBorder(radius=0), animation_duration=0))


def spinner(size=22):
    return ft.ProgressRing(width=size, height=size, stroke_width=2.5, color=T.SIGNAL)


def placeholder_row(count, width):
    """Сірі «афіші», поки вантажиться поличка."""
    return [ft.Container(width=width + 12, height=width / RATIO + 58,
                         content=ft.Container(bgcolor=T.WALL_2, border_radius=3,
                                              margin=ft.Margin.only(left=6, top=6, right=6, bottom=52)))
            for _ in range(count)]
