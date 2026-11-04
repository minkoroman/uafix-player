"""Візуальна мова «Табло кінотеатру».

Темний теплий фасад; табло кольору слонової кістки з чорними наборними літерами; червоний
сигнал — лише там, де він щось означає (час до кінця, вибране, помилка). Одна гарнітура
(Golos Text), ранг — вагою й регістром, а не десятком кеглів.
"""
import os

import flet as ft

import appearance

LIGHT = appearance.CURRENT.get("theme") == "light"

try:  # TV-збірка додає модуль-прапорець; на ПК — UAFIX_TV=1
    import uafix_tv_flag  # noqa: F401
    TV = True
except ImportError:
    TV = os.environ.get("UAFIX_TV") == "1"

FONT = "Golos Text"
FONT_FILE = "fonts/GolosText.ttf"

if LIGHT:  # денний фасад: світлий камінь, табло — темне з літерами кольору кістки
    FACADE = "#EAE3D5"     # тло
    WALL = "#F4EFE5"       # панелі
    WALL_2 = "#FBF8F2"     # картки, поля
    LINE = "#D8CEBB"       # лінії
    INK = "#1A1714"        # основний текст
    DIM = "#6B6152"        # другорядний
    SIGNAL = "#B8321B"     # червоний сигнал
    BOARD = "#1F1B17"      # табло
    LETTER = "#F1E6CC"     # літери табло
    BOARD_LINE = "#3A332C"
    BOARD_DIM = "#B8AC95"
    BOARD_SIGNAL = "#FF7A5C"
else:
    FACADE = "#13100E"
    WALL = "#1C1815"
    WALL_2 = "#252019"
    LINE = "#352D26"
    INK = "#F1E6CC"
    DIM = "#A89C86"
    SIGNAL = "#E0492E"
    BOARD = "#F1E6CC"
    LETTER = "#1A1714"
    BOARD_LINE = "#D6C7A6"
    BOARD_DIM = "#6B5E4B"
    BOARD_SIGNAL = "#B8321B"

STAR = "#E8B04B"

# розміри: TV — читати з 2–3 м; телефон — компактно (configure — на старті, коли вже
# відомо, на чому працюємо)
S_BOARD, S_TITLE, S_HEAD, S_BODY, S_SMALL, S_KICK = 20, 40, 16, 15, 13, 12
MOBILE = False


def configure(mobile):
    global S_BOARD, S_TITLE, S_HEAD, S_BODY, S_SMALL, S_KICK, MOBILE
    MOBILE = mobile
    if TV:
        S_BOARD, S_TITLE, S_HEAD, S_BODY, S_SMALL, S_KICK = 19, 34, 15, 15, 12, 11
    elif mobile:
        S_BOARD, S_TITLE, S_HEAD, S_BODY, S_SMALL, S_KICK = 16, 28, 15, 15, 13, 11

FAST = ft.Animation(140, ft.AnimationCurve.EASE_OUT)


def theme():
    return ft.Theme(
        font_family=FONT,
        color_scheme_seed=SIGNAL,
        color_scheme=ft.ColorScheme(primary=SIGNAL, surface=FACADE, on_surface=INK,
                                    on_surface_variant=DIM, surface_tint=ft.Colors.TRANSPARENT),
        navigation_bar_theme=ft.NavigationBarTheme(
            bgcolor=WALL, indicator_color=BOARD, height=68,
            label_text_style={ft.ControlState.SELECTED: ft.TextStyle(
                                  color=INK, size=12, weight=ft.FontWeight.W_800),
                              ft.ControlState.DEFAULT: ft.TextStyle(
                                  color=INK, size=12, weight=ft.FontWeight.W_600)}),
        scrollbar_theme=ft.ScrollbarTheme(thumb_color=ft.Colors.with_opacity(0.25, INK),
                                          thickness=5, radius=0),
        page_transitions=ft.PageTransitionsTheme(
            macos=ft.PageTransitionTheme.FADE_FORWARDS,
            windows=ft.PageTransitionTheme.FADE_FORWARDS,
            android=ft.PageTransitionTheme.FADE_FORWARDS,
        ),
    )


def caps(text, size=None, color=None, weight=ft.FontWeight.W_800, spacing=0.08, **kw):
    """Напис табло: великі літери, розріджено."""
    size = size or S_HEAD
    return ft.Text((text or "").upper(), size=size, weight=weight, color=color or INK,
                   style=ft.TextStyle(letter_spacing=size * spacing), **kw)


def text(value, size=None, color=None, weight=ft.FontWeight.W_400, **kw):
    size = size or S_BODY
    return ft.Text(value, size=size, color=color or INK, weight=weight, **kw)
