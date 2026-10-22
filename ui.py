"""Дизайн-токени та повторно використовувані компоненти."""
import flet as ft

import appearance

# ───────────── палітра ─────────────
LIGHT = appearance.CURRENT["theme"] == "light"
if LIGHT:
    # 2.0: світла — як нативні застосунки macOS: білий вміст, світло-сіра бічна панель
    BG = "#FFFFFF"
    SURFACE = "#F5F5F7"
    SURFACE_2 = "#F2F2F4"
    SURFACE_3 = "#E8E8ED"
    BORDER = "#1A000000"
    TEXT = "#1D1D1F"
    MUTED = "#6E6E73"
    ACCENT = "#D9452F"  # трохи темніший — контрастніший на білому
else:
    # 2.0 «Полиці»: стриманий графіт і корал
    BG = "#101114"
    SURFACE = "#15161A"
    SURFACE_2 = "#191B20"
    SURFACE_3 = "#23252B"
    BORDER = "#2A2C33"
    TEXT = "#F4F4F5"
    MUTED = "#A1A1AA"
    ACCENT = "#E5533D"
ACCENT_2 = "#FF8A6B"
GOLD = "#F5C518"

EASE = ft.AnimationCurve.EASE_OUT_CUBIC
FAST = ft.Animation(160, EASE)
MED = ft.Animation(280, EASE)
SLOW = ft.Animation(450, EASE)
APPEAR = ft.Animation(250, EASE)

# «легкий» режим для слабких пристроїв (телевізори): без анімацій появи
LITE = False

ACCENT_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT, colors=[ACCENT_2, ACCENT])


FONT = "Inter"  # assets/fonts/Inter.ttf (SIL OFL), реєструється в main через page.fonts


def theme():
    return ft.Theme(
        font_family=FONT,
        color_scheme_seed=ACCENT,
        # surface_tint прозорий: Material 3 інакше домішує оранжевий до фону меню й вікон
        color_scheme=ft.ColorScheme(primary=ACCENT, surface=BG, on_surface=TEXT,
                                    on_surface_variant=MUTED,
                                    surface_tint=ft.Colors.TRANSPARENT),
        page_transitions=ft.PageTransitionsTheme(
            macos=ft.PageTransitionTheme.FADE_FORWARDS,
            windows=ft.PageTransitionTheme.FADE_FORWARDS,
            linux=ft.PageTransitionTheme.FADE_FORWARDS,
            android=ft.PageTransitionTheme.PREDICTIVE,
            ios=ft.PageTransitionTheme.CUPERTINO,
        ),
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_color=ft.Colors.with_opacity(0.25, TEXT), thickness=6, radius=3),
    )


def menu_style():
    """Випадаючий список: фон як у вікон застосунку (без брунатного відтінку Material 3)."""
    return ft.MenuStyle(
        bgcolor=SURFACE_3,
        shadow_color=ft.Colors.with_opacity(0.4, "#000000"), elevation=8,
        shape=ft.RoundedRectangleBorder(radius=12), side=ft.BorderSide(1, BORDER),
        padding=ft.Padding.symmetric(vertical=6))


def option_style():
    """Пункт випадаючого списку: вибраний / під курсором — ледь світліший фон того ж тону."""
    soft = ft.Colors.with_opacity(0.08, TEXT)
    return ft.ButtonStyle(
        color=TEXT,
        bgcolor={ft.ControlState.SELECTED: soft, ft.ControlState.FOCUSED: soft,
                 ft.ControlState.HOVERED: soft, ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
        overlay_color=ft.Colors.with_opacity(0.06, TEXT),
        padding=ft.Padding.symmetric(horizontal=16, vertical=14))


def hovered(e):
    return e.data in (True, "true")


def pill(text, icon=None, color=MUTED, bg=SURFACE_2, icon_color=None, bold=False):
    """Невеликий бейдж: «★ 8.3», «2024», «18+»."""
    row = []
    if icon:
        row.append(ft.Icon(icon, size=14, color=icon_color or color))
    row.append(ft.Text(text, size=12, color=color,
                       weight=ft.FontWeight.W_700 if bold else ft.FontWeight.W_500))
    return ft.Container(
        ft.Row(row, spacing=4, tight=True),
        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
        border_radius=20, bgcolor=bg, border=ft.Border.all(1, BORDER),
    )


def glass_button(icon, on_click, tooltip=None, size=38, blur=True):
    """Кругла напівпрозора кнопка поверх зображень."""
    c = ft.Container(
        ft.Icon(icon, size=18, color="#FFFFFF"),  # поверх фото / темного кола — завжди біла
        width=size, height=size, border_radius=size / 2,
        bgcolor=ft.Colors.with_opacity(0.45, "#000000"),
        border=ft.Border.all(1, BORDER),
        blur=ft.Blur(12, 12) if blur else None,  # на телефоні без розмиття — дорого для GPU
        alignment=ft.Alignment.CENTER,
        tooltip=tooltip, on_click=on_click,
        animate_scale=FAST, scale=1,
    )

    def hover(e):
        c.scale = 1.08 if hovered(e) else 1
        c.update()

    c.on_hover = hover
    return c


def poster_image(src, radius=0, cache_width=None, **kw):
    """Постер з фоном-заглушкою, поки картинка вантажиться (або якщо її нема).
    cache_width — декодувати картинку під розмір картки, а не в повні 1200 px:
    інакше кеш зображень Flutter переповнюється і постери «зникають» при прокрутці."""
    fallback = ft.Container(
        ft.Icon(ft.Icons.MOVIE_FILTER_ROUNDED, size=36, color=SURFACE_3),
        alignment=ft.Alignment.CENTER, bgcolor=SURFACE_2, expand=True)
    if not src:
        return ft.Container(fallback, border_radius=radius, **kw)
    return ft.Container(
        ft.Image(src=src, fit=ft.BoxFit.COVER, gapless_playback=True,
                 error_content=fallback, expand=True, cache_width=cache_width),
        bgcolor=SURFACE_2, border_radius=radius,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS, **kw,
    )


def appear(control):
    """Готує контрол до появи (fade + легкий підйом). Викликати reveal() після update()."""
    if LITE:
        return control
    control.opacity = 0
    control.offset = ft.Offset(0, 0.04)
    control.animate_opacity = APPEAR
    control.animate_offset = APPEAR
    return control


def reveal(control):
    control.opacity = 1
    control.offset = ft.Offset(0, 0)
