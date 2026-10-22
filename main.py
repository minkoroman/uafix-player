"""UAFix Player — кросплатформений клієнт (Windows / macOS / Android) на Flet."""
import locale

# У зібраному застосунку Python працює в одному процесі з плеєром (libmpv), а mpv не
# підтримує нестандартну числову локаль (напр. uk_UA з десятковою комою): створення
# плеєра падає з assertion при першому ж відео. Тому числова локаль — завжди «C».
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass

import asyncio
import json
import os
import re
import time
from urllib.parse import urlencode, urlparse

import flet as ft
import flet_video as fv

import appearance
import airplay
import appicon
import cast as uafix_cast
import core
import dlna
import feedback
import sync
import screen
import tmdb
import ui
import updater
from ui import ACCENT, BG, BORDER, MUTED, SURFACE, SURFACE_2, SURFACE_3, TEXT

# Розділи бічного меню: (секція, [(назва, шлях, іконка)])
FAV_PATH = "favorites"  # «Обране» — локальний список, не розділ сайту
HISTORY_PATH = "history"  # «Історія переглядів» — усе переглянуте, разом із завершеним
LOCAL_PATHS = (FAV_PATH, HISTORY_PATH)

NAV = [
    ("Моє", [
        ("Обране", FAV_PATH, ft.Icons.BOOKMARK_ROUNDED),
        ("Історія переглядів", HISTORY_PATH, ft.Icons.HISTORY_ROUNDED),
    ]),
    ("Серіали", [
        ("Усі серіали", "/serials/", ft.Icons.LIVE_TV_ROUNDED),
        ("Нові серії", "/serials/new_uaserial/", ft.Icons.FIBER_NEW_ROUNDED),
        ("Серіали 2026", "/serials/serial_2026_online/", ft.Icons.CALENDAR_MONTH_ROUNDED),
        ("Серіали 2025", "/serials/serial_2025_online/", ft.Icons.CALENDAR_MONTH_ROUNDED),
    ]),
    ("Фільми", [
        ("Усі фільми", "/films/", ft.Icons.THEATERS_ROUNDED),
        ("Новинки", "/films/new_netflix_ua/", ft.Icons.NEW_RELEASES_ROUNDED),
        ("Фільми 2026", "/films/uafilms_2026/", ft.Icons.CALENDAR_MONTH_ROUNDED),
    ]),
    ("Анімація", [
        ("Мультфільми", "/cartoons/", ft.Icons.CHILD_CARE_ROUNDED),
        ("Мультсеріали", "/serials/multseial/", ft.Icons.ANIMATION_ROUNDED),
        ("Аніме", "/anime/", ft.Icons.AUTO_AWESOME_ROUNDED),
    ]),
    ("Інше", [
        ("Дорами", "/dorama/", ft.Icons.FAVORITE_ROUNDED),
        ("Озвучка від UAflix", "/dubbing/", ft.Icons.MIC_ROUNDED),
    ]),
]
DESTINATIONS = [d for _, items in NAV for d in items]

# жанри — окремі розділи сайту; показуються рядком над каталогом «Усі фільми» / «Усі серіали»
GENRES = {
    "/films/": ("Фільми", [
        ("Бойовики", "/films/action/"), ("Комедії", "/films/comedy/"),
        ("Жахи", "/films/films_horror/"), ("Фантастика", "/films/fantastics/"),
        ("Мелодрами та драми", "/films/melodrama/"), ("Про кохання", "/films/love_films/"),
        ("Для підлітків", "/films/teen_films/"), ("Про школу", "/films/school_films/"),
        ("Документальні", "/films/documental_films/"),
    ]),
    "/serials/": ("Серіали", [
        ("Комедійні", "/serials/comedy_serials/"), ("Детективні", "/serials/detective/"),
        ("Фантастика", "/serials/fantastichni_serialy/"), ("Фентезі", "/serials/fantasy_serialy/"),
        ("Історичні", "/serials/history_serials/"), ("Жахи", "/serials/horror_serials/"),
        ("Про кохання", "/serials/pro_love/"), ("Про підлітків", "/serials/pro_pidlitkiv/"),
        ("Про школу", "/serials/school/"), ("Документальні", "/serials/documental/"),
    ]),
}


def genre_root(path):
    """Розділ із жанрами, до якого належить path (сам розділ чи один із його жанрів)."""
    for root, (_, genres) in GENRES.items():
        if path == root or any(g == path for _, g in genres):
            return root
    return None

# полички під банером на головній: (назва, розділ)
# (назва, розділ, блок головної сайту — звідти найсвіжіше йде першим; див. core.fresh_catalog)
SHELVES = [
    ("Озвучка від UAflix", "/dubbing/", "Наша озвучка"),
    ("Нові серії", "/serials/new_uaserial/", "Серіали"),
    ("Новинки кіно", "/films/new_netflix_ua/", "Фільми"),
    ("Серіали 2026", "/serials/serial_2026_online/", None),
    ("Аніме", "/anime/", "Аніме"),
    ("Мультфільми", "/cartoons/", "Мультфільми"),
]
BANNER_INTERVAL = 7  # секунд на слайд

# Налаштування mpv для нестабільного відеосервера: за замовчуванням плеєр тримає запас
# лише на кілька секунд і при обриві з'єднання просто стоїть — звідси підвисання, фрізи
# й розсинхрон звуку з картинкою. Більший запас наперед + автоматичне перепідключення.
MPV_STREAMING = {
    "cache": "yes",
    "cache-secs": 60,                 # тримати до хвилини відео наперед
    "demuxer-readahead-secs": 60,
    "demuxer-max-bytes": "150MiB",
    "demuxer-max-back-bytes": "50MiB",  # можна трохи перемотати назад без перезавантаження
    "cache-pause-initial": "yes",     # на старті спершу накопичити трохи буфера
    "cache-pause-wait": 3,            # після підвисання грати, лише коли є ≥3 с запасу
    "network-timeout": 20,
    "stream-lavf-o": "reconnect=1,reconnect_streamed=1,reconnect_on_network_error=1,"
                     "reconnect_delay_max=5",
}

# Android / TV: яким плеєром дивитись (налаштування «Плеєр»)
PLAYER_OPTIONS = [("Запитувати", "ask"), ("Вбудований", "builtin"),
                  ("Інша програма", "external")]

# розмір субтитрів: (назва, множник базового розміру)
SUB_SIZES = [("Малі", 0.75), ("Середні", 1.0), ("Великі", 1.3), ("Дуже великі", 1.6)]
# масштаб відео: (ключ, назва, BoxFit)
FIT_MODES = [("contain", "Вписати", ft.BoxFit.CONTAIN), ("cover", "Заповнити", ft.BoxFit.COVER),
             ("fill", "Розтягнути", ft.BoxFit.FILL)]

# TV-збірка (Android TV) додає модуль-прапорець uafix_tv_flag.py (саме модуль, а не файл:
# на Android код може бути запакований у zip, де «файл поруч» не перевірити);
# для перевірки на ПК — змінна середовища UAFIX_TV=1
try:
    import uafix_tv_flag  # noqa: F401
    TV = True
except ImportError:
    TV = os.environ.get("UAFIX_TV") == "1"
TV_CARD_W = 300

POSTER_RATIO = 16 / 9  # картинки на сайті горизонтальні (1200×675, 800×450)


def plural(n, one, few, many):
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def quiet():
    """Без автоматичного оновлення всієї сторінки після цієї події. Flet після кожного
    обробника, що сам нічого не оновив, перевіряє на зміни ВЕСЬ застосунок — для частих
    подій (кожна кнопка пульта, прокрутка, фокус) це на телевізорі з довгим списком тайтлів
    займало секунди. Такі обробники оновлюють те, що змінили, явно."""
    ft.context.disable_auto_update()


# Перебудова інтерфейсу «на льоту» (зміна теми без перезапуску): кожен запуск main —
# нове «покоління»; фонові цикли попереднього бачать, що їх замінили, і зупиняються
RUN = {"gen": 0}
UI_COLORS = ("ACCENT", "BG", "BORDER", "MUTED", "SURFACE", "SURFACE_2", "SURFACE_3", "TEXT")


def main(page: ft.Page, reopen=None):
    # «перший» — звичайний запуск (зокрема повторне відкриття на Android, де процес Python
    # живе далі); не перший — лише перебудова після зміни теми (reopen)
    RUN["gen"] += 1  # фонові цикли попереднього запуску побачать це й зупиняться
    gen, first = RUN["gen"], reopen is None
    page.title = "UAFix Player"
    page.theme_mode = ft.ThemeMode.LIGHT if ui.LIGHT else ft.ThemeMode.DARK
    page.theme = page.dark_theme = ui.theme()
    if TV:
        # підсвітка фокуса там, де її малює сам Flutter (бокове меню): контрастна до теми
        page.theme.focus_color = page.dark_theme.focus_color = ft.Colors.with_opacity(
            0.2, "#000000" if ui.LIGHT else "#FFFFFF")
        ui.LITE = True  # слабкий процесор і GPU телевізора: без зайвих анімацій, тіней і розмиття

    def shimmer(content, **kw):
        """Блискуча заглушка завантаження; на телевізорі — статична (анімація постійно
        перемальовує екран)."""
        if not TV:
            return ft.Shimmer(content, **kw)
        for k in ("key", "expand"):
            if k in kw:
                setattr(content, k, kw[k])
        return content
    page.bgcolor = BG
    is_mac = page.platform == ft.PagePlatform.MACOS
    # телевізор — Android, але з розміткою «як на ПК» і керуванням з пульта
    # на Android TV Flet повідомляє окрему платформу ANDROID_TV — для нас це той самий Android
    is_android = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.ANDROID_TV)
    is_mobile = page.platform in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS) and not TV
    is_desktop = page.platform in (ft.PagePlatform.MACOS, ft.PagePlatform.WINDOWS,
                                   ft.PagePlatform.LINUX)
    if is_desktop and first:
        page.window.width, page.window.height = 1240, 800
        page.window.min_width, page.window.min_height = 760, 540
        page.window.bgcolor = BG
    if is_mac:
        # контент під «світлофором», як у нативних застосунках macOS
        page.window.title_bar_hidden = True
        page.window.title_bar_buttons_hidden = False
    traffic_gap = 78 if is_mac else 0
    # для перевірки на Mac — UAFIX_WIN=1 (панель і кнопки вікна як на Windows)
    is_win = page.platform == ft.PagePlatform.WINDOWS or os.environ.get("UAFIX_WIN") == "1"
    if is_win:
        # як на Mac: без системної смуги заголовка — верхня панель застосунку і є заголовком,
        # кнопки вікна (згорнути / розгорнути / закрити) — власні, праворуч (win_buttons)
        page.window.title_bar_hidden = True
        page.window.title_bar_buttons_hidden = True
        # вікно створене прихованим (pyproject: tool.flet.windows.app.hide_window_on_start) —
        # показуємо, коли вже без системного заголовка
        page.window.visible = True

    def win_buttons(on_dark=False):
        """Кнопки вікна Windows у стилі Windows 11 (на інших системах — нічого)."""
        if not is_win:
            return []
        fg = "#FFFFFF" if on_dark else TEXT

        def minimize(e):
            quiet()
            w = page.window
            if w.full_screen or st.get("fullscreen"):
                # з повного екрана Windows вікно не згортає — спершу вийти з нього
                if st.get("win_fullscreen"):
                    st["win_fullscreen"](False)
                else:
                    w.full_screen = False
                    page.update()
            w.minimized = True
            page.update()

        def maximize(e):
            page.window.maximized = not page.window.maximized
            page.update()

        def btn(icon, on_click, hover, hover_fg=None, tip=None):
            return ft.IconButton(
                icon, on_click=on_click, tooltip=tip, icon_size=16, width=46, height=34,
                hover_color=hover,
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=6), padding=0,
                    icon_color={ft.ControlState.HOVERED: hover_fg or fg,
                                ft.ControlState.DEFAULT: fg}))

        soft = ft.Colors.with_opacity(0.1, fg)
        return [ft.Row([
            btn(ft.Icons.REMOVE_ROUNDED, minimize, soft, tip="Згорнути"),
            btn(ft.Icons.CROP_SQUARE_ROUNDED, maximize, soft, tip="Розгорнути"),
            btn(ft.Icons.CLOSE_ROUNDED, lambda e: page.run_task(page.window.close), "#C42B1C",
                "#FFFFFF", tip="Закрити"),
        ], spacing=2, tight=True)]
    side_pad = 18 if is_mobile else 40

    def focusable(content, on_click, on_focus_change=None, radius=14, autofocus=False,
                  focus_bg=None):
        """Обгортка, що отримує фокус з пульта (кнопка без власного оформлення).
        Звичайні контейнери з on_click фокусу не отримують — пультом їх не вибрати.
        Рамку фокуса (і фон — focus_bg: (у фокусі, звичайний)) малює сам Flutter, миттєво:
        через Python на слабкому телевізорі вона не встигала за швидкими натисканнями."""
        bg = ({ft.ControlState.FOCUSED: focus_bg[0], ft.ControlState.DEFAULT: focus_bg[1]}
              if focus_bg else ft.Colors.TRANSPARENT)
        # колір тексту й іконок без явного кольору всередині — теж від стану (миттєво):
        # у стилі «Як Google TV» на білій плитці вони стають темними
        def click(e):
            quiet()  # дія сама оновлює, що змінила (інакше Flet перевіряв увесь застосунок)
            return on_click(e)

        return ft.Button(
            content=content, on_click=click, autofocus=autofocus,
            on_focus=(lambda e: (quiet(), on_focus_change(True))) if on_focus_change else None,
            on_blur=(lambda e: (quiet(), on_focus_change(False))) if on_focus_change else None,
            style=ft.ButtonStyle(
                padding=0, elevation=0, bgcolor=bg, side=TV_FOCUS_SIDE if TV else None,
                color={ft.ControlState.FOCUSED: TV_FOCUS_FG, ft.ControlState.DEFAULT: TEXT},
                icon_color={ft.ControlState.FOCUSED: TV_FOCUS_ICON,
                            ft.ControlState.DEFAULT: ACCENT},
                overlay_color=ft.Colors.TRANSPARENT, shadow_color=ft.Colors.TRANSPARENT,
                shape=ft.RoundedRectangleBorder(radius=radius), animation_duration=0))

    # Вибраний (пультом) елемент — стиль із налаштувань «Вигляд»:
    #  «Як Netflix»   — трохи світліший фон і світла рамка, оранжевий лише акцентом;
    #  «Як Google TV» — біла плитка з темним текстом (у світлій темі — навпаки).
    # окремого вибору стилю в налаштуваннях немає (лише тема) — завжди спокійний, «як Netflix»
    GOOGLE = False
    if GOOGLE:
        TV_FOCUS_BG = "#15171C" if ui.LIGHT else "#F3F4F6"
        TV_FOCUS_FG = "#FFFFFF" if ui.LIGHT else "#0D0E12"
        TV_FOCUS_ICON = TV_FOCUS_FG
        focus_ring = TV_FOCUS_BG
    else:
        TV_FOCUS_BG = "#D9DDE3" if ui.LIGHT else "#2C3039"
        TV_FOCUS_FG = TEXT
        TV_FOCUS_ICON = ACCENT
        focus_ring = "#15171C" if ui.LIGHT else "#FFFFFF"
    TV_FOCUS_SIDE = {ft.ControlState.FOCUSED: ft.BorderSide(4, focus_ring),
                     ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
    TV_FOCUS_TEXT = {ft.ControlState.FOCUSED: TV_FOCUS_FG, ft.ControlState.DEFAULT: TEXT}
    # наведення мишею на ПК — той самий стиль: «Як Google TV» — товста рамка, «Як Netflix» — тонка
    HOVER_W = 3 if GOOGLE else 1.5
    HOVER_COLOR = TEXT if ui.LIGHT else ("#FFFFFF" if GOOGLE else ft.Colors.with_opacity(0.7, "#FFFFFF"))
    # кнопки плеєра — поверх відео, тож завжди однаково: темне коло з білою рамкою
    PLAYER_FOCUS_BG = {ft.ControlState.FOCUSED: "#D92C3039",
                       ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT}
    PLAYER_FOCUS_SIDE = {ft.ControlState.FOCUSED: ft.BorderSide(4, "#FFFFFF"),
                         ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}

    def show_dialog(dlg):
        """page.show_dialog + на телевізорі: біла рамка фокуса на кнопках вікна й фокус на
        головній (autofocus у щойно відкритому вікні не спрацьовує — OK «нікуди» не натискався)."""
        if dlg.on_dismiss is None:
            # закриття вікна — подія; без quiet Flet після неї перевіряв і перемальовував усю
            # сторінку, і головна поверталась на початок
            dlg.on_dismiss = lambda e: quiet()
        if not TV:
            page.show_dialog(dlg)
            return
        buttons, fields = [], []

        def walk(c):
            if c is None:
                return
            if isinstance(c, (ft.FilledButton, ft.TextButton, ft.OutlinedButton, ft.IconButton)):
                buttons.append(c)
                return
            if isinstance(c, ft.TextField):
                fields.append(c)
                return
            for attr in ("content", "controls", "actions"):
                v = getattr(c, attr, None)
                if isinstance(v, list):
                    for x in v:
                        walk(x)
                elif isinstance(v, ft.Control):
                    walk(v)

        walk(dlg)
        for b in buttons:
            if b.style is None:
                b.style = ft.ButtonStyle()
            b.style.side = TV_FOCUS_SIDE
        # чи фокус уже у вікні (див. dialog_focus) — обробники ставимо до показу вікна
        got = {"in": False}
        for c in buttons + fields:
            prev = c.on_focus
            c.on_focus = (lambda e, prev=prev: (quiet(), got.update({"in": True}),
                                                prev(e) if prev else None))
        page.show_dialog(dlg)  # після стилів: інакше зміни вже не дійдуть до екрана
        # поле вводу (пошук) — першим: фокус на ньому відкриває клавіатуру телевізора
        target = next((f for f in fields if f.autofocus), None) or \
            next((b for b in buttons if getattr(b, "autofocus", False)), None) or \
            next((b for b in reversed(buttons) if isinstance(b, ft.FilledButton)), None) or \
            (buttons[0] if buttons else None)
        if target is not None:
            page.run_task(dialog_focus, dlg, got, target)

    async def dialog_focus(dlg, got, target):
        """TV: фокус у щойно відкритому вікні. Вікно оновлення відкривається під час запуску —
        тоді головна ще ставить фокус на свій перший рядок і забирала його з кнопки. Тож
        повторюємо, доки фокус не опиниться у вікні (або вікно не закриють)."""
        for delay in (0.35, 0.8, 1.5, 3.0):
            await asyncio.sleep(delay if delay == 0.35 else delay - 0.35)
            if got["in"] or not dlg.open:
                return
            try:
                await target.focus()
            except Exception:
                return

    def tv_tap(ctrl, action, highlight=None, radius=12, autofocus=False, focus_bg=None):
        """На ПК повертає контейнер як є; на телевізорі — обгортає його у фокусовану кнопку
        (біла рамка, поки фокус на ній; focus_bg — фон у фокусі / звичайний). highlight —
        додаткові зміни при фокусі (кольори іконок тощо), через Python — тож другорядні."""
        if not TV:
            return ctrl
        ctrl.on_click = None
        if focus_bg:
            ctrl.bgcolor = None  # фон малює кнопка (миттєво змінює у фокусі)

        def on_focus(f):
            highlight(f)
            ctrl.update()

        return focusable(ctrl, action, on_focus if highlight else None, radius=radius,
                         autofocus=autofocus, focus_bg=focus_bg)

    def round_button(icon, on_click, tooltip=None, size=38, over_image=False, on_dark=False):
        """Кругла кнопка «скляна» на ПК; на телевізорі — звичайна IconButton (з фокусом)."""
        if not TV and over_image:  # поверх фото — напівпрозоре коло, щоб стрілку було видно
            return ui.glass_button(icon, on_click, tooltip=tooltip, size=size, blur=not is_mobile)
        if not TV:  # на звичайній панелі — лише іконка, підсвітка тільки під мишею
            fg = "#FFFFFF" if on_dark else TEXT
            return ft.IconButton(icon, on_click=on_click, tooltip=tooltip, icon_color=fg,
                                 icon_size=20,
                                 style=ft.ButtonStyle(
                                     overlay_color=ft.Colors.with_opacity(0.1, fg),
                                     shape=ft.CircleBorder()))
        return ft.IconButton(icon, on_click=on_click, tooltip=tooltip,
                             style=ft.ButtonStyle(
                                 # без фокуса — просто стрілка (на TV кнопка на звичайній панелі,
                                 # не поверх фото); виділяється лише під курсором пульта
                                 bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                                          ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                                 icon_color=TV_FOCUS_TEXT,
                                 side=TV_FOCUS_SIDE, shape=ft.CircleBorder()))

    prefs = ft.SharedPreferences()

    # Телефон: положення пристрою за акселерометром — повернули горизонтально, і відео на весь
    # екран, назад вертикально — вихід (працює й тоді, коли сам інтерфейс у повному екрані
    # зафіксовано горизонтально). Датчик вмикається лише в плеєрі.
    orient = {"cb": None}
    def on_reading(e):
        ft.context.disable_auto_update()  # без перевірки всього екрана після кожного показу
        if orient["cb"]:
            orient["cb"](e.x, e.y)

    accel = ft.Accelerometer(
        enabled=False, interval=ft.Duration(milliseconds=400),
        on_reading=on_reading) if is_mobile else None
    # fit "auto": на телефоні в повному екрані — «Заповнити»; quality: "1080" / "720" / "480"
    settings = {"sub_scale": 1.0, "fit": "auto", "skipped_version": "",
                "auto_updates": True, "keep_history": True, "quality": "1080",
                "auto_fullscreen": True,  # телефон: поворот — повний екран
                "sync_code": "", "history_cleared": 0,  # синхронізація між пристроями
                "volume_boost": "6",  # підсилення гучності, дБ: "0" / "6" / "10"
                # вигляд розділів за замовчуванням: картки 16:9 чи список
                "layout": "list" if TV else "grid",
                # останній вибір субтитрів (мова або ""): окремо для оригінальних доріжок
                # і для українських (там субтитри — лише написи на екрані)
                "sub_orig": "", "sub_ua": "",
                # пропускати заставку UAFLIX (16 с на початку відео з «uaflix» у назві файлу)
                "skip_intro": True,
                "card_meta": True,  # рейтинг і жанри (TMDB) на картках тайтлів
                # декодування відео: "auto" — апаратне, "copy" — апаратне сумісне,
                # "off" — програмне (якщо на телевізорі картинка тремтить / відстає від звуку)
                "hwdec": "auto",
                "abroad": True,  # з-за кордону — плеєр через безкоштовний український проксі
                # Android / TV: "ask" — запитати при першому перегляді, "builtin" — свій плеєр,
                # "external" — інша програма (VLC, MX Player…)
                "player": "ask",
                "app_icon": "classic"}  # іконка застосунку (appicon.py)

    # Історія переглядів: [{url, title, orig, poster, age, season, label, si, ei, voice,
    # pos, dur, ts}] — найновіше першим. Позиція (pos) і тривалість (dur) — у мілісекундах.
    history = []
    HISTORY_MAX = 40
    voice_titles = {}  # адреса тайтлу → обрана озвучка
    voice_recent = []  # студії озвучення, які обирали, — новіші першими
    searches = []  # нещодавні пошуки (лише на цьому пристрої) — новіші першими
    SEARCHES_MAX = 10
    favorites = []  # обране: [{url, title, orig, poster, age}] — новіше першим

    def is_favorite(url):
        return any(f["url"] == url and not f.get("removed") for f in favorites)

    def active_favorites():
        return [f for f in favorites if not f.get("removed")]

    def toggle_favorite(item):
        # прибране лишається позначкою removed (з часом) — щоб синхронізація прибрала його
        # і на інших пристроях, а не повернула назад
        added = not is_favorite(item["url"])
        favorites[:] = [f for f in favorites if f["url"] != item["url"]]
        entry = {k: item.get(k, "") for k in ("url", "title", "orig", "poster", "age")}
        entry["ts"] = int(time.time())
        if not added:
            entry["removed"] = True
        favorites.insert(0, entry)
        page.run_task(prefs.set, "favorites", json.dumps(favorites, ensure_ascii=False))
        sync_changed(now=True)
        return added

    async def load_settings():
        try:
            for key in settings:
                value = await prefs.get(key)
                if value is not None:
                    settings[key] = value
            if settings["quality"] not in ("1080", "720", "480"):
                settings["quality"] = "1080"  # «Авто» з попередньої версії — найкраща якість
            raw = await prefs.get("history")
            if raw:
                history[:] = json.loads(raw)
            raw = await prefs.get("favorites")
            if raw:
                favorites[:] = json.loads(raw)
            raw = await prefs.get("searches")
            if raw:
                searches[:] = json.loads(raw)[:SEARCHES_MAX]
            raw = await prefs.get("voice_titles")  # обрана озвучка кожного серіалу
            if raw:
                voice_titles.update(json.loads(raw))
            raw = await prefs.get("voice_recent")  # студії, які обирали останніми
            if raw:
                voice_recent[:] = json.loads(raw)
            raw = await prefs.get("airplay_creds")  # ключі спарювання AirPlay
            if raw:
                airplay.CREDENTIALS.update(json.loads(raw))
            if settings["layout"] not in ("grid", "list"):
                settings["layout"] = "list" if TV else "grid"
            apply_layout_default()
            core.ABROAD["enabled"] = bool(settings.get("abroad", True))
        except Exception:
            pass
        render_continue()

    # ── оновлення через GitHub Releases
    UPDATE_CHECK_EVERY = 6 * 3600  # автоматично — не частіше ніж раз на 6 годин
    platform_name = {ft.PagePlatform.ANDROID: "android", ft.PagePlatform.ANDROID_TV: "android",
                     ft.PagePlatform.MACOS: "macos",
                     ft.PagePlatform.WINDOWS: "windows"}.get(page.platform)

    def dialog_width():
        return min(440, (page.width or 480) - 64)

    def notes_view(notes):
        return ft.Container(
            ft.Column([ft.Markdown(notes or "Без опису змін.", selectable=True,
                                   extension_set=ft.MarkdownExtensionSet.GITHUB_WEB)],
                      scroll=ft.ScrollMode.AUTO, tight=True),
            width=dialog_width())  # висота за вмістом; довгий опис прокручує саме вікно

    def show_update_dialog(rel):
        url = updater.asset_for(rel, platform_name, TV) or rel["url"]
        hint = ("Після завантаження відкрийте файл і натисніть «Встановити»."
                if platform_name == "android" else
                "Розпакуйте архів і замініть ним попередню версію застосунку.")

        def update(e):
            quiet()
            page.pop_dialog()
            if platform_name in ("macos", "windows") and url != rel["url"] and (
                    url.lower().endswith(".exe") or updater.desktop_target(platform_name)):
                # Mac / Windows: завантаження в застосунку й автоматичний перезапуск
                page.run_task(download_and_install, url, rel["version"])
                return
            if platform_name == "android":
                if update_dir() and url != rel["url"]:
                    # Android: завантажуємо самі (у фоні) й одразу відкриваємо вікно встановлення —
                    # завантаження через вбудований браузер на деяких телефонах зависало на 100%
                    page.run_task(download_and_install, url, rel["version"])
                    return
                # не мало б траплятися — але якщо так, хай буде видно чому
                why = ("немає теки для завантаження" if not update_dir() else
                       f"у релізі немає файлу для цього пристрою ({updater.android_abi()})")
                toast(f"Оновлення відкрито в браузері: {why}", ft.Icons.INFO_OUTLINE_ROUNDED)
                page.run_task(ft.UrlLauncher().launch_url, url,
                              mode=ft.LaunchMode.EXTERNAL_APPLICATION)
                return
            page.run_task(ft.UrlLauncher().launch_url, url, mode=ft.LaunchMode.EXTERNAL_APPLICATION)
            toast(hint, ft.Icons.DOWNLOAD_ROUNDED)

        def skip(e):
            quiet()
            save_setting("skipped_version", rel["version"])
            page.pop_dialog()

        # кнопки — власним блоком: стандартний ряд дії на вузькому екрані розсипався стовпчиком
        show_dialog(ft.AlertDialog(
            title=ft.Text(f"Доступна версія {rel['version']}", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18), scrollable=True,
            content=ft.Column([
                ft.Text(f"У вас версія {updater.VERSION}. Що нового:", color=MUTED, size=13),
                notes_view(rel["notes"]),
                ft.Container(height=4),
                ft.FilledButton(
                    "Оновити", icon=ft.Icons.DOWNLOAD_ROUNDED, on_click=update, autofocus=TV,
                    width=dialog_width(),
                    style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF", icon_color="#FFFFFF",
                                         padding=ft.Padding.symmetric(vertical=16),
                                         shape=ft.RoundedRectangleBorder(radius=12),
                                         text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700))),
                ft.Row([
                    ft.TextButton("Пізніше", on_click=lambda e: (quiet(), page.pop_dialog()),
                                  style=ft.ButtonStyle(color=TEXT)),
                    ft.TextButton("Пропустити версію", on_click=skip,
                                  style=ft.ButtonStyle(color=MUTED)),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, width=dialog_width()),
            ], tight=True, spacing=10),
        ))

    async def launch_installer():
        # обробляє MainActivity (див. tools/android_template.py): відкриває системне вікно
        # «Встановити» для cache/update.apk
        await ft.UrlLauncher().launch_url("uafixupdate://install",
                                          mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    def update_dir():
        """Тека для завантаженого оновлення: кеш застосунку (на Android це та сама тека, звідки
        MainActivity бере update.apk), запасні — тимчасова тека чи дані застосунку."""
        for key in ("FLET_APP_STORAGE_CACHE", "FLET_APP_STORAGE_TEMP", "FLET_APP_STORAGE_DATA"):
            if os.environ.get(key):
                return os.environ[key]
        return None

    async def download_and_install(url, version):
        desktop = platform_name in ("macos", "windows")
        if desktop:
            import tempfile
            dest = os.path.join(tempfile.gettempdir(),
                                "uafix-update.exe" if url.lower().endswith(".exe")
                                else "uafix-update.zip")
        else:
            dest = os.path.join(update_dir(), "update.apk")
        bar = ft.ProgressBar(value=None, color=ACCENT, bgcolor=SURFACE_2, bar_height=6,
                             border_radius=3, width=dialog_width())
        label = ft.Text("Підключаюсь…", color=MUTED, size=13)
        dlg = ft.AlertDialog(
            title=ft.Text(f"Завантаження версії {version}", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
            content=ft.Column([label, bar,
                               ft.Text("Можна сховати це вікно — завантаження триватиме у фоні.",
                                       color=MUTED, size=12)], tight=True, spacing=12),
            actions=[ft.TextButton("Сховати", on_click=lambda e: page.pop_dialog(),
                                   style=ft.ButtonStyle(color=TEXT))],
        )
        show_dialog(dlg)
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] < 0.3 and done != total:
                return
            last[0] = now
            mb = 1024 * 1024
            if total:
                bar.value = done / total
                label.value = f"{done / mb:.1f} з {total / mb:.1f} МБ · {done * 100 // total}%"
            else:
                label.value = f"{done / mb:.1f} МБ"
            try:
                dlg.update()
            except Exception:
                pass  # вікно сховали — качаємо далі у фоні

        try:
            await asyncio.to_thread(updater.download, url, dest, progress)
        except Exception as ex:
            try:
                page.pop_dialog()
            except Exception:
                pass
            msg = await explain(ex, "оновлення")
            toast(msg, error_icon(msg),
                  retry=lambda: page.run_task(download_and_install, url, version))
            return
        try:
            page.pop_dialog()
        except Exception:
            pass
        if desktop:
            await install_desktop_update(dest, version, url)
            return
        # вікно на випадок, якщо Android спершу попросить дозволити встановлення програм:
        # після дозволу достатньо повернутись і натиснути «Встановити»
        show_dialog(ft.AlertDialog(
            title=ft.Text(f"Версія {version} завантажена", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
            content=ft.Text("Натисніть «Встановити». Якщо Android попросить дозволити встановлення "
                            "з цього джерела — дозвольте, поверніться сюди й натисніть ще раз.",
                            color=TEXT, size=14, width=dialog_width()),
            actions=[
                ft.TextButton("Пізніше", on_click=lambda e: page.pop_dialog(),
                              style=ft.ButtonStyle(color=MUTED)),
                ft.FilledButton("Встановити", icon=ft.Icons.INSTALL_MOBILE_ROUNDED, autofocus=TV,
                                on_click=lambda e: page.run_task(launch_installer),
                                style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF",
                                                     icon_color="#FFFFFF")),
            ],
        ))
        await launch_installer()

    async def install_desktop_update(zip_path, version, url):
        """Mac / Windows: розпакувати й перезапустити застосунок уже новою версією."""
        show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(f"Встановлюю версію {version}", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
            content=ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=2, color=ACCENT),
                            ft.Text("Застосунок закриється й відкриється знову вже оновленим.",
                                    color=TEXT, size=14, expand=True)],
                           spacing=14, width=dialog_width())))
        try:
            await asyncio.to_thread(updater.install_desktop, zip_path, platform_name)
        except Exception as ex:
            page.pop_dialog()
            toast(f"Не вдалося встановити ({ex}) — відкриваю завантаження в браузері",
                  ft.Icons.ERROR_OUTLINE_ROUNDED)
            await ft.UrlLauncher().launch_url(url, mode=ft.LaunchMode.EXTERNAL_APPLICATION)
            return
        await asyncio.sleep(0.8)
        try:
            await page.window.destroy()
        finally:
            os._exit(0)  # скрипт заміни чекає саме на завершення процесу

    async def restart_app():
        """Перезапуск застосунку (зміна теми): Android — силами MainActivity,
        Mac / Windows — скриптом, що чекає завершення й відкриває застосунок знову."""
        page.pop_dialog()
        if platform_name == "android":
            await ft.UrlLauncher().launch_url("uafixupdate://restart",
                                              mode=ft.LaunchMode.EXTERNAL_APPLICATION)
            return
        if platform_name in ("macos", "windows") and updater.desktop_target(platform_name):
            try:
                updater.relaunch_desktop(platform_name)
            except Exception:
                pass
            else:
                await asyncio.sleep(0.3)
                try:
                    await page.window.destroy()
                finally:
                    os._exit(0)
        toast("Закрийте й знову відкрийте застосунок, щоб застосувати тему",
              ft.Icons.RESTART_ALT_ROUNDED)

    async def show_whats_new():
        """Перший запуск нової версії — коротко, що змінилось."""
        last = await prefs.get("last_version")
        if last == updater.VERSION:
            return
        await prefs.set("last_version", updater.VERSION)
        if not last:
            return  # перше встановлення — показувати нічого
        await show_release_notes()

    async def show_release_notes(manual=False):
        try:
            notes = await asyncio.to_thread(updater.release_notes, updater.VERSION)
        except Exception:
            if manual:
                toast("Не вдалося завантажити список змін", ft.Icons.ERROR_OUTLINE_ROUNDED)
            return
        show_dialog(ft.AlertDialog(
            title=ft.Text(f"Що нового у версії {updater.VERSION}", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18), scrollable=True,
            content=notes_view(notes),
            actions=[ft.FilledButton("Чудово", autofocus=TV, on_click=lambda e: page.pop_dialog(),
                                     style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF"))],
        ))

    async def check_updates(manual=False):
        if updater.VERSION == "dev":
            if manual:
                toast("Це версія для розробки — оновлення не перевіряються")
            return
        now = time.time()
        if not manual:
            if not settings["auto_updates"]:
                return
            try:
                if now - float(await prefs.get("update_checked_at") or 0) < UPDATE_CHECK_EVERY:
                    return
            except Exception:
                pass
        try:
            rel = await asyncio.to_thread(updater.latest_release)
        except Exception:
            if manual:
                toast("Не вдалося перевірити оновлення", ft.Icons.ERROR_OUTLINE_ROUNDED)
            return
        await prefs.set("update_checked_at", now)
        set_update_rel(rel if updater.is_newer(rel["version"], updater.VERSION) else None)
        if updater.is_newer(rel["version"]) and (
                manual or rel["version"] != settings.get("skipped_version")):
            show_update_dialog(rel)
        elif manual:
            toast(f"У вас найновіша версія {updater.VERSION}", ft.Icons.CHECK_CIRCLE_ROUNDED)

    async def open_settings_from_menu():
        await home.close_drawer()
        # спершу дати меню доїхати: інакше побудова екрана й анімація меню накладаються — ривок
        await asyncio.sleep(0.12)
        await open_settings()

    async def startup():
        is_tablet()  # телефон чи планшет — за розміром екрана на старті
        await load_settings()
        if first and settings["app_icon"] != "classic" and (is_mac or is_win):
            apply_icon(settings["app_icon"])  # Mac: оновлення ставить .app зі стандартною
        page.run_task(sync_loop)
        page.run_task(check_fav_new)  # нові серії серіалів з Обраного
        page.run_task(bell_loop, RUN["gen"])  # і після зміни теми (дзвіночок — новий)
        if not first:
            return  # перебудова після зміни теми — решта вже зроблена при запуску
        updater.mark_started()  # для скрипта оновлення: нова версія запустилась
        page.run_task(sync_now, True, True)  # при запуску — без спливаючого повідомлення
        await show_whats_new()
        await check_updates()

    def save_history():
        page.run_task(prefs.set, "history", json.dumps(history[:HISTORY_MAX], ensure_ascii=False))
        sync_changed()

    # ── синхронізація між пристроями (sync.py, сервер — sync_worker/)
    sync_state = {"dirty": False, "busy": False, "last": 0.0}

    def sync_doc():
        return {"history": history[:HISTORY_MAX], "favorites": favorites,
                "history_cleared": settings["history_cleared"]}

    def apply_sync(doc):
        """Злиті дані з усіх пристроїв → у цей пристрій (якщо щось змінилось)."""
        if json.dumps(doc, sort_keys=True) == json.dumps(sync_doc(), sort_keys=True):
            return
        before = (continue_signature(), favorites_signature())
        history[:] = doc.get("history") or []
        favorites[:] = doc.get("favorites") or []
        if doc.get("history_cleared", 0) != settings["history_cleared"]:
            save_setting("history_cleared", doc.get("history_cleared", 0))
        page.run_task(prefs.set, "history", json.dumps(history, ensure_ascii=False))
        page.run_task(prefs.set, "favorites", json.dumps(favorites, ensure_ascii=False))
        # перемальовувати лише те, що справді змінилось (сервер дописує порожні поля —
        # через це полички перебудовувались, і екран «стрибав»)
        if continue_signature() != before[0]:
            render_continue(focus=False)
            if len(page.views) == 1 and st["mode"] == ("cat", HISTORY_PATH):
                page.run_task(reload)
        if favorites_signature() != before[1] and len(page.views) == 1 and \
                st["mode"] == ("cat", FAV_PATH):
            page.run_task(reload)

    def continue_signature():
        return [(h["url"], h.get("pos"), h.get("label")) for h in history
                if not h.get("removed") and not watched(h)][:15]

    def favorites_signature():
        return [f["url"] for f in favorites if not f.get("removed")]

    async def sync_now(manual=False, quiet=False):
        code = settings["sync_code"]
        if not code or sync_state["busy"]:
            return
        sync_state["busy"] = True
        try:
            if sync_state["dirty"] or manual:
                sync_state["dirty"] = False
                merged = await asyncio.to_thread(sync.push, code, sync_doc())
            else:  # нічого свого нового — лише забрати зміни з інших пристроїв
                remote = await asyncio.to_thread(sync.pull, code)
                merged = sync.merge(sync_doc(), remote)
            apply_sync(merged)
            sync_state["last"] = time.time()
            label = sync_state.get("label")
            if label is not None:
                label.value = sync_last_text()
                try:
                    label.update()
                except Exception:
                    pass  # налаштування вже закрили
            if manual and not quiet:
                toast("Синхронізовано", ft.Icons.CLOUD_DONE_ROUNDED)
        except Exception as ex:
            sync_state["dirty"] = True  # спробуємо наступного разу
            if manual:
                msg = await explain(ex, "синхронізацію")
                toast(msg, error_icon(msg))
        finally:
            sync_state["busy"] = False

    def sync_last_text():
        last = sync_state["last"]
        return "Остання синхронізація: " + (
            time.strftime("%H:%M", time.localtime(last)) if last else "ще ні")

    def sync_changed(now=False):
        if not settings["sync_code"]:
            return
        sync_state["dirty"] = True
        if now:
            page.run_task(sync_now)

    async def sync_loop():
        # раз на хвилину: свої зміни (якщо є) — на сервер, чужі — сюди
        while True:
            await asyncio.sleep(60)
            if RUN["gen"] != gen:
                return  # інтерфейс перебудовано — цикл веде вже новий
            await sync_now()

    def history_entry(url):
        return next((h for h in history if h["url"] == url and not h.get("removed")), None)

    def remove_from_history(url):
        """Прибрати один фільм з «Продовжити перегляд». Лишається позначка removed (з часом),
        щоб синхронізація прибрала його й на інших пристроях, а не повернула назад."""
        old = next((h for h in history if h["url"] == url), None)
        if not old:
            return
        history[:] = [{"url": url, "title": old.get("title", ""), "ts": int(time.time()),
                       "removed": True}] + [h for h in history if h["url"] != url]
        save_history()
        sync_changed(now=True)
        if TV:
            page.run_task(remove_tv_row, url)
            return
        # телефон / ПК: картка плавно стискається й зникає, але її місце в рядку лишається —
        # якщо її вийняти, наступні картки зсуваються, перебудовуються й блимають постерами
        # (зовсім прибереться при наступному оновленні поличк)
        card = next((c for c in continue_row.controls if c.data == url), None)
        if card is None:
            return
        card.width, card.opacity, card.data = 0, 0, None
        try:
            card.update()
        except Exception:
            pass
        if not any(c.data for c in continue_row.controls):
            async def hide_section():
                await asyncio.sleep(0.35)
                continue_section.visible = False
                try:
                    continue_section.update()
                except Exception:
                    pass
            page.run_task(hide_section)

    async def remove_tv_row(url):
        """TV: спершу фокус — на попередній фільм (поки рядок ще на місці, інакше фокус
        падав на «Обране»), потім рядок плавно стискається й зникає."""
        box = continue_list
        rows = [c for c in box.controls if c.data]
        idx = next((i for i, c in enumerate(rows) if c.data == url), None)
        if idx is None:
            return
        row = rows[idx]
        rest = rows[:idx] + rows[idx + 1:]
        if rest:  # попередній; якщо прибрали перший — наступний (він стане першим)
            near = rest[max(0, idx - 1)]
            try:
                await near.content.controls[0].focus()
            except Exception:
                pass
        row.data = None
        row.height, row.opacity = 0, 0
        try:
            row.update()
        except Exception:
            pass
        # рядок лишається в списку невидимим (висота 0): прибирати його зі списку — це
        # перебудова, від якої наприкінці анімації був помітний фріз
        if not rest:
            await asyncio.sleep(0.35)
            continue_section.visible = False
            try:
                continue_section.update()
            except Exception:
                pass

    def fmt_time(ms):
        sec = int(ms // 1000)
        h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
        return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"

    def resume_caption(h):
        parts = []
        if h.get("season"):
            parts.append(h["season"].replace("Сезон ", "С"))
        if h.get("label") and h["label"] != "Фільм":
            parts.append(h["label"].split(" — ")[0])
        parts.append(fmt_time(h["pos"]))
        return " · ".join(parts)

    def watched(h):
        return h.get("dur") and h["pos"] >= 0.95 * h["dur"]

    def record_progress(ctx, ep, voice, pos_ms, dur_ms):
        """Зберігає, на якій серії й хвилині зупинились (викликається з плеєра)."""
        if not settings["keep_history"]:
            return
        si = ei = None
        for i, season in enumerate(ctx["seasons"]):
            for j, e in enumerate(season["episodes"]):
                if e is ep:
                    si, ei = i, j
        if si is None:
            return
        item, info = ctx["item"], ctx["info"] or {}
        multi = len(ctx["seasons"]) > 1
        entry = {
            "url": item["url"], "title": info.get("title") or item["title"],
            "orig": item.get("orig") or info.get("orig") or "",
            "poster": item.get("poster") or info.get("poster") or "", "age": item.get("age") or "",
            "season": ctx["seasons"][si]["name"] if multi else "", "label": ep["label"],
            "si": si, "ei": ei, "voice": voice, "pos": int(pos_ms), "dur": int(dur_ms),
            "ts": int(time.time()),
        }
        # скільки переглянуто кожної серії («сезон:серія» → частка) — для позначок у списку
        old = next((h for h in history if h["url"] == item["url"] and not h.get("removed")), None)
        seen = dict((old or {}).get("seen") or {})
        if dur_ms:
            seen[f"{si}:{ei}"] = round(min(1.0, pos_ms / dur_ms), 3)
        if len(seen) > 150:  # сервер синхронізації зберігає не більше 150
            seen = dict(list(seen.items())[-150:])
        entry["seen"] = seen
        history[:] = [entry] + [h for h in history if h["url"] != item["url"]]
        save_history()

    def mark_watched(ctx, ep):
        """Позначити серію переглянутою (✓ у списку серій), не чіпаючи «де зупинились»."""
        if not settings["keep_history"]:
            return
        key = next((f"{i}:{j}" for i, season in enumerate(ctx["seasons"])
                    for j, e in enumerate(season["episodes"]) if e is ep), None)
        h = history_entry(ctx["item"]["url"])
        if key is None or h is None:
            return
        seen = dict(h.get("seen") or {})
        if seen.get(key, 0) >= 0.9:
            return
        seen[key] = 1.0
        h["seen"] = seen
        save_history()

    def find_episode(ctx, h):
        """(сезон, серія) запису історії в поточному списку серій тайтлу."""
        for i, season in enumerate(ctx["seasons"]):
            if h.get("season") and season["name"] != h["season"]:
                continue
            for j, e in enumerate(season["episodes"]):
                if e["label"] == h.get("label"):
                    return i, j
        si, ei = h.get("si", 0), h.get("ei", 0)
        if si < len(ctx["seasons"]) and ei < len(ctx["seasons"][si]["episodes"]):
            return si, ei
        return None

    def save_setting(key, value):
        settings[key] = value
        page.run_task(prefs.set, key, value)

    # на телевізорі каталог — списком: легше, ніж сітка великих карток
    st = {"mode": ("home", None), "page": 1, "items": [], "has_more": True,
          "loading": False, "gen": 0, "layout": "list" if TV else "grid"}

    # Спливаючі повідомлення — власна плашка в кожному екрані. Стандартний SnackBar Flet при
    # появі й зникненні перебудовує весь екран (на телефоні головна помітно блимала).
    toast_slots = {}  # id екрана → місце для плашки
    toast_seq = [0]
    TOAST_SECONDS = 3

    def with_toast(content):
        """Обгортка вмісту екрана: поверх — місце для плашки повідомлення."""
        slot = ft.Container(left=0, right=0, bottom=24, visible=False,
                            alignment=ft.Alignment.BOTTOM_CENTER)
        holder = ft.Stack([content, slot], expand=True, fit=ft.StackFit.EXPAND)
        holder.data = slot
        return holder

    def view_with_toast(view):
        for c in view.controls:
            if isinstance(c, ft.Stack) and isinstance(c.data, ft.Container):
                return c.data
            if isinstance(c, ft.SafeArea) and isinstance(c.content, ft.Stack) and \
                    isinstance(c.content.data, ft.Container):
                return c.content.data
        return None

    def toast(msg, icon=ft.Icons.INFO_OUTLINE_ROUNDED, retry=None):
        """Спливаюче повідомлення; retry — дія для кнопки «Повторити»."""
        slot = view_with_toast(page.views[-1]) if page.views else None
        if slot is None:
            return snack(msg, icon, retry)
        toast_seq[0] += 1
        seq = toast_seq[0]

        def do_retry(e):
            hide()
            retry()

        card = ft.Container(
            ft.Row([ft.Icon(icon, color=ACCENT, size=18),
                    ft.Text(msg, color=TEXT, size=14, expand=True),
                    *([ft.TextButton("Повторити", on_click=do_retry,
                                     style=ft.ButtonStyle(color=ACCENT))] if retry else [])],
                   spacing=10),
            width=None if is_mobile else 520,
            bgcolor=ft.Colors.with_opacity(0.86, SURFACE_3), border_radius=12,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.08, "#FFFFFF")),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            margin=ft.Margin.symmetric(horizontal=16),
            shadow=ft.BoxShadow(blur_radius=16, offset=ft.Offset(0, 6),
                                color=ft.Colors.with_opacity(0.3, "#000000")),
            opacity=0, animate_opacity=ui.FAST, offset=ft.Offset(0, 0.3), animate_offset=ui.FAST,
        )

        def hide():
            if toast_seq[0] != seq:
                return
            card.opacity, card.offset = 0, ft.Offset(0, 0.3)
            try:
                card.update()
            except Exception:
                pass

        async def run():
            await asyncio.sleep(0.02)
            card.opacity, card.offset = 1, ft.Offset(0, 0)
            try:
                card.update()
            except Exception:
                return
            await asyncio.sleep(TOAST_SECONDS + (2 if retry else 0))
            hide()
            await asyncio.sleep(0.3)
            if toast_seq[0] == seq:
                slot.content, slot.visible = None, False
                try:
                    slot.update()
                except Exception:
                    pass

        slot.content, slot.visible = card, True
        try:
            slot.update()
        except Exception:
            return snack(msg, icon, retry)
        page.run_task(run)

    def snack(msg, icon=ft.Icons.INFO_OUTLINE_ROUNDED, retry=None):
        page.show_dialog(ft.SnackBar(
            ft.Row([ft.Icon(icon, color=ACCENT, size=18),
                    ft.Text(msg, color=TEXT, expand=True)], spacing=10),
            bgcolor=SURFACE_3, behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=12), width=None if is_mobile else 520,
            action=ft.SnackBarAction("Повторити", text_color=ACCENT) if retry else None,
            on_action=(lambda e: retry()) if retry else None,
            duration=(TOAST_SECONDS + (2 if retry else 0)) * 1000,
            persist=False,  # інакше плашка з кнопкою «Повторити» не зникає сама
        ))

    async def explain(err, what="дані"):
        """Зрозумілий текст помилки (перевірка інтернету — у фоновому потоці)."""
        return await asyncio.to_thread(core.friendly_error, err, what)

    def error_icon(msg):
        return ft.Icons.WIFI_OFF_ROUNDED if "інтернет" in msg else ft.Icons.ERROR_OUTLINE_ROUNDED

    def safe(control):
        """На телефоні — відступи від системних рядків (годинник, батарея, жести).
        Заразом — місце для спливаючих повідомлень (див. toast)."""
        control = with_toast(control)
        return ft.SafeArea(control, expand=True) if is_mobile else control

    def drag_area(content):
        return content if is_mobile else ft.WindowDragArea(content, maximizable=True)

    def empty_state(icon, title, text, retry=None):
        retry_btn = ft.FilledButton(
            "Спробувати ще", icon=ft.Icons.REFRESH_ROUNDED, on_click=lambda e: retry(),
            style=ft.ButtonStyle(
                # у фокусі — біла рамка навколо (як на інших кнопках TV), кнопка лишається
                # оранжевою
                bgcolor=ACCENT, color="#FFFFFF", icon_color="#FFFFFF",
                side={ft.ControlState.FOCUSED: ft.BorderSide(4, "#FFFFFF"),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
                if TV else None,
                padding=ft.Padding.symmetric(vertical=16) if TV else None,
                shape=ft.RoundedRectangleBorder(radius=12)),
            # на TV — широка: пульт «вниз» з верхньої панелі потрапляє лише на те, що під
            # кнопкою (перемикач список / картки), а не на вузьку кнопку посередині
            width=440 if TV else None) if retry else None
        actions = [ft.Container(height=4), retry_btn] if retry else []
        if TV and retry_btn is not None:
            # пультом до кнопки по центру екрана з верхньої панелі не дістатись — фокус одразу
            page.run_task(focus_later, retry_btn)
            st["retry_btn"] = retry_btn  # «вниз» з «Пошуку» теж веде сюди (див. on_key)
        return ft.Container(
            ft.Column([
                ft.Container(ft.Icon(icon, size=40, color=ACCENT), width=84, height=84,
                             border_radius=42, bgcolor=ft.Colors.with_opacity(0.1, ACCENT),
                             alignment=ft.Alignment.CENTER),
                ft.Text(title, size=18, weight=ft.FontWeight.W_700, color=TEXT),
                ft.Text(text, size=13, color=MUTED, text_align=ft.TextAlign.CENTER),
                *actions,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10, tight=True),
            alignment=ft.Alignment.CENTER, expand=True, padding=30,
        )

    # ═════════════════════ бічне меню ═════════════════════
    drawer_controls = [
        ft.Container(
            # без іконки: її тепер можна вибрати, а тут завжди була б класична
            ft.Column([ft.Text("UAFix", size=18, weight=ft.FontWeight.W_800, color=TEXT),
                       ft.Text("Фільми та серіали українською", size=11, color=MUTED)],
                      spacing=0),
            padding=ft.Padding.only(left=24, right=16, top=40 if is_mac else 20, bottom=12),
        ),
    ]
    drawer_controls.append(ft.NavigationDrawerDestination(
        label="Головна", icon=ft.Icon(ft.Icons.HOME_ROUNDED, color=MUTED),
        selected_icon=ft.Icon(ft.Icons.HOME_ROUNDED, color=ACCENT)))
    for section, items in NAV:
        drawer_controls.append(ft.Container(
            ft.Text(section.upper(), size=11, color=MUTED, weight=ft.FontWeight.W_700),
            padding=ft.Padding.only(left=28, top=14, bottom=6)))
        for name, _, icon in items:
            drawer_controls.append(ft.NavigationDrawerDestination(
                label=name, icon=ft.Icon(icon, color=MUTED), selected_icon=ft.Icon(icon, color=ACCENT)))

    drawer_controls += [
        ft.Container(ft.Divider(color=BORDER), padding=ft.Padding.only(left=20, right=20, top=10)),
        # такий самий пункт, як решта (раніше — окремий рядок, що виділявся інакше)
        ft.NavigationDrawerDestination(
            label="Налаштування", icon=ft.Icon(ft.Icons.SETTINGS_ROUNDED, color=MUTED),
            selected_icon=ft.Icon(ft.Icons.SETTINGS_ROUNDED, color=ACCENT)),
        ft.Container(ft.Text(f"Версія {updater.VERSION}", size=12, color=MUTED),
                     padding=ft.Padding.only(left=28, right=20, top=8,
                                             bottom=56 if TV else 24)),
    ]

    # Телевізор: пункти меню — власні кнопки (а не NavigationDrawerDestination), зверху й
    # знизу — невидимі «упори». Інакше «вгору» з «Головної» перескакувало на останній пункт
    # (Flutter зациклює фокус), а пункти меню програмно не сфокусувати.
    tv_nav = []  # [(кнопка, індекс)]

    def tv_nav_item(label, icon, index):
        sel = st.get("nav_index", 0) == index
        row = ft.Container(
            ft.Row([ft.Icon(icon, size=22, color=ACCENT if sel else MUTED),
                    ft.Text(label, size=16, weight=ft.FontWeight.W_600)], spacing=16),
            padding=ft.Padding.symmetric(horizontal=18, vertical=14))
        def track(f, index=index):
            if f:
                st["nav_focus"] = index

        btn = tv_tap(row, lambda e: page.run_task(on_nav, index), highlight=track, radius=12,
                     focus_bg=(TV_FOCUS_BG, ft.Colors.with_opacity(0.14, ACCENT) if sel
                               else ft.Colors.TRANSPARENT))
        tv_nav.append((btn, index))
        return ft.Container(btn, padding=ft.Padding.symmetric(horizontal=12))

    def nav_edge_back(first):
        """«Вгору» на першому пункті / «вниз» на останньому: Flutter перекидає фокус на
        протилежний кінець — повертаємо назад (межі меню: зверху «Головна», знизу
        «Налаштування»)."""
        async def back():
            await asyncio.sleep(0.12)
            if tv_nav:
                await (tv_nav[0] if first else tv_nav[-1])[0].focus()
        page.run_task(back)

    def tv_drawer_controls():
        tv_nav.clear()
        out = [drawer_controls[0], tv_nav_item("Головна", ft.Icons.HOME_ROUNDED, 0)]
        i = 1
        for section, items in NAV:
            out.append(ft.Container(
                ft.Text(section.upper(), size=11, color=MUTED, weight=ft.FontWeight.W_700),
                padding=ft.Padding.only(left=28, top=14, bottom=6)))
            for name, _, icon in items:
                out.append(tv_nav_item(name, icon, i))
                i += 1
        out += [ft.Container(ft.Divider(color=BORDER),
                             padding=ft.Padding.only(left=20, right=20, top=10)),
                tv_nav_item("Налаштування", ft.Icons.SETTINGS_ROUNDED, 1 + len(DESTINATIONS)),
                drawer_controls[-1]]
        return out

    def mark_nav():
        """TV: перемалювати виділення поточного розділу в меню."""
        if not TV:
            return
        drawer.controls = tv_drawer_controls()
        try:
            drawer.update()
        except Exception:
            pass

    async def open_drawer():
        release_search()
        st["drawer_open"] = True
        if TV:
            mark_nav()
        await home.show_drawer()
        if TV:  # фокус — на поточний розділ
            cur = next((b for b, i in tv_nav if i == st.get("nav_index", 0)),
                       tv_nav[0][0] if tv_nav else None)
            if cur is not None:
                page.run_task(focus_later, cur)

    def drawer_closed(e):
        quiet()
        st["drawer_open"] = False

    drawer = ft.NavigationDrawer(
        controls=drawer_controls, selected_index=0,  # на TV вміст замінить tv_drawer_controls bgcolor=SURFACE, on_dismiss=drawer_closed,
        indicator_color=ft.Colors.with_opacity(0.14, ACCENT),
        indicator_shape=ft.RoundedRectangleBorder(radius=12),
        on_change=lambda e: page.run_task(on_nav, e.control.selected_index),
    )

    SETTINGS_NAV = 1 + len(DESTINATIONS)  # «Налаштування» — останній пункт меню

    async def on_nav(index):
        if index == SETTINGS_NAV:
            drawer.selected_index = st.get("nav_index")  # виділення лишаємо на розділі
            drawer.update()
            await open_settings_from_menu()
            return
        st["nav_index"] = index
        await home.close_drawer()
        if index == 0:
            go_home()
            return
        await open_category(*DESTINATIONS[index - 1][:2])

    async def open_category(name, path):
        release_search()
        search.value = ""
        clear_btn.visible = False
        set_heading(name)
        st["mode"] = ("cat", path)
        # для жанру в меню підсвічується його розділ («Усі фільми» / «Усі серіали»)
        nav_path = genre_root(path) or path
        idx = next((i for i, d in enumerate(DESTINATIONS) if d[1] == nav_path), None)
        drawer.selected_index = st["nav_index"] = idx + 1 if idx is not None else None
        mark_nav()
        await reload()

    # ── рядок жанрів над каталогом
    genre_row = ft.Row(spacing=8, scroll=None if TV else ft.ScrollMode.HIDDEN, wrap=TV,
                       run_spacing=8)
    genre_bar = ft.Container(genre_row, visible=False,
                             padding=ft.Padding.only(left=14 if is_mobile else 24, right=14,
                                                     top=10, bottom=2))

    # ── фільтри розділу (сортування, жанр, рік, країна, студія — із самого сайту)
    filter_row = ft.Row(spacing=8, scroll=None if TV else ft.ScrollMode.HIDDEN, wrap=TV,
                        run_spacing=8)
    filter_bar = ft.Container(filter_row, visible=False,
                              padding=ft.Padding.only(left=14 if is_mobile else 24, right=14,
                                                      top=8, bottom=2))

    def filter_button(label, active, on_click, icon=ft.Icons.ARROW_DROP_DOWN_ROUNDED):
        return ft.OutlinedButton(
            label, icon=icon, on_click=on_click,
            style=ft.ButtonStyle(
                color={ft.ControlState.FOCUSED: TV_FOCUS_FG if TV else ACCENT,
                       ft.ControlState.DEFAULT: ACCENT if active else TEXT},
                icon_color={ft.ControlState.DEFAULT: ACCENT if active else MUTED},
                bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG if TV else SURFACE_2,
                         ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.1, ACCENT)
                         if active else SURFACE_2},
                side={ft.ControlState.FOCUSED: ft.BorderSide(2 if TV else 1, "#FFFFFF" if TV
                                                             else ACCENT),
                      ft.ControlState.DEFAULT: ft.BorderSide(1, ft.Colors.with_opacity(0.4, ACCENT)
                                                             if active else BORDER)},
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8 if TV else 4)))

    COLLECTIONS = ("Про кохання", "Для підлітків", "Про підлітків", "Про школу")

    def collection_items():
        """Тематичні добірки поточного розділу (їх немає серед жанрів фільтра):
        [(назва, шлях, чи відкрита зараз)], плюс «Без добірки», якщо добірку вже відкрито."""
        path = st["mode"][1] if st["mode"][0] == "cat" else None
        root = genre_root(path) if path else None
        if not root:
            return []
        items = [(n, g, g == path) for n, g in GENRES[root][1] if n in COLLECTIONS]
        if any(on for _, _, on in items):
            items.insert(0, ("Без добірки", root, False))
        return items

    def open_collection(path):
        quiet()
        if TV:
            page.pop_dialog()
        page.run_task(open_category, chip_heading(path), path)

    def filter_menu(d, text, cur):
        active = cur is not None

        def pick(v):
            def handler(e):
                quiet()
                set_filter(d["field"], v)
            return handler

        opts = [("", "За замовчуванням" if d["field"] == "default" else "Будь-який")] + d["options"]
        return ft.PopupMenuButton(
            content=ft.Container(
                ft.Row([ft.Text(text, size=13, color=ACCENT if active else TEXT,
                                weight=ft.FontWeight.W_600),
                        ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, size=18,
                                color=ACCENT if active else MUTED)], spacing=2, tight=True),
                padding=ft.Padding.only(left=12, right=6, top=6, bottom=6), border_radius=10,
                bgcolor=ft.Colors.with_opacity(0.1, ACCENT) if active else SURFACE_2,
                border=ft.Border.all(1, ft.Colors.with_opacity(0.4, ACCENT) if active else BORDER)),
            items=[ft.PopupMenuItem(
                content=ft.Row([ft.Icon(ft.Icons.CHECK_ROUNDED, size=16,
                                        color=ACCENT if (v or None) == cur else ft.Colors.TRANSPARENT),
                                ft.Text(t, color=TEXT)], spacing=10),
                on_click=pick(v)) for v, t in opts] + (
                [ft.PopupMenuItem()] + [ft.PopupMenuItem(
                    content=ft.Row([ft.Icon(ft.Icons.CHECK_ROUNDED if on else
                                            ft.Icons.COLLECTIONS_BOOKMARK_OUTLINED, size=16,
                                            color=ACCENT if on else MUTED),
                                    ft.Text(f"Добірка: {n}" if n != "Без добірки" else n,
                                            color=TEXT)], spacing=10),
                    on_click=lambda e, g=g: open_collection(g))
                    for n, g, on in collection_items()]
                if d["field"] == "janr" and collection_items() else []),
            tooltip="", bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=12),
            menu_position=ft.PopupMenuPosition.UNDER)

    def render_filters():
        defs, cur = st.get("filter_defs") or [], st.get("filters") or {}
        btns = []
        for d in defs:
            val = cur.get(d["field"])
            label = next((t for v, t in d["options"] if v == val), None)
            text = f"{d['title']}: {label}" if label else d["title"]
            if TV:  # з пульта зручніше вікно по центру
                # quiet: без автооновлення всієї сторінки після кліку — від нього блимав екран
                btn = filter_button(text, bool(label), lambda e, d=d: (quiet(), open_filter(d)))
                st.setdefault("filter_btns", {})[d["field"]] = btn
                btns.append(btn)
            else:  # випадний список під кнопкою — без затемнення екрана (воно виглядало як
                #     блимання)
                btns.append(filter_menu(d, text, val))
        if cur:
            btns.append(filter_button("Скинути", False,
                                      lambda e: (quiet(), set_filter(None, None)),
                                      icon=ft.Icons.CLOSE_ROUNDED))
        filter_row.controls = btns
        filter_bar.visible = bool(defs)

    def set_filter(field, value):
        if field is None:
            st["filters"] = {}
        elif value:
            st["filters"][field] = value
        else:
            st["filters"].pop(field, None)
        render_filters()
        try:
            filter_bar.update()
        except Exception:
            pass
        if TV:  # кнопки створено заново — фокус назад на щойно змінений фільтр
            btn = (st.get("filter_btns") or {}).get(field) if field else None
            target = btn or (filter_row.controls[0] if filter_row.controls else None)
            if target is not None:
                page.run_task(focus_later, target)
        page.run_task(reload, True)

    def open_filter(d):
        cur = (st.get("filters") or {}).get(d["field"])

        def pick(v):
            def handler(e):
                quiet()
                page.pop_dialog()
                set_filter(d["field"], v)
            return handler

        opts = [("", "За замовчуванням" if d["field"] == "default" else "Будь-який")] + d["options"]
        buttons = [ft.FilledButton(t, icon=ft.Icons.CHECK_ROUNDED if (v or None) == cur else None,
                                   autofocus=(v or None) == cur, on_click=pick(v), width=300,
                                   style=ft.ButtonStyle(
                                       bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                                                ft.ControlState.DEFAULT: SURFACE_2},
                                       side=TV_FOCUS_SIDE, color=TV_FOCUS_TEXT,
                                       icon_color=TV_FOCUS_TEXT,
                                       shape=ft.RoundedRectangleBorder(radius=10)))
                   for v, t in opts]
        if d["field"] == "janr":  # тематичні добірки — у тому ж вікні
            buttons += [ft.FilledButton(f"Добірка: {n}" if n != "Без добірки" else n,
                                        icon=ft.Icons.CHECK_ROUNDED if on else None, width=300,
                                        on_click=lambda e, g=g: open_collection(g),
                                        style=ft.ButtonStyle(
                                            bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                                                     ft.ControlState.DEFAULT: SURFACE_3},
                                            side=TV_FOCUS_SIDE, color=TV_FOCUS_TEXT,
                                            icon_color=TV_FOCUS_TEXT,
                                            shape=ft.RoundedRectangleBorder(radius=10)))
                        for n, g, on in collection_items()]
        many = len(buttons) > 7
        show_dialog(ft.AlertDialog(
            title=ft.Text(d["title"], size=20, weight=ft.FontWeight.W_800, color=TEXT),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
            content=ft.Column(buttons, spacing=8, tight=True,
                              scroll=ft.ScrollMode.AUTO if many else None,
                              height=min(len(buttons) * 48, (page.height or 700) - 220)
                              if many else None)))

    async def load_filters(path):
        try:
            defs = await asyncio.to_thread(core.section_filters, path)
        except Exception:
            defs = []
        if st.get("filters_for") != path:
            return  # уже інший розділ
        st["filter_defs"] = defs
        render_filters()
        try:
            filter_bar.update()
        except Exception:
            pass

    def genre_chip(label, path, selected):
        return ft.FilledButton(
            label, on_click=lambda e: page.run_task(open_category, chip_heading(path), path),
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.FOCUSED: ACCENT if selected else SURFACE_3,
                         ft.ControlState.HOVERED: ACCENT if selected else SURFACE_3,
                         ft.ControlState.DEFAULT: ACCENT if selected else SURFACE_2},
                color="#FFFFFF" if selected else TEXT,
                side={ft.ControlState.FOCUSED: ft.BorderSide(3, "#FFFFFF"),
                      ft.ControlState.DEFAULT: ft.BorderSide(1, ACCENT if selected else BORDER)},
                shape=ft.RoundedRectangleBorder(radius=20),
                padding=ft.Padding.symmetric(horizontal=16, vertical=10 if TV else 6),
                text_style=ft.TextStyle(size=16 if TV else 13, weight=ft.FontWeight.W_600),
                animation_duration=0 if TV else None))

    def chip_heading(path):
        root = genre_root(path)
        title, genres = GENRES[root]
        if path == root:
            return f"Усі {title.lower()}"
        return f"{title} · {next(n for n, g in genres if g == path)}"

    def refresh_genre_bar():
        path = st["mode"][1] if st["mode"][0] == "cat" else None
        root = genre_root(path) if path else None
        # рядок жанрів прибрано: жанр — у фільтрі «Жанр», а тематичні добірки (про кохання,
        # підлітків, школу) — у тому ж списку окремим блоком (див. collection_items)
        genre_bar.visible = False
        if root:
            genre_row.controls = [genre_chip("Усі", root, path == root)] + [
                genre_chip(n, g, path == g) for n, g in GENRES[root][1]]

    # ═════════════════════ верхня панель ═════════════════════
    subheading = ft.Text("", size=12, color=MUTED, visible=False)

    def set_subheading(text):
        subheading.value = text
        subheading.visible = bool(text)

    # старий заголовок зникає миттєво (reverse_duration=0), щоб тексти не накладались
    heading_switcher = ft.AnimatedSwitcher(
        ft.Text(""), duration=180, reverse_duration=0, transition=ft.AnimatedSwitcherTransition.FADE,
        switch_in_curve=ui.EASE, switch_out_curve=ui.EASE)

    def set_heading(text):
        heading_switcher.content = ft.Text(text, size=22, weight=ft.FontWeight.W_800, color=TEXT,
                                           key=text, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    set_heading("Головна")

    def clear_search(e=None):
        search.value = ""
        clear_btn.visible = False
        go_home()

    clear_btn = ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=18, icon_color=MUTED,
                              tooltip="Очистити пошук", visible=False, on_click=clear_search,
                              style=ft.ButtonStyle(padding=0), width=32, height=32)

    def on_search_change(e):
        visible = bool(search.value)
        if visible != clear_btn.visible:
            clear_btn.visible = visible
            clear_btn.update()
        if st.get("search_has_focus"):
            fill_recent()  # нещодавні — лише ті, що містять набране

    # ── нещодавні пошуки: смуга під верхньою панеллю, поки поле пошуку у фокусі
    recent_row = ft.Row([], spacing=8, scroll=ft.ScrollMode.HIDDEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)
    recent_bar = ft.Container(
        recent_row, visible=False, bgcolor=BG,
        padding=ft.Padding.only(left=14 if is_mobile else 24, right=14, top=10, bottom=10),
        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)))

    def recent_chips(on_pick, typed=""):
        t = typed.strip().lower()
        items = [q for q in searches if t in q.lower()] if t else list(searches)

        def forget(q):
            def handler(e):
                searches[:] = [x for x in searches if x != q]
                page.run_task(prefs.set, "searches", json.dumps(searches, ensure_ascii=False))
                fill_recent()
            return handler

        def clear_all(e):
            searches.clear()
            page.run_task(prefs.set, "searches", "[]")
            show_recent(False)

        chips = [ft.Chip(ft.Text(q, size=13, color=TEXT), bgcolor=SURFACE_2,
                         leading=ft.Icon(ft.Icons.HISTORY_ROUNDED, size=16, color=MUTED),
                         on_click=lambda e, q=q: on_pick(q), on_delete=forget(q),
                         delete_icon_color=MUTED, delete_icon_tooltip="Прибрати",
                         shape=ft.RoundedRectangleBorder(radius=10),
                         border_side=ft.BorderSide(1, BORDER))
                 for q in items]
        if chips:
            chips.append(ft.TextButton("Очистити історію", on_click=clear_all,
                                       style=ft.ButtonStyle(color=MUTED)))
        return chips

    def pick_recent(q):
        search.value = q
        clear_btn.visible = True
        page.run_task(do_search, q)

    def fill_recent():
        recent_row.controls = [ft.Text("Нещодавні:", size=13, color=MUTED)] + \
            recent_chips(pick_recent, search.value or "")
        recent_bar.visible = bool(st.get("search_has_focus")) and len(recent_row.controls) > 1
        try:
            recent_bar.update()
        except Exception:
            pass

    def show_recent(on):
        st["search_has_focus"] = on
        fill_recent()

    async def hide_recent_later():
        await asyncio.sleep(0.25)  # дати спрацювати кліку по запиту, перш ніж сховати
        if not st.get("search_has_focus"):
            fill_recent()

    def search_blur(e):
        quiet()
        st["search_has_focus"] = False
        page.run_task(hide_recent_later)

    def release_search():
        """Телефон: прибрати фокус із поля пошуку (і клавіатуру). У Flet немає «blur», тож
        фокус переходить на кнопку меню; інакше Flutter після закриття меню чи переходу
        повертав фокус у пошук — і клавіатура вискакувала сама."""
        if is_mobile and st.get("search_has_focus"):
            st["search_has_focus"] = False
            fill_recent()
            page.run_task(menu_btn.focus)

    search = ft.TextField(
        on_focus=lambda e: (quiet(), show_recent(True)), on_blur=search_blur,
        hint_text="Пошук фільмів і серіалів", height=40, text_size=14, dense=True,
        filled=True, fill_color=SURFACE_2,
        border={
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                border_radius=12, side=ft.BorderSide(1, ft.Colors.with_opacity(0.6, ACCENT))),
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                border_radius=12, side=ft.BorderSide(1, BORDER)),
        },
        prefix_icon=ft.Icons.SEARCH_ROUNDED, cursor_color=ACCENT,
        hint_style=ft.TextStyle(color=MUTED, size=14),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=0),
        text_vertical_align=ft.VerticalAlignment.CENTER,
        # іконки не повинні розтягувати поле (на Windows воно ставало дуже високим)
        prefix_icon_size_constraints=ft.BoxConstraints(min_width=40, max_width=40,
                                                       min_height=40, max_height=40),
        suffix_icon_size_constraints=ft.BoxConstraints(min_width=36, max_width=36,
                                                       min_height=40, max_height=40),
        size_constraints=ft.BoxConstraints(min_height=40, max_height=40),
        width=None if is_mobile else 320, expand=is_mobile,
        suffix_icon=clear_btn, on_change=on_search_change,
        on_submit=lambda e: page.run_task(do_search, e.control.value),
    )

    layout_toggle = ft.SegmentedButton(visible=not TV,
        selected=["grid"], show_selected_icon=False,
        segments=[ft.Segment("grid", icon=ft.Icon(ft.Icons.GRID_VIEW_ROUNDED, size=18),
                             tooltip="Сітка"),
                  ft.Segment("list", icon=ft.Icon(ft.Icons.VIEW_AGENDA_ROUNDED, size=18),
                             tooltip="Список")],
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=10),
            side=ft.BorderSide(1, BORDER),
            bgcolor={ft.ControlState.SELECTED: ft.Colors.with_opacity(0.16, ACCENT),
                     ft.ControlState.DEFAULT: SURFACE_2},
            icon_color={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: MUTED},
            padding=ft.Padding.symmetric(horizontal=6),
        ),
        on_change=lambda e: switch_layout(e.control.selected[0]),
    )

    menu_btn = ft.IconButton(ft.Icons.MENU_ROUNDED, icon_color=None if TV else TEXT,
                             tooltip="Розділи",
                             on_click=lambda e: page.run_task(open_drawer),
                             style=ft.ButtonStyle(bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG},
                                                  icon_color=TV_FOCUS_TEXT, side=TV_FOCUS_SIDE)
                             if TV else None)

    def back_home():
        search.value = ""
        clear_btn.visible = False
        go_home()
        if TV and st.get("return_focus"):  # фокус — на плитку, з якої відкрили розділ
            page.run_task(focus_later, st["return_focus"])

    # ПК: дзвіночок біля пошуку — помаранчева крапка, коли вийшла нова версія; у меню —
    # «Оновити до …» і «Перевірити оновлення»
    bell_dot = ft.Container(width=9, height=9, border_radius=5, bgcolor=ACCENT,
                            border=ft.Border.all(1.5, BG), right=7, top=7, visible=False)
    bell = ft.PopupMenuButton(
        content=ft.Stack([ft.Container(ft.Icon(ft.Icons.NOTIFICATIONS_NONE_ROUNDED, color=TEXT,
                                               size=22),
                                       width=40, height=40, alignment=ft.Alignment.CENTER),
                          bell_dot], width=40, height=40),
        tooltip="Сповіщення", bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=12),
        menu_position=ft.PopupMenuPosition.UNDER, visible=is_desktop and not TV,
        # відкриття / закриття меню — теж події: без quiet перемальовувалась уся сторінка
        on_open=lambda e: quiet(), on_cancel=lambda e: quiet(), on_select=lambda e: quiet())

    def fill_bell():
        rel = st.get("update_rel")
        items = []
        if rel:
            items.append(ft.PopupMenuItem(
                content=ft.Row([ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT_ROUNDED, color=ACCENT, size=20),
                                ft.Column([ft.Text(f"Доступна версія {rel['version']}",
                                                   color=TEXT, weight=ft.FontWeight.W_700),
                                           ft.Text("Натисніть, щоб оновити", color=MUTED, size=12)],
                                          spacing=0, tight=True)], spacing=12),
                on_click=lambda e: (quiet(), show_update_dialog(rel))))
        else:
            items.append(ft.PopupMenuItem(
                content=ft.Text("Нових сповіщень немає", color=MUTED), disabled=True))
        items.append(ft.PopupMenuItem(
            content=ft.Row([ft.Icon(ft.Icons.REFRESH_ROUNDED, color=MUTED, size=18),
                            ft.Text("Перевірити оновлення", color=TEXT)], spacing=12),
            # quiet: без автооновлення всієї сторінки після кліку — від нього блимав вміст
            on_click=lambda e: (quiet(), page.run_task(check_updates, True))))
        bell.items = items
        bell_dot.visible = bool(rel)

    def set_update_rel(rel):
        """Запам'ятати знайдену версію; дзвіночок оновлюється лише якщо щось змінилось —
        інакше його оновлення перемальовувало верхню панель (блимало)."""
        old = (st.get("update_rel") or {}).get("version")
        st["update_rel"] = rel
        if (rel or {}).get("version") == old:
            return
        fill_bell()
        try:
            bell.update()
        except Exception:
            pass

    async def refresh_bell():
        """Тихо дізнатись, чи вийшла нова версія (для крапки на дзвіночку) — без вікон."""
        if not bell.visible or updater.VERSION == "dev":
            fill_bell()
            return
        try:
            rel = await asyncio.to_thread(updater.latest_release)
        except Exception:
            return
        set_update_rel(rel if updater.is_newer(rel["version"], updater.VERSION) else None)

    async def bell_loop(gen):
        while RUN["gen"] == gen:
            await refresh_bell()
            await asyncio.sleep(3 * 3600)

    fill_bell()

    # ПК: «←» біля назви розділу — назад на головну одним кліком (видно лише не на головній)
    cat_back = ft.IconButton(ft.Icons.ARROW_BACK_ROUNDED, icon_color=TEXT, tooltip="На головну",
                             visible=False, on_click=lambda e: back_home())
    title_block = ft.Column([heading_switcher, subheading], spacing=0, expand=True,
                            alignment=ft.MainAxisAlignment.CENTER)
    if is_mobile:
        settings_btn = ft.IconButton(ft.Icons.SETTINGS_OUTLINED, icon_color=TEXT,
                                     tooltip="Налаштування",
                                     on_click=lambda e: page.run_task(open_settings))
        top_bar = ft.Column([ft.Row([menu_btn, title_block, layout_toggle, settings_btn],
                                    spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                             ft.Row([search])], spacing=8)
    elif TV:
        # Поле вводу на телевізорі «захоплює» стрілки пульта — з нього не вийти. Тому тут
        # кнопка, а текст вводиться у вікні (його закриває «Назад»).
        def open_tv_search(e):
            def submit(e=None):
                q = field.value
                page.pop_dialog()
                page.run_task(do_search, q)

            field = ft.TextField(value=search.value, autofocus=True, hint_text="Назва фільму або серіалу",
                                 text_size=20, filled=True, fill_color=SURFACE_2, cursor_color=ACCENT,
                                 border={ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                                             border_radius=12, side=ft.BorderSide(2, ACCENT)),
                                         ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                                             border_radius=12, side=ft.BorderSide(1, BORDER))},
                                 width=560, on_submit=submit)
            def tv_pick(q):
                page.pop_dialog()
                page.run_task(do_search, q)

            # нещодавні — кнопками: їх можна вибрати пультом (Chip фокус програмно не бере)
            tv_recent = [ft.Button(
                q, icon=ft.Icons.HISTORY_ROUNDED, on_click=lambda e, q=q: tv_pick(q),
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                             ft.ControlState.DEFAULT: SURFACE_2},
                    color=TV_FOCUS_TEXT, icon_color=TV_FOCUS_TEXT, side=TV_FOCUS_SIDE,
                    elevation=0, shape=ft.RoundedRectangleBorder(radius=10),
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12)))
                for q in searches]
            go_btn = ft.FilledButton("Шукати", icon=ft.Icons.SEARCH_ROUNDED, on_click=submit,
                                     style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF",
                                                          icon_color="#FFFFFF",
                                                          side=TV_FOCUS_SIDE))
            # поле вводу «з'їдає» стрілку вниз — переводимо фокус самі (див. on_key)
            st["tv_search_down"] = (tv_recent or [go_btn])[0]
            field.on_focus = lambda e: (quiet(), st.update(tv_search_field=True))
            field.on_blur = lambda e: (quiet(), st.update(tv_search_field=False))
            show_dialog(ft.AlertDialog(
                title=ft.Text("Пошук", size=24, weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([field] + ([ft.Text("Нещодавні", color=MUTED, size=16),
                                              ft.Row(tv_recent, wrap=True, spacing=10,
                                                     run_spacing=10, width=560)]
                                             if tv_recent else []),
                                  tight=True, spacing=14),
                actions=[ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         go_btn],
            ))

        def search_focus(on):
            quiet()
            st["search_focused"] = on

        search_btn = ft.FilledButton(
            "Пошук", icon=ft.Icons.SEARCH_ROUNDED, on_click=open_tv_search,
            on_focus=lambda e: search_focus(True), on_blur=lambda e: search_focus(False),
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG, ft.ControlState.DEFAULT: SURFACE_2},
                side=TV_FOCUS_SIDE,
                color=TV_FOCUS_TEXT, icon_color=TV_FOCUS_TEXT, shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=20, vertical=14)))
        def layout_btn(icon, key, tip):
            def pick(e):
                switch_layout(key)
                look_layout()
            return ft.IconButton(icon, tooltip=tip, on_click=pick, data=key, icon_size=26)

        def look_layout():
            for b in tv_layout.controls:
                on = b.data == st["layout"]
                b.style = ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=12), padding=12,
                    side=TV_FOCUS_SIDE,
                    bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                             ft.ControlState.DEFAULT: SURFACE_3 if on else ft.Colors.TRANSPARENT},
                    icon_color={ft.ControlState.FOCUSED: TV_FOCUS_FG,
                                ft.ControlState.DEFAULT: TEXT if on else MUTED})
            try:
                tv_layout.update()
            except Exception:
                pass

        # список чи картки 16:9 — ліворуч від пошуку (лише в розділах і пошуку)
        tv_layout = ft.Row([layout_btn(ft.Icons.VIEW_AGENDA_ROUNDED, "list", "Списком"),
                            layout_btn(ft.Icons.GRID_VIEW_ROUNDED, "grid", "Картками")],
                           spacing=4, tight=True, visible=False)
        look_layout()
        st["tv_layout"], st["tv_look_layout"] = tv_layout, look_layout
        top_bar = ft.Row([menu_btn, title_block, tv_layout, search_btn], spacing=12,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER)
    else:
        # перетягувати вікно (і розгортати подвійним кліком) — лише за заголовок і порожнє
        # місце: раніше зоною перетягування була вся панель, і подвійний клік у полі пошуку
        # (щоб виділити слово) розгортав вікно на весь екран
        top_bar = ft.Row([menu_btn, cat_back,
                          ft.WindowDragArea(title_block, maximizable=True, expand=True),
                          bell, search, layout_toggle, *win_buttons()],
                         spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    top_bar_box = ft.Container(
        top_bar,
        padding=ft.Padding.only(left=12 + traffic_gap, right=8 if is_win else 20, top=10,
                                bottom=10),
        bgcolor=BG if is_mobile else ft.Colors.with_opacity(0.85, BG),
        blur=None if (is_mobile or TV) else ft.Blur(20, 20),
        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
    )
    if TV:  # на ПК зона перетягування — лише заголовок (див. top_bar вище)
        top_bar_box = drag_area(top_bar_box)

    # ═════════════════════ картки ═════════════════════
    tmdb_slots = asyncio.Semaphore(4)  # не більше 4 пошуків у TMDB одночасно

    def tmdb_swap(box, item, size="w780"):
        """Картинка тайтлу з TMDB (офіційний широкий кадр) замість картинки сайту.
        Знайдена раніше — одразу. Інакше картинку сайту не показуємо зовсім (лише фон-
        заглушка), поки не прийде відповідь: так на очах нічого не підміняється. Не знайшли
        або TMDB не відповів за 4 с — показуємо картинку сайту."""
        img = box if isinstance(box, ft.Image) else getattr(box, "content", None)
        if not tmdb.enabled() or not isinstance(img, ft.Image) or not item.get("url"):
            return box
        known, src = tmdb.cached(item["url"], size)
        if known:
            if src:
                img.src = src
            return box
        img.visible = False  # невидима — Flutter її не будує й не вантажить

        async def look():
            src = None
            try:
                async with tmdb_slots:
                    src = await asyncio.wait_for(asyncio.to_thread(
                        tmdb.backdrop, item["url"], item.get("title", ""), item.get("orig", ""),
                        item.get("year", ""), size), 4)
            except Exception:
                pass
            if src:
                img.src = src
            img.visible = True
            try:
                img.update()
            except Exception:
                pass  # картку ще не показано (усе вже виставлено) або вже прибрано

        page.run_task(look)
        return box

    def card_meta(item, show):
        """Рейтинг і жанри (TMDB) для картки: show(rating, genres) — одразу, якщо вже
        відомо, інакше після відповіді TMDB. Вимкнено в налаштуваннях — нічого."""
        if not settings.get("card_meta") or not tmdb.enabled() or not item.get("url"):
            return
        known, m = tmdb.meta_cached(item["url"])
        if known:
            if m and (m["rating"] or m["genres"]):
                show(m["rating"], m["genres"])
            return

        async def look():
            try:
                async with tmdb_slots:
                    m = await asyncio.wait_for(asyncio.to_thread(
                        tmdb.meta, item["url"], item.get("title", ""), item.get("orig", ""),
                        item.get("year", ""), core.site_meta), 12)
            except Exception:
                return
            if m and (m["rating"] or m["genres"]):
                show(m["rating"], m["genres"], True)

        page.run_task(look)

    def rating_badge(rating):
        return ft.Container(
            ft.Row([ft.Icon(ft.Icons.STAR_ROUNDED, size=12, color="#F5C518"),
                    ft.Text(f"{rating:.1f}", size=11, weight=ft.FontWeight.W_700,
                            color="#FFFFFF")], spacing=3, tight=True),
            padding=ft.Padding.symmetric(horizontal=7, vertical=3), border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.6, "#000000"), border=ft.Border.all(1, "#22FFFFFF"))

    def grid_card(item, progress=None, caption=None, on_open=None, on_remove=None, tv_w=None):
        play = ft.Container(
            ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color="#FFFFFF", size=30),
            width=56, height=56, border_radius=28, gradient=ui.ACCENT_GRADIENT,
            alignment=ft.Alignment.CENTER, opacity=0, scale=0.7,
            animate_opacity=ui.FAST, animate_scale=ui.MED,
            shadow=ft.BoxShadow(blur_radius=24, color=ft.Colors.with_opacity(0.5, ACCENT)),
        )
        layers = [
            tmdb_swap(ui.poster_image(item["poster"], cache_width=480 if TV else 760,
                                      left=0, top=0, right=0, bottom=0), item),
            ft.Container(  # затемнення знизу під назвою
                left=0, right=0, bottom=0, height=110,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                    colors=["#00000000", "#CC000000", "#F2000000"]),
            ),
            ft.Container(
                ft.Column([
                    ft.Text(item["title"], size=15, weight=ft.FontWeight.W_700, color="#FFFFFF",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    sub_text := ft.Text(caption or item["orig"] or " ", size=11,
                                        color=ui.ACCENT_2 if caption else "#C8CCD4", max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=2, tight=True),
                left=12, right=12, bottom=16 if progress is not None else 12,
            ),
            ft.Container(play, left=0, right=0, top=0, bottom=0, alignment=ft.Alignment.CENTER),
            # праворуч угорі; поруч із ✕ «Прибрати» (якщо є) — лівіше, щоб не перекривались
            rating_slot := ft.Container(right=46 if on_remove and not TV else 10, top=10,
                                        visible=False),
        ]

        def show_meta(rating, genres, late=False):
            if rating:
                rating_slot.content, rating_slot.visible = rating_badge(rating), True
            if genres and not caption:  # жанри — замість оригінальної назви
                sub_text.value = " · ".join(genres[:2])
            if late:
                try:
                    rating_slot.update()
                    sub_text.update()
                except Exception:
                    pass

        card_meta(item, show_meta)
        refs = {"caption": sub_text} if caption else {}  # див. update_continue_in_place
        if progress is not None:  # смужка «скільки переглянуто»
            refs["progress"] = ft.ProgressBar(value=max(0.02, min(progress, 1)), color=ACCENT,
                                              bgcolor="#55FFFFFF", bar_height=4, border_radius=2)
            layers.append(ft.Container(refs["progress"], left=12, right=12, bottom=8))
        if item["age"]:
            layers.append(ft.Container(
                ft.Text(item["age"], size=10, weight=ft.FontWeight.W_700, color="#FFFFFF"),
                left=10, top=10, padding=ft.Padding.symmetric(horizontal=7, vertical=3),
                border_radius=6, bgcolor=ft.Colors.with_opacity(0.55, "#000000"),
                border=ft.Border.all(1, "#22FFFFFF"),
            ))
        remove_btn = None
        if on_remove and not TV:
            # ✕ — прибрати з «Продовжити перегляд»: на телефоні видно завжди, на ПК — при наведенні
            remove_btn = ft.Container(
                ft.Icon(ft.Icons.CLOSE_ROUNDED, size=16, color="#FFFFFF"),
                width=30, height=30, border_radius=15, alignment=ft.Alignment.CENTER,
                bgcolor=ft.Colors.with_opacity(0.6, "#000000"), border=ft.Border.all(1, "#33FFFFFF"),
                tooltip="Прибрати з історії", on_click=lambda e: on_remove(),
                top=8, right=8, opacity=1 if is_mobile else 0, animate_opacity=ui.FAST)
            layers.append(remove_btn)
        card = ft.Container(
            ft.Stack(layers, expand=True),
            # з «save layer» кути обрізаються чисто (без ореолу тіні біля рамки); на TV — простіше
            border_radius=14, clip_behavior=ft.ClipBehavior.ANTI_ALIAS if TV
            else ft.ClipBehavior.ANTI_ALIAS_WITH_SAVE_LAYER,
            # без рамки (крім TV): світла лінія на темних постерах виглядала як «скляний»
            # обідок
            border=ft.Border.all(1, BORDER) if TV else None,
            shadow=ft.BoxShadow(blur_radius=18, offset=ft.Offset(0, 8),
                                color=ft.Colors.with_opacity(0.35, "#000000")),
            scale=1, animate_scale=ui.MED, animate=ui.MED,
            on_click=lambda e: on_open() if on_open else page.run_task(open_title, item),
        )

        def highlight(h):
            card.scale = 1.045 if h else 1
            # на ПК без рамки й під мишею: наведення видно зі збільшення, тіні й кнопки ▶
            card.shadow = ft.BoxShadow(
                blur_radius=34 if h else 18, offset=ft.Offset(0, 14 if h else 8),
                color=ft.Colors.with_opacity(0.5 if h else 0.35, "#000000"))
            play.opacity, play.scale = (1, 1) if h else (0, 0.7)
            if remove_btn is not None and not is_mobile:
                remove_btn.opacity = 1 if h else 0
            card.update()

        if TV:
            card.on_click = None
            card.shadow = None
            card.animate = card.animate_scale = None
            # рамка фокуса — ЗОВНІ картки, із зазором (як у Netflix / Apple TV): намальована на
            # самій картці, вона мала інший заокруглений край, ніж постер, і в кутах світились
            # білі «клинці». Загальний розмір (картка + зазор + рамка) — як і раніше.
            ring_pad = 6  # 3 рамка + 3 зазор
            w = (tv_w or TV_CARD_W) - 2 * ring_pad
            card.width, card.height = w, w / POSTER_RATIO
            outer = ft.Container(card, padding=3, border_radius=20,
                                 border=ft.Border.all(3, ft.Colors.TRANSPARENT))

            def ring(f):
                outer.border = ft.Border.all(3, focus_ring if f else ft.Colors.TRANSPARENT)
                outer.update()

            btn = ui.appear(focusable(
                outer, lambda e: on_open() if on_open else page.run_task(open_title, item),
                on_focus_change=ring, radius=20))
            btn.refs = refs
            return btn
        card.on_hover = lambda e: highlight(ui.hovered(e))
        card.refs = refs
        return ui.appear(card)

    def list_card(item, caption=None, on_open=None, progress=None, autofocus=False):
        open_ = on_open or (lambda: page.run_task(open_title, item))
        lines = [ft.Text(item["title"], size=16, weight=ft.FontWeight.W_700,
                         color=None if TV else TEXT,  # TV: колір від кнопки (вибір)
                         max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
        if item["orig"]:
            lines.append(ft.Text(item["orig"], size=13, color=MUTED, max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS))
        meta_line = ft.Text("", size=13, color=MUTED, max_lines=1, visible=False,
                            overflow=ft.TextOverflow.ELLIPSIS)
        lines.append(meta_line)

        def show_meta(rating, genres, late=False):
            parts = ([f"★ {rating:.1f}"] if rating else []) + genres[:3]
            meta_line.value, meta_line.visible = " · ".join(parts), bool(parts)
            if late:
                try:
                    meta_line.update()
                except Exception:
                    pass

        card_meta(item, show_meta)
        if item["age"] and not caption:
            lines.append(ft.Row([ui.pill(item["age"])]))
        refs = {}  # підпис і смужка — щоб оновити на місці (див. render_continue)
        if caption:
            refs["caption"] = ft.Text(caption, size=13, color=ACCENT, max_lines=1)
            lines.append(refs["caption"])
        if progress:
            refs["progress"] = ft.ProgressBar(value=progress, color=ACCENT, bgcolor=SURFACE_3,
                                              bar_height=4, border_radius=2, width=220)
            lines.append(refs["progress"])
        arrow = ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, color=None if TV else MUTED,
                        offset=ft.Offset(0, 0), animate_offset=ui.FAST)
        row = ft.Container(
            ft.Row([
                tmdb_swap(ui.poster_image(item["poster"], radius=10, width=176, height=99,
                                          cache_width=360), item, "w300"),
                ft.Column(lines, spacing=4, expand=True, alignment=ft.MainAxisAlignment.CENTER),
                arrow,
            ], spacing=16),
            padding=10, border_radius=14, bgcolor=SURFACE, border=ft.Border.all(1, BORDER),
            animate=None if TV else ui.FAST,
            on_click=lambda e: open_(),
        )
        if TV:  # фокус з пульта: біла рамка (tv_tap), без анімацій
            # перший пункт «Продовжити перегляд» — прокрутити головну на самий верх,
            # щоб над ним було видно заголовок
            top = (lambda f: f and page.run_task(home_content.scroll_to, offset=0, duration=150)) \
                if autofocus else None
            def open_tv(e):
                st["return_focus"] = btn  # після «Назад» фокус — знову на цей рядок
                open_()

            btn = tv_tap(row, open_tv, highlight=top, radius=14, autofocus=autofocus,
                         focus_bg=(TV_FOCUS_BG, SURFACE))
            btn.refs = refs
            return btn

        def hover(e):
            h = ui.hovered(e)
            row.bgcolor = SURFACE_2 if h else SURFACE
            row.border = ft.Border.all(1, HOVER_COLOR if h else BORDER)
            arrow.offset = ft.Offset(0.3 if h else 0, 0)
            arrow.color = TEXT if h else MUTED
            row.update()

        row.on_hover = hover
        return ui.appear(row)

    # ═════════════════════ каталог ═════════════════════
    def on_scroll(e: ft.OnScrollEvent):
        quiet()
        release_search()  # гортають — пошук уже не потрібен
        if e.pixels >= e.max_scroll_extent - 700:
            page.run_task(load_more)

    # знизу — запас, щоб останній рядок (особливо вибраний пультом) не обрізався краєм екрана
    pad = ft.Padding.only(left=14, right=14, top=14, bottom=30) if is_mobile else \
        ft.Padding.only(left=24, right=24, top=20, bottom=120 if TV else 40)
    grid = ft.GridView(
        expand=True, max_extent=420 if is_mobile else 360, child_aspect_ratio=POSTER_RATIO,
        spacing=12 if is_mobile else 20, run_spacing=14 if is_mobile else 22, padding=pad,
        on_scroll=on_scroll, scroll_interval=80,
    )
    listing = ft.ListView(expand=True, spacing=10, padding=pad, on_scroll=on_scroll,
                          scroll_interval=80)
    if TV:
        # кнопка-обгортка не розтягує вміст на всю клітинку сітки, тому на телевізорі —
        # картки фіксованого розміру в рядку з переносом, у прокручуваному списку
        # Ряди карток — окремими елементами списку: Flutter будує лише ряди на екрані
        # (і запас cache_extent), а не всі сотні карток розділу одразу
        tv_cards = []
        tv_grid_scroller = grid = ft.ListView(expand=True, spacing=24, padding=pad,
                                              on_scroll=on_scroll, scroll_interval=80,
                                              cache_extent=1600)

    def skeleton():
        if st["layout"] == "list":
            tiles = [ft.Container(height=121, bgcolor=SURFACE_2, border_radius=14) for _ in range(8)]
            inner = ft.ListView(tiles, expand=True, spacing=10, padding=pad)
        else:
            tiles = [ft.Container(bgcolor=SURFACE_2, border_radius=14) for _ in range(12)]
            inner = ft.GridView(tiles, expand=True, max_extent=TV_CARD_W if TV else 420 if is_mobile else 360,
                                child_aspect_ratio=POSTER_RATIO, spacing=20,
                                run_spacing=22, padding=pad)
        return shimmer(inner, base_color=SURFACE_2, highlight_color=SURFACE_3, expand=True,
                          key=f"skeleton{st['gen']}")

    # Плаваючий індикатор дозавантаження. Лише змінює прозорість: якщо ховати/показувати
    # його через visible поруч із сіткою, Flutter перебудовує сітку і скидає прокрутку.
    more_loader = ft.Container(
        ft.Container(
            ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT),
                    ft.Text("Завантажую ще…", size=12, color=TEXT)], spacing=10, tight=True),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10), border_radius=20,
            bgcolor=ft.Colors.with_opacity(0.9, SURFACE_3), border=ft.Border.all(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=20, color=ft.Colors.with_opacity(0.4, "#000000")),
        ),
        left=0, right=0, bottom=18, alignment=ft.Alignment.CENTER,
        opacity=0, animate_opacity=ui.MED, offset=ft.Offset(0, 0.3), animate_offset=ui.MED,
        ignore_interactions=True,
    )

    def set_more_loading(on):
        more_loader.opacity = 1 if on else 0
        more_loader.offset = ft.Offset(0, 0 if on else 0.3)

    # reverse_duration — як швидко зникає старий вміст: коротко, щоб старі картки
    # не просвічували під новими під час перемикання сітка/список
    # не AnimatedSwitcher: Flet дає його вмісту новий ключ при КОЖНІЙ перебудові екрана —
    # відкриття / закриття будь-якого вікна (оновлення, пошук на TV…) створювало головну чи
    # розділ заново: вони блимали й прокрутка скидалась на початок
    body = ft.Container(ft.Container(), expand=True)

    def tv_grid_cols():
        return 4 if (page.width or 960) - 56 >= 840 else 3

    def tv_grid_card_w():
        """TV: картки сітки на всю ширину екрана — 4 в ряд (на вужчому — 3)."""
        avail = (page.width or 960) - 48 - 8  # відступи сітки й смужки біля країв
        cols = tv_grid_cols()
        return int((avail - 24 * (cols - 1)) / cols) - 1

    def tv_grid_place(cards, append):
        """TV: розкласти картки по рядах; при дозавантаженні — дозаповнити останній ряд."""
        cols = tv_grid_cols()
        if not append:
            tv_cards.clear()
            grid.controls = []
        tv_cards.extend(cards)
        rows = grid.controls
        if rows and len(rows[-1].controls) < cols:
            last = rows[-1]
            take = cols - len(last.controls)
            last.controls.extend(cards[:take])
            cards = cards[take:]
        for i in range(0, len(cards), cols):
            rows.append(ft.Row(cards[i:i + cols], spacing=24))

    def apply_layout_default():
        """Вигляд розділів із налаштувань (перемикач у розділі змінює його лише до виходу)."""
        st["layout"] = settings["layout"]
        layout_toggle.selected = [st["layout"]]
        if st.get("tv_look_layout"):
            st["tv_look_layout"]()

    def current_view():
        return grid if st["layout"] == "grid" else listing

    def show_items():
        view = tv_grid_scroller if (TV and st["layout"] == "grid") else current_view()
        body.content = ft.Stack([view, more_loader], expand=True,
                                key=f"{st['layout']}{st['gen']}")

    async def reveal_cards(cards):
        """Каскадна поява карток."""
        for i, c in enumerate(cards):
            ui.reveal(c)
            if i % 4 == 3 or i == len(cards) - 1:
                try:
                    current_view().update()
                except Exception:
                    return  # вигляд уже замінено
                await asyncio.sleep(0.025)

    def render(items, append=False, animate=True):
        # в «Обраному» серіали з новою серією — з підписом
        new = st.get("fav_new", set()) if st["mode"] == ("cat", FAV_PATH) else set()

        def caption(it):
            h = it.get("_h")
            if h:  # «Історія переглядів»: завершене чи де зупинились
                return "Переглянуто ✓" if watched(h) else resume_caption(h)
            return "Нова серія" if it["url"] in new else None

        def progress(it):
            h = it.get("_h")
            return h["pos"] / h["dur"] if h and h.get("dur") and not watched(h) else None

        if st["layout"] == "grid":
            w = tv_grid_card_w() if TV else None
            cards = [grid_card(it, tv_w=w, caption=caption(it), progress=progress(it))
                     for it in items]
        else:
            cards = [list_card(it, caption=caption(it), progress=progress(it)) for it in items]
        if not animate:
            for c in cards:
                ui.reveal(c)
        view = current_view()
        if TV and st["layout"] == "grid":
            tv_grid_place(cards, append)
        elif append:
            view.controls.extend(cards)
        else:
            view.controls = cards
        return cards

    def update_subheading():
        n = len(st["items"])
        set_subheading(f"{n} {plural(n, 'тайтл', 'тайтли', 'тайтлів')}" if n else "")

    async def fetch_page(page_no):
        kind, val = st["mode"]
        if kind == "cat" and val == FAV_PATH:
            return active_favorites() if page_no == 1 else []
        if kind == "cat" and val == HISTORY_PATH:
            if page_no > 1:
                return []
            return [dict({k: h.get(k, "") for k in ("url", "title", "orig", "poster", "age")},
                         _h=h)
                    for h in history if not h.get("removed") and h.get("title")]
        if kind == "cat" and st.get("filters"):
            filters = dict(st["filters"])
            return await asyncio.to_thread(core.catalog_filtered, val, page_no, filters)
        block = next((b for _, pth, b in SHELVES if pth == val and b), None)
        if kind == "cat" and page_no == 1 and block:
            # перша сторінка — зі свіжим із головної сайту (наступні без повторів: load_more)
            return await asyncio.to_thread(core.fresh_catalog, val, block)
        url = core.catalog_url(val, page_no) if kind == "cat" else core.search_url(val, page_no)
        return await asyncio.to_thread(lambda: core.parse_catalog(core.fetch(url)))

    async def reload(soft=False):
        """soft — зміна фільтра: старі картки лишаються, поки вантажаться нові (без заглушок,
        які виглядали як блимання)."""
        refresh_genre_bar()
        kind_, val_ = st["mode"]
        if kind_ == "cat" and val_ not in LOCAL_PATHS:
            if st.get("filters_for") != val_:  # інший розділ — фільтри з нуля
                st["filters_for"], st["filters"], st["filter_defs"] = val_, {}, []
                render_filters()
                page.run_task(load_filters, val_)
        else:
            st["filters_for"], st["filters"], st["filter_defs"] = None, {}, []
            render_filters()
        if st["mode"][0] == "home":
            go_home()
            return
        layout_toggle.visible = not TV
        cat_back.visible = is_desktop
        if st.get("tv_layout"):
            st["tv_layout"].visible = True
        st["gen"] += 1
        gen = st["gen"]
        had_items = bool(st["items"])
        st.update(page=1, items=[], has_more=True, loading=True)
        set_subheading("Завантаження…")
        if soft and had_items:
            set_more_loading(True)
            try:
                more_loader.update()
                subheading.update()
            except Exception:
                pass
        else:
            body.content = skeleton()
            page.update()
        try:
            items = await fetch_page(1)
        except Exception as ex:
            msg = await explain(ex, "список")
            if gen == st["gen"]:
                st["loading"] = False
                offline = "інтернет" in msg
                body.content = empty_state(
                    error_icon(msg), "Немає з'єднання" if offline else "Не вдалося завантажити",
                    msg, retry=lambda: page.run_task(reload))
                set_subheading("")
                page.update()
            return
        if gen != st["gen"]:
            return
        st.update(items=items, loading=False, has_more=bool(items))
        update_subheading()
        if not items and st["mode"] == ("cat", FAV_PATH):
            body.content = empty_state(ft.Icons.BOOKMARK_BORDER_ROUNDED, "Тут поки порожньо",
                                       "Додавайте фільми й серіали кнопкою «В обране» "
                                       "на їхній сторінці")
            page.update()
            return
        if not items and st["mode"] == ("cat", HISTORY_PATH):
            body.content = empty_state(ft.Icons.HISTORY_ROUNDED, "Історія порожня",
                                       "Тут будуть фільми й серіали, які ви дивились — "
                                       "і ті, що вже переглянули до кінця")
            page.update()
            return
        if not items:
            body.content = empty_state(ft.Icons.SEARCH_OFF_ROUNDED, "Нічого не знайдено",
                                       "Спробуйте інший запит або розділ")
            page.update()
            return
        set_more_loading(False)
        cards = render(items)
        show_items()
        page.update()
        await reveal_cards(cards)
        await load_more()  # сторінка сайту — лише ~12 карток, одразу підвантажуємо наступну

    async def load_more():
        if st["loading"] or not st["has_more"] or not st["items"]:
            return
        st["loading"] = True
        gen = st["gen"]
        set_more_loading(True)
        more_loader.update()
        try:
            items = await fetch_page(st["page"] + 1)
        except Exception:
            items = None  # без повідомлення: наступна прокрутка спробує ще раз
        if gen != st["gen"]:
            return
        st["loading"] = False
        set_more_loading(False)
        if items is None:
            more_loader.update()
            return
        known = {it["url"] for it in st["items"]}
        fresh = [it for it in items if it["url"] not in known]
        st["page"] += 1
        st["has_more"] = bool(fresh)
        st["items"].extend(fresh)
        update_subheading()
        render(fresh, append=True, animate=False)  # нові картки з'являються нижче екрана
        current_view().update()
        more_loader.update()
        subheading.update()

    def switch_layout(layout):
        if layout == st["layout"]:
            return
        st["layout"] = layout
        if not st["items"]:
            return
        cards = render(st["items"])
        for c in cards[24:]:  # анімуємо лише перший екран
            ui.reveal(c)
        show_items()
        page.update()
        if not TV:
            page.run_task(reveal_cards, cards[:24])

    def remember_search(q):
        searches[:] = [q] + [x for x in searches if x.lower() != q.lower()][:SEARCHES_MAX - 1]
        page.run_task(prefs.set, "searches", json.dumps(searches, ensure_ascii=False))

    async def do_search(q):
        q = (q or "").strip()
        if not q:
            return
        remember_search(q)
        release_search()  # пошук запущено — клавіатуру сховати
        show_recent(False)
        st["mode"] = ("search", q)
        drawer.selected_index = st["nav_index"] = None
        mark_nav()
        layout_toggle.visible = not TV
        set_heading(f"Пошук: «{q}»")
        await reload()

    # ═════════════════════ головна (в стилі Apple TV) ═════════════════════
    hero = {"items": [], "i": 0, "hover": False, "loaded": False, "timer": 0}
    side = 20 if is_mobile else 56

    def banner_height():
        h = page.height or 800
        return max(360, int((h - 64) * (0.62 if is_mobile else 0.9)))

    def kind_of(url):
        path = urlparse(url).path
        for key, label in (("/anime/", "Аніме"), ("/cartoons/", "Мультфільм"),
                           ("/dorama/", "Дорама"), ("/serials/", "Серіал"), ("/film", "Фільм")):
            if path.startswith(key):
                return label
        return ""

    # два шари на всю площу банера: новий слайд проявляється поверх старого (crossfade)
    fade = ft.Animation(900, ft.AnimationCurve.EASE_IN_OUT)
    # на телевізорі — без переходу: плавне змішування двох повноекранних картинок там гальмує
    layers = [ft.Container(left=0, top=0, right=0, bottom=0, opacity=o,
                           animate_opacity=None if TV else fade,
                           clip_behavior=ft.ClipBehavior.HARD_EDGE) for o in (1, 0)]
    layers[0].content = shimmer(ft.Container(bgcolor=SURFACE_2, expand=True),
                                   base_color=SURFACE_2, highlight_color=SURFACE_3, expand=True)
    banner_text = ft.AnimatedSwitcher(
        ft.Container(), duration=500, reverse_duration=150,
        transition=ft.AnimatedSwitcherTransition.FADE,
        switch_in_curve=ui.EASE, switch_out_curve=ui.EASE)
    dots = ft.Row([], spacing=8)
    arrows = []

    def slide_image(item):
        # «Ken Burns»: кадр повільно наближається, поки слайд на екрані
        return ft.Container(
            ft.Image(src=item.get("backdrop") or item["poster"], fit=ft.BoxFit.COVER,
                     gapless_playback=True,
                     width=float("inf"), height=float("inf")),
            expand=True, scale=1.0,
            animate_scale=ft.Animation(BANNER_INTERVAL * 1000 + 1500, ft.AnimationCurve.LINEAR),
            key=f"img{item['url']}")

    def banner_focus(e):
        quiet()
        hero["focus"] = True
        hero["autofocused"] = True

    def banner_blur(e):
        quiet()
        hero["focus"] = False

    def slide_text(item):
        info = item.get("info") or {}
        dark = ft.Colors.with_opacity(0.45, "#000000")
        meta = []
        if info.get("imdb"):
            meta.append(ui.pill(f"IMDb {info['imdb']}", ft.Icons.STAR_ROUNDED, color="#FFFFFF",
                                icon_color=ui.GOLD, bold=True, bg=dark))  # банер завжди темний
        for v in (info.get("year"), info.get("duration"), info.get("age") or item.get("age")):
            if v:
                meta.append(ui.pill(v, bg=dark, color="#E6E8EC"))
        genres = " · ".join(g.capitalize() for g in (info.get("genres") or [])[:3])
        kind = kind_of(item["url"])
        orig = item["orig"] or info.get("orig") or ""
        focus_side = {ft.ControlState.FOCUSED: ft.BorderSide(3, ACCENT),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)}
        primary = ft.ButtonStyle(
            side=focus_side,
            bgcolor={ft.ControlState.HOVERED: "#FFFFFF", ft.ControlState.DEFAULT: "#ECECEC"},
            color="#0D0E12", icon_color="#0D0E12", shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.Padding.symmetric(horizontal=26, vertical=18),
            text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700))
        secondary = ft.ButtonStyle(
            side=focus_side,
            bgcolor={ft.ControlState.HOVERED: ft.Colors.with_opacity(0.35, "#FFFFFF"),
                     ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.2, "#FFFFFF")},
            color="#FFFFFF", icon_color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.Padding.symmetric(horizontal=22, vertical=18),
            text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_600))
        return ft.Column([
            ft.Text(("Новинка · " + kind if kind else "Новинка").upper(), size=12,
                    weight=ft.FontWeight.W_800, color=ACCENT),
            ft.Text(info.get("title") or item["title"], size=30 if is_mobile else 54,
                    weight=ft.FontWeight.W_800, color="#FFFFFF", max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(orig, size=16, color="#C9CDD4", visible=bool(orig)),
            ft.Row(meta + ([ft.Text(genres, size=13, color="#C9CDD4")] if genres else []),
                   spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(info.get("description") or "", size=15, color="#D8DBE0", max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS, visible=not is_mobile),
            ft.Container(height=6),
            ft.Row([
                ft.FilledButton("Дивитись", icon=ft.Icons.PLAY_ARROW_ROUNDED, style=primary,
                                autofocus=TV and not hero.get("autofocused"),
                                on_focus=banner_focus, on_blur=banner_blur,
                                on_click=lambda e: page.run_task(open_title, item, True)),
                ft.FilledButton("Детальніше", icon=ft.Icons.INFO_OUTLINE_ROUNDED, style=secondary,
                                on_focus=banner_focus, on_blur=banner_blur,
                                on_click=lambda e: page.run_task(open_title, item)),
            ], spacing=12, wrap=True),
        ], spacing=8, tight=True, key=f"txt{item['url']}")

    def render_dots():
        dots.controls = [
            ft.Container(width=28 if i == hero["i"] else 8, height=8, border_radius=4,
                         bgcolor="#FFFFFF" if i == hero["i"]
                         else ft.Colors.with_opacity(0.4, "#FFFFFF"),
                         animate=ui.MED, on_click=lambda e, i=i: page.run_task(show_slide, i))
            for i in range(len(hero["items"]))]

    async def show_slide(i, user=True):
        items = hero["items"]
        if not items:
            return
        hero["i"] = i % len(items)
        if user and not TV:  # ручне перемикання — відлік до наступного слайда заново
            # (на телевізорі банер сам не гортається: слайди — стрілками)
            hero["timer"] += 1
            page.run_task(autoplay, hero["timer"])
        item = items[hero["i"]]
        img = slide_image(item)
        front, back = (layers if layers[0].opacity == 1 else layers[::-1])
        back.content = img
        back.opacity, front.opacity = 1, 0
        banner_text.content = slide_text(item)
        render_dots()
        page.update()
        if TV:
            return  # без «Ken Burns» — постійна анімація великої картинки
        await asyncio.sleep(0.05)
        img.scale = 1.08
        try:
            img.update()
        except Exception:
            pass

    async def autoplay(token):
        while hero["timer"] == token:
            await asyncio.sleep(BANNER_INTERVAL)
            if hero["timer"] != token or RUN["gen"] != gen:
                return
            # банер займає майже все вікно, тож на паузу при наведенні не ставимо —
            # стрілки й крапки дозволяють перемкнути вручну
            on_home = st["mode"][0] == "home" and len(page.views) == 1
            # на телевізорі не гортаємо, поки фокус на кнопках банера: інакше кнопка,
            # на якій стоїть фокус, зникне разом зі слайдом
            if on_home and not (TV and hero.get("focus")):
                await show_slide(hero["i"] + 1, user=False)

    def banner_hover(e):
        if TV:
            return
        hero["hover"] = ui.hovered(e)
        for a in arrows:
            a.opacity = 1 if hero["hover"] else 0
        banner.update()

    def arrow(icon, delta, at):
        btn = round_button(icon, lambda e: page.run_task(show_slide, hero["i"] + delta), size=46,
                           over_image=True)
        holder = ft.Container(btn, top=0, bottom=0, alignment=ft.Alignment.CENTER,
                              opacity=1 if TV else 0, animate_opacity=ui.MED, **{at: 16})
        arrows.append(holder)
        return holder

    banner = ft.Container(
        ft.Stack([
            *layers,
            ft.Container(left=0, top=0, right=0, bottom=0, gradient=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
                # завжди темне (і в світлій темі): білий текст банера лежить на фото
                colors=[ft.Colors.with_opacity(0.92, "#0D0E12"),
                        ft.Colors.with_opacity(0.55, "#0D0E12"), "#00000000"],
                stops=[0, 0.38, 0.75])),
            ft.Container(left=0, top=0, right=0, bottom=0, gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=["#00000000", "#00000000", "#0D0E12" if ui.LIGHT else BG],
                stops=[0, 0.6, 1])),
            ft.Container(banner_text, left=side, bottom=86,
                         width=None if is_mobile else 640, right=side if is_mobile else None),
            ft.Container(dots, left=side, bottom=40),
            arrow(ft.Icons.CHEVRON_LEFT_ROUNDED, -1, "left"),
            arrow(ft.Icons.CHEVRON_RIGHT_ROUNDED, 1, "right"),
        ], expand=True),
        height=banner_height(), clip_behavior=ft.ClipBehavior.HARD_EDGE,
        on_hover=banner_hover,
    )

    # свайп по банеру пальцем (або мишею) — попередній / наступний слайд
    swipe = {"x": 0.0}

    def swipe_start(e: ft.DragStartEvent):
        swipe["x"] = e.local_position.x

    def swipe_end(e: ft.DragEndEvent):
        dx = e.local_position.x - swipe["x"] if e.local_position else 0
        v = e.primary_velocity or 0
        if abs(v) > 250 or abs(dx) > 60:
            step = 1 if (v or dx) < 0 else -1  # палець вліво — наступний слайд
            page.run_task(show_slide, hero["i"] + step)

    banner_swipe = ft.GestureDetector(banner, on_horizontal_drag_start=swipe_start,
                                      on_horizontal_drag_end=swipe_end)

    # ── полички під банером
    shelf_card_w = 220 if is_mobile else TV_CARD_W

    def shelf(name, path, block=None):
        row = ft.ListView(
            horizontal=True, spacing=16, height=shelf_card_w / POSTER_RATIO + 28,
            padding=ft.Padding.symmetric(horizontal=side, vertical=14),
            controls=[shimmer(ft.Container(width=shelf_card_w, bgcolor=SURFACE_2, border_radius=14),
                                 base_color=SURFACE_2, highlight_color=SURFACE_3)
                      for _ in range(6)])

        async def load():
            try:
                items = await asyncio.to_thread(core.fresh_catalog, path, block)
            except Exception:
                items = []
            cards = []
            for it in items[:12] if TV else items:  # на TV менше карток — легше для пам'яті
                c = grid_card(it)
                ui.reveal(c)
                cards.append(ft.Container(c, width=shelf_card_w))
            if TV and cards:
                # замість кнопки «Усі» в заголовку (пульт перескакував на неї, і фокус губився) —
                # остання картка полички
                cards.append(tv_tap(ft.Container(
                    ft.Column([ft.Icon(ft.Icons.ARROW_FORWARD_ROUNDED, size=40, color=ACCENT),
                               ft.Text("Усі", size=20, weight=ft.FontWeight.W_700, color=TEXT)],
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              alignment=ft.MainAxisAlignment.CENTER, spacing=8),
                    width=shelf_card_w / 1.6, height=shelf_card_w / POSTER_RATIO,
                    bgcolor=SURFACE_2, border_radius=14, border=ft.Border.all(1, BORDER),
                    alignment=ft.Alignment.CENTER),
                    lambda e: page.run_task(open_category, name, path), radius=14))
            row.controls = cards or [ft.Text("Не вдалося завантажити", color=MUTED)]
            try:
                row.update()
            except Exception:
                pass

        header = ft.Row([
            ft.Text(name, size=20 if is_mobile else 22, weight=ft.FontWeight.W_800, color=TEXT),
            ft.Container(expand=True),
            ft.TextButton("Усі", icon=ft.Icons.CHEVRON_RIGHT_ROUNDED, visible=not TV,
                          style=ft.ButtonStyle(color=MUTED, icon_color=MUTED),
                          on_click=lambda e: page.run_task(open_category, name, path)),
        ])
        column = ft.Column([ft.Container(header, padding=ft.Padding.symmetric(horizontal=side)), row],
                           spacing=0)
        return column, load

    shelves = [shelf(*s) for s in SHELVES]

    continue_row = ft.ListView(horizontal=True, spacing=16, height=shelf_card_w / POSTER_RATIO + 28,
                               padding=ft.Padding.symmetric(horizontal=side, vertical=14))

    def clear_history():
        history.clear()
        # позначка часу очищення — щоб історія очистилась і на інших пристроях
        save_setting("history_cleared", int(time.time()))
        save_history()
        sync_changed(now=True)
        render_continue()
        toast("Історію переглядів очищено", ft.Icons.CHECK_CIRCLE_ROUNDED)

    # без spacing: проміжок — у самому рядку (нижній відступ), щоб при «Прибрати» він
    # стискався разом із рядком
    continue_list = ft.Column(spacing=0)  # телевізор: «Продовжити перегляд» — списком
    continue_section = ft.Column([
        ft.Container(ft.Row([
            ft.Text("Продовжити перегляд", size=20 if is_mobile else 22,
                    weight=ft.FontWeight.W_800, color=TEXT),
        ]), padding=ft.Padding.symmetric(horizontal=side)),
        ft.Container(continue_list, padding=ft.Padding.only(left=side, right=side, top=12))
        if TV else continue_row,
    ], spacing=0, visible=False)

    def update_continue_in_place(items, cards):
        """Той самий список (ті самі тайтли в тому ж порядку) — лише новий підпис і смужка.
        Повна перебудова після кожного «Назад» помітно перемальовувала головну."""
        changed = []
        for h, card in zip(items, cards):
            refs = getattr(card, "refs", None)
            if refs is None:
                return False
            cap, bar = refs.get("caption"), refs.get("progress")
            new_cap = resume_caption(h)
            new_val = h["pos"] / h["dur"] if h.get("dur") else None
            if (cap is None) != (not new_cap) or (bar is None) != (not new_val):
                return False  # з'явився / зник рядок — простіше перебудувати
            if cap is not None and cap.value != new_cap:
                cap.value = new_cap
                changed.append(cap)
            if bar is not None and abs((bar.value or 0) - new_val) > 0.001:
                bar.value = max(0.02, min(new_val, 1)) if not TV else new_val
                changed.append(bar)
        for c in changed:
            try:
                c.update()
            except Exception:
                pass
        return True

    def render_continue(focus=True):
        items = [h for h in history if not h.get("removed") and not watched(h)][:15]
        continue_section.visible = bool(items)
        urls = [h["url"] for h in items[:6 if TV else 15]]
        if urls and urls == st.get("continue_urls") and \
                update_continue_in_place(items, st.get("continue_cards") or []):
            if TV and focus and st.get("continue_cards") and len(page.views) <= 1 \
                    and st["mode"][0] == "home":
                page.run_task(focus_later, st["continue_cards"][0])
            return
        st["continue_urls"] = urls
        if TV:
            cards = [
                list_card({k: h.get(k, "") for k in ("url", "title", "orig", "poster", "age")},
                          caption=resume_caption(h),
                          progress=h["pos"] / h["dur"] if h.get("dur") else None,
                          autofocus=i == 0,
                          on_open=lambda h=h: page.run_task(
                              open_title, {k: h.get(k, "") for k in
                                           ("url", "title", "orig", "poster", "age")}))
                for i, h in enumerate(items[:6])]
            st["continue_cards"] = cards
            for c in cards:
                c.expand = True
            # праворуч від кожного рядка — «Прибрати» (пультом: стрілка вправо → OK)
            # рядок — у контейнері, щоб при «Прибрати» плавно стиснути його й сховати
            continue_list.controls = [
                ft.Container(ft.Row([c, tv_tap(ft.Container(
                    ft.Column([ft.Icon(ft.Icons.CLOSE_ROUNDED, size=26),
                               ft.Text("Прибрати", size=13)],
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                              alignment=ft.MainAxisAlignment.CENTER, spacing=4),
                    width=120, height=121, border_radius=14, bgcolor=SURFACE,
                    border=ft.Border.all(1, BORDER), alignment=ft.Alignment.CENTER),
                    lambda e, url=h["url"]: remove_from_history(url), radius=14,
                    focus_bg=(TV_FOCUS_BG, SURFACE))], spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    data=h["url"], height=131, padding=ft.Padding.only(bottom=10),
                    opacity=1, animate=ui.MED,
                    animate_opacity=ui.MED, clip_behavior=ft.ClipBehavior.HARD_EDGE)
                for c, h in zip(cards, items[:6])]
            try:
                continue_section.update()
            except Exception:
                pass
            # фокус — на перший пункт (autofocus у вже показаному екрані не спрацьовує,
            # і після повернення назад фокус губився)
            target = cards[0] if cards else (tv_first_tile[0] if tv_first_tile else None)
            if focus and target is not None and len(page.views) <= 1 and st["mode"][0] == "home":
                page.run_task(focus_later, target)
            return
        cards = []
        for h in items:
            item = {k: h.get(k, "") for k in ("url", "title", "orig", "poster", "age")}
            c = grid_card(item, progress=h["pos"] / h["dur"] if h.get("dur") else 0,
                          caption=resume_caption(h),
                          on_open=lambda item=item: page.run_task(open_title, item),
                          on_remove=lambda url=item["url"]: remove_from_history(url))
            ui.reveal(c)
            cards.append(ft.Container(c, width=shelf_card_w, data=item["url"],
                                      animate=ui.MED, animate_opacity=ui.FAST))
            cards[-1].refs = c.refs
        st["continue_cards"] = cards
        continue_row.controls = cards
        try:
            continue_section.update()
        except Exception:
            pass  # головна ще не на екрані

    def section_tile(name, path, icon, autofocus=False, last=False):
        # кольори тексту й іконки не задані — їх дає кнопка залежно від вибору (див. focusable)
        tile = ft.Container(
            ft.Row([ft.Icon(icon, size=28), ft.Text(name, size=18, weight=ft.FontWeight.W_600)],
                   spacing=14),
            height=76, padding=ft.Padding.symmetric(horizontal=20),
            bgcolor=SURFACE_2, border_radius=14, border=ft.Border.all(1, BORDER),
            alignment=ft.Alignment.CENTER_LEFT)

        def lit(f):
            if f and last:
                # пульт прокручує лише до краю плитки — для останнього ряду докручуємо
                # до кінця, щоб знизу лишався відступ
                page.run_task(home_content.scroll_to, offset=-1, duration=150)

        # вибрана плитка — оранжева, як кнопка «Пошук»
        btn = tv_tap(tile, lambda e: page.run_task(open_category, name, path),
                     highlight=lit if last else None,
                     radius=14, autofocus=autofocus, focus_bg=(TV_FOCUS_BG, SURFACE_2))
        btn.col = 4  # по три в ряд на всю ширину екрана
        return btn

    tv_first_tile = []  # куди ставити фокус, якщо «Продовжити перегляд» порожній

    def tv_sections():
        """Головна на телевізорі: замість банера й поличок із картками — плитки розділів
        (нічого не вантажиться, доки не відкрили розділ)."""
        blocks = []
        for gi, (group, items) in enumerate(NAV):
            tiles = [section_tile(name, path, icon, last=gi == len(NAV) - 1)
                     for name, path, icon in items]
            if not blocks:
                tv_first_tile.append(tiles[0])
            blocks.append(ft.Column([
                ft.Text(group, size=22, weight=ft.FontWeight.W_800, color=TEXT),
                ft.ResponsiveRow(tiles, spacing=16, run_spacing=16),
            ], spacing=12))
        return ft.Container(ft.Column(blocks, spacing=28),
                            padding=ft.Padding.only(left=side, right=side, top=8))

    # ── нові серії серіалів з Обраного (звіряємо з розділом сайту «Нові серії»)
    fav_new_box = ft.Column(spacing=10) if TV else ft.ListView(
        horizontal=True, spacing=16, height=shelf_card_w / POSTER_RATIO + 28,
        padding=ft.Padding.symmetric(horizontal=side, vertical=14))
    fav_new_section = ft.Column([
        ft.Container(ft.Text("Нові серії в обраному", size=20 if is_mobile else 22,
                             weight=ft.FontWeight.W_800, color=TEXT),
                     padding=ft.Padding.symmetric(horizontal=side)),
        ft.Container(fav_new_box, padding=ft.Padding.only(left=side, right=side, top=12))
        if TV else fav_new_box,
    ], spacing=0, visible=False)

    async def check_fav_new():
        favs = {f["url"]: f for f in active_favorites()}
        if not favs:
            return
        fresh = []
        for page_no in (1, 2):
            try:
                items = await asyncio.to_thread(lambda n=page_no: core.parse_catalog(
                    core.fetch(core.catalog_url("/serials/new_uaserial/", n))))
            except Exception:
                break
            fresh += [it for it in items if it["url"] in favs]
        st["fav_new"] = {it["url"] for it in fresh}
        if TV:
            fav_new_box.controls = [list_card(it, caption="Нова серія") for it in fresh[:6]]
        else:
            cards = []
            for it in fresh:
                c = grid_card(it, caption="Нова серія")
                ui.reveal(c)
                cards.append(ft.Container(c, width=shelf_card_w))
            fav_new_box.controls = cards
        fav_new_section.visible = bool(fresh)
        try:
            fav_new_section.update()
        except Exception:
            pass

    st["check_fav_new"] = check_fav_new

    # телефон: потягнути головну вниз від самого верху — оновити (плавно, без стрибків)
    refresh_ring = ft.Container(
        ft.ProgressRing(width=22, height=22, stroke_width=2.5, color=ACCENT),
        height=0, opacity=0, alignment=ft.Alignment.CENTER, animate=ui.MED,
        animate_opacity=ui.MED)
    pull = {"acc": 0.0, "busy": False}

    def home_scroll(e: ft.OnScrollEvent):
        quiet()
        release_search()
        if e.event_type == ft.ScrollType.OVERSCROLL and (e.overscroll or 0) < 0 \
                and e.pixels <= e.min_scroll_extent + 1:
            pull["acc"] += -(e.overscroll or 0)
        elif e.event_type == ft.ScrollType.END:
            if pull["acc"] > 50 and not pull["busy"]:
                page.run_task(refresh_home)
            pull["acc"] = 0.0

    async def refresh_home():
        pull["busy"] = True
        refresh_ring.height, refresh_ring.opacity = 56, 1
        refresh_ring.update()
        try:
            render_continue(focus=False)
            await asyncio.gather(load_home(), sync_now(False, True), check_fav_new(),
                                 return_exceptions=True)
        finally:
            await asyncio.sleep(0.3)
            refresh_ring.height, refresh_ring.opacity = 0, 0
            try:
                refresh_ring.update()
            except Exception:
                pass
            pull["busy"] = False

    home_content = ft.ListView(
        [continue_section, fav_new_section, tv_sections(), ft.Container(height=120)] if TV else
        [*([refresh_ring] if is_mobile else []), banner_swipe, continue_section,
         fav_new_section, *[col for col, _ in shelves], ft.Container(height=40)],
        expand=True, spacing=18 if not TV else 28,
        on_scroll=home_scroll if is_mobile else None,
        padding=ft.Padding.only(top=20) if TV else 0, key="home")

    async def load_home():
        hero["loaded"] = True
        if TV:
            return  # на телевізорі головна нічого не завантажує
        for _, load in shelves:
            page.run_task(load)
        try:
            items = await asyncio.to_thread(core.featured)
        except Exception as ex:
            hero["loaded"] = False
            msg = await explain(ex, "головну")
            layers[0].content, layers[0].opacity, layers[1].opacity = ft.Container(bgcolor=SURFACE), 1, 0
            banner_text.content = ft.Column([
                ft.Text(msg, size=16, color=TEXT),
                ft.FilledButton("Спробувати ще", icon=ft.Icons.REFRESH_ROUNDED,
                                on_click=lambda e: page.run_task(load_home))], tight=True)
            page.update()
            return
        hero["items"] = [it for it in items if it.get("poster")]
        await show_slide(0)

    def go_home():
        st["mode"] = ("home", None)
        refresh_genre_bar()
        st["filters_for"], st["filters"], st["filter_defs"] = None, {}, []
        render_filters()  # на головній фільтрів немає
        st["gen"] += 1
        drawer.selected_index = st["nav_index"] = 0
        mark_nav()
        set_heading("Головна")
        set_subheading("")
        layout_toggle.visible = False
        cat_back.visible = False
        if st.get("tv_layout"):
            st["tv_layout"].visible = False
        body.content = home_content
        page.update()
        if not hero["loaded"]:
            page.run_task(load_home)

    def on_resize(e):
        quiet()
        if st.get("player_resize"):
            st["player_resize"]()
        if st["mode"][0] == "home" and not TV:  # на телевізорі банера немає
            banner.height = banner_height()
            banner.update()

    page.on_resize = on_resize

    def focus_content():
        """TV: фокус — на перший пункт поточного вмісту головної чи розділу."""
        if st["mode"][0] == "home":
            alive_rows = [c for c in continue_list.controls if c.data]  # без прибраних (висота 0)
            target = (alive_rows[0].content.controls[0] if alive_rows
                      else tv_first_tile[0] if tv_first_tile else None)
        else:
            items = tv_cards if st["layout"] == "grid" else current_view().controls
            target = items[0] if items else None
        if target is not None:
            page.run_task(focus_later, target)

    def edge(target):
        """TV: невидима смужка біля краю екрана. Пульт «вліво» від крайньої картки
        потрапляє на ліву — і фокус одразу переходить на кнопку меню, «вправо» —
        на кнопку пошуку (самі вони нічого не відкривають)."""
        def on_focus(e):
            quiet()
            page.run_task(target().focus)

        return ft.Button(content=ft.Container(width=2), width=2, on_focus=on_focus,
                         style=ft.ButtonStyle(padding=0, elevation=0,
                                              bgcolor=ft.Colors.TRANSPARENT,
                                              overlay_color=ft.Colors.TRANSPARENT,
                                              shadow_color=ft.Colors.TRANSPARENT))

    home_body = body
    if TV:
        home_body = ft.Row([
            edge(lambda: menu_btn),
            ft.Container(body, expand=True),
            edge(lambda: search_btn),
        ], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    home = ft.View(
        route="/", padding=0, bgcolor=BG, drawer=drawer,
        controls=[safe(ft.Column([top_bar_box, recent_bar, genre_bar, filter_bar, home_body],
                                 expand=True, spacing=0))],
    )

    # «Назад» на головному екрані Android (телефон і TV): з пошуку чи розділу — на головну,
    # а вихід із застосунку — лише повторним натисканням
    async def home_back(e):
        await home.confirm_pop(False)
        if st.get("drawer_open"):  # «Назад» з відкритого меню — закрити меню
            st["drawer_open"] = False
            await home.close_drawer()
            return
        if st["mode"][0] != "home":
            search.value = ""
            clear_btn.visible = False
            go_home()
            return
        home.can_pop, home.on_confirm_pop = True, None  # наступне «Назад» закриє застосунок
        page.update()
        toast("Натисніть «Назад» ще раз, щоб вийти", ft.Icons.LOGOUT_ROUNDED)
        await asyncio.sleep(2.5)
        home.can_pop, home.on_confirm_pop = False, home_back
        page.update()

    if is_android:
        home.can_pop, home.on_confirm_pop = False, home_back

    def guard_back(view, handler=None):
        """Android: системне «Назад» спершу обробляємо самі. Інакше Flet одразу закриває
        весь екран — навіть коли поверх відкрите вікно (вибір якості в плеєрі): вікно
        лишалось «осиротілим», і застосунок закривався. Тепер відкрите вікно закриває
        сам Android (воно — верхній маршрут), а сюди приходить «Назад» для екрана."""
        if not is_android:
            return

        async def confirm(e):
            await view.confirm_pop(False)
            if page.views and page.views[-1] is view:
                (handler or go_back)()

        view.can_pop, view.on_confirm_pop = False, confirm

    def player_back():
        # відкрите вікно вибору (озвучка, субтитри…) — Esc / «Назад» закриває лише його
        pp = st.get("player_p") or {}
        if pp.get("dialog") or time.monotonic() - pp.get("dialog_closed", 0) < 0.5:
            return
        # телевізор: «Назад», поки видно панель плеєра, — лише ховає її
        if st.get("tv_back") and st["tv_back"]():
            return
        go_back()

    # ═════════════════════ налаштування ═════════════════════
    SETTINGS_GROUPS = [
        # (ключ, назва, іконка, підпис у списку)
        ("playback", "Відтворення", ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED,
         "Субтитри, гучність, якість, масштаб"),
        ("history", "Історія і синхронізація", ft.Icons.HISTORY_ROUNDED,
         "Продовжити перегляд, спільна історія між пристроями"),
        ("updates", "Оновлення", ft.Icons.SYSTEM_UPDATE_ROUNDED, "Нові версії застосунку"),
        ("about", "Про застосунок", ft.Icons.INFO_OUTLINE_ROUNDED,
         "Автор, сторінка проєкту, повідомити про проблему"),
    ]

    # ── іконка застосунку (appicon.py)
    def icon_where():
        """Де видно іконку на цій платформі (підпис рядка) або None — змінити не можна."""
        if TV:
            return "У меню телевізора"
        if is_android:
            return "На головному екрані й у списку застосунків"
        if is_mac:
            return "У Dock, Finder і Launchpad"
        if is_win:
            return "На панелі завдань, поки застосунок відкритий"
        return None

    def apply_icon(name):
        if is_android:
            page.run_task(ft.UrlLauncher().launch_url, f"uafixupdate://icon?name={name}",
                          mode=ft.LaunchMode.EXTERNAL_APPLICATION)
        elif is_mac or is_win:
            async def go():
                await asyncio.to_thread(appicon.apply, name, "macos" if is_mac else "windows",
                                        page)
                if is_win:
                    page.update()
            page.run_task(go)

    def icon_row(row):
        """Рядок налаштувань (row — будівник рядка з open_settings)."""
        names = dict(appicon.VARIANTS)
        current = ft.Text(names.get(settings["app_icon"], "Класична"), color=MUTED, size=14)

        def pick(name):
            page.pop_dialog()
            settings["app_icon"] = name
            page.run_task(prefs.set, "app_icon", name)
            current.value = names[name]
            current.update()
            apply_icon(name)
            toast("Іконка зміниться за кілька секунд" if is_android else "Іконку змінено",
                  ft.Icons.CHECK_CIRCLE_ROUNDED)

        def open_picker(e):
            tiles = []
            for key, label in appicon.VARIANTS:
                sel = key == settings["app_icon"]
                body = ft.Column([
                    ft.Container(ft.Image(src=appicon.preview(key), width=84, height=84,
                                          border_radius=20),
                                 padding=4, border_radius=24,
                                 border=ft.Border.all(3, ACCENT if sel else ft.Colors.TRANSPARENT)),
                    ft.Text(label, size=13, color=TEXT,
                            weight=ft.FontWeight.W_700 if sel else ft.FontWeight.W_500)],
                    spacing=6, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
                tiles.append(focusable(body, lambda e, k=key: pick(k), radius=24,
                                       autofocus=sel) if TV else
                             ft.Container(body, on_click=lambda e, k=key: pick(k),
                                          border_radius=24, ink=True, padding=4))
            show_dialog(ft.AlertDialog(
                title=ft.Text("Іконка застосунку", size=20, weight=ft.FontWeight.W_800,
                              color=TEXT),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([ft.Row(tiles, wrap=True, spacing=14, run_spacing=14,
                                          alignment=ft.MainAxisAlignment.CENTER),
                                   ft.Text(icon_where(), size=12, color=MUTED)],
                                  tight=True, spacing=14, width=min(480, (page.width or 520) - 60),
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                actions=[ft.TextButton("Закрити", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED))]))

        return row(ft.Icons.APPS_ROUNDED, "Іконка застосунку", icon_where(),
                   trailing=ft.Row([current, ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, color=MUTED)],
                                   spacing=4, tight=True),
                   on_click=open_picker)

    async def open_settings(group=None):
        """Налаштування: без group — список груп, з group — сторінка цієї групи."""
        def section(title, rows):
            return ft.Column([
                ft.Container(ft.Text(title.upper(), size=11, color=MUTED, weight=ft.FontWeight.W_700),
                             padding=ft.Padding.only(left=4, bottom=6), visible=bool(title)),
                # відступ зверху й знизу — інакше списки (Dropdown) впиралися в край картки
                # на телевізорі — відступ з усіх боків: вибраний рядок — окрема заокруглена
                # плашка всередині картки (без нього картка «зрізала» кути виділення)
                ft.Container(ft.Column(rows, spacing=4 if TV else 0), bgcolor=SURFACE_2,
                             border_radius=16, padding=6 if TV else ft.Padding.symmetric(vertical=6),
                             border=ft.Border.all(1, BORDER), clip_behavior=ft.ClipBehavior.HARD_EDGE),
            ], spacing=0)

        first_row = [True]
        this_view = [None]  # екран цієї сторінки налаштувань (див. reopen_settings)
        tv_focus_first = []  # autofocus у щойно відкритому екрані не спрацьовує — фокус вручну

        def row(icon, title, subtitle=None, trailing=None, on_click=None, color=TEXT):
            # на телевізорі значення змінюються натисканням OK на рядку (trailing.data —
            # обробник): випадаючі списки й перемикачі пультом незручні
            if TV and on_click is None and trailing is not None and callable(trailing.data):
                on_click = trailing.data
            sub = ft.Text(subtitle, color=MUTED, size=12) if isinstance(subtitle, str) else subtitle
            if TV:
                # рядок-кнопка з фокусом пульта; кольори основного тексту, іконки й значення не
                # задані — їх дає кнопка залежно від вибору (миттєво, див. focusable)
                plain = color == TEXT
                ic = ft.Icon(icon, color=None if plain else color)
                box = ft.Container(
                    ft.Row([ic,
                            ft.Column([ft.Text(title, color=None if plain else color, size=17,
                                               weight=ft.FontWeight.W_600)] + ([sub] if sub else []),
                                      spacing=2, expand=True, tight=True),
                            trailing or ft.Container()], spacing=16,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding.symmetric(horizontal=18, vertical=14), bgcolor=SURFACE_2)

                btn = tv_tap(box, on_click or (lambda e: None), radius=12,
                             focus_bg=(TV_FOCUS_BG, SURFACE_2))
                if first_row[0]:
                    tv_focus_first.append(btn)
                first_row[0] = False
                return btn
            autofocus = False
            tile = ft.ListTile(
                leading=ft.Icon(icon, color=ACCENT if color == TEXT else color),
                title=ft.Text(title, color=color, size=15, weight=ft.FontWeight.W_500),
                subtitle=sub,
                trailing=trailing, on_click=on_click, autofocus=autofocus,
                # власний фон — інакше підсвітка фокуса (пульт) малюється під карткою й не видна
                bgcolor=SURFACE_2,
                content_padding=ft.Padding.symmetric(horizontal=16, vertical=2))
            if autofocus:
                tv_focus_first.append(tile)
            return tile

        def tv_pick(title, options, current, on_pick):
            def pick(value):
                def handler(e):
                    page.pop_dialog()
                    on_pick(value)
                return handler

            show_dialog(ft.AlertDialog(
                title=ft.Text(title, size=22, weight=ft.FontWeight.W_800, color=TEXT),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([
                    ft.FilledButton(
                        name, icon=ft.Icons.CHECK_ROUNDED if str(v) == str(current) else None,
                        autofocus=str(v) == str(current), width=380, on_click=pick(v),
                        style=ft.ButtonStyle(
                            bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                                     ft.ControlState.DEFAULT: SURFACE_2},
                            side=TV_FOCUS_SIDE,
                            color=TV_FOCUS_TEXT, icon_color=TV_FOCUS_TEXT,
                            padding=ft.Padding.symmetric(horizontal=20, vertical=18),
                            shape=ft.RoundedRectangleBorder(radius=12),
                            text_style=ft.TextStyle(size=17, weight=ft.FontWeight.W_600)))
                    for name, v in options], tight=True, spacing=10),
            ))

        def choice(key, options, on_set=None):
            def select(e):
                value = e.control.value
                save_setting(key, float(value) if key == "sub_scale" else value)
                if on_set:
                    on_set()
            current = settings[key]
            if key == "sub_scale":
                current = next((str(v) for _, v in options if abs(v - float(current)) < 0.01), "1.0")
            if TV:
                label = ft.Text(next((n for n, v in options if str(v) == str(current)), ""),
                                size=15, weight=ft.FontWeight.W_600)  # колір — від рядка
                title = {"sub_scale": "Розмір субтитрів", "quality": "Якість відео",
                         "fit": "Масштаб на весь екран", "volume_boost": "Гучність",
                         "layout": "Вигляд розділів", "hwdec": "Декодування відео",
                         "player": "Плеєр"}.get(key, "")

                def on_pick(value):
                    save_setting(key, float(value) if key == "sub_scale" else value)
                    label.value = next(n for n, v in options if str(v) == str(value))
                    label.update()
                    if on_set:
                        on_set()

                label.data = lambda e: tv_pick(title, options, settings[key], on_pick)
                return label
            return ft.Dropdown(
                value=str(current), width=150 if is_mobile else 190, dense=True, border_radius=10, filled=True,
                fill_color=SURFACE_3, border_color=BORDER, color=TEXT, text_size=14,
                options=[ft.DropdownOption(key=str(v), text=n, style=ui.option_style())
                         for n, v in options],
                menu_style=ui.menu_style(), on_select=select)

        def switch(key, on_set=None):
            def store(value):
                save_setting(key, value)
                if on_set:
                    on_set(value)

            if TV:
                # на телевізорі — текст «Увімкнено / Вимкнено», а не оранжевий перемикач:
                # оранжевим має бути лише рядок, на якому зараз курсор пульта
                label = ft.Text(size=15, weight=ft.FontWeight.W_600)

                def look():
                    on = bool(settings[key])
                    label.value = "Увімкнено" if on else "Вимкнено"
                    label.color = None if on else MUTED  # увімкнено — колір від рядка

                def toggle(e):
                    store(not bool(settings[key]))
                    look()
                    label.update()

                focused = [False]
                label.data = toggle
                label.data_look = look  # для підсвітки рядка (row → lit)
                label.data_focused = focused
                look()
                return label
            return ft.Switch(value=bool(settings[key]), active_color=ACCENT,
                             on_change=lambda e: store(e.control.value))

        history_info = ft.Text(color=MUTED, size=12)

        def refresh_history_info():
            n = len([h for h in history if not h.get("removed")])
            history_info.value = (f"{n} {plural(n, 'запис', 'записи', 'записів')}" if n
                                  else "Порожня")

        refresh_history_info()

        def ask_clear_history(e):
            def confirm(e):
                page.pop_dialog()
                clear_history()
                refresh_history_info()
                history_info.update()
            show_dialog(ft.AlertDialog(
                title=ft.Text("Очистити історію?", weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Text("Зникне поличка «Продовжити перегляд» і збережені позиції "
                                "у фільмах та серіалах.", color=TEXT, width=dialog_width()),
                actions=[ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         ft.FilledButton("Очистити", on_click=confirm, autofocus=TV,
                                         style=ft.ButtonStyle(bgcolor="#E5484D", color="#FFFFFF"))],
            ))

        def reset_settings(e):
            for key, value in (("sub_scale", 1.0), ("fit", "auto"), ("quality", "1080"),
                               ("auto_fullscreen", True), ("volume_boost", "6"),
                               ("auto_updates", True),
                               ("keep_history", True), ("skipped_version", ""),
                               ("card_meta", True), ("hwdec", "auto"), ("player", "ask"),
                               ("abroad", True)):
                save_setting(key, value)
            for key, value in appearance.DEFAULTS.items():
                appearance.save(key, value)
            reopen_settings()
            toast("Налаштування скинуто", ft.Icons.CHECK_CIRCLE_ROUNDED)

        def open_link(url):
            page.run_task(ft.UrlLauncher().launch_url, url,
                          mode=ft.LaunchMode.EXTERNAL_APPLICATION)

        def open_feedback():
            kind = {"value": "bug"}
            kinds = ft.SegmentedButton(
                selected=["bug"], show_selected_icon=False,
                # короткі підписи без емодзі — на вузькому екрані телефона довгі розривалися
                segments=[ft.Segment(k, label=ft.Text(v, size=14, no_wrap=True,
                                                      weight=ft.FontWeight.W_600))
                          for k, v in (("bug", "Помилка"), ("idea", "Ідея"),
                                       ("review", "Відгук"))],
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=10), side=ft.BorderSide(1, BORDER),
                    bgcolor={ft.ControlState.SELECTED: ft.Colors.with_opacity(0.18, ACCENT),
                             ft.ControlState.DEFAULT: SURFACE_2},
                    color={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: TEXT}),
                on_change=lambda e: kind.update(value=e.control.selected[0]))
            text = ft.TextField(
                multiline=True, min_lines=4, max_lines=8, max_length=feedback.MAX_LEN,
                hint_text="Що сталося? Який фільм / серія, що натискали…", autofocus=not TV,
                filled=True, fill_color=SURFACE_2, cursor_color=ACCENT, text_size=14,
                border={ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                            border_radius=12, side=ft.BorderSide(1.5, ACCENT)),
                        ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                            border_radius=12, side=ft.BorderSide(1, BORDER))})
            sending = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ACCENT,
                                      visible=False)
            device = f"{platform_name or page.platform}{' (TV)' if TV else ''}"
            signature = f"UAFix Player {updater.VERSION} · {device}" + geo_note()

            async def submit(e):
                send_btn.disabled, sending.visible = True, True
                dlg.update()
                try:
                    await asyncio.to_thread(feedback.send, kind["value"], text.value, signature)
                except ValueError as ex:
                    send_btn.disabled, sending.visible = False, False
                    dlg.update()
                    toast(str(ex), ft.Icons.ERROR_OUTLINE_ROUNDED)
                    return
                page.pop_dialog()
                toast("Дякую! Повідомлення надіслано", ft.Icons.CHECK_CIRCLE_ROUNDED)

            send_btn = ft.FilledButton("Надіслати", icon=ft.Icons.SEND_ROUNDED, on_click=submit,
                                       style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF",
                                                            icon_color="#FFFFFF"))
            dlg = ft.AlertDialog(
                title=ft.Text("Повідомити про проблему", weight=ft.FontWeight.W_800,
                              size=20 if is_mobile else 24),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18), scrollable=True,
                content=ft.Column([
                    kinds, text,
                    ft.Text(f"До повідомлення додається: {signature}", size=11, color=MUTED),
                ], tight=True, spacing=12, width=dialog_width()),
                actions=[sending,
                         ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         send_btn],
            )
            show_dialog(dlg)

        def sync_rows():
            code = settings["sync_code"]
            if not code:
                return [
                    row(ft.Icons.CLOUD_SYNC_ROUNDED, "Увімкнути синхронізацію",
                        "Історія, хвилина, де зупинились, і обране — спільні на телефоні, "
                        "комп'ютері й телевізорі", on_click=lambda e: sync_enable()),
                    row(ft.Icons.PASSWORD_ROUNDED, "Ввести код з іншого пристрою",
                        "Якщо синхронізацію вже увімкнено на іншому пристрої",
                        on_click=lambda e: sync_enter_code()),
                ]
            last_label = ft.Text(sync_last_text(), color=MUTED, size=12)
            sync_state["label"] = last_label  # оновлюється після кожної синхронізації
            return [
                row(ft.Icons.KEY_ROUNDED, f"Код: {sync.pretty(code)}",
                    "Введіть цей код на інших своїх пристроях", on_click=lambda e: sync_show_code()),
                row(ft.Icons.SYNC_ROUNDED, "Синхронізувати зараз", last_label,
                    on_click=lambda e: page.run_task(sync_now, True)),
                row(ft.Icons.CLOUD_OFF_ROUNDED, "Вимкнути на цьому пристрої",
                    "Історія на цьому пристрої залишиться", on_click=lambda e: sync_disable()),
            ]

        def reopen_settings():
            # лише якщо ця сторінка ще відкрита — швидкі повторні натискання інакше закривали
            # зайві екрани (вікно з кодом опинялось на головній)
            if page.views and page.views[-1] is this_view[0]:
                pop_view()
                page.run_task(open_settings, group)

        def sync_show_code():
            show_dialog(ft.AlertDialog(
                title=ft.Text("Код синхронізації", weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([
                    ft.Container(ft.Text(sync.pretty(settings["sync_code"]), size=34,
                                         weight=ft.FontWeight.W_800, color=ACCENT,
                                         selectable=True, font_family="monospace"),
                                 alignment=ft.Alignment.CENTER, padding=10),
                    ft.Text("На іншому пристрої: Налаштування → Синхронізація → «Ввести код з "
                            "іншого пристрою». Порада: на телевізорі код зручніше показати, "
                            "а ввести — на телефоні.", color=MUTED, size=13),
                ], tight=True, spacing=8, width=dialog_width()),
                actions=[ft.FilledButton("Готово", on_click=lambda e: page.pop_dialog(),
                                         style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF"))],
            ))

        def sync_enable():
            if settings["sync_code"]:  # уже ввімкнено (повторне натискання) — лише показати код
                sync_show_code()
                return
            save_setting("sync_code", sync.new_code())
            sync_state["dirty"] = True
            page.run_task(sync_now, True)
            reopen_settings()
            sync_show_code()

        def sync_enter_code():
            err = ft.Text("", color="#E5484D", size=12, visible=False)

            def submit(e=None):
                code = sync.normalize(field.value)
                if not code:
                    err.value, err.visible = "Код має 12 символів, наприклад K7QF-29XM-PL4D", True
                    err.update()
                    return
                page.pop_dialog()
                save_setting("sync_code", code)
                sync_state["dirty"] = True
                page.run_task(sync_now, True)  # своє + з інших пристроїв, злите
                reopen_settings()

            field = ft.TextField(hint_text="XXXX-XXXX-XXXX", autofocus=True, text_size=22,
                                 capitalization=ft.TextCapitalization.CHARACTERS,
                                 filled=True, fill_color=SURFACE_2, cursor_color=ACCENT,
                                 on_submit=submit, width=dialog_width())
            show_dialog(ft.AlertDialog(
                title=ft.Text("Код з іншого пристрою", weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([field, err], tight=True, spacing=8),
                actions=[ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         ft.FilledButton("Підключити", on_click=submit,
                                         style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF"))],
            ))

        def sync_disable():
            def confirm(e):
                page.pop_dialog()
                save_setting("sync_code", "")
                reopen_settings()
                toast("Синхронізацію на цьому пристрої вимкнено", ft.Icons.CLOUD_OFF_ROUNDED)

            show_dialog(ft.AlertDialog(
                title=ft.Text("Вимкнути синхронізацію?", weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Text("Цей пристрій перестане обмінюватися історією з іншими. "
                                "Те, що вже є тут, залишиться.", color=TEXT, width=dialog_width()),
                actions=[ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         ft.FilledButton("Вимкнути", on_click=confirm,
                                         style=ft.ButtonStyle(bgcolor="#E5484D", color="#FFFFFF"))],
            ))

        def issue_url():
            device = f"{platform_name or page.platform}{' (TV)' if TV else ''}"
            body = (f"**Версія:** {updater.VERSION}\n**Пристрій:** {device}{geo_note()}\n\n"
                    "**Що сталося:**\n\n\n**Як повторити (фільм / серія, що натискали):**\n\n\n"
                    "**Скріншот або відео** (можна перетягнути сюди):\n")
            return (f"https://github.com/{updater.REPO}/issues/new?"
                    + urlencode({"title": "Проблема: ", "body": body, "labels": "bug"}))

        fit_options = [("Авто", "auto")] + [(n, k) for k, n, _ in FIT_MODES]

        def appearance_choice(key, options):
            """Як choice, але значення — у файлі вигляду (діє після перезапуску)."""
            def on_pick(value):
                if value == appearance.load()[key]:
                    return
                appearance.save(key, value)

                async def apply():
                    await asyncio.sleep(0.2)  # дати списку закритися
                    st["rebuild_with_theme"]()

                page.run_task(apply)

            current = appearance.load()[key]
            if TV:
                label = ft.Text(next(n for n, v in options if v == current), size=15,
                                weight=ft.FontWeight.W_600)

                def pick_tv(value):
                    label.value = next(n for n, v in options if v == value)
                    label.update()
                    on_pick(value)

                title_ = {"theme": "Тема", "focus_style": "Виділення"}[key]
                label.data = lambda e: tv_pick(title_, options, appearance.load()[key], pick_tv)
                return label
            return ft.Dropdown(
                value=current, width=150 if is_mobile else 190, dense=True, border_radius=10,
                filled=True, fill_color=SURFACE_3, border_color=BORDER, color=TEXT, text_size=14,
                options=[ft.DropdownOption(key=v, text=n, style=ui.option_style())
                         for n, v in options],
                menu_style=ui.menu_style(), on_select=lambda e: on_pick(e.control.value))

        groups = {  # будуються лише при відкритті своєї сторінки
            "playback": lambda: [section("Відтворення", [
                row(ft.Icons.SUBTITLES_ROUNDED, "Розмір субтитрів",
                    trailing=choice("sub_scale", SUB_SIZES)),
                row(ft.Icons.VOLUME_UP_ROUNDED, "Гучність",
                    "Підсилення звуку фільмів (діє з наступного запуску відео)",
                    trailing=choice("volume_boost", [("Звичайна", "0"), ("Голосніше", "6"),
                                                     ("Максимум", "10")])),
                row(ft.Icons.HIGH_QUALITY_ROUNDED, "Якість відео",
                    "Якщо такої немає — найближча нижча. Можна змінити й під час перегляду",
                    trailing=choice("quality", [("1080p", "1080"), ("720p", "720"),
                                                ("480p", "480")])),
                *([row(ft.Icons.SCREEN_ROTATION_ROUNDED, "Повний екран при повороті",
                       "Поверніть телефон горизонтально — відео на весь екран",
                       trailing=switch("auto_fullscreen"))] if is_mobile else []),
                row(ft.Icons.ASPECT_RATIO_ROUNDED, "Масштаб на весь екран",
                    "Авто: на телефоні — заповнити, на комп'ютері — вписати",
                    trailing=choice("fit", fit_options)),
                *([row(ft.Icons.SMART_DISPLAY_OUTLINED, "Плеєр",
                       "Інша програма: VLC, MX Player… (вибір під час запуску). Прогрес "
                       "перегляду тоді не запам'ятовується",
                       trailing=choice("player", PLAYER_OPTIONS))]
                  if is_android or TV else []),
                row(ft.Icons.MEMORY_ROUNDED, "Декодування відео",
                    "Якщо картинка тремтить чи звук випереджає відео — спробуйте «Сумісне» "
                    "або якість 720p (діє з наступного запуску відео)",
                    trailing=choice("hwdec", [("Апаратне", "auto"), ("Сумісне", "copy"),
                                              ("Програмне", "off")])),
                row(ft.Icons.FAST_FORWARD_ROUNDED, "Пропускати заставку UAFLIX",
                    "Відео починається одразу з фільму чи серії (заставка — 16 с)",
                    trailing=switch("skip_intro")),
            ])],
            "history": lambda: [section("Історія переглядів", [
                row(ft.Icons.HISTORY_ROUNDED, "Запам'ятовувати, де зупинились",
                    "Поличка «Продовжити перегляд» на головній", trailing=switch("keep_history")),
                row(ft.Icons.DELETE_OUTLINE_ROUNDED, "Очистити історію", history_info,
                    on_click=ask_clear_history, color="#E5484D"),
            ]),
            section("Синхронізація", sync_rows())],
            "updates": lambda: [section("Оновлення", [
                row(ft.Icons.UPDATE_ROUNDED, "Перевіряти автоматично",
                    "При запуску, не частіше ніж раз на 6 годин", trailing=switch("auto_updates")),
                row(ft.Icons.SYSTEM_UPDATE_ROUNDED, "Перевірити оновлення",
                    f"Встановлено версію {updater.VERSION}",
                    on_click=lambda e: page.run_task(check_updates, True)),
                row(ft.Icons.NEW_RELEASES_OUTLINED, "Що нового в цій версії",
                    on_click=lambda e: page.run_task(show_release_notes, True)),
            ])],
            "about": lambda: [section("Про застосунок", [
                row(ft.Icons.PERSON_OUTLINE_ROUNDED, "Автор", "Roman Minko"),
                row(ft.Icons.CODE_ROUNDED, "Сторінка проєкту", "GitHub: усі версії й зміни",
                    on_click=lambda e: open_link(f"https://github.com/{updater.REPO}/releases")),
                row(ft.Icons.BUG_REPORT_OUTLINED, "Повідомити про проблему",
                    "Помилка, побажання чи відгук — напряму автору" if feedback.enabled()
                    else "Відкриє форму на GitHub з даними про версію й пристрій",
                    on_click=lambda e: open_feedback() if feedback.enabled()
                    else open_link(issue_url())),
                row(ft.Icons.GAVEL_ROUNDED, "Застереження",
                    "Неофіційний некомерційний застосунок, не пов'язаний з uafix.net чи UAflix. "
                    "Відео не зберігає й не розповсюджує — лише показує те, що сайт відкрито "
                    "публікує. Права на фільми належать правовласникам, відповідальність за "
                    "вміст — на сайті, що його розміщує.",
                    on_click=lambda e: open_link(
                        f"https://github.com/{updater.REPO}#застереження")),
                row(ft.Icons.IMAGE_OUTLINED, "Кадри фільмів — TMDB",
                    "Цей застосунок використовує API TMDB, але не схвалений і не сертифікований TMDB",
                    on_click=lambda e: open_link("https://www.themoviedb.org")),
                row(ft.Icons.RESTART_ALT_ROUNDED, "Скинути налаштування", on_click=reset_settings),
            ])],
        }
        footer = ft.Text(f"UAFix Player {updater.VERSION} · автор — Roman Minko · вміст — uafix.net · кадри — TMDB",
                         size=12, color=MUTED, text_align=ft.TextAlign.CENTER)
        if group:
            sections = groups[group]() + [footer]
            heading = next(t for k, t, _, _ in SETTINGS_GROUPS if k == group)
        else:
            sections = [section("", [
                row(ft.Icons.DARK_MODE_OUTLINED, "Тема", "Світла чи темна",
                    trailing=appearance_choice("theme", [("Темна", "dark"), ("Світла", "light")])),
                row(ft.Icons.GRID_VIEW_ROUNDED, "Вигляд розділів", "Картки чи список",
                    trailing=choice("layout", [("Картки", "grid"), ("Список", "list")],
                                    on_set=apply_layout_default)),
                row(ft.Icons.STAR_OUTLINE_ROUNDED, "Рейтинг і жанр на картках",
                    "Оцінка й жанри з TMDB — одразу в списках, без відкриття фільму",
                    trailing=switch("card_meta")),
                row(ft.Icons.PUBLIC_ROUNDED, "Перегляд з-за кордону",
                    "Якщо плеєр недоступний у вашій країні — застосунок сам відкриє його через "
                    "український сервер (лише плеєр, відео — напряму)",
                    trailing=switch("abroad", on_set=lambda v: core.ABROAD.update(enabled=v))),
                *([icon_row(row)] if icon_where() else []),
            ]), section("", [
                row(icon, title, subtitle,
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, color=MUTED),
                    on_click=lambda e, k=k: page.run_task(open_settings, k))
                for k, title, icon, subtitle in SETTINGS_GROUPS]), footer]
            heading = "Налаштування"
        bar = ft.Container(
            ft.Row([round_button(ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED, lambda e: go_back(),
                                 tooltip="Назад"),
                    ft.Text(heading, size=20, weight=ft.FontWeight.W_800, color=TEXT,
                            expand=True), *win_buttons()],
                   spacing=14),
            height=58, padding=ft.Padding.only(left=14 + traffic_gap, right=8 if is_win else 14),
            bgcolor=BG,
        )
        content = ui.appear(ft.Column(sections, spacing=22))
        body_col = ft.ListView(
            [ft.Row([ft.Container(content,
                                  width=min(640, (page.width or 672) - 32))],
                    alignment=ft.MainAxisAlignment.CENTER)],
            expand=True, padding=ft.Padding.only(top=8, bottom=32))
        this_view[0] = ft.View(
            route=f"/settings/{group or ''}", padding=0, bgcolor=BG,
            controls=[safe(ft.Column([drag_area(bar), body_col], expand=True, spacing=0))],
        )
        guard_back(this_view[0])
        page.views.append(this_view[0])
        cover_views()
        page.update()
        await asyncio.sleep(0.05)
        ui.reveal(content)
        content.update()
        if tv_focus_first:
            await focus_later(tv_focus_first[0])

    # ═════════════════════ сторінка тайтлу ═════════════════════
    # ── актори з фото (TMDB): ряд на сторінці тайтлу й картка актора з відомими роботами
    def _latin_or_cyr(name):
        return bool(re.search(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]", name or ""))

    async def load_cast(item, info, cast_text, row):
        try:
            people = await asyncio.to_thread(
                tmdb.cast, item["url"], info.get("title") or item.get("title", ""),
                info.get("orig") or item.get("orig", ""), info.get("year", ""))
        except Exception:
            return
        if not people:
            return
        site = info.get("actors") or []
        for i, a in enumerate(people):  # ім'я корейською/японською — краще як на сайті
            if not _latin_or_cyr(a["name"]) and i < len(site):
                a["name"] = site[i]
        row.controls = [actor_tile(a) for a in people]
        row.visible, cast_text.visible = True, False
        try:
            row.update()
            cast_text.update()
        except Exception:
            pass

    def actor_tile(a):
        photo = (ft.Image(src=a["photo"], width=72, height=72, fit=ft.BoxFit.COVER,
                          border_radius=36, gapless_playback=True)
                 if a["photo"] else
                 ft.Container(ft.Icon(ft.Icons.PERSON_ROUNDED, color=MUTED, size=34),
                              width=72, height=72, border_radius=36, bgcolor=SURFACE_2,
                              alignment=ft.Alignment.CENTER))
        photo_box = ft.Container(photo, padding=3 if TV else 0, shape=ft.BoxShape.CIRCLE,
                                 border=ft.Border.all(3, ft.Colors.TRANSPARENT) if TV else None)
        body = ft.Container(
            ft.Column([photo_box,
                       ft.Text(a["name"], size=12, weight=ft.FontWeight.W_600, color=TEXT,
                               max_lines=2, text_align=ft.TextAlign.CENTER, width=92),
                       ft.Text(a["character"], size=11, color=MUTED, max_lines=1,
                               overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER,
                               width=92)],
                      spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
            width=96, padding=ft.Padding.symmetric(vertical=6))
        open_ = lambda e, a=a: page.run_task(open_actor, a)  # noqa: E731
        if TV:  # пультом — лише кнопка отримує фокус; рамка — навколо фото
            def ring(f):
                photo_box.border = ft.Border.all(3, focus_ring if f else ft.Colors.TRANSPARENT)
                photo_box.update()
            btn = focusable(body, open_, on_focus_change=ring, radius=12)
            btn.style.side = None
            return btn
        return ft.Container(body, border_radius=12, ink=True, on_click=open_)

    async def open_actor(a):
        """Картка актора: фото, коротко про нього й відомі роботи (натиснути — знайти на сайті)."""
        try:
            pers = await asyncio.to_thread(tmdb.person, a["id"])
        except Exception:
            toast("Не вдалося завантажити дані про актора", ft.Icons.ERROR_OUTLINE_ROUNDED)
            return
        name = a["name"] if not _latin_or_cyr(pers["name"]) else pers["name"]

        def work_card(w):
            img = (ft.Image(src=w["poster"], width=110, height=165, fit=ft.BoxFit.COVER,
                            border_radius=10) if w["poster"] else
                   ft.Container(width=110, height=165, border_radius=10, bgcolor=SURFACE_2))
            # TV: рамка фокуса — лише навколо постера, із зазором
            ring_box = ft.Container(img, padding=3 if TV else 0, border_radius=14,
                                    border=ft.Border.all(3, ft.Colors.TRANSPARENT) if TV else None)
            body = ft.Column([ring_box, ft.Text(w["title"], size=12, color=TEXT, max_lines=2,
                                                width=110, weight=ft.FontWeight.W_600),
                              ft.Text(w["year"], size=11, color=MUTED)], spacing=4, tight=True)
            go = lambda e, w=w: search_title(w["title"] or w["orig"])  # noqa: E731
            if TV:
                def ring(f, box=ring_box):
                    box.border = ft.Border.all(3, focus_ring if f else ft.Colors.TRANSPARENT)
                    box.update()
                btn = focusable(body, go, on_focus_change=ring, radius=14)
                btn.style.side = None
                return btn
            return ft.Container(body, width=110, border_radius=10, ink=True, on_click=go)

        src = pers["photo"] or a["photo"]
        bio = pers["bio"] or "Біографії немає"
        works = ft.Row([work_card(w) for w in pers["works"]], spacing=12, scroll=ft.ScrollMode.AUTO)
        hint = ft.Text("Відомі роботи — натисніть, щоб знайти на сайті", size=13, color=MUTED)
        if is_mobile:
            # телефон: фото й ім'я вгорі, під ними — уся біографія; вікно прокручується
            head = ft.Row([
                ft.Image(src=src, width=84, height=112, fit=ft.BoxFit.COVER, border_radius=10)
                if src else ft.Container(),
                ft.Text(name, size=20, weight=ft.FontWeight.W_800, color=TEXT, expand=True)],
                spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            body = ft.Column([head, ft.Text(bio, size=13, color=MUTED), hint, works],
                             spacing=12, tight=True, scroll=ft.ScrollMode.AUTO,
                             height=(page.height or 700) - 230, width=(page.width or 400) - 60)
        else:
            photo = (ft.Image(src=src, width=110, height=150, fit=ft.BoxFit.COVER,
                              border_radius=12) if src else ft.Container())
            text = ft.Column([ft.Text(name, size=20, weight=ft.FontWeight.W_800, color=TEXT),
                              ft.Text(bio, size=13, color=MUTED)],
                             spacing=6, scroll=ft.ScrollMode.AUTO, height=170, expand=True)
            body = ft.Column([
                ft.Row([photo, text], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START),
                hint, works,
            ], tight=True, spacing=14, width=min(640, (page.width or 700) - 60))
        show_dialog(ft.AlertDialog(
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18), content=body,
            actions=[ft.TextButton("Закрити", on_click=lambda e: page.pop_dialog(),
                                   style=ft.ButtonStyle(color=MUTED))]))

    # ── франшиза: частини по порядку (TMDB) → ті самі фільми на сайті
    async def load_saga(item, info, title, row, section):
        try:
            saga = await asyncio.to_thread(
                tmdb.collection, item["url"], info.get("title") or item.get("title", ""),
                info.get("orig") or item.get("orig", ""), info.get("year", ""))
        except Exception:
            return
        if not saga:
            return
        # франшиза є — поки шукаємо частини на сайті (кілька секунд), показуємо заготовки
        title.value = f"Франшиза «{saga['name']}»" if saga["name"] else "Франшиза"
        row.controls = [ft.Container(shimmer(ft.Column([
            ft.Container(aspect_ratio=2 / 3, bgcolor=SURFACE_2, border_radius=10),
            ft.Container(height=12, bgcolor=SURFACE_2, border_radius=4),
            ft.Container(height=10, width=40, bgcolor=SURFACE_2, border_radius=4)],
            spacing=6, tight=True), base_color=SURFACE_2, highlight_color=SURFACE_3),
            col={"xs": 4}, padding=3 if TV else 0) for _ in saga["parts"][:9]]
        section.visible = True
        ui.reveal(section)
        try:
            section.update()
        except Exception:
            pass

        def lookup(p):
            if not p["released"] and not p["poster"]:
                return None
            try:
                return core.find_title(p["orig"], p["title"])
            except Exception:
                return None
        found = await asyncio.gather(*[asyncio.to_thread(lookup, p) for p in saga["parts"]])
        if not any(found):  # жодної частини на сайті — розділ не потрібен
            section.visible = False
            try:
                section.update()
            except Exception:
                pass
            return
        row.controls = [saga_card(i + 1, p, site, p["id"] == saga["current"] or
                                  (site or {}).get("url") == item["url"])
                        for i, (p, site) in enumerate(zip(saga["parts"], found))]
        try:
            section.update()
        except Exception:
            pass

    def show_reviews(comments, title, box, section, limit=4):
        """Відгуки з сайту: спершу найпопулярніші (за «вподобайками»), решта — за кнопкою."""
        if not comments:
            return
        ranked = sorted(comments, key=lambda c: -c["likes"])
        title.value = f"Відгуки глядачів · {len(comments)}"

        def card(c):
            staff = "адмін" in c["role"].lower()
            head = ft.Row([
                ft.Container(ft.Text((c["author"][:1] or "?").upper(), size=14,
                                     weight=ft.FontWeight.W_800, color="#FFFFFF"),
                             width=32, height=32, border_radius=16,
                             bgcolor=ACCENT if staff else SURFACE_3,
                             alignment=ft.Alignment.CENTER),
                ft.Column([ft.Text(c["author"] + (" · сайт" if staff else ""), size=13,
                                   weight=ft.FontWeight.W_700, color=TEXT),
                           ft.Text(c["date"], size=11, color=MUTED)], spacing=0, tight=True,
                          expand=True),
                *([ft.Row([ft.Icon(ft.Icons.THUMB_UP_ALT_ROUNDED, size=14, color=MUTED),
                           ft.Text(str(c["likes"]), size=12, color=MUTED)], spacing=4,
                          tight=True)] if c["likes"] else []),
            ], spacing=10)
            body = ft.Container(
                ft.Column([head, ft.Text(c["text"], size=14, color=TEXT, selectable=not TV)],
                          spacing=8, tight=True),
                padding=14, border_radius=14, bgcolor=SURFACE, border=ft.Border.all(1, BORDER))
            # TV: до відгуків пульт «доходить», лише якщо вони фокусуються
            return focusable(body, lambda e: None, radius=14) if TV else body

        def fill(n):
            box.controls = [card(c) for c in ranked[:n]]
            if len(ranked) > n:
                more = ft.TextButton(f"Показати всі ({len(ranked)})",
                                     style=ft.ButtonStyle(color=ACCENT,
                                                          side=TV_FOCUS_SIDE if TV else None),
                                     on_click=lambda e: (quiet(), fill(len(ranked)),
                                                         box.update()))
                box.controls.append(more)

        fill(limit)
        section.visible = True
        ui.reveal(section)

    def saga_card(num, p, site, current):
        """Частина франшизи: номер, постер, назва, рік. Немає на сайті — притушена."""
        # вертикальний постер TMDB (без написів сайту й не обрізаний); сайт — лише якщо нема
        src = p["poster"] or (site or {}).get("poster")
        layers = [
            ui.poster_image(src, radius=10, aspect_ratio=2 / 3),
            ft.Container(
                ft.Text(str(num), size=13, weight=ft.FontWeight.W_800, color="#FFFFFF"),
                bgcolor=ACCENT if current else ft.Colors.with_opacity(0.75, "#000000"),
                border_radius=8, padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                left=6, top=6),
        ]
        if current:
            layers.append(ft.Container(border_radius=10, border=ft.Border.all(3, ACCENT),
                                       left=0, top=0, right=0, bottom=0))
        if site and current:
            note, color = "Ви тут", ACCENT
        elif site:
            note, color = p["year"], MUTED
        elif not p["released"]:
            note, color = f"Скоро · {p['year']}" if p["year"] else "Скоро", MUTED
        else:
            note, color = "Немає на сайті", MUTED
        # TV: рамка фокуса — лише навколо постера, із зазором (як у картках каталогу),
        # а не довкола всієї картки з підписом
        ring_box = ft.Container(ft.Stack(layers), padding=3 if TV else 0, border_radius=14,
                                border=ft.Border.all(3, ft.Colors.TRANSPARENT) if TV else None)
        body = ft.Column([
            ring_box,
            ft.Text((site or {}).get("title") or p["title"], size=12, color=TEXT, max_lines=2,
                    weight=ft.FontWeight.W_600, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(note, size=11, color=color),
        ], spacing=4, tight=True, opacity=1 if site else 0.45)
        col = {"xs": 4}
        if not site or current:
            return ft.Container(body, col=col)
        go = lambda e, it=site: page.run_task(open_title, it)  # noqa: E731
        if TV:
            def ring(f):
                ring_box.border = ft.Border.all(3, focus_ring if f else ft.Colors.TRANSPARENT)
                ring_box.update()
            btn = focusable(body, go, on_focus_change=ring, radius=14)
            btn.style.side = None
            btn.col = col
            return btn
        return ft.Container(body, col=col, border_radius=10, ink=True, on_click=go)

    def search_title(q):
        """Знайти тайтл на сайті: закрити вікно, повернутись на головну й шукати."""
        page.pop_dialog()
        while len(page.views) > 1 and page.views[-1].route != "/play":
            pop_view()
        search.value = q
        clear_btn.visible = True
        page.run_task(do_search, q)

    def geo_note():
        """Для звіту про проблему: чи застосунок за кордоном і як справи з обходом."""
        g, a = core.GEO, core.ABROAD
        if not (g["site"] or g["blocked"]):
            return ""
        return (f" · за кордоном (сайт: {'так' if g['site'] else 'ні'}, ashdi: "
                f"{'так' if g['blocked'] else 'ні'}, обхід: "
                f"{'вимкнено' if not a['enabled'] else len(a.get('pool', []))} проксі)")

    async def abroad_hint(loading):
        """За кордоном перший фільм відкривається довше (застосунок шукає український
        сервер) — пояснюємо один раз, щоб не здавалося, що все зависло."""
        for _ in range(40):
            await asyncio.sleep(0.25)
            if not loading["on"] or st.get("abroad_hinted"):
                return
            if core.GEO["site"] and not core.ABROAD.get("pool"):
                st["abroad_hinted"] = True
                toast("Ви за кордоном — підключаюсь через український сервер. Перший раз це "
                      "до пів хвилини", ft.Icons.PUBLIC_ROUNDED)
                return

    async def open_title(item, autoplay=False, resume=False):
        release_search()
        ctx = {"item": item, "info": None, "seasons": [], "season": 0}

        def backdrop_image(src):
            # «Розмиття» без BackdropFilter: картинка декодується крихітною (36 px) і плавно
            # розтягується. BackdropFilter на Android збивався при прокрутці (то розмито,
            # то ні) і відбирав кадри — цей спосіб стабільний і майже нічого не коштує.
            if not src:
                return None
            return ft.Image(src=src, fit=ft.BoxFit.COVER, opacity=0.45, cache_width=36,
                            filter_quality=ft.FilterQuality.HIGH, gapless_playback=True)

        backdrop = ft.Container(tmdb_swap(backdrop_image(item["poster"]), item, "w300"),
                                left=0, top=0, right=0, height=640,
                                clip_behavior=ft.ClipBehavior.HARD_EDGE)
        backdrop_fade = ft.Container(
            left=0, top=0, right=0, height=640,
            gradient=ft.LinearGradient(
                begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                colors=[ft.Colors.with_opacity(0.3, BG), ft.Colors.with_opacity(0.75, BG), BG],
                stops=[0, 0.55, 1]),
        )

        poster = ft.Container(
            tmdb_swap(ui.poster_image(item["poster"], radius=18, aspect_ratio=POSTER_RATIO),
                      item, "w1280"),
            border_radius=18,
            shadow=ft.BoxShadow(blur_radius=50, offset=ft.Offset(0, 24),
                                color=ft.Colors.with_opacity(0.6, "#000000")),
            scale=0.92, animate_scale=ui.SLOW,
        )
        title = ft.Text(item["title"], size=26 if is_mobile else 36, weight=ft.FontWeight.W_800,
                        color=TEXT)
        orig = ft.Text(item["orig"], size=16, color=MUTED, visible=bool(item["orig"]))
        meta_row = ft.Row([ui.pill(item["age"])] if item["age"] else [], spacing=8, wrap=True)
        genres_row = ft.Row([], spacing=8, wrap=True)
        description = ft.Text("", size=14, color="#3F4550" if ui.LIGHT else "#C9CDD4", max_lines=4,
                              overflow=ft.TextOverflow.ELLIPSIS, selectable=True)
        # відступи всередині — інакше підсвітка (фокус/наведення) йшла впритул і букви вилазили
        more_btn = ft.TextButton("Детальніше", visible=False,
                                 style=ft.ButtonStyle(
                                     color=ACCENT, padding=ft.Padding.symmetric(horizontal=12,
                                                                               vertical=6),
                                     shape=ft.RoundedRectangleBorder(radius=10),
                                     side=TV_FOCUS_SIDE if TV else None),
                                 # зсув назад на величину відступу: текст — на одній лінії з описом
                                 offset=ft.Offset(-0.1, 0))

        def toggle_desc(e):
            expanded = description.max_lines is None
            description.max_lines = 4 if expanded else None
            more_btn.content = "Детальніше" if expanded else "Згорнути"
            view.update()

        more_btn.on_click = toggle_desc
        cast = ft.Text("", size=12, color=MUTED, visible=False)
        # актори з фото (TMDB) — замість рядка «У ролях», коли знайдуться
        cast_row = ft.Row([], spacing=14, scroll=ft.ScrollMode.HIDDEN, visible=False)

        # поки шукаємо відео (буває довго) — на кнопці видно, що треба зачекати
        # головна дія — оранжева: «Дивитись», якщо ще не дивились, інакше «Продовжити»
        # (тоді «Дивитись» — біла)
        def orange_style(pad=26):
            return ft.ButtonStyle(
                bgcolor={ft.ControlState.DISABLED: SURFACE_3, ft.ControlState.DEFAULT: ACCENT},
                color={ft.ControlState.DISABLED: MUTED, ft.ControlState.DEFAULT: "#FFFFFF"},
                icon_color={ft.ControlState.DISABLED: MUTED, ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=pad, vertical=18),
                text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700),
                elevation={ft.ControlState.HOVERED: 10, ft.ControlState.DEFAULT: 0},
                shadow_color=ACCENT,
                side={ft.ControlState.FOCUSED: ft.BorderSide(3, "#FFFFFF"),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)})

        def white_style(pad=22):
            return ft.ButtonStyle(
                bgcolor={ft.ControlState.DISABLED: SURFACE_3, ft.ControlState.HOVERED: "#FFFFFF",
                         ft.ControlState.DEFAULT: "#ECECEC"},
                color={ft.ControlState.DISABLED: MUTED, ft.ControlState.DEFAULT: "#0D0E12"},
                icon_color={ft.ControlState.DISABLED: MUTED, ft.ControlState.DEFAULT: "#0D0E12"},
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=pad, vertical=18),
                text_style=ft.TextStyle(size=15, weight=ft.FontWeight.W_700),
                side={ft.ControlState.FOCUSED: ft.BorderSide(3, ACCENT),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)})

        play_btn = ft.FilledButton(
            ft.Row([ft.ProgressRing(width=16, height=16, stroke_width=2, color=MUTED),
                    ft.Text("Зачекайте, шукаю відео…")], spacing=10, tight=True),
            disabled=True, autofocus=TV, style=orange_style(),
            on_click=lambda e: page.run_task(play_episode, ctx, ctx["season"], 0),
        )
        secondary = ft.ButtonStyle(
            color=TEXT, icon_color=TEXT,
            bgcolor={ft.ControlState.HOVERED: SURFACE_3, ft.ControlState.FOCUSED: SURFACE_3,
                     ft.ControlState.DEFAULT: SURFACE_2},
            side={ft.ControlState.FOCUSED: ft.BorderSide(3, ACCENT),
                  ft.ControlState.DEFAULT: ft.BorderSide(1, BORDER)}, shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.Padding.symmetric(horizontal=18, vertical=18),
        )
        resume_btn = ft.FilledButton(
            "Продовжити", icon=ft.Icons.PLAY_ARROW_ROUNDED, visible=False, style=orange_style(22))

        def update_resume():
            """Кнопка «Продовжити · С1 · Серія 3 · 23:14», якщо тайтл уже почали дивитись."""
            h = history_entry(item["url"])
            spot = find_episode(ctx, h) if (h and ctx["seasons"] and not watched(h)) else None
            resume_btn.visible = spot is not None
            # оранжева — головна дія: «Продовжити», якщо вже почали, інакше «Дивитись»
            play_btn.style = white_style(26) if spot is not None else orange_style()
            if not play_btn.disabled:  # поки шукаємо відео — на кнопці «Зачекайте…»
                # продовжити — ▶ (на оранжевій), дивитись спочатку — кругла стрілка
                play_btn.content = "Дивитись спочатку" if spot is not None else "Дивитись"
                play_btn.icon = (ft.Icons.REPLAY_ROUNDED if spot is not None
                                 else ft.Icons.PLAY_ARROW_ROUNDED)
            if spot:
                resume_btn.content = f"Продовжити · {resume_caption(h)}"
                resume_btn.on_click = lambda e: page.run_task(resume_watching)
            return spot

        async def resume_watching():
            h = history_entry(item["url"])
            spot = find_episode(ctx, h) if h else None
            if not spot:
                await play_episode(ctx, ctx["season"], 0)
                return
            if h.get("voice") in ctx["voices"]:
                ctx["voice"] = h["voice"]
            await play_episode(ctx, *spot, start_ms=h["pos"])

        trailer_btn = ft.OutlinedButton("Трейлер", icon=ft.Icons.SMART_DISPLAY_ROUNDED,
                                        style=secondary, visible=False)

        def fav_look():
            on = is_favorite(item["url"])
            fav_btn.content = "В обраному" if on else "В обране"
            fav_btn.icon = ft.Icons.BOOKMARK_ROUNDED if on else ft.Icons.BOOKMARK_BORDER_ROUNDED

        def on_fav(e):
            added = toggle_favorite(item)
            fav_look()
            fav_btn.update()
            toast("Додано в обране" if added else "Прибрано з обраного",
                  ft.Icons.BOOKMARK_ROUNDED if added else ft.Icons.BOOKMARK_BORDER_ROUNDED)

        fav_btn = ft.OutlinedButton("В обране", style=secondary, on_click=on_fav)
        fav_look()
        site_btn = ft.IconButton(
            ft.Icons.OPEN_IN_NEW_ROUNDED, visible=not TV, tooltip="Відкрити на сайті", icon_color=MUTED,
            on_click=lambda e: page.run_task(ft.UrlLauncher().launch_url, item["url"]))

        voice_row = ft.Row([], spacing=8, wrap=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER)
        # рядок озвучок з'являється, коли стане відомо, які доріжки є: плавно розгортається
        # (висота 0 → 46), а не зсуває вміст різким стрибком
        voice_box = ft.Container(voice_row, height=0, opacity=0, animate=ui.MED,
                                 animate_opacity=ui.MED, clip_behavior=ft.ClipBehavior.HARD_EDGE)
        ctx["voice_row"] = voice_row
        ctx["voice_box"] = voice_box

        actions_row = ft.Row([resume_btn, play_btn, fav_btn, trailer_btn, site_btn],
                             spacing=10 if not TV else 12, wrap=True, run_spacing=12)
        if TV:
            # курсор на кнопках під постером — сторінка вгору: видно постер і опис (самі вони
            # фокус не отримують, тож інакше пульт стрибав одразу на «Назад»)
            for b_ in (resume_btn, play_btn, fav_btn, trailer_btn):
                b_.on_focus = lambda e: page.run_task(scroller.scroll_to, offset=0, duration=200)
        hero_info = ui.appear(ft.Container(ft.Column([
            title, orig,
            ft.Container(height=6),
            meta_row, genres_row,
            ft.Container(height=6),
            description, more_btn,
            ft.Container(height=8),
            voice_box,  # які озвучки є (лише інформація — обирають у плеєрі)
            # на телевізорі кнопки — одразу під постером (див. нижче): під довгим описом вони
            # опинялися внизу екрана й ховалися, коли сторінка прокручувалась угору
            *([] if TV else [actions_row]),
            ft.Container(height=4),
            cast, cast_row,
        ], spacing=6), padding=ft.Padding.only(top=8)))

        poster_col = ft.Column(
            [poster, *([ft.Container(actions_row, padding=ft.Padding.only(top=20))] if TV else [])],
            spacing=0)
        hero = ft.ResponsiveRow([
            ft.Container(poster_col,
                         col={"xs": 12, "md": 6, "lg": 5},
                         alignment=ft.Alignment.CENTER if is_mobile else ft.Alignment.TOP_LEFT),
            ft.Container(hero_info, col={"xs": 12, "md": 6, "lg": 7}),
        ], spacing=36, run_spacing=24)

        # ── блок серій
        ep_status = ft.Text("Шукаю серії…", size=13, color=MUTED)
        ep_progress = ft.ProgressBar(value=None, color=ACCENT, bgcolor=SURFACE_2,
                                     border_radius=4, height=4, width=260)
        # на TV — з переносом: у вкладену горизонтальну прокрутку фокус пульта ↑/↓ не заходить
        seasons_row = ft.Row([], spacing=8, wrap=TV, run_spacing=8,
                             scroll=None if TV else ft.ScrollMode.AUTO)
        episodes_box = ft.AnimatedSwitcher(
            shimmer(
                ft.Column([ft.Container(height=58, bgcolor=SURFACE_2, border_radius=12)
                           for _ in range(5)], spacing=8),
                base_color=SURFACE_2, highlight_color=SURFACE_3),
            duration=180, reverse_duration=0, transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ui.EASE, switch_out_curve=ui.EASE,
        )
        # «Серії» з'являються лише для серіалу (для фільму досить кнопки «Дивитись»,
        # на якій і так видно «Зачекайте, шукаю відео…»)
        episodes_section = ui.appear(ft.Column([
            ft.Row([ft.Text("Серії", size=22, weight=ft.FontWeight.W_800, color=TEXT),
                    ft.Container(expand=True), ep_status], spacing=12),
            ft.Row([ep_progress]),
            seasons_row,
            ft.Container(height=4),
            episodes_box,
        ], spacing=10, visible=False))
        ctx.update(seasons_row=seasons_row, episodes_box=episodes_box)

        # ── франшиза (TMDB): усі частини по порядку виходу — з'являється, якщо знайдеться
        saga_title = ft.Text("", size=22 if is_mobile else 20, weight=ft.FontWeight.W_800,
                             color=TEXT)
        # сітка по 3 в ряд (ширина — як у постера над нею; на телефоні — на всю ширину)
        saga_row = ft.ResponsiveRow([], columns=12, spacing=12, run_spacing=16,
                                    vertical_alignment=ft.CrossAxisAlignment.START)
        saga_section = ui.appear(ft.Column([
            saga_title,
            ft.Text("Дивитись по порядку виходу", size=13, color=MUTED),
            ft.Container(height=2),
            saga_row,
        ], spacing=6, visible=False))

        # ── відгуки глядачів (коментарі з сайту)
        reviews_title = ft.Text("Відгуки глядачів", size=22, weight=ft.FontWeight.W_800,
                                color=TEXT)
        reviews_list = ft.Column([], spacing=10)
        reviews_section = ui.appear(ft.Column([reviews_title, ft.Container(height=2),
                                               reviews_list], spacing=6, visible=False))

        if not is_mobile:  # під постером (на телефоні — внизу сторінки, після опису)
            poster_col.controls.append(ft.Container(saga_section, padding=ft.Padding.only(top=28)))
        content = ft.Container(
            ft.Column([hero, ft.Container(height=24), episodes_section,
                       *([ft.Container(height=24), saga_section] if is_mobile else []),
                       ft.Container(height=24), reviews_section],
                      spacing=0),
            padding=ft.Padding.only(left=side_pad, right=side_pad, top=8 if is_mobile else 16,
                                    bottom=60),
        )
        # верхня панель — окрема смуга над контентом (зображення під «світлофор» і кнопку
        # «Назад» не заїжджає); після прокрутки в ній з'являється назва й нижня межа
        bar_title = ft.Text(item["title"], size=15, weight=ft.FontWeight.W_700, color=TEXT,
                            opacity=0, animate_opacity=ui.MED, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        bar = ft.Container(
            ft.Row([round_button(ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED, lambda e: go_back(),
                                    tooltip="Назад"), bar_title, *win_buttons()], spacing=14),
            height=58, padding=ft.Padding.only(left=14 + traffic_gap, right=8 if is_win else 14),
            bgcolor=BG, animate=ui.MED,
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.with_opacity(0, "#FFFFFF"))),
        )

        def on_scroll(e: ft.OnScrollEvent):
            quiet()
            compact = e.pixels > 40
            if compact == ctx.get("compact", False):
                return
            ctx["compact"] = compact
            bar.border = ft.Border.only(bottom=ft.BorderSide(1, BORDER if compact else "#00FFFFFF"))
            bar_title.value = title.value
            bar_title.opacity = 1 if compact else 0
            bar.update()

        scroller = ft.ListView([ft.Stack([backdrop, backdrop_fade, content])],
                               expand=True, padding=0, on_scroll=on_scroll, scroll_interval=50)
        title_bar = drag_area(bar)
        view = ft.View(
            route="/title", padding=0, bgcolor=BG,
            controls=[safe(ft.Column([title_bar, scroller], expand=True, spacing=0))],
        )
        guard_back(view)
        page.views.append(view)
        cover_views()
        page.update()
        await asyncio.sleep(0.05)
        poster.scale = 1
        ui.reveal(hero_info)
        view.update()  # лише цей екран (не всю сторінку з картками під ним)

        # ── інформація про тайтл
        loading = {"on": True}
        page.run_task(abroad_hint, loading)
        try:
            html = await asyncio.to_thread(core.fetch, item["url"])
            loading["on"] = False
            info = await asyncio.to_thread(core.parse_title, html, item["url"])
        except Exception as ex:
            ep_status.value = await explain(ex, "сторінку тайтлу")
            ep_progress.visible = False
            episodes_section.visible = True
            ui.reveal(episodes_section)
            view.update()
            return
        ctx["info"] = info
        if info["poster"] and info["poster"] != item["poster"]:
            poster.content = tmdb_swap(ui.poster_image(info["poster"], radius=18,
                                                       aspect_ratio=POSTER_RATIO),
                                       dict(item, year=info.get("year", "")), "w1280")
            backdrop.content = tmdb_swap(backdrop_image(info["poster"]),
                                         dict(item, year=info.get("year", "")), "w300")
        title.value = info["title"] or item["title"]
        if info["orig"]:
            orig.value, orig.visible = info["orig"], True
        meta = []
        if info["imdb"]:
            meta.append(ui.pill(f"IMDb {info['imdb']}", ft.Icons.STAR_ROUNDED, color=TEXT,
                                icon_color=ui.GOLD, bold=True))
        meta += [ui.pill(info[k]) for k in ("year", "duration", "age", "country") if info[k]]
        meta_row.controls = meta
        genres_row.controls = [
            ft.Container(ft.Text(g.capitalize(), size=12, color=ACCENT, weight=ft.FontWeight.W_600),
                         padding=ft.Padding.symmetric(horizontal=10, vertical=4), border_radius=20,
                         bgcolor=ft.Colors.with_opacity(0.1, ACCENT),
                         border=ft.Border.all(1, ft.Colors.with_opacity(0.25, ACCENT)))
            for g in info["genres"]]
        description.value = info["description"]
        more_btn.visible = len(info["description"]) > 280
        if info["actors"]:
            cast.value = "У ролях: " + ", ".join(info["actors"])
            cast.visible = True
        page.run_task(load_cast, item, info, cast, cast_row)
        page.run_task(load_saga, item, info, saga_title, saga_row, saga_section)
        show_reviews(info.get("comments") or [], reviews_title, reviews_list, reviews_section)
        if info["trailer"]:
            trailer_btn.visible = True
            trailer_btn.on_click = lambda e: page.run_task(ft.UrlLauncher().launch_url, info["trailer"])
        ui.reveal(episodes_section)
        view.update()

        # ── серії: або плеєри прямо на сторінці (фільм / серіал з плейлистом),
        #    або окремі сторінки серій — тоді лише список, відео шукаємо при запуску
        err = None
        try:
            found = await asyncio.to_thread(core.page_streams, item["url"], html)
            if found:
                seasons = core.group_streams(found)
            else:
                ep_status.value = "Завантажую список серій…"
                episodes_section.visible = True  # окремі сторінки серій — це серіал
                view.update()
                episodes, locked = await asyncio.to_thread(core.list_episodes, item["url"], html)
                seasons = core.group_episode_pages(episodes, locked)
        except Exception as ex:
            seasons, err = [], ex
        if view not in page.views:
            return  # користувач уже пішов з екрана
        ctx["seasons"] = seasons
        ctx["season"] = next((i for i, x in enumerate(seasons) if x["episodes"]), 0)
        ep_progress.visible = False
        if not seasons:
            play_btn.content = "Відео не знайдено"
            ep_status.value = (await explain(err, "серії")) if err else ""
            if core.GEO["blocked"] or core.GEO["site"]:  # плеєр не пускає з-за кордону
                episodes_box.content = empty_state(
                    ft.Icons.PUBLIC_OFF_ROUNDED, "Недоступно у вашій країні",
                    "Цей плеєр працює лише в Україні, а український сервер для нього зараз не "
                    "знайшовся. Спробуйте трохи пізніше або увімкніть VPN з українським сервером")
            else:
                episodes_box.content = empty_state(ft.Icons.VIDEOCAM_OFF_ROUNDED,
                                                   "Потоків не знайдено",
                                                   "Можливо, відео ще не додано на сайт")
            episodes_section.visible = True
            view.update()
            return
        total = sum(len(s["episodes"]) for s in seasons)
        ep_status.value = f"{total} {plural(total, 'серія', 'серії', 'серій')}"
        play_btn.disabled = False
        play_btn.content, play_btn.icon = "Дивитись", ft.Icons.PLAY_ARROW_ROUNDED
        has_resume = update_resume() is not None
        # після виходу з плеєра кнопку «Продовжити» оновлюємо (нова хвилина)
        # після виходу з плеєра — нова хвилина в «Продовжити» і позначки переглянутих серій
        view.data = {"refresh": lambda: (update_resume(),
                                         render_seasons(ctx) if total > 1 else None,
                                         view.update())}
        if TV:  # autofocus не спрацьовує на вимкненій кнопці — фокус ставимо, щойно ввімкнули
            page.run_task(focus_later, resume_btn if has_resume else play_btn)
        episodes_section.visible = total > 1  # для фільму вистачає кнопки «Дивитись»
        ctx["voices"] = all_voices(seasons, item["url"])
        ctx["voice"] = preferred_voice(ctx)
        render_voices(ctx)
        render_seasons(ctx)
        view.update()
        if not ctx["voices"]:
            # серії ще без потоків — дізнаємось набір озвучок з першої серії
            page.run_task(detect_voices, ctx)
        if resume:  # картка з «Продовжити перегляд» — одразу з тієї ж хвилини
            page.run_task(resume_watching)
        elif autoplay:  # «Дивитись» з банера — одразу вмикаємо
            page.run_task(play_episode, ctx, ctx["season"], 0)

    async def focus_later(control):
        await asyncio.sleep(0.35)  # після того, як кнопку ввімкнено (чи закрито вікно)
        try:
            await control.focus()
        except Exception as ex:
            if os.environ.get("UAFIX_DEBUG"):
                print("focus_later failed:", type(control).__name__, repr(ex))

    async def ensure_sources(ep):
        """Потоки серії; для серіалів з окремими сторінками серій — завантажуються раз, на вимогу."""
        if ep["sources"] is not None:
            return ep["sources"]
        task = ep.get("_loading")
        if task is None:
            task = ep["_loading"] = asyncio.ensure_future(
                asyncio.to_thread(core.episode_sources, ep["page"]))
        try:
            ep["sources"] = await task
        except Exception:
            ep.pop("_loading", None)
            raise
        return ep["sources"]

    async def detect_voices(ctx):
        ep = next((e for x in ctx["seasons"] for e in x["episodes"]), None)
        if ep is None:
            return
        try:
            await ensure_sources(ep)
        except Exception:
            return
        ctx["voices"] = all_voices(ctx["seasons"], ctx["item"]["url"])
        if ctx["voices"] and ctx["voice"] not in ctx["voices"]:
            ctx["voice"] = preferred_voice(ctx)
        render_voices(ctx)
        page.update()

    # ── озвучки
    def all_voices(seasons, url=None):
        """Озвучки тайтлу: з уже завантажених серій і (для серіалів зі сторінками серій) —
        з плейлиста, який показує всі студії одразу (core.VOICE_STATS)."""
        voices = []
        for s in seasons:
            for ep in s["episodes"]:
                for src in ep["sources"] or []:
                    v = src["entry"].get("voice") or ""
                    if v and v not in voices:
                        voices.append(v)
        for v in core.VOICE_STATS.get(core.series_url(url), {}) if url else ():
            if v and v not in voices:
                voices.append(v)
        return sorted(voices, key=core.is_original)

    def voice_counts(ctx):
        """Скільки серій у кожній озвучці (для підпису на кнопці)."""
        stats = core.VOICE_STATS.get(core.series_url(ctx["item"]["url"]))
        if stats:
            return stats
        counts = {}
        for s in ctx["seasons"]:
            for ep in s["episodes"]:
                for v in {src["entry"].get("voice") for src in ep["sources"] or []}:
                    counts[v] = counts.get(v, 0) + 1
        return counts

    def preferred_voice(ctx):
        """Озвучка за замовчуванням: обрана раніше для цього тайтлу → та, з якою дивились
        (історія) → студія, яку останнім часом обирали → перша українська."""
        voices = ctx["voices"]
        if not voices:
            return ""
        h = history_entry(ctx["item"]["url"])
        for v in (voice_titles.get(ctx["item"]["url"]), (h or {}).get("voice"), *voice_recent):
            if v in voices:
                return v
        return voices[0]

    def remember_voice(ctx, voice):
        """Запам'ятати вибір: для цього тайтлу й як улюблену студію для нових."""
        if not voice:
            return
        voice_titles[ctx["item"]["url"]] = voice
        if not core.is_original(voice) and voice not in ("Українська",
                                                          "Українська (багатоголоса)"):
            voice_recent[:] = [voice] + [v for v in voice_recent if v != voice][:19]
            page.run_task(prefs.set, "voice_recent", json.dumps(voice_recent, ensure_ascii=False))
        # не роздуваємо сховище: лише останні 300 тайтлів
        if len(voice_titles) > 300:
            for k in list(voice_titles)[:len(voice_titles) - 300]:
                voice_titles.pop(k, None)
        page.run_task(prefs.set, "voice_titles", json.dumps(voice_titles, ensure_ascii=False))

    def voice_icon(voice):
        return ft.Icons.SUBTITLES_ROUNDED if core.is_original(voice) else ft.Icons.RECORD_VOICE_OVER_ROUNDED

    def pick_source(ep, voice):
        for src in ep["sources"]:
            if src["entry"].get("voice") == voice:
                return src["entry"]
        # цієї серії в обраній озвучці немає — улюблена студія, інакше перша українська
        by_voice = {src["entry"].get("voice"): src["entry"] for src in ep["sources"]}
        for v in voice_recent:
            if v in by_voice:
                return by_voice[v]
        return next((src["entry"] for src in ep["sources"]
                     if not core.is_original(src["entry"].get("voice") or "")),
                    ep["sources"][0]["entry"])

    def render_voices(ctx):
        """Вибір озвучки ще до перегляду: кнопки зі студіями (і скільки серій у кожної).
        Вибір змінює лише вигляд наявних кнопок — на TV фокус лишається на місці."""
        row, box = ctx["voice_row"], ctx["voice_box"]
        voices = ctx["voices"]
        shown = len(voices) > 1
        box.height, box.opacity = (None, 1) if shown else (0, 0)
        if not shown:
            return
        counts = voice_counts(ctx)
        many = any(c > 1 for c in counts.values())
        chips = ctx["voice_chips"] = {}

        def look(v, focused=None):
            body, icon, text = chips[v]
            sel = v == ctx["voice"]
            if focused is not None:
                body.data = focused
            icon.icon = ft.Icons.CHECK_ROUNDED if sel else voice_icon(v)
            icon.color = ACCENT if sel else MUTED
            text.weight = ft.FontWeight.W_700 if sel else ft.FontWeight.W_600
            body.bgcolor = ft.Colors.with_opacity(0.14, ACCENT) if sel else SURFACE_2
            # TV: курсор пульта — біла рамка (поверх оранжевої «обраної»)
            body.border = ft.Border.all(3, focus_ring) if body.data else \
                ft.Border.all(1.5 if sel else 1, ACCENT if sel else BORDER)

        def pick(v):
            def handler(e):
                quiet()
                if ctx["voice"] == v:
                    return
                ctx["voice"] = v
                remember_voice(ctx, v)
                for x in chips:
                    look(x)
                try:
                    row.update()
                except Exception:
                    pass
                toast(f"Озвучка: {v}", voice_icon(v))
            return handler

        def focus_change(v):
            def handler(f):
                look(v, f)
                chips[v][0].update()
            return handler

        controls = []
        for v in voices:
            n = counts.get(v)
            label = v + (f" · {n} {plural(n, 'серія', 'серії', 'серій')}" if many and n else "")
            icon = ft.Icon(voice_icon(v), size=16)
            text = ft.Text(label, size=13, color=TEXT)
            body = ft.Container(ft.Row([icon, text], spacing=8, tight=True),
                                padding=ft.Padding.symmetric(horizontal=14, vertical=9),
                                border_radius=12, data=False)
            chips[v] = (body, icon, text)
            look(v)
            if TV:
                btn = focusable(body, pick(v), on_focus_change=focus_change(v), radius=12)
                btn.style.side = None
                controls.append(btn)
            else:
                body.on_click = pick(v)
                body.ink = True
                body.tooltip = ("Оригінальна доріжка з субтитрами" if core.is_original(v)
                                else "Обрати цю озвучку")
                controls.append(body)
        row.controls = [ft.Text("Озвучка", size=13, color=MUTED, weight=ft.FontWeight.W_600),
                        *controls]

    def render_seasons(ctx):
        seasons, current = ctx["seasons"], ctx["season"]
        if TV:
            # на телевізорі — усі сезони одним списком із заголовками: пультом просто
            # гортаєш униз (до кнопок-сезонів збоку стрілками ↑/↓ фокус не доходить)
            ctx["seasons_row"].controls = []
            blocks = []
            for si, season in enumerate(seasons):
                if len(seasons) > 1:
                    blocks.append(ft.Container(
                        ft.Text(season["name"], size=18, weight=ft.FontWeight.W_800, color=TEXT),
                        padding=ft.Padding.only(top=14 if si else 0, bottom=2)))
                if season.get("locked"):
                    blocks.append(ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE_ROUNDED, size=16, color=MUTED),
                                          ft.Text("Сайт показує серії цього сезону лише "
                                                  "зареєстрованим користувачам", size=13, color=MUTED)],
                                         spacing=8))
                    continue
                blocks += [episode_row(ctx, si, ei, ep) for ei, ep in enumerate(season["episodes"])]
            ctx["episodes_box"].content = ft.Column(blocks, spacing=8, key="all-seasons")
            return

        def select(i):
            ctx["season"] = i
            ctx["season_selected"] = True  # на TV фокус повертається на вибраний сезон
            render_seasons(ctx)
            page.update()

        ctx["seasons_row"].controls = [] if len(seasons) == 1 else [
            tv_tap(ft.Container(
                ft.Row([ft.Text(s["name"], size=13, weight=ft.FontWeight.W_700,
                                color="#FFFFFF" if i == current else MUTED)]
                       + ([ft.Icon(ft.Icons.LOCK_OUTLINE_ROUNDED, size=13,
                                   color="#FFFFFF" if i == current else MUTED)]
                          if s.get("locked") else []), spacing=5, tight=True),
                padding=ft.Padding.symmetric(horizontal=16, vertical=9), border_radius=20,
                gradient=ui.ACCENT_GRADIENT if i == current else None,
                bgcolor=None if i == current else SURFACE_2,
                border=ft.Border.all(1, BORDER), animate=ui.FAST,
                on_click=lambda e, i=i: select(i),
            ), lambda e, i=i: select(i), radius=20,
                autofocus=i == current and ctx.get("season_selected", False))
            for i, s in enumerate(seasons)]
        if seasons[current].get("locked"):
            ctx["episodes_box"].content = ft.Container(empty_state(
                ft.Icons.LOCK_OUTLINE_ROUNDED, "Серії недоступні",
                "Сайт показує серії цього сезону лише зареєстрованим користувачам"),
                key=f"season{current}", height=260)
        else:
            ctx["episodes_box"].content = ft.Column(
                [episode_row(ctx, current, ei, ep)
                 for ei, ep in enumerate(seasons[current]["episodes"])],
                spacing=8, key=f"season{current}")

    def episode_row(ctx, si, ei, ep):
        num_text = ft.Text(str(ep["num"]) if ep["num"] is not None else "▶", size=13,
                           weight=ft.FontWeight.W_800, color=ACCENT)
        num = ft.Container(
            num_text, width=38, height=38, border_radius=19, alignment=ft.Alignment.CENTER,
            bgcolor=ft.Colors.with_opacity(0.1, ACCENT),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.3, ACCENT)), animate=ui.FAST,
        )
        # маленькі значки доступних озвучок серії
        badges = [ft.Icon(voice_icon(src["entry"].get("voice") or ""), size=16, color=MUTED,
                          tooltip=src["label"])
                  for src in ep["sources"]] if len(ep["sources"] or []) > 1 else []
        # переглянуто: ✓; почато: тонка смужка прогресу під назвою
        h = history_entry(ctx["item"]["url"])
        seen = ((h or {}).get("seen") or {}).get(f"{si}:{ei}", 0)
        done = seen >= 0.9
        play_icon = ft.Icon(ft.Icons.CHECK_ROUNDED if done else ft.Icons.PLAY_ARROW_ROUNDED,
                            color=ACCENT if done else MUTED, opacity=1 if done else 0.5,
                            animate_opacity=ui.FAST, tooltip="Переглянуто" if done else None)
        label = ft.Text(ep["label"], size=15, weight=ft.FontWeight.W_600,
                        color=None if TV else TEXT,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        title_col = ft.Column(
            [label, ft.ProgressBar(value=max(0.03, seen), color=ACCENT, bgcolor=SURFACE_3,
                                   bar_height=3, border_radius=2)]
            if 0.02 < seen < 0.9 else [label],
            spacing=6, expand=True, tight=True)
        row = ft.Container(
            ft.Row([
                num,
                title_col,
                ft.Row(badges, spacing=6),
                play_icon,
            ], spacing=14),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10), border_radius=12,
            bgcolor=SURFACE, border=ft.Border.all(1, BORDER), animate=ui.FAST,
            on_click=lambda e: page.run_task(play_episode, ctx, si, ei),
        )

        def highlight(h):
            if not TV:  # на телевізорі фон дає кнопка (миттєво)
                row.bgcolor = SURFACE_2 if h else SURFACE
            num.gradient = ui.ACCENT_GRADIENT if h else None
            num_text.color = "#FFFFFF" if h else ACCENT
            play_icon.opacity = 1 if (h or done) else 0.5
            play_icon.color = ACCENT if (h or done) else MUTED
            row.update()

        if TV:
            return tv_tap(row, lambda e: page.run_task(play_episode, ctx, si, ei), highlight,
                          focus_bg=(TV_FOCUS_BG, SURFACE))
        row.on_hover = lambda e: highlight(ui.hovered(e))
        return row

    # ═════════════════════ плеєр ═════════════════════
    async def ask_player():
        """Android / TV, перший перегляд: яким плеєром дивитись. Вибір запам'ятовується;
        змінити — Налаштування → Відтворення → Плеєр. → "builtin" / "external" / None."""
        done = asyncio.get_running_loop().create_future()

        def pick(value):
            def handler(e):
                quiet()
                save_setting("player", value)
                page.pop_dialog()
                if not done.done():
                    done.set_result(value)
            return handler

        def dismissed(e):
            quiet()
            if not done.done():
                done.set_result(None)

        show_dialog(ft.AlertDialog(
            title=ft.Text("Яким плеєром дивитись?", weight=ft.FontWeight.W_800),
            bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
            on_dismiss=dismissed,
            content=ft.Column([
                ft.Text("Вбудований — запам'ятовує, де ви зупинились, пропускає заставку, "
                        "вмикає наступну серію. Інша програма — VLC, MX Player чи інший плеєр "
                        "на пристрої.", size=14, color=TEXT, width=dialog_width()),
                ft.Text("Вибір можна змінити: Налаштування → Відтворення → Плеєр.", size=13,
                        color=MUTED, width=dialog_width()),
            ], tight=True, spacing=12),
            actions=[ft.TextButton("Інша програма", on_click=pick("external"),
                                   style=ft.ButtonStyle(color=TEXT)),
                     ft.FilledButton("Вбудований", on_click=pick("builtin"), autofocus=True,
                                     style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF"))],
        ))
        return await done

    async def play_external(ctx, ep):
        """Android / TV, «Плеєр: інша програма»: потік обраної якості — у VLC / MX Player…
        (вікно вибору програми показує MainActivity)"""
        entry = pick_source(ep, ctx.get("voice", ""))
        try:
            url = await asyncio.to_thread(core.quality_url, entry["url"], settings["quality"])
        except Exception:
            url = entry["url"]
        show = (ctx["info"] or {}).get("title") or ctx["item"]["title"]
        title = show + (f" · {ep['label']}" if len(ctx["seasons"]) > 1 or
                        len(ctx["seasons"][0]["episodes"]) > 1 else "")
        await ft.UrlLauncher().launch_url(
            "uafixupdate://external?" + urlencode({"url": url, "title": title}),
            mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    async def play_episode(ctx, si, ei, start_ms=None):
        if not ctx["seasons"]:
            toast("Серії ще завантажуються…", ft.Icons.HOURGLASS_TOP_ROUNDED)
            return
        ep = ctx["seasons"][si]["episodes"][ei]
        if ep["sources"] is None:
            toast("Завантажую серію…", ft.Icons.HOURGLASS_TOP_ROUNDED)
        try:
            sources = await ensure_sources(ep)
        except Exception as ex:
            msg = await explain(ex, "серію")
            toast(msg, error_icon(msg), retry=lambda: page.run_task(play_episode, ctx, si, ei))
            return
        if not sources:
            toast("Плеєр недоступний у вашій країні — увімкніть VPN з українським сервером"
                  if core.GEO["blocked"] or core.GEO["site"] else
                  "Для цієї серії відео не знайдено",
                  ft.Icons.VIDEOCAM_OFF_ROUNDED)
            return
        if ctx["voices"] != all_voices(ctx["seasons"], ctx["item"]["url"]):
            await detect_voices(ctx)
        if is_android or TV:
            mode = settings.get("player") or "ask"
            if mode == "ask":
                mode = await ask_player()
                if mode is None:
                    return  # вікно закрили — нічого не запускаємо
            if mode == "external":
                await play_external(ctx, ep)
                return
        multi = len(ctx["seasons"]) > 1
        # усі серії по порядку; черга — з обраної й далі, «before» — попередні
        everything, start = [], 0
        for i, season in enumerate(ctx["seasons"]):
            for j, e in enumerate(season["episodes"]):
                if (i, j) == (si, ei):
                    start = len(everything)
                prefix = f"{season['name']} · " if multi else ""
                everything.append((prefix + e["label"], e))
        show = (ctx["info"] or {}).get("title") or ctx["item"]["title"]
        # список якостей потрібен одразу: щоб плеєр стартував в обраній якості й показав кнопку
        first = []
        for _, e in everything[start:start + 6]:
            if not e["sources"]:
                break
            first.append(pick_source(e, ctx.get("voice", "")))
        try:
            await asyncio.wait_for(asyncio.gather(
                *[asyncio.to_thread(core.hls_variants, x) for x in first]), 5)
        except Exception:
            pass
        view = ft.View(route="/play", padding=0, bgcolor="#000000")
        start_pos = ft.Duration(milliseconds=int(start_ms)) if start_ms else None
        voice = ctx.get("voice", "")
        sub_pref = settings["sub_orig" if core.is_original(voice) else "sub_ua"] or None
        view.controls = [safe(build_player(view, ctx, show, everything[start:], voice,
                                      start_pos=start_pos, sub_pref=sub_pref,
                                      before=everything[:start]))]
        guard_back(view, player_back)
        page.views.append(view)
        cover_views()
        page.update()  # плаваюче вікно дозволяє pip_watch — лише поки відео грає

    def build_player(view, ctx, show, queue, voice, start_pos=None, sub_pref=None, before=()):
        """Плеєр для черги серій у вибраній озвучці; sub_pref — мова увімкнених субтитрів
        (None — вимкнено, за замовчуванням); before — серії перед чергою (для «попередньої»)."""
        queue, before = list(queue), list(before)
        entries = []  # потоки серій, що вже в плейлисті (решта догружається у фоні)
        for _, ep in queue:
            if not ep["sources"]:
                break
            entries.append(pick_source(ep, voice))
        names = [n if n != "Фільм" else "" for n, _ in queue]
        p = st["player_p"] = {"idx": 0, "sub_lang": sub_pref,  # None — субтитри вимкнено, інакше мова
             "voice": voice, "quality": settings["quality"], "target": start_pos, "extending": False,
             "restoring": bool(start_pos and start_pos.in_milliseconds > 3000)}
        voices = all_voices([{"episodes": [ep for _, ep in queue]}])

        def subtitle_for(entry):
            subs = entry.get("subs") or []
            if not p["sub_lang"] or not subs:
                return None
            return next((x for x in subs if x["lang"] == p["sub_lang"]), subs[0])

        def track(sub):
            if not sub:
                return fv.VideoSubtitleTrack.none()
            return fv.VideoSubtitleTrack(src=sub["url"], title=sub["title"], language=sub["lang"])

        def sub_config():
            base = 20 if is_mobile else 34 if TV else 28
            return fv.VideoSubtitleConfiguration(
                text_style=ft.TextStyle(size=round(base * float(settings["sub_scale"])),
                                        color="#FFFFFF", weight=ft.FontWeight.W_600,
                                        bgcolor="#99000000"))

        def fit_value(fullscreen_on=False):
            if is_mobile and not fullscreen_on:
                # на телефоні у звичайному (вертикальному) режимі — завжди «вписати»:
                # обраний для повного екрана масштаб тут розтягував би кадр
                return ft.BoxFit.CONTAIN
            mode = settings["fit"]
            if mode == "auto":  # на телефоні в повному екрані — заповнити ширину
                return ft.BoxFit.COVER if is_mobile else ft.BoxFit.CONTAIN
            return next((f for k, _, f in FIT_MODES if k == mode), ft.BoxFit.CONTAIN)

        FIT_ICONS = {"contain": ft.Icons.FIT_SCREEN_ROUNDED, "cover": ft.Icons.CROP_FREE_ROUNDED,
                     "fill": ft.Icons.OPEN_IN_FULL_ROUNDED}

        def current_fit_mode():
            keys = [k for k, _, _ in FIT_MODES]
            if settings["fit"] in keys:
                return settings["fit"]
            return "cover" if is_mobile else "contain"  # «auto»

        def refresh_fit_button():
            """Іконка й підказка кнопки показують поточний режим (замість спливаючої плашки)."""
            mode = current_fit_mode()
            fit_btn.icon = FIT_ICONS[mode]
            fit_btn.tooltip = f"Масштаб: {next(n for k, n, _ in FIT_MODES if k == mode).lower()}"

        def set_sub_size(scale):
            save_setting("sub_scale", scale)
            video.subtitle_configuration = sub_config()
            refresh_bar()
            view.update()  # лише цей екран (не всю сторінку з картками під ним)

        def set_fit(mode):
            save_setting("fit", mode)
            video.fit = fit_value(st.get("fullscreen", False))
            refresh_fit_button()
            refresh_bar()
            view.update()

        def cycle_fit(e=None):
            keys = [k for k, _, _ in FIT_MODES]
            mode = keys[(keys.index(current_fit_mode()) + 1) % len(keys)]
            save_setting("fit", mode)
            video.fit = fit_value(st.get("fullscreen", False))
            refresh_fit_button()
            video.update()

        # панель плеєра завжди чорна — текст світлий і в світлій темі
        now = ft.Text(names[0], size=12, color="#9BA1AC", visible=bool(names[0]))

        def mpv_properties():
            props = dict(MPV_STREAMING)
            boost = str(settings.get("volume_boost") or "0")
            if boost != "0":
                # підсилення гучності (у сайту звук голосніший, ніж у плеєра «на 100 %»).
                # Звукових фільтрів (volume, alimiter) у вбудованому libmpv немає — з ними
                # звук зникав зовсім; тому вбудоване підсилення replaygain: потоки без тегів
                # гучності отримують replaygain-fallback дБ
                props.update({"replaygain": "track", "replaygain-fallback": boost})
            if os.environ.get("UAFIX_MPV_LOG"):  # діагностика: журнал mpv у файл
                props.update({"log-file": os.environ["UAFIX_MPV_LOG"], "msg-level": "all=v"})
            return props

        def video_config():
            mode = settings.get("hwdec") or "auto"
            kw = {}
            if mode == "off":
                kw["enable_hardware_acceleration"] = False
            elif mode == "copy":  # кадри копіюються з декодера — повільніше, але сумісніше
                kw["hardware_decoding_api"] = "mediacodec-copy" if is_android else "auto-copy"
            return fv.VideoConfiguration(mpv_properties=mpv_properties(), **kw)

        def media(e):
            return fv.VideoMedia(core.quality_url(e["url"], p["quality"]),
                                 http_headers=core.stream_headers(e))

        video = fv.Video(
            expand=True, autoplay=True, title=show, fill_color="#000000",
            # телефон: у плаваючому вікні («картинка в картинці») відео грає далі; паузу при
            # справжньому виході у фон ставимо самі (on_lifecycle)
            pause_upon_entering_background_mode=not is_mobile,
            playlist=[media(e) for e in entries],
            playlist_mode=fv.PlaylistMode.NONE,
            subtitle_track=track(subtitle_for(entries[0])),
            subtitle_configuration=sub_config(),
            fit=fit_value(),
            configuration=video_config(),
        )

        async def on_error(e):
            # Плеєр інколи повідомляє про короткочасні збої потоку, які сам і переживає,
            # тож повідомлень не показуємо. Якщо ж відтворення справді зупинилось —
            # тихо перевідкриваємо поточну серію з того ж місця (не частіше разу на 30 с).
            if time.monotonic() - p.get("error_at", 0) < 30:
                return
            p["error_at"] = time.monotonic()
            await asyncio.sleep(4)
            try:
                if not alive() or await video.is_playing():
                    return
            except Exception:
                return
            await switch_voice(entries[p["idx"]].get("voice") or p["voice"])

        video.on_error = on_error

        def badge(text, bright=True, width=34):
            """Значок у стилі «CC»: текст у рамці; напівпрозорий — вимкнено."""
            # плеєр завжди темний (відео) — значки білі і в світлій темі
            return ft.Container(
                ft.Text(text, size=12, weight=ft.FontWeight.W_800, color="#FFFFFF"),
                width=width, height=24, border_radius=6, alignment=ft.Alignment.CENTER,
                border=ft.Border.all(1.6, "#FFFFFF"), opacity=1 if bright else 0.4,
                margin=2 if is_mobile else 6,
            )

        def menu_item(text, checked, on_click):
            return ft.PopupMenuItem(
                content=ft.Row([ft.Icon(ft.Icons.CHECK_ROUNDED, size=16,
                                        color=ACCENT if checked else ft.Colors.TRANSPARENT),
                                ft.Text(text, color=TEXT)], spacing=10),
                on_click=on_click)

        def voice_code(v):
            if core.is_original(v):
                return "EN"
            if v.startswith("Українська"):
                return "UA"
            return v[:2].upper()

        def menu_state(is_open):
            st["menu_open"] = is_open

        menu_style = dict(bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=12),
                          menu_position=ft.PopupMenuPosition.OVER,
                          on_open=lambda e: menu_state(True), on_cancel=lambda e: menu_state(False),
                          on_select=lambda e: menu_state(False))
        def set_sub(lang):
            p["sub_lang"] = lang
            # запам'ятати вибір — наступні фільми почнуться з тими самими субтитрами
            cur = entries[p["idx"]].get("voice") or p["voice"]
            save_setting("sub_orig" if core.is_original(cur) else "sub_ua", lang or "")
            video.subtitle_track = track(subtitle_for(entries[p["idx"]]))
            refresh_bar()
            view.update()

        voice_menu = ft.PopupMenuButton(tooltip="Озвучка", **menu_style)
        sub_menu = ft.PopupMenuButton(tooltip="Субтитри", **menu_style)

        # На TV випадаюче меню пультом незручне й не показує фокус — замість нього кнопка
        # з білою рамкою фокуса, а вибір відкривається у вікні по центру екрана.
        def choice_dialog(title, options):
            def pick(action):
                def handler(e):
                    page.pop_dialog()
                    p["dialog"] = False
                    action(e)
                    if st.get("tv_activity"):
                        st["tv_activity"]()
                    # після вікна фокус — на кнопку, з якої його відкрили (якість, CC…)
                    back_to = p.get("dialog_origin") or p.get("refocus")
                    if back_to:
                        page.run_task(focus_later, back_to)
                return handler

            p["dialog"] = True  # поки вікно відкрите, панель плеєра не ховаємо

            def dismissed(e):
                p["dialog"] = False
                p["dialog_closed"] = time.monotonic()

            # телефон горизонтально (низький екран): кнопки нижчі й у дві колонки, а якщо
            # і так не вміщається — вікно прокручується
            compact = (page.height or 800) < 560
            buttons = [
                ft.FilledButton(
                    text, icon=ft.Icons.CHECK_ROUNDED if checked else None, autofocus=checked,
                    on_click=pick(action), width=250 if compact else 380,
                    style=ft.ButtonStyle(
                        bgcolor={ft.ControlState.FOCUSED: TV_FOCUS_BG,
                                 ft.ControlState.DEFAULT: SURFACE_2},
                        side=TV_FOCUS_SIDE,
                        color=TV_FOCUS_TEXT, icon_color=TV_FOCUS_TEXT,
                        padding=ft.Padding.symmetric(horizontal=16 if compact else 20,
                                                     vertical=10 if compact else 18),
                        shape=ft.RoundedRectangleBorder(radius=12),
                        text_style=ft.TextStyle(size=15 if compact else 17,
                                                weight=ft.FontWeight.W_600)))
                for text, checked, action in options]
            if compact:
                body_ = ft.Column([ft.Row(buttons, wrap=True, spacing=10, run_spacing=10,
                                          width=510)],
                                  tight=True, scroll=ft.ScrollMode.AUTO,
                                  height=min((len(buttons) + 1) // 2 * 54 + 4,
                                             max(120, (page.height or 400) - 150)))
            else:
                # без прокрутки: прокручувана колонка у вікні розтягувала його на весь
                # екран (телефон вертикально); прокрутка — лише для довгих списків
                body_ = ft.Column(buttons, tight=True, spacing=10,
                                  scroll=ft.ScrollMode.AUTO if len(buttons) > 7 else None,
                                  height=min(len(buttons) * 72, (page.height or 800) - 200)
                                  if len(buttons) > 7 else None)
            show_dialog(ft.AlertDialog(
                on_dismiss=dismissed,
                title=ft.Text(title, size=18 if compact else 22, weight=ft.FontWeight.W_800,
                              color=TEXT),
                title_padding=ft.Padding.only(left=24, right=24, top=16, bottom=4)
                if compact else None,
                content_padding=ft.Padding.only(left=24, right=24, top=8, bottom=16)
                if compact else None,
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=body_,
            ))

        def open_choice(e, title, key):
            p["dialog_origin"] = e.control  # сюди повернеться фокус після вибору
            choice_dialog(title, p.get(key, []))

        def tv_menu_button(title, key):
            # саме IconButton: звичайна Button усередині панелі плеєра фокус не отримує
            return ft.IconButton(
                tooltip=title, on_click=lambda e: open_choice(e, title, key),
                style=ft.ButtonStyle(
                    bgcolor=PLAYER_FOCUS_BG, side=PLAYER_FOCUS_SIDE,
                    shape=ft.RoundedRectangleBorder(radius=12), padding=4))

        tv_voice_btn = tv_menu_button("Озвучка", "voice_opts")
        tv_sub_btn = tv_menu_button("Субтитри", "sub_opts")
        quality_menu = ft.PopupMenuButton(tooltip="Якість", **menu_style)
        # телефон: якість і масштаб — в одному меню (на вузькому екрані все окремими кнопками
        # не вміщалося, і крайні виходили за край)
        more_menu = ft.PopupMenuButton(icon=ft.Icons.TUNE_ROUNDED, icon_color="#FFFFFF",
                                       tooltip="Якість і масштаб", **menu_style)
        tv_quality_btn = tv_menu_button("Якість", "quality_opts")
        # телефон: якість і масштаб — одним вікном
        tv_more_btn = tv_menu_button("Якість і масштаб", "more_opts")
        tv_more_btn.icon, tv_more_btn.icon_color = ft.Icons.TUNE_ROUNDED, "#FFFFFF"
        # таймер сну (лише TV): зупинити відтворення через N хвилин або після цієї серії
        tv_sleep_btn = tv_menu_button("Таймер сну", "sleep_opts")
        tv_sleep_btn.icon, tv_sleep_btn.icon_color = ft.Icons.BEDTIME_OUTLINED, "#FFFFFF"

        def current_quality():
            variants = core._VARIANTS.get(entries[p["idx"]]["url"])
            return core.pick_quality(variants, p["quality"]) if variants else p["quality"]

        def quality_badge():
            return badge(current_quality(), width=44)

        def fill_menus():
            entry = entries[p["idx"]]
            cur_voice = entry.get("voice") or p["voice"]
            voice_menu.content = badge(voice_code(cur_voice))
            voice_menu.items = [
                menu_item(v, v == cur_voice, lambda e, v=v: page.run_task(switch_voice, v))
                for v in voices]
            subs = entry.get("subs") or []
            current = subtitle_for(entry)
            if len(subs) == 1:
                on_items = [menu_item("Увімкнено", current is not None,
                                      lambda e: set_sub(subs[0]["lang"] or "on"))]
            else:
                on_items = [menu_item(f"Увімкнено ({x['title'].lower()})",
                                      current is not None and current["url"] == x["url"],
                                      lambda e, x=x: set_sub(x["lang"] or "on"))
                            for x in subs]
            sub_menu.content = badge("CC", bright=current is not None)
            size_items = [menu_item(f"Розмір: {name.lower()}",
                                    abs(float(settings["sub_scale"]) - scale) < 0.01,
                                    lambda e, scale=scale: set_sub_size(scale))
                          for name, scale in SUB_SIZES]
            if not subs:
                # кнопка є завжди: краще прямо сказати, що субтитрів немає, ніж гадати,
                # чи це помилка
                no_subs = ft.PopupMenuItem(content=ft.Text(
                    "Для цього відео сайт не має субтитрів", color=MUTED, size=13))
                sub_menu.items = [no_subs]
            else:
                sub_menu.items = [menu_item("Вимкнено", current is None, lambda e: set_sub(None)),
                                  *on_items, ft.PopupMenuItem(), *size_items]
            # ті самі пункти — для вікна вибору на TV
            p["voice_opts"] = [(v, v == cur_voice, lambda e, v=v: page.run_task(switch_voice, v))
                               for v in voices]
            sub_on = ([("Увімкнено", current is not None,
                        lambda e: set_sub(subs[0]["lang"] or "on"))] if len(subs) == 1 else
                      [(f"Увімкнено ({x['title'].lower()})",
                        current is not None and current["url"] == x["url"],
                        lambda e, x=x: set_sub(x["lang"] or "on")) for x in subs])
            p["sub_opts"] = [("Вимкнено", current is None, lambda e: set_sub(None)), *sub_on,
                             *[(f"Розмір: {name.lower()}",
                                abs(float(settings["sub_scale"]) - scale) < 0.01,
                                lambda e, scale=scale: set_sub_size(scale))
                               for name, scale in SUB_SIZES]] if subs else [
                ("Для цього відео сайт не має субтитрів", False, lambda e: None)]
            variants = core._VARIANTS.get(entry["url"]) or {}
            q_opts = [(f"{k}p", current_quality() == k,
                       lambda e, k=k: page.run_task(switch_quality, k))
                      for k in sorted(variants, key=int, reverse=True)]
            quality_menu.content = quality_badge()
            quality_menu.items = [menu_item(t, c, a) for t, c, a in q_opts]
            more_menu.items = [menu_item(f"Якість: {t}", c, a) for t, c, a in q_opts] + \
                ([ft.PopupMenuItem()] if q_opts else []) + [
                menu_item(f"Масштаб: {n.lower()}", current_fit_mode() == k,
                          lambda e, k=k: set_fit(k)) for k, n, _ in FIT_MODES]
            p["quality_opts"] = q_opts
            p["more_opts"] = [(f"Якість: {t}", c, a) for t, c, a in q_opts] + [
                (f"Масштаб: {n.lower()}", current_fit_mode() == k, lambda e, k=k: set_fit(k))
                for k, n, _ in FIT_MODES]
            tv_quality_btn.icon = quality_badge()
            tv_voice_btn.icon = badge(voice_code(cur_voice))
            tv_sub_btn.icon = badge("CC", bright=current is not None)

        def reload_playlist(pos=None):
            """Новий плейлист у тому ж плеєрі (а не новий плеєр): інакше в повноекранному
            режимі лишається зображення старого, вже закритого плеєра — чорний екран."""
            entries.clear()
            for _, ep in queue:
                if not ep["sources"]:
                    break
                entries.append(pick_source(ep, p["voice"]))
            p["idx"] = 0
            video.playlist = [media(e) for e in entries]
            video.subtitle_track = track(subtitle_for(entries[0]))
            now.value, now.visible = names[0], bool(names[0])
            refresh_bar()
            view.update()
            if pos and pos.in_milliseconds > 3000:
                p["target"], p["restoring"] = pos, True
                page.run_task(restore_position)
            if len(entries) < len(queue) and not p["extending"]:
                page.run_task(extend_playlist)

        async def switch_voice(v):
            # та сама серія в іншій озвучці — продовжуємо з того ж місця
            try:
                pos = await video.get_current_position()
            except Exception:
                pos = None
            if p["restoring"] or not pos or pos.in_milliseconds <= 0:
                # позиція ще не відновилась (озвучку перемкнули двічі поспіль) — беремо цільову
                pos = p["target"] if p["restoring"] else pos
            idx = p["idx"]
            before.extend(queue[:idx])
            del queue[:idx], names[:idx]
            p["voice"] = v
            remember_voice(ctx, v)
            # субтитри — як востаннє обирали для такого типу доріжки
            p["sub_lang"] = settings["sub_orig" if core.is_original(v) else "sub_ua"] or None
            reload_playlist(pos)

        async def switch_quality(q):
            # та сама серія в іншій якості — з тієї ж секунди (як і зі зміною озвучки)
            save_setting("quality", q)
            if q == p["quality"]:
                return
            try:
                pos = await video.get_current_position()
            except Exception:
                pos = None
            if p["restoring"] or not pos or pos.in_milliseconds <= 0:
                pos = p["target"] if p["restoring"] else pos
            idx = p["idx"]
            before.extend(queue[:idx])
            del queue[:idx], names[:idx]
            p["quality"] = q
            reload_playlist(pos)

        # ── трансляція на Chromecast / Google TV
        BETA_KIND = {"dlna": "  ·  DLNA (бета)", "airplay": "  ·  AirPlay (бета)"}

        async def pair_airplay(device):
            """Спарювання з AirPlay-телевізором: він показує PIN, користувач вводить його тут."""
            pairing = airplay.Pairing(device["host"])
            try:
                await asyncio.to_thread(pairing.start)
            except Exception as ex:
                toast(f"Телевізор не почав спарювання: {ex}", ft.Icons.ERROR_OUTLINE_ROUNDED)
                return
            field = ft.TextField(hint_text="PIN-код", autofocus=True, text_size=22,
                                 keyboard_type=ft.KeyboardType.NUMBER, max_length=8,
                                 filled=True, fill_color=SURFACE_2, cursor_color=ACCENT)
            msg = ft.Text("Введіть код, який зараз показує телевізор. Це потрібно лише один раз.",
                          color=MUTED, size=13)

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
                page.run_task(prefs.set, "airplay_creds", json.dumps(airplay.CREDENTIALS))
                page.pop_dialog()
                toast("Спарено — запускаю трансляцію", ft.Icons.CHECK_CIRCLE_ROUNDED)
                await start_cast(device)

            field.on_submit = lambda e: page.run_task(done)
            show_dialog(ft.AlertDialog(
                title=ft.Text(f"Спарювання з «{device['name']}»", weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([msg, field], tight=True, spacing=12, width=dialog_width()),
                actions=[ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         ft.FilledButton("Спарити", on_click=lambda e: page.run_task(done),
                                         style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF"))]))

        async def open_cast():
            if p.get("cast"):
                show_cast_panel()
                return
            devices_col = ft.Column([ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2,
                                                             color=ACCENT),
                                             ft.Text("Шукаю пристрої в мережі…", color=MUTED)],
                                            spacing=12)], tight=True, spacing=10,
                                    width=dialog_width())
            dlg = ft.AlertDialog(
                title=ft.Text("Транслювати на", weight=ft.FontWeight.W_800),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=devices_col,
                actions=[ft.TextButton("Скасувати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED))])
            show_dialog(dlg)
            try:
                devices = await asyncio.to_thread(uafix_cast.discover)
            except Exception:
                devices = []
            if not devices:
                devices_col.controls = [
                    ft.Text("Пристроїв не знайдено.", color=TEXT, weight=ft.FontWeight.W_600),
                    ft.Text("Телефон і телевізор (Chromecast, Google TV, Android TV або з DLNA — "
                            "Hisense VIDAA, Samsung, LG) мають бути в одній Wi-Fi мережі, а "
                            "телевізор — увімкнений.", color=MUTED, size=13),
                    ft.Text("Hisense VIDAA: увімкніть у телевізорі «Налаштування → Мережа → "
                            "Content Sharing» (або «Підключення до мобільного»).",
                            color=MUTED, size=13),
                    *([ft.Text("У мережі відповіли, але це не телевізори: "
                               + ", ".join(dlna.LAST_SEEN[:6]), color=MUTED, size=12)]
                      if dlna.LAST_SEEN else []),
                    ft.TextButton("Шукати ще раз", icon=ft.Icons.REFRESH_ROUNDED,
                                  style=ft.ButtonStyle(color=ACCENT, icon_color=ACCENT),
                                  on_click=lambda e: (page.pop_dialog(), page.run_task(open_cast)))]
            else:
                devices_col.controls = [
                    ft.FilledButton(
                        d["name"] + BETA_KIND.get(d.get("kind"), ""),
                        icon=ft.Icons.TV_ROUNDED, width=dialog_width(),
                        on_click=lambda e, d=d: page.run_task(start_cast, d),
                        style=ft.ButtonStyle(bgcolor=SURFACE_2, color=TEXT, icon_color=ACCENT,
                                             alignment=ft.Alignment.CENTER_LEFT,
                                             padding=ft.Padding.symmetric(horizontal=18, vertical=16),
                                             shape=ft.RoundedRectangleBorder(radius=12)))
                    for d in devices]
            try:
                dlg.update()
            except Exception:
                pass

        async def start_cast(device):
            page.pop_dialog()
            try:
                pos = await video.get_current_position()
            except Exception:
                pos = None
            start = (p["target"] if p["restoring"] else pos) or ft.Duration()
            await video.pause()
            entry = entries[p["idx"]]
            sub = subtitle_for(entry)
            title = show + (f" · {names[p['idx']]}" if names[p["idx"]] else "")
            toast(f"Підключаюсь до «{device['name']}»…", ft.Icons.CAST_ROUNDED)

            def connect():
                sess = uafix_cast.open_session(
                    device, core.stream_headers(entry) if entry.get("referer") else None)
                sess.play(core.quality_url(entry["url"], p["quality"]), title,
                          ctx["item"].get("poster"), start.in_milliseconds / 1000,
                          sub["url"] if sub else None)
                return sess

            try:
                sess = await asyncio.to_thread(connect)
            except airplay.PairingRequired:
                await pair_airplay(device)  # один раз: PIN-код з екрана телевізора
                return
            except Exception as ex:
                # для бета-способів (DLNA, AirPlay) — причина: її можна переслати автору
                why = f": {ex}" if device.get("kind") in ("dlna", "airplay") and str(ex) else ""
                if device.get("kind") == "airplay" and dlna.SCAN_OPEN:
                    why += f"; відкриті порти: {', '.join(map(str, dlna.SCAN_OPEN[:20]))}"
                toast(f"Не вдалося почати трансляцію на «{device['name']}»{why}",
                      ft.Icons.ERROR_OUTLINE_ROUNDED)
                return
            p["cast"] = sess
            p["cast_state"] = (start.in_milliseconds / 1000, 0.0, "BUFFERING")
            cast_btn.icon, cast_btn.icon_color = ft.Icons.CAST_CONNECTED_ROUNDED, ACCENT
            cast_btn.update()
            show_cast_panel()
            page.run_task(watch_cast, sess)

        def fmt_time(sec):
            sec = int(sec or 0)
            h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
            return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"

        cast_ui = {}

        def show_cast_panel():
            sess = p.get("cast")
            if not sess:
                return
            pos_text = ft.Text("", color=MUTED, size=13)
            bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor=SURFACE_2, bar_height=6,
                                 border_radius=3, width=dialog_width())
            toggle = ft.IconButton(ft.Icons.PAUSE_ROUNDED, icon_size=40, icon_color="#FFFFFF",
                                   tooltip="Пауза / відтворення",
                                   on_click=lambda e: page.run_task(cast_do, "toggle"),
                                   style=ft.ButtonStyle(bgcolor=ACCENT, shape=ft.CircleBorder()))

            def ctrl(icon, tip, action):
                return ft.IconButton(icon, icon_size=32, icon_color=TEXT, tooltip=tip,
                                     on_click=lambda e: page.run_task(cast_do, action))

            cast_ui.update(pos=pos_text, bar=bar, toggle=toggle)
            p["cast_dlg"] = cast_dlg = ft.AlertDialog(
                title=ft.Row([ft.Icon(ft.Icons.CAST_CONNECTED_ROUNDED, color=ACCENT),
                              ft.Text(sess.name, weight=ft.FontWeight.W_800, expand=True,
                                      max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=10),
                bgcolor=SURFACE_3, shape=ft.RoundedRectangleBorder(radius=18),
                content=ft.Column([
                    ft.Text(show + (f" · {names[p['idx']]}" if names[p["idx"]] else ""),
                            color=TEXT, weight=ft.FontWeight.W_600, max_lines=2),
                    bar, pos_text,
                    ft.Row([ctrl(ft.Icons.REPLAY_10_ROUNDED, "Назад на 10 с", -10), toggle,
                            ctrl(ft.Icons.FORWARD_10_ROUNDED, "Вперед на 10 с", 10)],
                           alignment=ft.MainAxisAlignment.CENTER, spacing=18),
                ], tight=True, spacing=12, width=dialog_width()),
                actions=[ft.TextButton("Сховати", on_click=lambda e: page.pop_dialog(),
                                       style=ft.ButtonStyle(color=MUTED)),
                         ft.FilledButton("Дивитись тут", icon=ft.Icons.PHONE_ANDROID_ROUNDED,
                                         on_click=lambda e: page.run_task(stop_cast, True),
                                         style=ft.ButtonStyle(bgcolor=ACCENT, color="#FFFFFF",
                                                              icon_color="#FFFFFF"))],
                on_dismiss=lambda e: cast_ui.clear(),
            )
            show_dialog(cast_dlg)
            render_cast_state()

        def render_cast_state():
            if not cast_ui or not p.get("cast_state"):
                return
            pos, dur, state = p["cast_state"][:3]
            cast_ui["pos"].value = (f"{fmt_time(pos)} / {fmt_time(dur)}" if dur else fmt_time(pos)) + \
                ("  ·  завантаження…" if state == "BUFFERING" else "")
            cast_ui["bar"].value = pos / dur if dur else None
            cast_ui["toggle"].icon = (ft.Icons.PLAY_ARROW_ROUNDED if state == "PAUSED"
                                      else ft.Icons.PAUSE_ROUNDED)
            try:
                cast_ui["pos"].page.update()
            except Exception:
                cast_ui.clear()

        async def cast_do(action):
            sess = p.get("cast")
            if not sess:
                return
            try:
                if action == "toggle":
                    await asyncio.to_thread(sess.toggle)
                else:
                    pos = p["cast_state"][0] if p.get("cast_state") else 0
                    await asyncio.to_thread(sess.seek, pos + action)
            except Exception:
                toast("Телевізор не відповідає", ft.Icons.ERROR_OUTLINE_ROUNDED)

        async def watch_cast(sess):
            """Стан відтворення на телевізорі раз на секунду; вихід із плеєра — кінець трансляції."""
            fails = 0
            while p.get("cast") is sess:
                if not alive():
                    await stop_cast(False)
                    return
                try:
                    pos, dur, state, reason = await asyncio.to_thread(sess.status)
                    fails = 0
                except Exception:
                    fails += 1
                    if fails >= 5:
                        toast("Зв'язок із телевізором втрачено", ft.Icons.CAST_ROUNDED)
                        await stop_cast(True, quit_tv=False)
                        return
                    await asyncio.sleep(1)
                    continue
                if state == "IDLE" and reason in ("FINISHED", "CANCELLED", "INTERRUPTED",
                                                  "OTHER_APP"):
                    await stop_cast(False, quit_tv=False)  # фільм закінчився або зупинили з пульта
                    return
                p["cast_state"] = (pos, dur, state)
                render_cast_state()
                await asyncio.sleep(1)

        async def stop_cast(resume_here, quit_tv=True):
            sess = p.pop("cast", None)
            if not sess:
                return
            pos = p.get("cast_state", (0,))[0]
            # вікно керування — закрити одразу (раніше закривалось лише після відповіді
            # телевізора й лише якщо посилання на його елементи ще не очистились — тоді
            # лишалось на екрані, поки застосунок не згорнеш)
            cast_ui.clear()
            dlg = p.pop("cast_dlg", None)
            if dlg is not None and dlg.open:
                dlg.open = False
                try:
                    dlg.update()
                except Exception:
                    pass
            if quit_tv:
                try:
                    await asyncio.to_thread(sess.stop)
                except Exception:
                    pass
            if not alive():
                return
            cast_btn.icon, cast_btn.icon_color = ft.Icons.CAST_ROUNDED, "#FFFFFF"
            if pos > 5:
                p["target"], p["restoring"] = ft.Duration(milliseconds=int(pos * 1000)), True
                page.run_task(restore_position)
            if resume_here:
                await video.play()
            view.update()

        async def seek_by(seconds):
            try:
                pos = await video.get_current_position()
                await video.seek(ft.Duration(milliseconds=max(0, pos.in_milliseconds + seconds * 1000)))
            except Exception:
                pass

        st["seek_by"] = seek_by
        st["play_pause"] = lambda: page.run_task(video.play_or_pause)

        async def go_next():
            for _ in range(80):  # наступна серія ще може догружатися
                if len(entries) > p["idx"] + 1:
                    await video.next()
                    return
                await asyncio.sleep(0.25)
            toast("Не вдалося завантажити наступну серію", ft.Icons.ERROR_OUTLINE_ROUNDED)

        async def go_prev():
            if p["idx"] > 0:
                await video.previous()
                return
            if not before:
                return
            # попередня серія ще не в плейлисті — ставимо її на початок плейлиста
            _, ep = before[-1]
            try:
                sources = await ensure_sources(ep)
            except Exception:
                sources = []
            if not sources:
                toast("Для попередньої серії відео не знайдено", ft.Icons.VIDEOCAM_OFF_ROUNDED)
                return
            label, prev_ep = before.pop()
            del queue[:p["idx"]], names[:p["idx"]]
            queue.insert(0, (label, prev_ep))
            names.insert(0, label if label != "Фільм" else "")
            reload_playlist()

        def bar_button(icon, tooltip, on_click):
            return ft.IconButton(icon, icon_color="#FFFFFF", icon_size=34 if TV else 28,
                                 tooltip=tooltip, on_click=on_click,
                                 style=ft.ButtonStyle(
                                     bgcolor=PLAYER_FOCUS_BG, side=PLAYER_FOCUS_SIDE,
                                     shape=ft.CircleBorder(), padding=10) if TV else None)

        # кнопки створюються один раз і лише переставляються між слотами
        prev_btn = bar_button(ft.Icons.SKIP_PREVIOUS_ROUNDED, "Попередня серія",
                              lambda e: page.run_task(go_prev))
        next_btn = bar_button(ft.Icons.SKIP_NEXT_ROUNDED, "Наступна серія",
                              lambda e: page.run_task(go_next))

        # Панель плеєра будується один раз: кнопки, яких не було в ній від початку (або з
        # visible=False), плеєр уже не покаже. Тому — постійні «слоти», у які кнопку
        # вставляємо чи прибираємо; зміни вмісту слотів доходять до екрана.
        prev_slot, next_slot, voice_slot, sub_slot, quality_slot, more_slot = \
            (ft.Container() for _ in range(6))
        fit_btn = bar_button(ft.Icons.ASPECT_RATIO_ROUNDED, "Масштаб", cycle_fit)
        cast_btn = bar_button(ft.Icons.CAST_ROUNDED, "Транслювати на телевізор",
                              lambda e: page.run_task(open_cast))
        refresh_fit_button()

        def refresh_bar():
            fill_menus()
            prev_slot.content = prev_btn if (p["idx"] > 0 or before) else None
            next_slot.content = next_btn if p["idx"] < len(queue) - 1 else None
            # вибір — у вікні по центру (як на TV): випадаюче меню біля панелі висіло над
            # відео, коли панель плеєра ховалась
            voice_slot.content = tv_voice_btn if voices else None
            # CC — завжди (без субтитрів кнопка тьмяна, а вікно пояснює, що їх немає)
            sub_slot.content = tv_sub_btn
            quality_slot.content = (tv_quality_btn
                                    if core._VARIANTS.get(entries[p["idx"]]["url"]) else None)
            more_slot.content = tv_more_btn

        win_fs = is_desktop and not TV

        def win_fullscreen(on):
            """ПК: на весь екран розгортається саме вікно. Вбудований повноекранний режим
            плеєра відкривається поверх усього застосунку й ховає плашку гучності, картку
            наступної серії та назву."""
            if on and st.get("mini_restore"):
                st["mini_restore"]()  # з міні-плеєра — спершу назад до звичайного вікна
            st["fullscreen"] = on
            if not on:
                st["fullscreen_exit"] = time.monotonic()
            w = page.window
            if on and not w.full_screen:
                # розмір вікна до повного екрана — до нього повертається міні-плеєр
                st["pre_fs"] = dict(width=w.width, height=w.height, left=w.left, top=w.top)
                if is_win and w.maximized:
                    # розгорнуте вікно без рамки вже на весь екран: повний екран поверх нього
                    # нічого не міняв на око, а згорнути вікно після цього Windows не давала
                    w.maximized = False
                    page.update()
            if is_win:
                # Flet надсилає команду лише при зміні значення, а розгорнуте вікно без рамки
                # він, схоже, сам вважає повноекранним — тоді «на весь екран» / «вийти» не
                # доходили до Windows. Спершу протилежне значення, потім потрібне.
                w.full_screen = not on
                page.update()
            page.window.full_screen = on
            fs_btn.icon = ft.Icons.FULLSCREEN_EXIT_ROUNDED if on else ft.Icons.FULLSCREEN_ROUNDED
            fs_btn.tooltip = "Вийти з повного екрана" if on else "На весь екран"
            page.update()

        fs_btn = bar_button(ft.Icons.FULLSCREEN_ROUNDED, "На весь екран",
                            lambda e: win_fullscreen(not st.get("fullscreen")))
        if win_fs:
            st["win_fullscreen"] = win_fullscreen

        style = dict(seek_bar_position_color=ACCENT, seek_bar_thumb_color=ACCENT,
                     automatically_imply_skip_next_button=False,
                     automatically_imply_skip_previous_button=False,
                     bottom_button_bar=[prev_slot, fv.VideoPlayOrPauseButton(), next_slot,
                                        fv.VideoVolumeButton(), fv.VideoPositionIndicator(),
                                        fv.VideoSpacer(), voice_slot, sub_slot, quality_slot, cast_btn,
                                        fit_btn,
                                        fs_btn if win_fs else fv.VideoFullscreenButton()])
        if TV:
            # Телевізор: власна панель поверх відео замість вбудованої. У вбудованої таймлайн
            # не отримує фокус з пульта (перемотувати можна було лише кнопками), а сама панель
            # не ховається від натискань пульта. Тут: ↑/↓ — між таймлайном і кнопками,
            # на таймлайні ←/→ — перемотка (що довше тримати, то більший крок), OK — пауза.
            def tv_btn(icon, tooltip, action, autofocus=False):
                return ft.IconButton(icon, icon_size=34, icon_color="#FFFFFF", tooltip=tooltip,
                                     autofocus=autofocus, on_click=action,
                                     style=ft.ButtonStyle(
                                         bgcolor=PLAYER_FOCUS_BG, side=PLAYER_FOCUS_SIDE,
                                         shape=ft.CircleBorder(), padding=10))

            def toggle_play(e=None):
                p["paused"] = not p.get("paused", False)
                play_pause.icon = ft.Icons.PLAY_ARROW_ROUNDED if p["paused"] else ft.Icons.PAUSE_ROUNDED
                play_pause.update()
                page.run_task(video.play_or_pause)
                tv_activity()

            play_pause = tv_btn(ft.Icons.PAUSE_ROUNDED, "Пауза / відтворення", toggle_play,
                                autofocus=True)
            p["refocus"] = play_pause
            video.controls = None

            def tv_suspend():
                """Застосунок пішов у фон (заставка телевізора після довгої паузи тощо)."""
                p["suspended"] = True
                if alive() and not p.get("paused"):
                    toggle_play()

            def tv_resume():
                """Повернулись: фокус — знову на ▶/⏸ (інакше він губився, і OK натискав
                кнопку сторінки фільму під плеєром)."""
                # лише після справжнього виходу у фон: індикатор гучності телевізора теж
                # ненадовго «забирає» застосунок, і панель з'являлась після кожного натискання
                if alive() and p.pop("suspended", False):
                    tv_activity()
                    page.run_task(focus_later, play_pause)

            st["tv_suspend"], st["tv_resume"] = tv_suspend, tv_resume

            tv_bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor="#40FFFFFF", bar_height=8,
                                    border_radius=4, expand=True)
            tv_time = ft.Text("", size=18, color="#FFFFFF", weight=ft.FontWeight.W_600)
            tv_hint = ft.Text("", size=16, color=ui.ACCENT_2,
                              weight=ft.FontWeight.W_700)
            timeline_box = ft.Container(
                ft.Column([ft.Row([tv_bar]),
                           ft.Row([tv_time, tv_hint], spacing=16)], spacing=10, tight=True),
                padding=ft.Padding.symmetric(horizontal=16, vertical=12), border_radius=14,
                border=ft.Border.all(3, ft.Colors.TRANSPARENT))

            def timeline_focus(f):
                p["timeline_focus"] = f
                timeline_box.border = ft.Border.all(3, "#FFFFFF" if f else ft.Colors.TRANSPARENT)
                tv_bar.bar_height = 12 if f else 8
                tv_hint.value = "← → перемотка" if f and not p.get("seek_target") else tv_hint.value
                if not f and not p.get("seek_target"):
                    tv_hint.value = ""
                timeline_box.update()

            timeline = focusable(timeline_box, toggle_play, timeline_focus)
            tv_title = ft.Text(show, size=22, weight=ft.FontWeight.W_800, color="#FFFFFF",
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
            tv_overlay = ft.Container(
                ft.Column([
                    ft.Container(expand=True),
                    ft.Container(ft.Row([tv_title, now]), padding=ft.Padding.only(left=16)),
                    timeline,
                    ft.Row([prev_slot, play_pause, next_slot, voice_slot, sub_slot, quality_slot,
                            fit_btn, tv_sleep_btn], spacing=12),
                ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                left=0, right=0, top=0, bottom=0,
                padding=ft.Padding.only(left=40, right=40, bottom=28),
                gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                           end=ft.Alignment.BOTTOM_CENTER,
                                           colors=["#00000000", "#00000000", "#E6000000"],
                                           stops=[0, 0.5, 1]))
            now.size, now.color = 18, "#C9CDD4"

            def render_time():
                pos, dur = p.get("pos", 0), p.get("dur", 0)
                shown_pos = p["seek_target"] if p.get("seek_target") is not None else pos
                tv_bar.value = shown_pos / dur if dur else None
                tv_time.value = f"{fmt_time(shown_pos)} / {fmt_time(dur)}" if dur else fmt_time(shown_pos)

            async def tv_poll():
                while alive():
                    if p["shown"]:
                        try:
                            pos = (await video.get_current_position()).in_milliseconds / 1000
                            dur = (await video.get_duration()).in_milliseconds / 1000
                            p["pos"], p["dur"] = pos, dur
                        except Exception:
                            pass
                        render_time()
                        try:
                            tv_overlay.update()
                        except Exception:
                            pass
                    await asyncio.sleep(1)

            async def apply_seek(token):
                await asyncio.sleep(0.8)  # чекаємо, поки перестануть натискати
                if p.get("seek_token") != token or p.get("seek_target") is None:
                    return
                target, p["seek_target"] = p["seek_target"], None
                p["pos"] = target
                tv_hint.value = "← → перемотка" if p.get("timeline_focus") else ""
                render_time()
                tv_overlay.update()
                try:
                    await video.seek(ft.Duration(milliseconds=int(target * 1000)))
                except Exception:
                    pass

            async def tv_key(key):
                """Стрілки на таймлайні — перемотка (повертає True, якщо натискання оброблено)."""
                if not p.get("timeline_focus") or key not in ("Arrow Left", "Arrow Right"):
                    return False
                now_t = time.monotonic()
                p["seek_streak"] = p.get("seek_streak", 0) + 1 \
                    if now_t - p.get("seek_last", 0) < 0.6 else 0
                p["seek_last"] = now_t
                streak = p["seek_streak"]
                step = 10 if streak < 4 else 30 if streak < 10 else 60
                base = p["seek_target"] if p.get("seek_target") is not None else p.get("pos", 0)
                dur = p.get("dur") or 0
                target = base + (step if key == "Arrow Right" else -step)
                target = max(0.0, min(target, dur - 3 if dur else target))
                p["seek_target"] = target
                delta = target - p.get("pos", 0)
                tv_hint.value = f"{'+' if delta >= 0 else '−'}{fmt_time(abs(delta))}"
                render_time()
                tv_overlay.update()
                p["seek_token"] = p.get("seek_token", 0) + 1
                page.run_task(apply_seek, p["seek_token"])
                return True

            st["tv_key"] = tv_key

            # Панель ховається через 5 с без натискань (якщо не на паузі й не перемотуємо),
            # будь-яка кнопка пульта показує її знову.
            p["shown"], p["hide_token"] = True, 0

            def hide_panel():
                p["shown"] = False
                # схована панель втрачає фокус, а on_blur при цьому не приходить
                p["timeline_focus"] = False
                timeline_box.border = ft.Border.all(3, ft.Colors.TRANSPARENT)
                tv_bar.bar_height, tv_hint.value = 8, ""
                tv_overlay.visible = False
                tv_overlay.update()

            async def hide_later(token):
                await asyncio.sleep(5)
                if (p["hide_token"] != token or p.get("paused") or p.get("dialog")
                        or p.get("seek_target") is not None or not p["shown"] or not alive()):
                    return
                hide_panel()

            def tv_back():
                """«Назад» пульта: видно панель — сховати її (вийти — наступним «Назад»)."""
                if alive() and p.get("dialog"):
                    return True  # відкрите вікно вибору закривається саме
                if alive() and p.get("next_shown"):
                    cancel_next()  # «Назад» на плашці наступної серії — «Скасувати»
                    return True
                if not alive() or not p["shown"]:
                    return False
                p["hide_token"] += 1
                hide_panel()
                return True

            st["tv_back"] = tv_back

            def tv_activity():
                if not p["shown"]:
                    p["shown"] = True
                    tv_overlay.visible = True
                    render_time()
                    tv_overlay.update()
                    if not p.get("next_shown"):  # інакше фокус — на «Наступна серія»
                        page.run_task(focus_later, play_pause)
                p["hide_token"] += 1
                page.run_task(hide_later, p["hide_token"])

            st["tv_activity"] = tv_activity
            page.run_task(hide_later, 0)
            page.run_task(tv_poll)
        else:
            mobile_style = dict(
                style,
                # таймлайн не впритул до країв екрана (особливо в повноекранному режимі)
                seek_bar_margin=ft.Padding.only(left=24, right=24, bottom=18),
                # подвійний тап по лівій / правій частині — перемотка на 10 с (звичайний тап
                # у будь-якому місці так само показує панель — плеєр ловить його одразу)
                seek_on_double_tap=True,
                seek_on_double_tap_backward_duration=ft.Duration(seconds=10),
                seek_on_double_tap_forward_duration=ft.Duration(seconds=10),
                # попередня / наступна серія — у центрі, поруч із ±10 с і ▶/⏸
                primary_button_bar=[
                    prev_slot, ft.Container(width=12),
                    ft.IconButton(ft.Icons.REPLAY_10_ROUNDED, icon_size=36, icon_color="#FFFFFF",
                                  tooltip="Назад на 10 с",
                                  on_click=lambda e: page.run_task(seek_by, -10)),
                    ft.Container(width=20), fv.VideoPlayOrPauseButton(icon_size=56), ft.Container(width=20),
                    ft.IconButton(ft.Icons.FORWARD_10_ROUNDED, icon_size=36, icon_color="#FFFFFF",
                                  tooltip="Вперед на 10 с",
                                  on_click=lambda e: page.run_task(seek_by, 10)),
                    ft.Container(width=12), next_slot,
                ],
                # знизу — лише те, що вміщається на вузькому екрані (▶/⏸ уже по центру)
                bottom_button_bar=[fv.VideoPositionIndicator(), fv.VideoSpacer(), voice_slot,
                                   sub_slot, more_slot, cast_btn, fv.VideoFullscreenButton()],
                bottom_button_bar_margin=ft.Padding.only(left=8, right=8, bottom=22),
            )
            video.controls = fv.AdaptiveVideoControls(
                material=fv.MaterialVideoControls(**mobile_style) if is_mobile else None,
                material_desktop=None if is_mobile else fv.MaterialDesktopVideoControls(
                    **style, hide_mouse_on_controls_removal=True,
                    play_and_pause_on_tap=True))  # клік лівою — пауза / відтворення, як пробіл

        def alive():
            try:
                return video.page is not None
            except Exception:
                return False

        async def extend_playlist():
            """Догружає наступні серії (до 3 наперед) і дописує їх у кінець плейлиста —
            плеєр додає їх без переривання відтворення."""
            p["extending"] = True
            try:
                await _extend()
            finally:
                p["extending"] = False

        async def _extend():
            while len(entries) < len(queue):
                if not alive():
                    return
                if len(entries) > p["idx"] + 3:
                    await asyncio.sleep(1)
                    continue
                _, ep = queue[len(entries)]
                try:
                    sources = await ensure_sources(ep)
                except Exception:
                    sources = []
                if not alive():
                    return
                if len(entries) >= len(queue) or queue[len(entries)][1] is not ep:
                    continue  # поки вантажилось, плейлист перебудували (зміна озвучки тощо)
                if not sources:  # серію без відео пропускаємо
                    del queue[len(entries)]
                    del names[len(entries)]
                    continue
                entry = pick_source(ep, p["voice"])
                await asyncio.to_thread(core.hls_variants, entry)  # щоб серія була в обраній якості
                if not alive() or len(entries) >= len(queue) or queue[len(entries)][1] is not ep:
                    continue
                entries.append(entry)
                video.playlist = video.playlist + [media(entry)]
                refresh_bar()
                video.update()

        def on_track(e):
            try:
                idx = int(e.data)
            except (TypeError, ValueError):
                return
            if 0 <= idx < len(entries):
                prev_idx, p["idx"] = p["idx"], idx
                now.value, now.visible = names[idx], bool(names[idx])
                video.subtitle_track = track(subtitle_for(entries[idx]))
                refresh_bar()
                hide_next()
                view.update()
                if idx == prev_idx + 1:
                    # перейшли на наступну серію — попередня переглянута, навіть якщо титри
                    # не додивились
                    mark_watched(ctx, queue[prev_idx][1])
                if idx != prev_idx and not p["restoring"]:
                    skip_intro(idx)  # нова серія — теж без заставки
                if idx > prev_idx and (p.get("stop_after") == prev_idx
                                       or p.get("sleep_after") == prev_idx):
                    # «Скасувати» наступну серію / таймер сну «після цієї серії» — наступна
                    # стоїть на паузі на самому початку
                    reason = "Таймер сну" if p.get("sleep_after") == prev_idx else None
                    p["stop_after"] = p["sleep_after"] = None
                    page.run_task(stop_now, reason)

        # ── наступна серія: за 15 с до кінця — плашка з відліком; і таймер сну
        NEXT_LEAD = 15
        next_name = ft.Text("", size=15, weight=ft.FontWeight.W_700, color="#FFFFFF",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        next_count = ft.Text("", size=13, color="#C9CDD4")
        next_now_btn = ft.FilledButton(
            "Дивитись зараз", icon=ft.Icons.SKIP_NEXT_ROUNDED,
            on_click=lambda e: page.run_task(play_next_now),
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.FOCUSED: "#FFFFFF", ft.ControlState.DEFAULT: ACCENT},
                color={ft.ControlState.FOCUSED: "#111214", ft.ControlState.DEFAULT: "#FFFFFF"},
                icon_color={ft.ControlState.FOCUSED: "#111214",
                            ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.Padding.symmetric(horizontal=18, vertical=14)))
        next_cancel = ft.TextButton(
            "Скасувати", on_click=lambda e: cancel_next(),
            style=ft.ButtonStyle(
                color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=12),
                bgcolor={ft.ControlState.FOCUSED: "#33FFFFFF"},
                side={ft.ControlState.FOCUSED: ft.BorderSide(3, "#FFFFFF"),
                      ft.ControlState.DEFAULT: ft.BorderSide(0, ft.Colors.TRANSPARENT)},
                padding=ft.Padding.symmetric(horizontal=16, vertical=14)))
        next_card = ft.Container(
            ft.Column([
                ft.Text("НАСТУПНА СЕРІЯ", size=11, weight=ft.FontWeight.W_800, color=ACCENT),
                next_name, next_count,
                ft.Row([next_now_btn, next_cancel], spacing=8),
            ], spacing=6, tight=True),
            right=28, bottom=110 if TV else 90, width=360, visible=False,
            padding=16, border_radius=16, bgcolor="#E6141519",
            border=ft.Border.all(1, "#26FFFFFF"),
            shadow=ft.BoxShadow(blur_radius=24, color="#80000000"))

        def hide_next():
            if p.get("next_shown"):
                p["next_shown"] = False
                next_card.visible = False
                try:
                    next_card.update()
                except Exception:
                    pass

        def cancel_next():
            p["stop_after"] = p["idx"]  # наступна почнеться на паузі
            p["next_dismissed"] = p["idx"]
            hide_next()
            if TV and st.get("tv_activity"):
                page.run_task(focus_later, p["refocus"])

        async def play_next_now():
            hide_next()
            p["next_dismissed"] = p["idx"]
            await go_next()

        async def stop_now(reason=None):
            try:
                await video.pause()
                if TV:
                    p["paused"] = True
                    play_pause.icon = ft.Icons.PLAY_ARROW_ROUNDED
                    play_pause.update()
                    st["tv_activity"]()
            except Exception:
                pass
            if reason:
                toast(f"{reason}: відтворення зупинено", ft.Icons.BEDTIME_ROUNDED)

        async def end_watch():
            while alive():
                await asyncio.sleep(1)
                if p.get("cast"):
                    continue
                if p.get("sleep_at") and time.monotonic() >= p["sleep_at"]:
                    p["sleep_at"] = None
                    sleep_options()
                    await stop_now("Таймер сну")
                has_next = p["idx"] < len(queue) - 1
                if not has_next or p.get("next_dismissed") == p["idx"]:
                    continue
                try:
                    pos = (await video.get_current_position()).in_milliseconds / 1000
                    dur = (await video.get_duration()).in_milliseconds / 1000
                except Exception:
                    continue
                left = dur - pos
                if dur < 120 or left <= 0:
                    continue
                if left <= NEXT_LEAD:
                    nxt = p["idx"] + 1
                    next_name.value = names[nxt] if nxt < len(names) else ""
                    next_count.value = f"Почнеться через {int(left)} с"
                    first_show = not p.get("next_shown")
                    p["next_shown"] = True
                    next_card.visible = True
                    try:
                        next_card.update()
                    except Exception:
                        continue
                    if first_show and TV:
                        page.run_task(focus_later, next_now_btn)
                elif p.get("next_shown"):
                    hide_next()  # перемотали назад

        def sleep_options():
            """Пункти таймера сну (для вікна вибору на TV)."""
            left = (p["sleep_at"] - time.monotonic()) / 60 if p.get("sleep_at") else None

            def set_sleep(minutes=None, after=False):
                p["sleep_at"] = time.monotonic() + minutes * 60 if minutes else None
                p["sleep_after"] = p["idx"] if after else None
                sleep_options()
                tv_sleep_btn.icon_color = ACCENT if (minutes or after) else "#FFFFFF"
                try:
                    tv_sleep_btn.update()
                except Exception:
                    pass
                if minutes or after:
                    toast("Таймер сну: " + (f"через {minutes} хв" if minutes
                                            else "після цієї серії"), ft.Icons.BEDTIME_ROUNDED)

            off = not p.get("sleep_at") and p.get("sleep_after") is None
            opts = [("Вимкнено", off, lambda e: set_sleep())]
            for m in (15, 30, 60, 90):
                on = left is not None and abs(left - m) < 0.5
                opts.append((f"Через {m} хв", on, lambda e, m=m: set_sleep(m)))
            opts.append(("Після цієї серії" if len(queue) > 1 else "Після цього фільму",
                         p.get("sleep_after") == p["idx"], lambda e: set_sleep(after=True)))
            if left is not None and not any(c for _, c, _ in opts[1:5]):
                opts.insert(1, (f"Залишилось {max(1, round(left))} хв", True, lambda e: None))
            p["sleep_opts"] = opts

        sleep_options()
        page.run_task(end_watch)

        INTRO = 16_000  # заставка UAFLIX: 16 с (однакова в усіх відео з «uaflix» у назві)

        def skip_intro(idx):
            """Почати серію одразу після заставки UAFLIX — якщо вона там є й це увімкнено."""
            if not settings["skip_intro"] or not (0 <= idx < len(entries)):
                return
            if "uaflix" not in entries[idx]["url"].lower():
                return  # інші джерела (Hurtom, студії…) заставки не мають
            p["target"], p["restoring"] = ft.Duration(milliseconds=INTRO), True
            page.run_task(restore_position)

        async def restore_position():
            """Перемотати на збережену позицію (зміна озвучки, «Продовжити перегляд»).
            Перемотка, зроблена до того, як HLS-потік реально почав грати, ігнорується
            плеєром, тому чекаємо на відтворення й перевіряємо результат, повторюючи за потреби."""
            start = p["target"]
            target = start.in_milliseconds
            for _ in range(360):  # до ~90 с: на повільній мережі потік стартує 30–40 с
                await asyncio.sleep(0.25)
                if not alive():
                    return  # плеєр уже закрили
                try:
                    playing = await video.is_playing()
                    duration = await video.get_duration()
                except Exception:
                    continue
                if not playing or not duration or duration.in_milliseconds <= 0:
                    continue
                if p["target"] is not start:
                    return  # з'явилась нова ціль (ще одна зміна озвучки) — її відновить інша задача
                await video.seek(start)
                await asyncio.sleep(0.8)
                try:
                    pos = await video.get_current_position()
                except Exception:
                    continue
                if pos and pos.in_milliseconds >= target - 3000:
                    p["restoring"] = False
                    return  # позицію відновлено
            p["restoring"] = False

        video.on_track_change = on_track
        # кнопки — у власній панелі плеєра, тож вони є й у повноекранному режимі
        refresh_bar()

        def fullscreen(on):
            st["fullscreen"] = on
            if not on:
                st["fullscreen_exit"] = time.monotonic()
                p["auto_fs"] = False  # вийшли (кнопкою чи поворотом) — далі знову за поворотом
            if is_mobile and not on and is_tablet():
                # планшет: після виходу орієнтація лишається, як була (його тримають і
                # горизонтально) — лише знімаємо обмеження повноекранного плеєра
                page.run_task(page.set_allowed_device_orientations, [])
            elif is_mobile and not on and accel is not None:
                # вийшли кнопкою, а телефон ще в руках горизонтально — інтерфейс
                # повертаємо вертикально й тримаємо так, доки телефон не поставлять
                # вертикально (тоді поворот знову вільний, див. on_orient)
                p["portrait_lock"] = True
                page.run_task(page.set_allowed_device_orientations,
                              [ft.DeviceOrientation.PORTRAIT_UP])
            if accel is not None:
                sensor()
            if is_mobile:
                # на весь екран — обраний масштаб (за замовчуванням «заповнити»: фільми
                # закодовано в кадр 16:9 з чорними смугами, а екран телефона ширший);
                # назад у вертикальний режим — знову «вписати»
                video.fit = fit_value(on)
                video.update()

        def on_enter_fullscreen(e):
            if win_fs:
                # подвійний клік по відео: замість вбудованого режиму — на весь екран вікно
                video.fullscreen = False
                video.update()
                win_fullscreen(not st.get("fullscreen"))
                return
            fullscreen(True)

        video.on_enter_fullscreen = on_enter_fullscreen
        video.on_exit_fullscreen = lambda e: None if win_fs else fullscreen(False)

        if accel is not None:
            o = {"state": None, "count": 0}

            def sensor():
                """Датчик потрібен лише двічі: щоб вийти з повного екрана, коли в нього
                увійшли поворотом, і щоб зняти тимчасову вертикальну фіксацію. Решту часу
                він вимкнений — його показники кожні 0,4 с давали ривки відео."""
                need = alive() and (p.get("portrait_lock") or
                                    (p.get("auto_fs") and st.get("fullscreen")))
                if bool(need) != bool(accel.enabled):
                    accel.enabled = bool(need)
                    o["state"], o["count"] = None, 0
                    accel.update()

            def on_orient(x, y):
                if not alive():
                    orient["cb"] = None
                    unlock_rotation()
                    sensor()
                    return
                s_ = "land" if abs(x) > 7 and abs(y) < 5 else "port" if abs(y) > 7 and abs(x) < 5 \
                    else None
                if s_ is None:
                    return  # лежить на столі чи нахилений — нічого не робимо
                o["count"] = o["count"] + 1 if s_ == o["state"] else 1
                o["state"] = s_
                if o["count"] == 2 and s_ == "port":
                    unlock_rotation()
                # Вихід: повернули вертикально — лише якщо в повний екран увійшли саме
                # поворотом (повноекранний плеєр тримає інтерфейс горизонтальним, тож
                # «вертикально» видно тільки за датчиком)
                if o["count"] == 2 and s_ == "port" and p.get("auto_fs") and st.get("fullscreen"):
                    p["auto_fs"] = False
                    video.fullscreen = False
                    video.update()

            def unlock_rotation():
                if p.pop("portrait_lock", False):
                    page.run_task(page.set_allowed_device_orientations, [])  # як у системі
                    sensor()

            def on_page_resize():
                # Вхід: інтерфейс сам повернувся горизонтально. Це стається лише коли
                # автоповорот у телефоні увімкнено — заблокований поворот поважаємо
                if not alive():
                    st["player_resize"] = None
                    return
                land = (page.width or 0) > (page.height or 0)
                was_land, p["last_land"] = p.get("last_land"), land
                if (land and not was_land and not st.get("fullscreen")
                        and settings["auto_fullscreen"] and not p.get("cast")
                        and time.monotonic() - st.get("fullscreen_exit", 0) > 2):
                    p["auto_fs"] = True
                    video.fullscreen = True
                    video.update()

            p["last_land"] = (page.width or 0) > (page.height or 0)
            st["player_resize"] = on_page_resize
            orient["cb"] = on_orient
        st["video"] = video
        # ПК: міні-плеєр — маленьке вікно поверх інших (як плаваюче вікно на телефоні)
        mini_btn = ft.IconButton(
            ft.Icons.PICTURE_IN_PICTURE_ALT_ROUNDED, icon_color="#FFFFFF", icon_size=20,
            tooltip="Міні-плеєр", visible=is_desktop,
            on_click=lambda e: page.run_task(p["toggle_mini"]) if p.get("toggle_mini") else None)
        player_win_btns = win_buttons(on_dark=True)  # Windows: у мініатюрі ховаємо
        peek_btn = ft.IconButton(ft.Icons.LAST_PAGE_ROUNDED, icon_color="#FFFFFF", icon_size=20,
                                 tooltip="Сховати за край екрана", visible=False)
        bar = drag_area(ft.Container(
            ft.Row([
                back_btn := round_button(
                    ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
                    lambda e: page.run_task(p["close_mini"]) if p.get("mini_on") else go_back(),
                    "Назад", on_dark=True),
                ft.Column([ft.Text(show, size=15, weight=ft.FontWeight.W_700, color="#FFFFFF",
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), now],
                          spacing=0, expand=True),
                peek_btn,
                mini_btn,
                *player_win_btns,
            ], spacing=10),
            padding=ft.Padding.only(left=14 + traffic_gap, right=8 if is_win else 14, top=8,
                                    bottom=8),
            bgcolor="#000000",
        ))
        async def save_progress():
            if p.get("cast") and p.get("cast_state"):  # дивимось на телевізорі
                pos_s, dur_s = p["cast_state"][:2]
                if pos_s > 5 and dur_s > 0:
                    _, ep = queue[p["idx"]]
                    record_progress(ctx, ep, entries[p["idx"]].get("voice") or p["voice"],
                                    int(pos_s * 1000), int(dur_s * 1000))
                return
            if p["restoring"]:
                return  # позиція ще не відновлена — не перезаписуємо збережену нулем
            try:
                pos = await video.get_current_position()
                dur = await video.get_duration()
            except Exception:
                return
            if not pos or not dur or dur.in_milliseconds <= 0 or pos.in_milliseconds < 5000:
                return
            _, ep = queue[p["idx"]]
            record_progress(ctx, ep, entries[p["idx"]].get("voice") or p["voice"],
                            pos.in_milliseconds, dur.in_milliseconds)

        async def track_progress():
            while True:
                await asyncio.sleep(10)
                if not alive():
                    return
                await save_progress()

        st["save_progress"] = save_progress
        page.run_task(track_progress)
        if start_pos and start_pos.in_milliseconds > 3000:
            page.run_task(restore_position)
        else:
            skip_intro(0)
        if len(entries) < len(queue):
            page.run_task(extend_playlist)
        if TV:
            # autofocus усередині панелі плеєра не спрацьовує, а фокус лишається на сторінці
            # під плеєром (і OK натискав би там) — ставимо його на ▶/⏸, коли панель уже на екрані
            async def focus_player():
                for delay in (0.6, 1.5):
                    await asyncio.sleep(delay)
                    try:
                        await play_pause.focus()
                    except Exception:
                        pass

            page.run_task(focus_player)
            return ft.Stack([video, tv_overlay, next_card], expand=True)  # «Назад» — кнопкою пульта
        if is_mobile:
            # телефон: без назви й стрілки над відео — назад системним жестом / кнопкою
            return ft.Stack([video, next_card], expand=True)
        if TV:
            return ft.Column([bar, video], expand=True, spacing=0)

        # ПК: смуга з назвою — поверх відео й ховається, коли миша не рухається (у
        # повноекранному вікні macOS вона інакше так і лишалась над фільмом)
        (bar.content if isinstance(bar, ft.WindowDragArea) else bar).bgcolor = None
        bar_box = ft.Container(
            bar, left=0, top=0, right=0, opacity=1, animate_opacity=ui.MED,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                       end=ft.Alignment.BOTTOM_CENTER,
                                       colors=["#CC000000", "#00000000"]))

        async def hide_bar_later(token):
            await asyncio.sleep(2.5)
            if p.get("bar_token") == token and alive() and bar_box.opacity:
                bar_box.opacity = 0
                bar_box.update()

        def on_move(e):
            quiet()
            p["bar_token"] = p.get("bar_token", 0) + 1
            if not bar_box.opacity:
                bar_box.opacity = 1
                bar_box.update()
            page.run_task(hide_bar_later, p["bar_token"])

        # гучність (стрілки ↑/↓ — їх обробляє сам плеєр, по 5 %): плашка праворуч угорі
        vol_icon = ft.Icon(ft.Icons.VOLUME_UP_ROUNDED, size=18, color="#E6E8EC")
        vol_bar = ft.ProgressBar(value=1, width=90, bar_height=4, border_radius=2,
                                 color="#E6E8EC", bgcolor="#33FFFFFF")
        vol_text = ft.Text("100", size=12, color="#C9CDD4", width=26,
                           text_align=ft.TextAlign.RIGHT)
        hud = ft.Container(
            ft.Row([vol_icon, vol_bar, vol_text], spacing=10, tight=True),
            top=22, right=22, opacity=0, animate_opacity=ui.FAST,
            ignore_interactions=True,  # не перехоплювати кліки по кнопках під нею
            padding=ft.Padding.symmetric(horizontal=14, vertical=10), border_radius=12,
            bgcolor="#99101114", border=ft.Border.all(1, "#1FFFFFFF"))
        p["vol"] = 100

        async def hide_hud_later(token):
            await asyncio.sleep(1.2)
            if p.get("hud_token") == token and alive():
                hud.opacity = 0
                hud.update()

        def volume_hud(delta):
            p["vol"] = max(0, min(100, p["vol"] + delta))
            v = p["vol"]
            vol_icon.name = (ft.Icons.VOLUME_OFF_ROUNDED if v == 0 else
                             ft.Icons.VOLUME_DOWN_ROUNDED if v < 50 else ft.Icons.VOLUME_UP_ROUNDED)
            vol_bar.value = v / 100
            vol_text.value = str(v)
            hud.opacity = 1
            hud.update()
            p["hud_token"] = p.get("hud_token", 0) + 1
            page.run_task(hide_hud_later, p["hud_token"])

        st["volume_hud"] = volume_hud

        # ── міні-плеєр: вікно 480×270 поверх інших у правому нижньому куті поточного вікна;
        #    повторне натискання (чи вихід із плеєра) — назад до попереднього розміру й місця
        mini = {"on": False, "saved": None}

        def bars_aspect(raw):
            """Співвідношення сторін картинки без чорних смуг зверху й знизу з сирого кадру
            BGRA (ширина невідома — підбираємо типову, з якою кадр ділиться націло)."""
            n = len(raw)
            for fw in (3840, 2560, 1920, 1600, 1280, 1024, 960, 854, 848, 720, 640):
                if n % (fw * 4) == 0 and 1.2 < fw / (n // (fw * 4)) < 2.8:
                    break
            else:
                return None
            fh = n // (fw * 4)
            xs = range(fw // 20, fw - fw // 20, fw // 20)

            def lit(y):
                o = y * fw * 4
                return max(max(raw[o + x * 4], raw[o + x * 4 + 1], raw[o + x * 4 + 2])
                           for x in xs) > 28

            top = next((y for y in range(fh) if lit(y)), None)
            if top is None:
                return None  # чорний кадр (заставка) — нічого не скажеш
            bottom = next(y for y in range(fh - 1, -1, -1) if lit(y))
            if abs(top - (fh - 1 - bottom)) > fh * 0.04:
                return fw / fh  # смуги несиметричні — це не смуги, а темна сцена
            return fw / (bottom - top + 1)

        async def video_aspect():
            """Справжнє співвідношення картинки (фільми часто закодовано в кадр 16:9 з чорними
            смугами) — з одного знімка кадру; для того самого відео запам'ятовується."""
            if p.get("aspect_for") and p["aspect_for"][0] == p["idx"]:
                return p["aspect_for"][1]
            try:
                raw = await video.take_screenshot(format=None)
            except Exception:
                raw = None
            a = await asyncio.to_thread(bars_aspect, raw) if raw else None
            if a and 1.3 < a < 2.7:
                p["aspect_for"] = (p["idx"], a)
                return a
            return None

        async def fit_mini_to_picture():
            """Мініатюра вже на екрані (16:9, зі смугами) — плавно прибрати чорні смуги:
            верхній і нижній краї вікна сходяться до картинки, а вона сама стоїть на місці
            (тому нерівний темп кадрів не помітний)."""
            a = await video_aspect()
            w = page.window
            if not a or not mini["on"] or mini.get("peek"):
                return
            h0, t0 = w.height or 0, w.top or 0
            mh = int((w.width or 480) / a)
            if abs(mh - h0) <= 3:
                return
            steps = 18
            for i in range(1, steps + 1):
                x = i / steps
                k = 3 * x * x - 2 * x ** 3  # плавно розганяється й гальмує
                h = h0 + (mh - h0) * k
                w.height, w.top = h, t0 + (h0 - h) / 2
                page.update()
                await asyncio.sleep(0.018)

        # у мініатюрі вікно тягнеться за будь-яке місце відео (крім нижньої панелі кнопок).
        # Шар ще й забирає прокрутку: переміщення трьома пальцями на тачпаді macOS частково
        # приходить як прокрутка, а над відео вона змінювала гучність
        mini_drag = ft.WindowDragArea(
            ft.Container(bgcolor=ft.Colors.with_opacity(0.01, "#000000"),
                         on_hover=lambda e: on_move(e)),
            maximizable=False, left=0, top=0, right=0, bottom=64, visible=False)

        # міні-плеєр можна сховати за правий край екрана — видно лише смужку; клік по ній
        # повертає на попереднє місце
        PEEK = 28
        STRIP_BG = ft.Colors.with_opacity(0.55, "#000000")

        async def morph_frame(left, top, width, height):
            """Перехід вікна в нове положення й розмір без «тряски»: покадрова зміна розміру
            через Flet смикається (кожен кадр — перебудова відео), тож вікно м'яко
            зникає, миттєво стає новим, а вміст проявляється з легким наближенням."""
            w = page.window
            # масштаб і його анімація задані плеєру від створення: якщо додати їх лише тут,
            # Flet обгортає плеєр новим віджетом і відео перезапускається
            player_stack.scale = 0.92
            for o in (0.75, 0.45, 0.15, 0.0):
                w.opacity = o
                page.update()
                await asyncio.sleep(0.016)
            w.left, w.top, w.width, w.height = left, top, width, height
            page.update()
            await asyncio.sleep(0.05)  # дати вікну стати новим, поки воно прозоре
            player_stack.scale = 1
            page.update()
            for o in (0.3, 0.6, 0.85, 1.0):
                w.opacity = o
                page.update()
                await asyncio.sleep(0.02)

        async def slide(to_left, ms, ease_in):
            """Плавно пересунути вікно по горизонталі (кадри ~60 Гц)."""
            w = page.window
            start = w.left or 0
            steps = max(1, ms // 16)
            for i in range(1, steps + 1):
                t = i / steps
                k = t ** 3 if ease_in else 1 - (1 - t) ** 3
                w.left = start + (to_left - start) * k
                page.update()
                await asyncio.sleep(0.016)

        async def set_peek(on):
            w = page.window
            if mini.get("peek_busy"):
                return
            mini["peek_busy"] = True
            try:
                if on and not mini.get("peek"):
                    ax, ay, aw, ah = mini.get("area") or (0, 0, (w.left or 0) + (w.width or 0), 0)
                    edge = ax + aw - PEEK
                    mini["peek"] = (w.left, w.top)
                    peek_btn.visible = False
                    bar_box.opacity = 0
                    peek_cover.visible = True
                    page.update()
                    await slide(edge, 260, ease_in=True)  # з розгону — у край
                    # дотик: смужка спалахує, вікно ледь відскакує й «прилипає»
                    strip.bgcolor = ACCENT
                    page.update()
                    await slide(edge - 14, 90, ease_in=False)
                    await slide(edge, 140, ease_in=True)
                    await asyncio.sleep(0.15)
                    strip.bgcolor = STRIP_BG
                    page.update()
                elif not on and mini.get("peek"):
                    left, top = mini.pop("peek")
                    await slide(left, 300, ease_in=False)  # виїжджає й плавно гальмує
                    peek_cover.visible = False
                    peek_btn.visible = mini["on"]
                    page.update()
            finally:
                mini["peek_busy"] = False

        def drop_peek():
            """Розгортання з мініатюри: просто забути «сховане» положення."""
            mini.pop("peek", None)
            peek_cover.visible = False
            peek_btn.visible = False
            strip.bgcolor = STRIP_BG

        strip = ft.Container(ft.Icon(ft.Icons.CHEVRON_LEFT_ROUNDED, color="#FFFFFF", size=22),
                             width=PEEK, alignment=ft.Alignment.CENTER, bgcolor=STRIP_BG,
                             animate=ft.Animation(220, ft.AnimationCurve.EASE_OUT))
        peek_cover = ft.Container(
            strip, left=0, top=0, right=0, bottom=0, alignment=ft.Alignment.CENTER_LEFT,
            visible=False, tooltip="Повернути міні-плеєр",
            on_click=lambda e: page.run_task(set_peek, False))
        peek_btn.on_click = lambda e: page.run_task(set_peek, True)

        async def toggle_mini(restore_fs=True, animate=True):
            w = page.window
            if not mini["on"]:
                was_fs = bool(w.full_screen)
                mini["saved"] = dict(width=w.width, height=w.height, left=w.left, top=w.top,
                                     min_width=w.min_width, min_height=w.min_height,
                                     maximized=w.maximized, full_screen=was_fs)
                screen_box = dict(mini["saved"])  # де шукати монітор
                if was_fs and st.get("pre_fs"):
                    # з повного екрана: «звичайний» розмір — той, що був до нього (інакше після
                    # повернення й виходу з повного екрана вікно лишалось на весь екран)
                    mini["saved"].update(st["pre_fs"])
                mini["on"] = p["mini_on"] = True
                peek_btn.visible = True
                mini_drag.visible = True
                back_btn.icon, back_btn.tooltip = ft.Icons.CLOSE_ROUNDED, "Закрити"
                mini_btn.icon, mini_btn.tooltip = ft.Icons.OPEN_IN_FULL_ROUNDED, "Розгорнути"
                if was_fs:
                    # вихід із повного екрана на Mac — анімація ~0,7 с; розмір, заданий під
                    # час неї, macOS перебиває, і мініатюра лишалась на весь екран
                    w.full_screen = False
                    page.update()
                    await asyncio.sleep(1.0)
                if w.maximized:
                    w.maximized = False
                # чверть екрана (пів ширини, 16:9) у правому нижньому куті робочої області
                # монітора (без рядка меню, Dock і панелі завдань)
                sv = screen_box
                cx = (sv["left"] or 0) + (sv["width"] or 0) / 2
                cy = (sv["top"] or 0) + (sv["height"] or 0) / 2
                area = await asyncio.to_thread(screen.work_area, cx, cy) or \
                    (sv["left"] or 0, sv["top"] or 0, sv["width"] or 960, sv["height"] or 540)
                ax, ay, aw, ah = area
                # спершу 16:9 зі смугами (масштаб відео не міняємо — це давало ривок), а
                # смуги плавно прибирає fit_mini_to_picture уже після появи
                mw = max(480, int(aw / 2))
                mh = int(mw * 9 / 16)
                mini["area"] = area
                w.min_width, w.min_height = 320, 120
                w.always_on_top = True
                page.update()
                await morph_frame(ax + aw - mw - 16, ay + 16, mw, mh)  # угорі праворуч
                if is_mac:
                    w.title_bar_buttons_hidden = True  # «світлофор» у мініатюрі заважає
                for b in player_win_btns:  # як і кнопки вікна Windows
                    b.visible = False
                page.update()
                page.run_task(fit_mini_to_picture)
            else:
                sv = mini["saved"] or {}
                mini["on"] = p["mini_on"] = False
                mini_drag.visible = False
                back_btn.icon, back_btn.tooltip = ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED, "Назад"
                drop_peek()
                w.always_on_top = False
                if is_mac:
                    w.title_bar_buttons_hidden = False
                for b in player_win_btns:
                    b.visible = True
                if animate and None not in (sv.get("left"), sv.get("top"), sv.get("width"),
                                            sv.get("height")):
                    await morph_frame(sv["left"], sv["top"], sv["width"], sv["height"])
                for k in ("min_width", "min_height", "width", "height", "left", "top"):
                    if sv.get(k) is not None:
                        setattr(w, k, sv[k])
                if sv.get("maximized"):
                    w.maximized = True
                mini_btn.icon, mini_btn.tooltip = ft.Icons.PICTURE_IN_PICTURE_ALT_ROUNDED, \
                    "Міні-плеєр"
                if sv.get("full_screen") and restore_fs:
                    page.update()
                    await asyncio.sleep(0.3)
                    w.full_screen = True  # був повний екран — туди й повертаємось
            page.update()

        p["toggle_mini"] = toggle_mini

        async def close_mini():
            """Закрити мініатюру: вікно одразу стає прозорим (розгортання не видно), плеєр
            закривається, а застосунок у звичайному розмірі згортається в Dock / панель
            завдань. Ховати вікно (visible=False) не можна: на Mac це закриває застосунок."""
            w = page.window
            w.opacity = 0
            page.update()
            await asyncio.sleep(0.15)
            await toggle_mini(False, animate=False)  # повернути розмір, поки вікно прозоре
            st["mini_restore"] = None
            go_back()
            for _ in range(50):  # дочекатись, поки плеєр збереже прогрес і закриється
                if not (page.views and page.views[-1].route == "/play"):
                    break
                await asyncio.sleep(0.1)
            # macOS запам'ятовує вигляд вікна в момент згортання і показує його при
            # розгортанні — спершу дати домалювати сторінку фільму (вікно ще прозоре)
            page.update()
            await asyncio.sleep(0.9)
            w.minimized = True
            page.update()
            await asyncio.sleep(0.6)  # уже в Dock — повернути непрозорість
            w.opacity = 1
            page.update()

        p["close_mini"] = close_mini
        # вихід із плеєра в міні-режимі — спершу повернути вікно
        # (без повернення в повний екран — плеєр закривається, звідти виходимо й так)
        st["mini_restore"] = lambda: page.run_task(toggle_mini, False) if mini["on"] else None
        page.run_task(hide_bar_later, 0)

        async def pip_watch():
            """Телефон: плаваюче вікно при згортанні — лише якщо відео грає (на паузі — ні)."""
            last = None
            while alive():
                try:
                    playing = bool(await video.is_playing())
                except Exception:
                    playing = last
                if playing is not None and playing != last:
                    set_pip(playing)
                    last = playing
                await asyncio.sleep(1)

        if is_mobile and is_android:
            page.run_task(pip_watch)
        return ft.GestureDetector(
            player_stack := ft.Stack(
                [video, mini_drag, bar_box, hud, next_card, peek_cover], expand=True, scale=1,
                animate_scale=ft.Animation(260, ft.AnimationCurve.EASE_OUT_CUBIC)),
            on_hover=on_move, expand=True)

    # ═════════════════════ навігація ═════════════════════
    def go_back():
        if len(page.views) <= 1:
            return
        if st.get("leaving") is page.views[-1]:
            return  # плеєр уже закривається (зберігаємо, де зупинились) — друге «Назад» не
            #         має закрити ще й сторінку фільму
        if page.views[-1].route == "/play" and st.get("save_progress"):
            page.run_task(leave_player)  # спершу зберегти, на якій хвилині зупинились
            return
        pop_view()

    async def leave_player():
        st["leaving"] = page.views[-1]
        save, st["save_progress"] = st.get("save_progress"), None
        try:
            await asyncio.wait_for(save(), 2)
        except Exception:
            pass
        pop_view()
        st["leaving"] = None
        page.run_task(sync_now)  # де зупинились — одразу на інші пристрої

    def cover_views():
        """Телевізор: екрани під верхнім — неактивні, щоб загублений фокус пульта не потрапив
        на їхні кнопки (OK натискав «Назад» сторінки фільму під плеєром, і екрани
        розходились: видно сторінку, а плеєр грає далі)."""
        if not TV:
            return
        for i, v in enumerate(page.views):
            if v.controls:
                v.controls[0].disabled = i < len(page.views) - 1

    def pop_view():
        if len(page.views) <= 1:
            return
        if page.views[-1].route == "/play":
            set_pip(False)
            if st.get("mini_restore"):
                st["mini_restore"]()
                st["mini_restore"] = None
            if st.get("win_fullscreen"):
                if st.get("fullscreen"):
                    st["win_fullscreen"](False)  # із плеєра — назад у звичайне вікно
                st["win_fullscreen"] = None
        page.views.pop()
        cover_views()
        top = page.views[-1]
        if isinstance(top.data, dict) and top.data.get("refresh"):
            top.data["refresh"]()
        render_continue()
        if len(page.views) == 1 and st["mode"] in (("cat", FAV_PATH), ("cat", HISTORY_PATH)):
            page.run_task(reload)  # могли прибрати з обраного / додати в історію
        elif TV and len(page.views) == 1 and st["mode"][0] != "home" and st.get("return_focus"):
            page.run_task(focus_later, st["return_focus"])
        page.update()

    BACK_KEYS = ("Escape", "Go Back", "Browser Back")
    # гучність, вимкнення звуку, живлення — панель плеєра не показують
    QUIET_KEYS = ("Audio Volume Up", "Audio Volume Down", "Audio Volume Mute", "Volume Up",
                  "Volume Down", "Volume Mute", "Power", "Sleep", "Wake Up")

    async def on_key(e: ft.KeyboardEvent):
        quiet()  # що змінюється — оновлюється явно (див. quiet)
        if (not TV and not is_mobile and e.key in ("Arrow Up", "Arrow Down")
                and page.views and page.views[-1].route == "/play" and st.get("volume_hud")):
            st["volume_hud"](5 if e.key == "Arrow Up" else -5)
        if TV and st.get("drawer_open") and tv_nav and e.key in ("Arrow Up", "Arrow Down"):
            first, last = tv_nav[0][1], tv_nav[-1][1]
            if e.key == "Arrow Up" and st.get("nav_focus") == first:
                nav_edge_back(True)
                return
            if e.key == "Arrow Down" and st.get("nav_focus") == last:
                nav_edge_back(False)
                return
        if TV and e.key == "Arrow Down" and st.get("tv_search_field") and st.get("tv_search_down"):
            st["tv_search_field"] = False
            await st["tv_search_down"].focus()  # одразу: поле стрілку вже «з'їло»
            return
        if TV and e.key == "Arrow Down" and st.get("search_focused") and len(page.views) == 1:
            try:
                # з «Пошуку» в правому куті — на «Спробувати ще», якщо вона на екрані
                if st.get("retry_btn") is not None and st["retry_btn"].page is not None:
                    # з невеликою затримкою: інакше Flutter ще раз обробляв ту саму стрілку
                    # і повертав фокус на «Пошук»
                    page.run_task(focus_later, st["retry_btn"])
                    return
            except Exception:
                pass
        if TV and page.views and page.views[-1].route == "/play":
            if st.get("tv_activity") and e.key not in BACK_KEYS and e.key not in QUIET_KEYS:
                st["tv_activity"]()  # кнопка пульта (крім «Назад» і гучності) показує панель
            if st.get("tv_key") and await st["tv_key"](e.key):
                return
            if e.key in ("Media Play Pause", "Media Play", "Media Pause") and st.get("play_pause"):
                st["play_pause"]()
                return
            if e.key in ("Media Fast Forward", "Media Rewind") and st.get("seek_by"):
                await st["seek_by"](30 if e.key == "Media Fast Forward" else -10)
                return
        if e.key == "Escape" and st.get("menu_open"):
            st["menu_open"] = False  # Esc лише закриває відкрите меню
        elif e.key == "Escape" and (st.get("fullscreen") or
                                    time.monotonic() - st.get("fullscreen_exit", 0) < 0.8):
            # Esc у повноекранному режимі — повертаємось до звичайного плеєра, а не до серій
            video = st.get("video")
            if st.get("fullscreen") and st.get("win_fullscreen"):
                st["win_fullscreen"](False)
            elif st.get("fullscreen") and video is not None:
                video.fullscreen = False
                video.update()
            st["fullscreen"] = False
        elif e.key == "Escape" and len(page.views) == 1 and st["mode"][0] != "home":
            back_home()  # з розділу чи пошуку — на головну одним натисканням
        elif e.key == "Escape" and len(page.views) > 1:
            player_back() if page.views[-1].route == "/play" else go_back()
        elif e.key.upper() == "F" and (e.meta or e.ctrl) and len(page.views) == 1:
            await search.focus()

    def is_tablet():
        """Планшет (а не телефон): коротша сторона екрана від 600 точок — як визначає сам
        Android. Рахуємо один раз (на старті), щоб розділений екран не плутав."""
        if "tablet" not in st and page.width and page.height:
            st["tablet"] = is_mobile and min(page.width, page.height) >= 600
        return st.get("tablet", False)

    def set_pip(on):
        """Телефон: дозволити / заборонити плаваюче вікно з відео (MainActivity, див.
        tools/android_template.py)."""
        if is_mobile and is_android:
            page.run_task(ft.UrlLauncher().launch_url, f"uafixupdate://pip?on={1 if on else 0}",
                          mode=ft.LaunchMode.EXTERNAL_APPLICATION)

    st["set_pip"] = set_pip

    def on_lifecycle(e):
        quiet()
        if is_mobile and page.views and page.views[-1].route == "/play" and \
                e.state in (ft.AppLifecycleState.HIDE, ft.AppLifecycleState.PAUSE):
            # плаваюче вікно закрили / застосунок пішов у фон — пауза (у самому плаваючому
            # вікні застосунок лише «неактивний», і відео грає далі)
            if st.get("video") is not None:
                page.run_task(st["video"].pause)
            return
        if not TV or not page.views or page.views[-1].route != "/play":
            return
        if e.state in (ft.AppLifecycleState.HIDE, ft.AppLifecycleState.PAUSE):
            if st.get("tv_suspend"):
                st["tv_suspend"]()
        elif e.state in (ft.AppLifecycleState.SHOW, ft.AppLifecycleState.RESUME):
            if st.get("tv_resume"):
                st["tv_resume"]()

    page.on_app_lifecycle_state_change = on_lifecycle
    page.on_view_pop = lambda e: go_back()
    page.on_keyboard_event = on_key
    page.views.clear()
    page.views.append(home)
    page.update()
    if is_desktop and first:
        page.run_task(page.window.center)
    page.run_task(startup)
    go_home()
    if reopen == "settings":
        page.run_task(open_settings)

    def rebuild_with_theme():
        """Нова тема — одразу: перечитати палітру й перебудувати інтерфейс (без перезапуску)."""
        import importlib
        appearance.CURRENT = appearance.load()
        importlib.reload(ui)
        for name in UI_COLORS:
            globals()[name] = getattr(ui, name)
        main(page, reopen="settings")  # старі екрани main замінить новими наприкінці

    st["rebuild_with_theme"] = rebuild_with_theme


if __name__ == "__main__":
    ft.run(main)
