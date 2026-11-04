"""Службова схема посилань до MainActivity (Tor, «картинка в картинці», інша програма,
встановлення оновлення). Тестова збірка, що ставиться поруч зі звичайною, має свою схему
(модуль-прапорець uafix_side.py) — інакше Android передавав би ці команди іншому застосунку."""
try:
    from uafix_side import SCHEME as CTRL  # noqa: F401
except ImportError:
    CTRL = "uafixupdate"
