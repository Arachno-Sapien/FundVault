from django.db import connections
from django.test import TestCase


class DatabaseWiringTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_both_databases_are_postgres(self):
        for alias in ("default", "tenant_dev"):
            vendor = connections[alias].vendor
            self.assertEqual(vendor, "postgresql", f"{alias} is {vendor}, expected postgresql")

    def test_databases_are_distinct_servers(self):
        default_port = connections["default"].settings_dict["PORT"]
        tenant_port = connections["tenant_dev"].settings_dict["PORT"]
        self.assertNotEqual(
            default_port,
            tenant_port,
            "control plane and tenant must not share a server in development",
        )
