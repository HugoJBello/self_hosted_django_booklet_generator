from __future__ import annotations

from django.conf import settings
from django.core.management.commands.runserver import Command as RunserverCommand


class Command(RunserverCommand):
    """
    Override de runserver para imprimir una URL clicable al arrancar.
    """

    def handle(self, *args, **options):
        # Calcula host/puerto ANTES de arrancar (super().handle no vuelve hasta que paras el server)
        addrport = options.get("addrport") or ""
        addr, port = self.parse_addrport(addrport)

        # Si solo dieron "8000", addr viene vacío -> usa default_addr
        if not addr:
            addr = self.default_addr

        # URL clicable: si escuchas en 0.0.0.0, lo “clickable” es 127.0.0.1
        click_host = "127.0.0.1" if addr in ("0.0.0.0", "", None) else addr

        subpath = getattr(settings, "APP_SUBPATH", "/pdf_manager").rstrip("/")
        url = f"http://{click_host}:{port}{subpath}/booklets/"

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"➡ Booklets: {url}"))
        self.stdout.write("")

        # Arranca el servidor
        return super().handle(*args, **options)

