"""Запуск нового інтерфейсу (2.0) поруч зі старим: python app2.py"""
import locale

try:  # mpv не працює з десятковою комою в числовій локалі
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass

import flet as ft

from uafix.app import run

if __name__ == "__main__":
    ft.run(run, assets_dir="assets")
