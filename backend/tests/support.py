"""Shared setup for tests that run against organisation "o1"'s tenant database.

Django computes each TestCase's database allowlist before setUp runs, so the
org's alias must already be registered when a test module is imported -- this
module does that once. The isolated runner (fundvault_backend/test_runner.py)
points the alias at a private test copy, never at the real dev database.
"""

from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"
ORG_ID = "o1"
ORG_ALIAS = alias_for_org(ORG_ID)
ensure_connection(Org(id=ORG_ID, db_connection=TENANT_URL))


class OrgTestMixin:
    """Mix into TestCase or TransactionTestCase (mixin first) for org-"o1" tests."""

    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # The connection LRU is process-wide; another module may have evicted it.
        ensure_connection(Org(id=ORG_ID, db_connection=TENANT_URL))
        super().setUpClass()

    def make_org(self, **fields):
        defaults = {
            "id": ORG_ID,
            "name": "Acme",
            "slug": "acme",
            "owner_email": "owner@example.com",
            "db_connection": TENANT_URL,
        }
        return Org.objects.create(**{**defaults, **fields})

    def make_user(self, user_id, role, *, username=None, email=None, is_active=True):
        """Create a user with a live session in the org and return its bearer token."""
        token = create_session_token(user_id, ORG_ID)
        with org_context(ORG_ALIAS):
            User.objects.create(
                id=user_id,
                username=username or user_id,
                email=email or f"{user_id}@example.com",
                password_hash="x",
                role=role,
                is_active=is_active,
            )
            Session.objects.create(
                id=f"s_{user_id}",
                user_id=user_id,
                token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    @staticmethod
    def auth(token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}
