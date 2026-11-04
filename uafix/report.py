"""«Повідомити про проблему»: вікно з видом звіту й текстом → автору в Telegram (feedback)."""
import asyncio

import flet as ft

import core
import feedback
import tor_runner
from . import kit, theme as T

ISSUES_URL = "https://github.com/minkoroman/uafix-player/issues/new"


def geo_note():
    """Для звіту: чи застосунок за кордоном і як справи з обходом."""
    g, a = core.GEO, core.ABROAD
    if not (g["site"] or g["blocked"]):
        return ""
    cdn_off = [h for h, ok in g.get("cdn", {}).items() if not ok]
    return (f" · регіон обмежено (сайт: {'так' if g['site'] else 'ні'}, ashdi: "
            f"{'так' if g['blocked'] else 'ні'}, обхід: "
            f"{'вимкнено' if not a['enabled'] else 'Tor ' + ('є' if a.get('circuit') else 'немає')}"
            f"{' — ' + tor_runner.last_error() if tor_runner.last_error() else ''}"
            f"{'; остання спроба: ' + a['last'] if a.get('last') else ''}"
            f"{'; без відео з: ' + ', '.join(cdn_off) if cdn_off else ''})")


def open_report(app):
    if not feedback.enabled():  # збірка без ключів бота — форма на GitHub
        app.page.run_task(ft.UrlLauncher().launch_url, ISSUES_URL)
        return
    kind = {"value": "bug"}
    kinds = ft.Row(spacing=8, wrap=True)

    def render_kinds():
        kinds.controls = [kit.plate(kit.label(name.split(" ", 1)[-1]),
                                    on_click=lambda e, k=k: (kind.update(value=k), render_kinds(),
                                                             kinds.update()),
                                    kind="lit" if k == kind["value"] else "plain", pad=(14, 10))
                          for k, name in feedback.KINDS.items()]

    render_kinds()
    text = ft.TextField(multiline=True, min_lines=4, max_lines=8, autofocus=not app.tv,
                        hint_text="Що сталося? Який фільм / серія, що натискали…",
                        bgcolor=T.WALL_2, border_color=T.LINE, focused_border_color=T.SIGNAL,
                        border_radius=3, color=T.INK, text_size=T.S_BODY)
    status = T.text("", size=T.S_SMALL, color=T.SIGNAL)
    platform = {ft.PagePlatform.ANDROID: "android", ft.PagePlatform.ANDROID_TV: "android",
                ft.PagePlatform.MACOS: "macos", ft.PagePlatform.WINDOWS: "windows"}.get(
        app.page.platform, str(app.page.platform))
    try:
        from app_version import VERSION
    except Exception:
        VERSION = "dev"
    signature = f"UAFix Player {VERSION} · {platform}{' (TV)' if app.tv else ''} · 2.0" + geo_note()

    async def send(e):
        status.value, status.color = "Надсилаю…", T.DIM
        status.update()
        attach = None
        if geo_note() or tor_runner.last_error():  # проблеми з регіоном — додаємо діагностику
            def diag():
                return ("uafix-diagnostics.txt",
                        f"{signature}\n\n{core.diagnostics(tor_runner.current_proxy())}\n\n"
                        f"--- Tor ({tor_runner.last_error() or 'без помилки'}) ---\n"
                        f"{tor_runner.log_tail()}")
            attach = await asyncio.to_thread(diag)
        try:
            await asyncio.to_thread(feedback.send, kind["value"], text.value, signature, attach)
        except ValueError as ex:
            status.value, status.color = str(ex), T.SIGNAL
            status.update()
            return
        app.page.pop_dialog()
        app.toast("Дякую! Повідомлення надіслано")

    app.page.show_dialog(ft.AlertDialog(
        bgcolor=T.WALL, shape=ft.RoundedRectangleBorder(radius=4),
        title=T.caps("Повідомити про проблему", size=T.S_HEAD),
        content=ft.Container(ft.Column([kinds, text, status], spacing=12, tight=True), width=560),
        actions=[kit.plate(kit.label("Скасувати"), lambda e: app.page.pop_dialog()),
                 kit.plate(kit.label("Надіслати", ft.Icons.SEND_ROUNDED),
                           lambda e: app.page.run_task(send, e), kind="board")]))
