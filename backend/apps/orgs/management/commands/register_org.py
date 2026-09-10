"""Register an organisation and build its database.

Development and operations tool. Task 12 exposes the same sequence over HTTP.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils.text import slugify

from apps.common.utils import uid
from apps.orgs.connections import InvalidConnectionString, build_config, drop_connection
from apps.orgs.models import Org


class Command(BaseCommand):
    help = "Create an org, verify its Postgres, and run tenant migrations against it."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--url", required=True, help="postgres:// connection string")
        parser.add_argument("--owner-email", required=True)
        parser.add_argument("--id", default=None, help="explicit org id (default: generated)")

    def handle(self, *args, **options):
        org_id = options["id"] or uid()
        alias = f"org_{org_id}"

        try:
            config = build_config(options["url"])
        except InvalidConnectionString as exc:
            raise CommandError(str(exc))

        connections.databases[alias] = config
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
            self.stdout.write(f"Connected: {version.split(',')[0]}")

            with connections[alias].cursor() as cursor:
                cursor.execute("CREATE TABLE _fundvault_probe (id integer)")
                cursor.execute("DROP TABLE _fundvault_probe")
            self.stdout.write("Write access confirmed.")

            call_command("migrate", database=alias, verbosity=1)
        except Exception as exc:
            drop_connection(alias)
            raise CommandError(f"Could not prepare the database: {exc}")

        Org.objects.create(
            id=org_id,
            name=options["name"],
            slug=slugify(options["name"])[:80] or org_id,
            owner_email=options["owner_email"],
            db_connection=options["url"],
        )
        self.stdout.write(self.style.SUCCESS(f"Organisation created: {org_id}"))
        self.stdout.write("Create the first user by signing up against this org id.")
