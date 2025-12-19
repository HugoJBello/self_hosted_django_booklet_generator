#!/usr/bin/env python3
import os
import sys


def _print_clickable_url_if_runserver():
    """
    Imprime una URL clicable cuando arrancas con: python manage.py runserver ...
    Funciona siempre, independientemente de overrides de management commands.
    """
    if len(sys.argv) < 2:
        return
    if sys.argv[1] != "runserver":
        return

    # Subpath fijo de tu app
    subpath = os.environ.get("PDF_MANAGER_SUBPATH", "/pdf_manager").rstrip("/")

    # Por defecto Django usa 127.0.0.1:8000 si no se indica nada
    host = "127.0.0.1"
    port = "8000"

    # Busca si el usuario pasó addr:port o solo port en argumentos
    # Ejemplos válidos:
    #   runserver
    #   runserver 0.0.0.0:8000
    #   runserver 8001
    #   runserver 127.0.0.1:9000
    for a in sys.argv[2:]:
        if a.startswith("-"):
            continue
        if ":" in a:
            h, p = a.split(":", 1)
            if h:
                host = h
            if p:
                port = p
            break
        elif a.isdigit():
            port = a
            break

    # Si escuchas en 0.0.0.0, lo clicable suele ser 127.0.0.1
    click_host = "127.0.0.1" if host in ("0.0.0.0", "") else host

    url = f"http://{click_host}:{port}{subpath}/booklets/"
    print("")
    print(f"➡ Booklets: {url}")
    print("")


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pdf_manager_project.settings")

    _print_clickable_url_if_runserver()

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available on your PYTHONPATH?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

