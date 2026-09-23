"""Apply tenant migrations to every organisation's database.

The deploy runs this after migrating the control plane, so a release that
changes the tenant schema reaches existing orgs, not only ones created after
it. One org's unreachable database is reported and skipped rather than
failing the deploy for everyone else.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections

from apps.orgs.connections import ensure_connection
from apps.orgs.models import Org


class Command(BaseCommand):
    help = "Run tenant migrations against every registered organisation's database."

    def handle(self, *args, **options):
        # Not caught: if the control plane cannot be read, the deploy should fail.
        orgs = list(Org.objects.order_by("created_at"))
        failed = 0
        for org in orgs:
            label = f"{org.slug} ({org.id})"
            try:
                alias = ensure_connection(org)
                # A firewalled host would otherwise hang the build on libpq's
                # unbounded default.
                connections.databases[alias]["OPTIONS"].setdefault("connect_timeout", 10)
                call_command("migrate", database=alias, verbosity=0, interactive=False)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"{label}: FAILED - {type(exc).__name__}: {exc}")
                continue
            self.stdout.write(f"{label}: migrated")

        summary = f"Tenant migrations: {len(orgs) - failed} of {len(orgs)} organisations migrated, {failed} failed."
        self.stdout.write(self.style.WARNING(summary) if failed else self.style.SUCCESS(summary))
