import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from printmanager.models import PrintJob
from printmanager.tasks import refresh_print_job


def _refresh(job_id):
    close_old_connections()
    try:
        refresh_print_job(job_id)
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Continuously reconcile active print jobs with their printers."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=15)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        interval = max(5, options["interval"])
        self.stdout.write(f"Monitoring printer job status every {interval} seconds.")
        while True:
            now = timezone.now()
            active = PrintJob.objects.filter(status__in=("queued", "processing"))
            tracked_ids = list(
                active.filter(job_uri__gt="")
                .filter(Q(last_checked_at__isnull=True) | Q(last_checked_at__lte=now - timedelta(seconds=interval)))
                .values_list("pk", flat=True)[:100]
            )
            stale_untracked = active.filter(job_uri="").filter(
                Q(started_at__lte=now - timedelta(minutes=15))
                | Q(started_at__isnull=True, created_at__lte=now - timedelta(minutes=15))
            )
            stale_count = stale_untracked.update(
                status="submitted",
                status_detail="The submission did not record a printer receipt, so its outcome is unknown. Check the printer before resubmitting.",
                last_checked_at=now,
            )
            if tracked_ids:
                with ThreadPoolExecutor(max_workers=min(8, len(tracked_ids))) as executor:
                    list(executor.map(_refresh, tracked_ids))
                self.stdout.write(f"Refreshed {len(tracked_ids)} tracked print job(s).")
            if stale_count:
                self.stdout.write(f"Marked {stale_count} untracked submission(s) as outcome unknown.")
            if options["once"]:
                return
            time.sleep(interval)
