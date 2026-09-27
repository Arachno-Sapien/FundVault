# FundVault Multi-Tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn FundVault from a single-user local application into a deployed platform where each organisation connects its own Postgres, invites members with roles, and supplies its own AI provider credentials.

**Architecture:** One Django process serves every organisation. A control-plane Postgres owned by the operator stores the org registry, encrypted connection strings, join codes, and an email→org index. Each org's ledger lives in its own Postgres. A middleware resolves the org from a JWT claim and sets a contextvar; a Django database router reads that contextvar and sends every tenant-model query to the right connection, so existing view and service code needs no per-query changes.

**Tech Stack:** Django 5.2.1 · Python 3.14 · psycopg 3 · cryptography (Fernet) · boto3 (S3-compatible storage) · Next.js 16 · React 19 · Django's built-in test runner

**Spec:** [`docs/superpowers/specs/2026-09-09-fundvault-multitenant-design.md`](../specs/2026-09-09-fundvault-multitenant-design.md)

## Global Constraints

- **Python 3.14.3, Django 5.2.1** — already installed; do not upgrade as part of this work.
- **Tenant databases are Postgres only.** No SQLite tenant support ships. Local development uses Postgres too (Phase 1 Task 1).
- **`select_for_update()` requires Postgres.** Django raises `NotSupportedError` on SQLite. This is why local development moves to Postgres in Phase 1 rather than at deploy time.
- **No `org_id` column on any tenant table.** The database is the tenant boundary. If a task ever calls for filtering by org inside a tenant DB, the task is wrong — stop and re-read the spec.
- **The router raises rather than falling back.** A tenant-model query with no org context must raise `NoOrgContext`, never silently use `default`.
- **Secrets never reach the client.** Connection strings, storage keys, and AI keys are never serialised into any API response, in any form, masked or otherwise — except the deliberately masked display value in Task 5.9.
- **Money uses `FloatField` today and stays that way in this plan.** Converting to `DecimalField` is a separate migration project; do not mix it in.
- **Every commit message ends with:** `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Currency symbol in user-facing strings is `₹`** — matches existing audit messages and `lib/format.js`.
- **Existing public API response shapes must not change** except where a task explicitly says so. The frontend reads `snake_case` keys from serializers and sends `camelCase` keys in request bodies; preserve that asymmetry.

---

## Phase 1 — Foundations: Postgres, migrations, concurrency, hygiene

Phase 1 leaves the app single-tenant and working. Nothing about orgs appears yet. Its deliverable is a codebase whose schema is defined by migrations rather than by a legacy Node script, running on Postgres, with the balance race fixed and a test suite that proves it.

### Task 1: Development Postgres and test infrastructure

**Files:**
- Create: `docker-compose.yml`
- Create: `backend/fundvault_backend/settings_test.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_smoke.py`
- Modify: `backend/fundvault_backend/settings.py:30-35` (DATABASES block)
- Modify: `backend/requirements.txt`
- Modify: `backend/.env.example`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `DATABASE_URL` environment variable convention; `python backend/manage.py test` as the test entry point; `backend/tests/` as the test package root.

- [ ] **Step 1: Add the Postgres driver to requirements**

Append to `backend/requirements.txt`:

```text
psycopg[binary]==3.2.3
```

- [ ] **Step 2: Install it**

Run: `pip install -r backend/requirements.txt`
Expected: `psycopg` and `psycopg-binary` install successfully.

- [ ] **Step 3: Create the development database service**

Create `docker-compose.yml` at the repository root:

```yaml
# Development databases only. Production uses managed Postgres (Render for the
# control plane, the org's own provider for each tenant).
services:
  controlplane:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: fundvault
      POSTGRES_PASSWORD: devpassword
      POSTGRES_DB: fundvault_control
    ports:
      - "5433:5432"
    volumes:
      - controlplane_data:/var/lib/postgresql/data

  tenant_dev:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: fundvault
      POSTGRES_PASSWORD: devpassword
      POSTGRES_DB: fundvault_tenant_dev
    ports:
      - "5434:5432"
    volumes:
      - tenant_dev_data:/var/lib/postgresql/data

volumes:
  controlplane_data:
  tenant_dev_data:
```

Two separate servers, not two databases on one server, because the tenant is meant to be somewhere the operator does not control. Developing against that shape catches assumptions early.

If Docker is unavailable, any two Postgres databases work — a local install with two `CREATE DATABASE` statements, or two free Neon projects. Only the two URLs matter.

- [ ] **Step 4: Start the databases**

Run: `docker compose up -d`
Expected: two containers running. Verify with `docker compose ps` — both show `running`.

- [ ] **Step 5: Point Django at Postgres**

Replace `backend/fundvault_backend/settings.py` lines 30-35 (the whole `DATABASES` block) with:

```python
def _parse_database_url(url):
    """Turn postgres://user:pass@host:port/name into a Django DATABASES entry."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported database scheme: {parsed.scheme!r}. Postgres only.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {},
    }


CONTROL_PLANE_URL = os.getenv(
    "DATABASE_URL",
    "postgres://fundvault:devpassword@127.0.0.1:5433/fundvault_control",
)
DEV_TENANT_URL = os.getenv(
    "DEV_TENANT_DATABASE_URL",
    "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev",
)

DATABASES = {
    "default": _parse_database_url(CONTROL_PLANE_URL),
    # Phase 2 replaces this fixed alias with dynamically registered tenants.
    # It exists now so Phase 1 has somewhere to run ledger migrations.
    "tenant_dev": _parse_database_url(DEV_TENANT_URL),
}
```

- [ ] **Step 6: Add the new variables to the env template**

Append to `backend/.env.example`:

```text

# ── Databases ─────────────────────────────────────────────────────────────
# Control plane (orgs, join codes, email index). Owned by the operator.
DATABASE_URL=postgres://fundvault:devpassword@127.0.0.1:5433/fundvault_control
# Development tenant, standing in for an organisation's own Postgres.
DEV_TENANT_DATABASE_URL=postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev
```

- [ ] **Step 7: Create the test settings module**

Create `backend/fundvault_backend/settings_test.py`:

```python
"""Test settings. Django creates test_* copies of both databases."""

from fundvault_backend.settings import *  # noqa: F401,F403

# Fast, deterministic password hashing is irrelevant here (bcrypt is called
# directly, not through Django auth), but keep tests quiet and repeatable.
FUNDVAULT_JWT_SECRET = "test-jwt-secret"
FUNDVAULT_SESSION_HOURS = 24
DEBUG = False
```

- [ ] **Step 8: Create the test package and a smoke test**

Create `backend/tests/__init__.py` as an empty file.

Create `backend/tests/test_smoke.py`:

```python
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
```

- [ ] **Step 9: Run the smoke test and watch it fail**

Run: `python backend/manage.py test tests.test_smoke --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL. Django cannot create the test databases because the `accounts` and `ledger` apps have no migrations — you will see `relation "users" does not exist` or a migration-related error. This failure is the reason Task 2 exists.

- [ ] **Step 10: Commit**

```bash
git add docker-compose.yml backend/requirements.txt backend/.env.example backend/fundvault_backend/settings.py backend/fundvault_backend/settings_test.py backend/tests/
git commit -m "$(cat <<'EOF'
Move development onto Postgres and add test infrastructure

Tenant databases are Postgres-only per the spec, and select_for_update raises
NotSupportedError on SQLite, so the concurrency fix in this phase cannot be
tested against the old SQLite file.

Two separate Postgres servers in development, not two databases on one server,
because a tenant is meant to live somewhere the operator does not control.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Initial migrations for accounts and ledger

The models already exist and match the schema the legacy Node server created. This task makes Django the source of truth for that schema.

**Files:**
- Create: `backend/apps/accounts/migrations/__init__.py`
- Create: `backend/apps/accounts/migrations/0001_initial.py` (generated)
- Create: `backend/apps/ledger/migrations/__init__.py`
- Create: `backend/apps/ledger/migrations/0001_initial.py` (generated)

**Interfaces:**
- Consumes: the two-database settings from Task 1.
- Produces: a schema buildable by `migrate` alone, which every later task and every future tenant depends on.

- [ ] **Step 1: Create the migration packages**

```bash
mkdir -p backend/apps/accounts/migrations backend/apps/ledger/migrations
touch backend/apps/accounts/migrations/__init__.py backend/apps/ledger/migrations/__init__.py
```

- [ ] **Step 2: Generate the migrations**

Run: `python backend/manage.py makemigrations accounts ledger`
Expected: `0001_initial.py` created in both apps. `accounts` contains `User` and `Session`; `ledger` contains `DatabaseFund`, `TransactionFund`, `AuditLog`, `RecurringTransaction`, `TrashItem`.

- [ ] **Step 3: Verify the generated migration matches the legacy schema**

Run: `python backend/manage.py sqlmigrate ledger 0001 --database=tenant_dev`
Expected: `CREATE TABLE "transactions"` with columns `id, database_id, type, amount, date, sender, receiver, mode, mode_data, location, notes, running_balance, receipt_image, requires_approval, approved, approved_by, approved_at, is_voided, void_reason, voided_by, voided_at, created_at`.

Confirm the table names are the legacy ones — `users`, `sessions`, `databases`, `transactions`, `audit_log`, `recurring_transactions`, `trash` — and not Django's default `appname_modelname`. They come from each model's `Meta.db_table`. If any table is named `ledger_transactionfund`, a `Meta` class was lost; stop and restore it before continuing.

- [ ] **Step 4: Apply migrations to the development tenant**

Run: `python backend/manage.py migrate --database=tenant_dev`
Expected: `accounts` and `ledger` migrations apply. Django's own `contenttypes` and `staticfiles` are in `INSTALLED_APPS` but only `contenttypes` creates tables; that is expected and harmless.

- [ ] **Step 5: Run the smoke test to verify it now passes**

Run: `python backend/manage.py test tests.test_smoke --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 2 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/accounts/migrations backend/apps/ledger/migrations
git commit -m "$(cat <<'EOF'
Add initial migrations for accounts and ledger

The schema previously came from legacy/server.js, so a fresh clone had no way
to build a database. Every future tenant Postgres is created from these.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Delete ensure_profile_schema

`ensure_profile_schema()` runs a `PRAGMA table_info` and a conditional `ALTER TABLE` on every authenticated request. It is a hand-rolled migration, it is SQLite-specific, and it is now dead weight because `profile_image` is in `0001_initial`.

**Files:**
- Modify: `backend/apps/accounts/models.py:28-42` (remove `_profile_schema_checked` and `ensure_profile_schema`)
- Modify: `backend/apps/common/auth.py:9` (import), `backend/apps/common/auth.py:40` (call site)
- Modify: `backend/apps/accounts/views.py:7` (import), and its three call sites
- Create: `backend/tests/test_no_legacy_schema_hacks.py`

**Interfaces:**
- Consumes: migrations from Task 2.
- Produces: nothing new; removes `ensure_profile_schema` from the codebase entirely.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_no_legacy_schema_hacks.py`:

```python
import pathlib

from django.test import SimpleTestCase

BACKEND = pathlib.Path(__file__).resolve().parent.parent


class LegacySchemaHackTests(SimpleTestCase):
    def test_ensure_profile_schema_is_gone(self):
        hits = []
        for path in BACKEND.rglob("*.py"):
            if "migrations" in path.parts or path.name == __file__.rsplit("\\")[-1]:
                continue
            text = path.read_text(encoding="utf-8")
            if "ensure_profile_schema" in text:
                hits.append(str(path.relative_to(BACKEND)))
        self.assertEqual(hits, [], f"ensure_profile_schema still referenced in: {hits}")

    def test_no_pragma_table_info_anywhere(self):
        hits = []
        for path in BACKEND.rglob("*.py"):
            if path.name == pathlib.Path(__file__).name:
                continue
            if "PRAGMA table_info" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(BACKEND)))
        self.assertEqual(hits, [], f"SQLite PRAGMA still present in: {hits}")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_no_legacy_schema_hacks --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL, both tests, listing `apps/accounts/models.py`, `apps/common/auth.py`, `apps/accounts/views.py`.

- [ ] **Step 3: Remove the function**

In `backend/apps/accounts/models.py`, delete this entire block (it sits between the `User` class and the `Session` class):

```python
_profile_schema_checked = False


def ensure_profile_schema():
    global _profile_schema_checked
    if _profile_schema_checked:
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            return
        cursor.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}
        if "profile_image" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_image TEXT")
    _profile_schema_checked = True
```

Then change the first import line of that file from:

```python
from django.db import models, connection
```

to:

```python
from django.db import models
```

- [ ] **Step 4: Remove the call sites in auth.py**

In `backend/apps/common/auth.py`, change:

```python
from apps.accounts.models import Session, User, ensure_profile_schema
```

to:

```python
from apps.accounts.models import Session, User
```

and delete the line `        ensure_profile_schema()` inside `auth_required`'s `wrapped` (it is the second statement, immediately after `_clean_expired_sessions()`).

- [ ] **Step 5: Remove the call sites in accounts/views.py**

Change:

```python
from apps.accounts.models import Session, User, ensure_profile_schema
```

to:

```python
from apps.accounts.models import Session, User
```

Delete the three `    ensure_profile_schema()` lines — one in `signup`, one in `login`. (There are two calls plus the import; if `grep -n ensure_profile_schema backend/apps/accounts/views.py` returns anything after this step, remove those too.)

- [ ] **Step 6: Verify the tests pass**

Run: `python backend/manage.py test tests.test_no_legacy_schema_hacks --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 2 tests.

- [ ] **Step 7: Commit**

```bash
git add backend/apps backend/tests/test_no_legacy_schema_hacks.py
git commit -m "$(cat <<'EOF'
Remove ensure_profile_schema

A hand-rolled ALTER TABLE running on every authenticated request, standing in
for the migration that now exists. It was also SQLite-specific and would have
failed against Postgres tenants.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Schema changes for the org model

Four changes from the spec: the role enum gains `owner` and `viewer`; funds record who created them rather than who owns them; transactions record their creator; receipts store an object key.

**Files:**
- Modify: `backend/apps/accounts/models.py` (Role choices)
- Modify: `backend/apps/ledger/models.py` (DatabaseFund.user → created_by; TransactionFund.receipt_image → receipt_key; TransactionFund.created_by added)
- Create: `backend/apps/accounts/migrations/0002_add_owner_viewer_roles.py` (generated)
- Create: `backend/apps/ledger/migrations/0002_org_ownership_fields.py` (generated)
- Modify: `backend/apps/ledger/serializers.py:44` (`receipt_image` key)
- Modify: `backend/apps/ledger/views.py:43-48` (`_get_user_database`), `:60-77` (create), `:246-262` (transaction create)
- Create: `backend/tests/test_schema_changes.py`

**Interfaces:**
- Consumes: migrations from Task 2.
- Produces:
  - `User.Role.OWNER == "owner"`, `User.Role.VIEWER == "viewer"`
  - `DatabaseFund.created_by` — FK to `User`, `db_column="created_by"`, nullable
  - `TransactionFund.created_by` — FK to `User`, `db_column="created_by"`, nullable
  - `TransactionFund.receipt_key` — `TextField(null=True)`
  - `_get_user_database(user, database_id, include_deleted=False)` — signature unchanged, no longer filters by user

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_schema_changes.py`:

```python
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, TransactionFund


class RoleChoiceTests(TestCase):
    databases = {"tenant_dev"}

    def test_owner_and_viewer_roles_exist(self):
        self.assertEqual(User.Role.OWNER, "owner")
        self.assertEqual(User.Role.VIEWER, "viewer")

    def test_all_four_roles_are_choices(self):
        values = {choice[0] for choice in User.Role.choices}
        self.assertEqual(values, {"owner", "admin", "member", "viewer"})


class OwnershipFieldTests(TestCase):
    databases = {"tenant_dev"}

    def setUp(self):
        self.user = User.objects.using("tenant_dev").create(
            id="u1",
            username="alice",
            email="alice@example.com",
            password_hash="x",
            role=User.Role.OWNER,
        )

    def test_fund_records_creator_not_owner(self):
        fund = DatabaseFund.objects.using("tenant_dev").create(
            id="f1", created_by=self.user, name="Fund One"
        )
        self.assertEqual(fund.created_by_id, "u1")
        self.assertFalse(
            hasattr(fund, "user_id"),
            "DatabaseFund.user was renamed to created_by; the old attribute must be gone",
        )

    def test_transaction_records_creator_and_receipt_key(self):
        fund = DatabaseFund.objects.using("tenant_dev").create(
            id="f2", created_by=self.user, name="Fund Two"
        )
        txn = TransactionFund.objects.using("tenant_dev").create(
            id="t1",
            database=fund,
            type="credit",
            amount=100.0,
            date=timezone.now(),
            mode="cash",
            running_balance=100.0,
            created_by=self.user,
            receipt_key="receipts/f2/t1.jpg",
        )
        self.assertEqual(txn.created_by_id, "u1")
        self.assertEqual(txn.receipt_key, "receipts/f2/t1.jpg")
        self.assertFalse(
            hasattr(txn, "receipt_image"),
            "receipt_image was replaced by receipt_key",
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_schema_changes --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `AttributeError: type object 'Role' has no attribute 'OWNER'`.

- [ ] **Step 3: Add the two roles**

In `backend/apps/accounts/models.py`, replace the `Role` class body:

```python
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"
```

- [ ] **Step 4: Rename the fund ownership field**

In `backend/apps/ledger/models.py`, inside `DatabaseFund`, replace:

```python
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column="user_id", related_name="databases")
```

with:

```python
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        db_column="created_by",
        related_name="created_databases",
        null=True,
        blank=True,
    )
```

`SET_NULL` rather than `CASCADE`: a fund belongs to the org now, so removing the person who created it must not delete the org's ledger. Then update the `Meta.indexes` entry in the same class:

```python
    class Meta:
        db_table = "databases"
        indexes = [models.Index(fields=["created_by"], name="idx_databases_creator")]
```

- [ ] **Step 5: Change the transaction fields**

In `backend/apps/ledger/models.py`, inside `TransactionFund`, replace:

```python
    receipt_image = models.TextField(null=True, blank=True)
```

with:

```python
    receipt_key = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        db_column="created_by",
        related_name="created_transactions",
        null=True,
        blank=True,
    )
```

- [ ] **Step 6: Generate the migrations**

Run: `python backend/manage.py makemigrations accounts ledger`

When Django asks whether `user` was renamed to `created_by` on `databasefund`, answer **yes** — that produces a `RenameField`, which preserves existing rows. When it asks whether `receipt_image` was renamed to `receipt_key` on `transactionfund`, answer **yes** as well.

Rename the generated files for readability:

```bash
mv backend/apps/accounts/migrations/0002_*.py backend/apps/accounts/migrations/0002_add_owner_viewer_roles.py
mv backend/apps/ledger/migrations/0002_*.py backend/apps/ledger/migrations/0002_org_ownership_fields.py
```

Open each and update the `dependencies` list if the filename change broke a reference (it will not, because dependencies reference migration *names* which you just changed — set them to `("accounts", "0001_initial")` and `("ledger", "0001_initial")` respectively).

- [ ] **Step 7: Apply the migrations**

Run: `python backend/manage.py migrate --database=tenant_dev`
Expected: both `0002` migrations apply cleanly.

- [ ] **Step 8: Update every reference to the renamed fields**

`backend/apps/ledger/serializers.py` — in `serialize_transaction`, change:

```python
        "receipt_image": txn.receipt_image,
```

to:

```python
        "receipt_key": txn.receipt_key,
```

`backend/apps/ledger/views.py` — `_get_user_database` (line 43) becomes org-scoped rather than user-scoped:

```python
def _get_user_database(user, database_id, include_deleted=False):
    """Look up a fund within the caller's org.

    The org boundary is the database connection itself, so no ownership filter
    is applied here. The `user` parameter is retained because callers pass it
    and Phase 4 uses it for role checks.
    """
    query = DatabaseFund.objects.filter(id=database_id)
    if not include_deleted:
        query = query.filter(is_deleted=False)
    return query.first()
```

In `databases_list_create` (line 52), the GET branch changes from filtering by user to listing the org's funds:

```python
    if request.method == "GET":
        rows = DatabaseFund.objects.filter(is_deleted=False).order_by("-created_at")
        return JsonResponse([serialize_database(row) for row in rows], safe=False)
```

and the POST branch's `DatabaseFund.objects.create(...)` call changes `user_id=request.fv_user.id` to `created_by_id=request.fv_user.id`.

In `database_transactions` (line 246), the `TransactionFund.objects.create(...)` call changes `receipt_image=receipt_image` to `receipt_key=None` and gains `created_by_id=request.fv_user.id`. The `receipt_image = body.get("receiptImage")` line above it is now unused — delete it. Receipt upload is rebuilt in Phase 5; until then, transactions are created without receipts.

- [ ] **Step 9: Fix every remaining reference**

Run: `grep -rn "user_id=\|\.user_id\|receipt_image" backend/apps --include=*.py | grep -v migrations`

Expected remaining hits and their fixes:
- `apps/ledger/serializers.py` `serialize_database` — `"user_id": database.user_id` becomes `"created_by": database.created_by_id`
- `apps/ledger/views.py` `databases_merge` — `user_id=request.fv_user.id` becomes `created_by_id=request.fv_user.id`
- `apps/ledger/views.py` `_delete_trash_item_permanently` — `DatabaseFund.objects.filter(id=db_id, user_id=user.id)` becomes `DatabaseFund.objects.filter(id=db_id)`
- `apps/ledger/views.py` `trash_restore` — `.filter(id=data.get("id"), user_id=request.fv_user.id)` becomes `.filter(id=data.get("id"))`
- `apps/ledger/views.py` `analytics_overview` — `DatabaseFund.objects.filter(user_id=request.fv_user.id, is_deleted=False)` becomes `DatabaseFund.objects.filter(is_deleted=False)`
- `apps/ledger/services.py` `process_due_recurring` — `database__user_id=user.id` drops out of the filter entirely, leaving `.filter(database__is_deleted=False, is_active=True, next_run__lte=today)`
- `apps/accounts/views.py` `admin_users` — `DatabaseFund.objects.filter(user_id=user.id)` becomes `DatabaseFund.objects.filter(created_by_id=user.id)` (both occurrences), and `TransactionFund.objects.filter(database__user_id=user.id)` becomes `TransactionFund.objects.filter(created_by_id=user.id)`
- `apps/accounts/views.py` `admin_user_detail` DELETE branch — the block deleting the target's funds and transactions must be removed entirely; funds now belong to the org and must survive the removal of the person who created them. Replace the whole `with transaction.atomic():` body with:

```python
        with transaction.atomic():
            from apps.ledger.models import AuditLog, TrashItem

            TrashItem.objects.filter(deleted_by_id=target.id).delete()
            AuditLog.objects.filter(user_id=target.id).update(user_id=None)
            Session.objects.filter(user_id=target.id).delete()
            target.delete()
```

`AuditLog` rows are preserved with a null user rather than deleted — an audit trail that disappears when someone leaves is not an audit trail.

Also transaction-level views that filtered by `database__user_id` — `transaction_void`, `transaction_delete_voided`, `transaction_approve`, `transaction_update`, `recurring_delete` — drop that filter clause, keeping `database__is_deleted=False` where present.

- [ ] **Step 10: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 11: Verify no user-scoping remains in the ledger**

Run: `grep -rn "user_id=request.fv_user\|database__user_id" backend/apps/ledger`
Expected: no output. Any hit is a fund still scoped to a person rather than the org.

- [ ] **Step 12: Commit**

```bash
git add backend/apps backend/tests/test_schema_changes.py
git commit -m "$(cat <<'EOF'
Make funds belong to the org rather than to a user

Adds owner and viewer roles, renames DatabaseFund.user to created_by, records
transaction creators, and replaces base64 receipt_image with a receipt_key
object reference.

Fund ownership becomes creation attribution: removing a member no longer
deletes the org's ledger, and audit rows survive with a null user.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Fix the balance race with row locking

`database_transactions` reads `db.balance` at line 240, decides the new balance at line 244, and only opens its atomic block at line 246. Two concurrent debits both read the old balance, both pass the sufficiency check, and the fund goes negative.

**Files:**
- Modify: `backend/apps/ledger/views.py:203-283` (`database_transactions`)
- Modify: `backend/apps/ledger/views.py:344-388` (`transaction_approve`)
- Create: `backend/tests/test_balance_concurrency.py`

**Interfaces:**
- Consumes: Postgres from Task 1, `created_by` from Task 4.
- Produces: no new API; the fund row is locked for the duration of any balance-changing write.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_balance_concurrency.py`:

```python
import threading

from django.db import connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, TransactionFund


def _post_debit(fund_id, amount, barrier, errors):
    """Mimic the view's read-decide-write sequence in a real thread."""
    try:
        with transaction.atomic(using="tenant_dev"):
            fund = (
                DatabaseFund.objects.using("tenant_dev")
                .select_for_update()
                .get(id=fund_id)
            )
            barrier.wait(timeout=5)  # force both threads past the read together
            if amount > fund.balance:
                return
            new_balance = fund.balance - amount
            TransactionFund.objects.using("tenant_dev").create(
                id=f"txn-{threading.get_ident()}",
                database_id=fund_id,
                type="debit",
                amount=amount,
                date=timezone.now(),
                mode="cash",
                running_balance=new_balance,
            )
            fund.balance = new_balance
            fund.save(update_fields=["balance"])
    except Exception as exc:  # surfaced in the assertion below
        errors.append(exc)
    finally:
        connections["tenant_dev"].close()


class ConcurrentDebitTests(TransactionTestCase):
    databases = {"tenant_dev"}

    def test_two_concurrent_debits_cannot_overdraw(self):
        user = User.objects.using("tenant_dev").create(
            id="u1", username="a", email="a@example.com", password_hash="x"
        )
        fund = DatabaseFund.objects.using("tenant_dev").create(
            id="f1", created_by=user, name="Fund", balance=100.0
        )

        barrier = threading.Barrier(2)
        errors = []
        threads = [
            threading.Thread(target=_post_debit, args=(fund.id, 80.0, barrier, errors))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        fund.refresh_from_db()
        self.assertGreaterEqual(
            fund.balance,
            0,
            "two 80.00 debits against a 100.00 balance overdrew the fund",
        )
        posted = TransactionFund.objects.using("tenant_dev").count()
        self.assertEqual(posted, 1, "only one of the two debits should have succeeded")
```

- [ ] **Step 2: Run it to confirm the locking primitive works**

Run: `python backend/manage.py test tests.test_balance_concurrency --settings=fundvault_backend.settings_test -v 2`
Expected: PASS. This test proves `select_for_update` serialises the two threads. It is the reference behaviour the view must now match — the view itself is still wrong, which the next steps fix.

If this test *fails* with `NotSupportedError`, the database is not Postgres; revisit Task 1 Step 5.

- [ ] **Step 3: Lock the fund row in the create path**

In `backend/apps/ledger/views.py`, in `database_transactions`, replace everything from `    if tx_type == "debit" and amount > db.balance:` (line 240) through the end of the `with transaction.atomic():` block (line 267) with:

```python
    with transaction.atomic():
        # Re-read the fund under a row lock: the balance read above happened
        # outside any transaction and another request may have moved it.
        locked = DatabaseFund.objects.select_for_update().filter(id=database_id).first()
        if not locked:
            return json_error("Database not found", 404)

        if tx_type == "debit" and amount > locked.balance:
            return json_error("Insufficient balance", 400)

        requires_approval = locked.approval_threshold > 0 and amount >= locked.approval_threshold
        new_balance = (
            locked.balance
            if requires_approval
            else (locked.balance + amount if tx_type == "credit" else locked.balance - amount)
        )

        txn = TransactionFund.objects.create(
            id=uid(),
            database_id=database_id,
            type=tx_type,
            amount=amount,
            date=tx_date,
            sender=sender or None,
            receiver=receiver or None,
            mode=mode,
            mode_data=json.dumps(mode_data),
            location=location or None,
            notes=notes or None,
            running_balance=new_balance,
            receipt_key=None,
            requires_approval=requires_approval,
            approved=(not requires_approval),
            created_by_id=request.fv_user.id,
        )
        if not requires_approval:
            locked.balance = new_balance
            locked.save(update_fields=["balance"])
```

Returning a `JsonResponse` from inside `transaction.atomic()` is safe — the block exits normally, so the transaction commits with no rows written.

- [ ] **Step 4: Lock the fund row in the approve path**

In `transaction_approve`, replace lines 365-381 (from `    if txn.type == "debit" and txn.amount > db.balance:` through the end of the atomic block) with:

```python
    with transaction.atomic():
        locked = DatabaseFund.objects.select_for_update().filter(id=txn.database_id).first()
        if not locked:
            return json_error("Database not found", 404)

        if txn.type == "debit" and txn.amount > locked.balance:
            return json_error("Insufficient balance to approve this debit transaction", 400)

        new_balance = (
            locked.balance + txn.amount if txn.type == "credit" else locked.balance - txn.amount
        )
        txn.approved = True
        txn.approved_by = request.fv_user.username
        txn.approved_at = timezone.now()
        txn.running_balance = new_balance
        txn.save(update_fields=["approved", "approved_by", "approved_at", "running_balance"])
        locked.balance = new_balance
        locked.save(update_fields=["balance"])
        recalculate_running_balances(locked.id)
```

Delete the now-unused `    db = txn.database` line above it.

- [ ] **Step 5: Run the whole suite**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 6: Verify no unlocked balance arithmetic remains**

Run: `grep -n "db.balance\|\.balance +\|\.balance -" backend/apps/ledger/views.py`
Expected: every hit is inside a `with transaction.atomic():` block operating on a `select_for_update()` row. `services.py` `process_due_recurring` is already wrapped in `@transaction.atomic` — add `.select_for_update()` to its `RecurringTransaction.objects.select_related("database")` query by re-reading each `rec.database` as `DatabaseFund.objects.select_for_update().get(id=rec.database_id)` at the top of the loop body, replacing `db = rec.database`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ledger backend/tests/test_balance_concurrency.py
git commit -m "$(cat <<'EOF'
Lock the fund row before changing its balance

Read-decide-write outside a transaction let two concurrent debits both pass the
sufficiency check and overdraw the fund. Harmless with one user; a live
corruption path once an org has several.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Fix the install script's env path

`install.bat` writes `.env` to the repository root; `manage.py` loads `backend/.env`. Every value the installer sets is silently ignored.

**Files:**
- Modify: `install.bat:41-66`
- Modify: `run.bat`
- Create: `backend/tests/test_env_wiring.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `backend/.env` as the single env file location.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_env_wiring.py`:

```python
import pathlib

from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


class InstallScriptTests(SimpleTestCase):
    def test_installer_writes_env_where_manage_py_reads_it(self):
        script = (REPO / "install.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn(
            "backend\\.env",
            script,
            "install.bat must write backend\\.env — manage.py loads that path, not the repo root",
        )
        self.assertNotIn(
            '> .env\n',
            script,
            "install.bat still writes a root .env, which manage.py never reads",
        )

    def test_manage_py_loads_backend_env(self):
        manage = (REPO / "backend" / "manage.py").read_text(encoding="utf-8")
        self.assertIn('load_dotenv(Path(__file__).resolve().parent / ".env")', manage)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_env_wiring --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL on the first test — `install.bat` contains `> .env`.

- [ ] **Step 3: Fix the installer**

In `install.bat`, change the guard line:

```bat
if not exist ".env" (
```

to:

```bat
if not exist "backend\.env" (
```

change the redirect at the end of that block:

```bat
    ) > .env
```

to:

```bat
    ) > backend\.env
```

and change the two echoed status lines from `.env file created.` / `Creating .env file...` to `backend\.env file created.` / `Creating backend\.env file...` so the message matches reality.

Then replace the block's contents so the generated file matches the current `.env.example` — the AI keys are per-org from Phase 5 onward and no longer belong in a server env file:

```bat
        echo # Django
        echo DJANGO_SECRET_KEY=change-me-in-production
        echo DJANGO_DEBUG=true
        echo DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
        echo.
        echo # Auth
        echo JWT_SECRET=change-me-in-production
        echo SESSION_HOURS=24
        echo.
        echo # Encryption key for stored org credentials. Generate with:
        echo #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        echo FUNDVAULT_SECRET_KEY=
        echo.
        echo # Databases
        echo DATABASE_URL=postgres://fundvault:devpassword@127.0.0.1:5433/fundvault_control
        echo DEV_TENANT_DATABASE_URL=postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev
```

- [ ] **Step 4: Add the database startup to run.bat**

In `run.bat`, immediately after the `cd /d "%~dp0"` line, insert:

```bat
echo Starting development databases...
docker compose up -d >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo WARNING: could not start Docker databases.
    echo Set DATABASE_URL and DEV_TENANT_DATABASE_URL in backend\.env to use your own Postgres.
    echo.
)
```

- [ ] **Step 5: Verify the tests pass**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
git add install.bat run.bat backend/tests/test_env_wiring.py
git commit -m "$(cat <<'EOF'
Write the installer env file where manage.py actually reads it

install.bat wrote .env at the repository root while manage.py loads
backend/.env, so every value the installer set was silently ignored.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

**Phase 1 is complete.** The app still behaves exactly as it did, on Postgres, with a schema defined by migrations, a fixed balance race, and 5 test modules. Nothing about organisations exists yet.

---

## Phase 2 — Control plane, router, middleware

Phase 2 builds the tenancy machinery and proves it against a single hardcoded org pointing at the development tenant database. No user-visible behaviour changes. This is the riskiest phase; it ships alone so a regression here is unambiguous.

### Task 7: The orgs app and encrypted credential storage

**Files:**
- Create: `backend/apps/orgs/__init__.py`
- Create: `backend/apps/orgs/apps.py`
- Create: `backend/apps/orgs/fields.py`
- Create: `backend/apps/orgs/models.py`
- Create: `backend/apps/orgs/migrations/__init__.py`
- Create: `backend/apps/orgs/migrations/0001_initial.py` (generated)
- Modify: `backend/fundvault_backend/settings.py:11-18` (INSTALLED_APPS), and append `FUNDVAULT_SECRET_KEY`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/test_orgs_models.py`

**Interfaces:**
- Consumes: Phase 1's control-plane database (`default`).
- Produces:
  - `apps.orgs.models.Org` — fields `id`, `name`, `slug`, `owner_email`, `db_connection`, `storage_config`, `ai_config`, `created_at`
  - `apps.orgs.models.JoinCode` — `code`, `org`, `grants_role`, `expires_at`, `max_uses`, `uses`, `revoked`; methods `is_usable()`, `consume()`
  - `apps.orgs.models.EmailIndex` — `email`, `org`, `last_seen_at`
  - `apps.orgs.fields.EncryptedTextField` — transparent Fernet encryption
  - `apps.orgs.models.new_join_code()` returning a `FUNDVAULT-XXXX-XXXX` string

- [ ] **Step 1: Add the encryption library**

Append to `backend/requirements.txt`:

```text
cryptography==44.0.0
```

Run: `pip install -r backend/requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_orgs_models.py`:

```python
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.orgs.models import EmailIndex, JoinCode, Org, new_join_code


class EncryptedFieldTests(TestCase):
    databases = {"default"}

    def test_connection_string_is_readable_through_the_orm(self):
        org = Org.objects.create(
            id="o1",
            name="Acme Funds",
            slug="acme-funds",
            owner_email="owner@example.com",
            db_connection="postgres://u:p@host:5432/db",
        )
        org.refresh_from_db()
        self.assertEqual(org.db_connection, "postgres://u:p@host:5432/db")

    def test_connection_string_is_ciphertext_on_disk(self):
        Org.objects.create(
            id="o2",
            name="Beta",
            slug="beta",
            owner_email="b@example.com",
            db_connection="postgres://secret:hunter2@host:5432/db",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT db_connection FROM orgs WHERE id = %s", ["o2"])
            stored = cursor.fetchone()[0]
        self.assertNotIn("hunter2", stored, "credential stored in plaintext")
        self.assertTrue(stored.startswith("gAAAAA"), "expected a Fernet token")

    def test_empty_values_round_trip_as_empty(self):
        org = Org.objects.create(
            id="o3",
            name="Gamma",
            slug="gamma",
            owner_email="g@example.com",
            db_connection="postgres://u:p@h:5432/d",
        )
        org.refresh_from_db()
        self.assertEqual(org.storage_config, "")
        self.assertEqual(org.ai_config, "")


class JoinCodeTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.org = Org.objects.create(
            id="o1",
            name="Acme",
            slug="acme",
            owner_email="o@example.com",
            db_connection="postgres://u:p@h:5432/d",
        )

    def _code(self, **overrides):
        defaults = dict(
            code=new_join_code(),
            org=self.org,
            grants_role="member",
            expires_at=timezone.now() + timedelta(days=7),
            max_uses=5,
        )
        defaults.update(overrides)
        return JoinCode.objects.create(**defaults)

    def test_fresh_code_is_usable(self):
        self.assertTrue(self._code().is_usable())

    def test_expired_code_is_not_usable(self):
        code = self._code(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(code.is_usable())

    def test_exhausted_code_is_not_usable(self):
        code = self._code(max_uses=1)
        code.consume()
        self.assertFalse(code.is_usable())

    def test_revoked_code_is_not_usable(self):
        self.assertFalse(self._code(revoked=True).is_usable())

    def test_consume_increments_uses(self):
        code = self._code(max_uses=3)
        code.consume()
        code.refresh_from_db()
        self.assertEqual(code.uses, 1)

    def test_consume_refuses_past_the_cap(self):
        code = self._code(max_uses=1)
        self.assertTrue(code.consume())
        self.assertFalse(code.consume())

    def test_generated_codes_are_unique_and_shaped(self):
        codes = {new_join_code() for _ in range(200)}
        self.assertEqual(len(codes), 200)
        for code in list(codes)[:5]:
            self.assertRegex(code, r"^FUNDVAULT-[A-Z0-9]{4}-[A-Z0-9]{4}$")


class EmailIndexTests(TestCase):
    databases = {"default"}

    def test_one_person_can_belong_to_several_orgs(self):
        first = Org.objects.create(
            id="o1", name="A", slug="a", owner_email="x@example.com",
            db_connection="postgres://u:p@h:5432/a",
        )
        second = Org.objects.create(
            id="o2", name="B", slug="b", owner_email="x@example.com",
            db_connection="postgres://u:p@h:5432/b",
        )
        EmailIndex.objects.create(email="x@example.com", org=first)
        EmailIndex.objects.create(email="x@example.com", org=second)
        self.assertEqual(EmailIndex.objects.filter(email="x@example.com").count(), 2)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_orgs_models --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orgs'`.

- [ ] **Step 4: Create the app package**

```bash
mkdir -p backend/apps/orgs/migrations
touch backend/apps/orgs/__init__.py backend/apps/orgs/migrations/__init__.py
```

Create `backend/apps/orgs/apps.py`:

```python
from django.apps import AppConfig


class OrgsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orgs"
```

- [ ] **Step 5: Write the encrypted field**

Create `backend/apps/orgs/fields.py`:

```python
"""Transparent Fernet encryption for credential columns.

Values are ciphertext at rest and plain strings in Python. The key lives in
FUNDVAULT_SECRET_KEY; losing it makes every stored credential unrecoverable,
which is why it is required rather than defaulted.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


@lru_cache(maxsize=1)
def _fernet():
    key = getattr(settings, "FUNDVAULT_SECRET_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "FUNDVAULT_SECRET_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(f"FUNDVAULT_SECRET_KEY is not a valid Fernet key: {exc}")


class EncryptedTextField(models.TextField):
    """TextField whose value is encrypted in the database."""

    def get_prep_value(self, value):
        if value is None:
            return None
        if value == "":
            return ""
        return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise ValueError(
                "Could not decrypt a stored credential. FUNDVAULT_SECRET_KEY has "
                "changed, or the row was written with a different key."
            )
```

Empty strings pass through unencrypted so "not configured yet" is distinguishable without a decryption round-trip, and a blank storage or AI config costs nothing.

- [ ] **Step 6: Write the models**

Create `backend/apps/orgs/models.py`:

```python
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.orgs.fields import EncryptedTextField

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0, 1


def new_join_code():
    """FUNDVAULT-XXXX-XXXX, from an alphabet with no visually ambiguous characters."""
    left = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    right = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    return f"FUNDVAULT-{left}-{right}"


class Org(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    name = models.TextField()
    slug = models.SlugField(max_length=80, unique=True)
    owner_email = models.EmailField()
    db_connection = EncryptedTextField()
    storage_config = EncryptedTextField(blank=True, default="")
    ai_config = EncryptedTextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "orgs"

    def __str__(self):
        return self.name


class JoinCode(models.Model):
    code = models.CharField(max_length=32, primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="join_codes")
    grants_role = models.CharField(max_length=16)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=1)
    uses = models.PositiveIntegerField(default=0)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "join_codes"

    def is_usable(self):
        if self.revoked:
            return False
        if self.expires_at <= timezone.now():
            return False
        return self.uses < self.max_uses

    def consume(self):
        """Atomically claim one use. Returns True if claimed."""
        claimed = (
            JoinCode.objects.filter(
                code=self.code, revoked=False, uses__lt=models.F("max_uses")
            )
            .filter(expires_at__gt=timezone.now())
            .update(uses=models.F("uses") + 1)
        )
        if claimed:
            self.uses += 1
        return bool(claimed)

    @staticmethod
    def default_expiry():
        return timezone.now() + timedelta(days=14)


class EmailIndex(models.Model):
    """Which orgs an email belongs to. Discovery only — no password data."""

    id = models.BigAutoField(primary_key=True)
    email = models.EmailField(db_index=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="member_emails")
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "email_index"
        constraints = [
            models.UniqueConstraint(fields=["email", "org"], name="uniq_email_per_org")
        ]
```

`consume()` uses a conditional `UPDATE` rather than read-then-save, so two people redeeming the last use of a code cannot both succeed.

- [ ] **Step 7: Register the app and the secret key**

In `backend/fundvault_backend/settings.py`, add to `INSTALLED_APPS` after `"apps.ledger"`:

```python
    "apps.orgs.apps.OrgsConfig",
```

Append at the end of the same file:

```python
FUNDVAULT_SECRET_KEY = os.getenv("FUNDVAULT_SECRET_KEY", "")
```

In `backend/fundvault_backend/settings_test.py`, append a deterministic test key:

```python
FUNDVAULT_SECRET_KEY = "cP7mHqLxKcVfJhTgYnWzRbNdSaQeUiOpAsDfGhJkLmM="
```

Append to `backend/.env.example`:

```text

# ── Credential encryption ─────────────────────────────────────────────────
# Encrypts org database, storage, and AI credentials at rest. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# LOSING THIS KEY MAKES EVERY STORED ORG CREDENTIAL UNRECOVERABLE.
FUNDVAULT_SECRET_KEY=
```

- [ ] **Step 8: Generate a real key for your own environment**

Run: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

Put the output in `backend/.env` as `FUNDVAULT_SECRET_KEY=<output>`. That file is gitignored.

- [ ] **Step 9: Generate and apply the migration**

Run: `python backend/manage.py makemigrations orgs`
Run: `python backend/manage.py migrate --database=default`
Expected: `orgs.0001_initial` applies to the control plane.

- [ ] **Step 10: Run the tests**

Run: `python backend/manage.py test tests.test_orgs_models --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 12 tests.

- [ ] **Step 11: Commit**

```bash
git add backend/apps/orgs backend/fundvault_backend backend/requirements.txt backend/.env.example backend/tests/test_orgs_models.py
git commit -m "Add the control plane: orgs, join codes, email index

Credentials are Fernet-encrypted at rest through a custom field, so a database
dump of the control plane yields ciphertext. Join codes are consumed with a
conditional UPDATE so the last use cannot be claimed twice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Org context and the connection registry

**Files:**
- Create: `backend/apps/orgs/context.py`
- Create: `backend/apps/orgs/connections.py`
- Create: `backend/tests/test_org_connections.py`

**Interfaces:**
- Consumes: `Org` from Task 7.
- Produces:
  - `apps.orgs.context.current_org_alias()` → `str | None`
  - `apps.orgs.context.set_current_org(alias)` → token
  - `apps.orgs.context.reset_current_org(token)` → None
  - `apps.orgs.context.org_context(alias)` — context manager
  - `apps.orgs.connections.alias_for_org(org_id)` → `"org_<org_id>"`
  - `apps.orgs.connections.build_config(url)` → dict
  - `apps.orgs.connections.ensure_connection(org)` → alias string
  - `apps.orgs.connections.drop_connection(alias)` → None
  - `apps.orgs.connections.InvalidConnectionString` — exception
  - `apps.orgs.connections.MAX_TENANT_CONNECTIONS` = 50

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_org_connections.py`:

```python
from django.db import connections
from django.test import TestCase

from apps.orgs import connections as tenant_connections
from apps.orgs.context import (
    current_org_alias,
    org_context,
    reset_current_org,
    set_current_org,
)
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class ContextTests(TestCase):
    databases = {"default"}

    def test_context_is_empty_by_default(self):
        self.assertIsNone(current_org_alias())

    def test_set_and_reset(self):
        token = set_current_org("org_abc")
        self.assertEqual(current_org_alias(), "org_abc")
        reset_current_org(token)
        self.assertIsNone(current_org_alias())

    def test_context_manager_restores_previous_value(self):
        with org_context("org_one"):
            self.assertEqual(current_org_alias(), "org_one")
            with org_context("org_two"):
                self.assertEqual(current_org_alias(), "org_two")
            self.assertEqual(current_org_alias(), "org_one")
        self.assertIsNone(current_org_alias())

    def test_context_manager_resets_on_exception(self):
        with self.assertRaises(RuntimeError):
            with org_context("org_boom"):
                raise RuntimeError("boom")
        self.assertIsNone(current_org_alias())


class ConnectionStringTests(TestCase):
    databases = {"default"}

    def test_rejects_non_postgres_scheme(self):
        with self.assertRaises(tenant_connections.InvalidConnectionString):
            tenant_connections.build_config("mysql://u:p@h:3306/d")

    def test_rejects_missing_database_name(self):
        with self.assertRaises(tenant_connections.InvalidConnectionString):
            tenant_connections.build_config("postgres://u:p@h:5432/")

    def test_rejects_missing_host(self):
        with self.assertRaises(tenant_connections.InvalidConnectionString):
            tenant_connections.build_config("postgres://u:p@/d")

    def test_accepts_postgresql_scheme_too(self):
        config = tenant_connections.build_config("postgresql://u:p@h:5432/d")
        self.assertEqual(config["NAME"], "d")

    def test_percent_encoded_password_is_decoded(self):
        config = tenant_connections.build_config("postgres://u:p%40ss@h:5432/d")
        self.assertEqual(config["PASSWORD"], "p@ss")


class ConnectionRegistryTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.org = Org.objects.create(
            id="abc123", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.addCleanup(tenant_connections.drop_connection, "org_abc123")

    def test_alias_is_derived_from_org_id(self):
        self.assertEqual(tenant_connections.alias_for_org("abc123"), "org_abc123")

    def test_ensure_connection_registers_the_alias(self):
        alias = tenant_connections.ensure_connection(self.org)
        self.assertEqual(alias, "org_abc123")
        self.assertIn(alias, connections.databases)
        self.assertEqual(connections.databases[alias]["NAME"], "fundvault_tenant_dev")
        self.assertEqual(connections.databases[alias]["PORT"], "5434")

    def test_ensure_connection_is_idempotent(self):
        first = tenant_connections.ensure_connection(self.org)
        second = tenant_connections.ensure_connection(self.org)
        self.assertEqual(first, second)

    def test_registered_connection_has_all_django_required_keys(self):
        alias = tenant_connections.ensure_connection(self.org)
        config = connections.databases[alias]
        for key in (
            "ENGINE", "NAME", "USER", "PASSWORD", "HOST", "PORT",
            "ATOMIC_REQUESTS", "AUTOCOMMIT", "CONN_MAX_AGE",
            "CONN_HEALTH_CHECKS", "OPTIONS", "TIME_ZONE", "TEST",
        ):
            self.assertIn(key, config, f"missing required key {key}")

    def test_drop_connection_removes_the_alias(self):
        alias = tenant_connections.ensure_connection(self.org)
        tenant_connections.drop_connection(alias)
        self.assertNotIn(alias, connections.databases)

    def test_registry_evicts_beyond_the_cap(self):
        made = []
        for index in range(tenant_connections.MAX_TENANT_CONNECTIONS + 5):
            org = Org(
                id=f"bulk{index}", name=f"Org {index}", slug=f"org-{index}",
                owner_email="b@example.com", db_connection=TENANT_URL,
            )
            made.append(tenant_connections.ensure_connection(org))
        live = [alias for alias in made if alias in connections.databases]
        self.assertLessEqual(len(live), tenant_connections.MAX_TENANT_CONNECTIONS)
        self.assertIn(made[-1], connections.databases, "the most recent org was evicted")
        for alias in made:
            tenant_connections.drop_connection(alias)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_org_connections --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orgs.context'`.

- [ ] **Step 3: Write the context module**

Create `backend/apps/orgs/context.py`:

```python
"""The current request's tenant, as a contextvar.

A contextvar rather than thread-local storage: it is correct under async views
and thread pools alike, and the reset token makes nesting unambiguous.
"""

import contextlib
from contextvars import ContextVar

_current_org_alias: ContextVar = ContextVar("fundvault_org_alias", default=None)


def current_org_alias():
    return _current_org_alias.get()


def set_current_org(alias):
    return _current_org_alias.set(alias)


def reset_current_org(token):
    _current_org_alias.reset(token)


@contextlib.contextmanager
def org_context(alias):
    token = set_current_org(alias)
    try:
        yield alias
    finally:
        reset_current_org(token)
```

- [ ] **Step 4: Write the connection registry**

Create `backend/apps/orgs/connections.py`:

```python
"""Register tenant database connections with Django at runtime.

Django's ConnectionHandler exposes its settings dict as `connections.databases`.
Adding an entry there makes `connections[alias]` a usable connection. Entries
added this way bypass ConnectionHandler.configure_settings, which runs once at
startup, so every default it would have filled must be supplied explicitly.
"""

import threading
from collections import OrderedDict
from urllib.parse import unquote, urlparse

from django.db import connections

MAX_TENANT_CONNECTIONS = 50

_lru = OrderedDict()
_lock = threading.Lock()


class InvalidConnectionString(ValueError):
    """The org's stored connection string is not a usable Postgres URL."""


def alias_for_org(org_id):
    return f"org_{org_id}"


def build_config(url):
    """Parse a postgres:// URL into a fully populated Django database config."""
    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise InvalidConnectionString(
            f"Expected a postgres:// URL, got {parsed.scheme or 'no'} scheme."
        )
    name = parsed.path.lstrip("/")
    if not name:
        raise InvalidConnectionString("Connection string has no database name.")
    if not parsed.hostname:
        raise InvalidConnectionString("Connection string has no host.")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(parsed.port or 5432),
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {},
        "TIME_ZONE": None,
        "TEST": {
            "CHARSET": None,
            "COLLATION": None,
            "MIGRATE": True,
            "MIRROR": None,
            "NAME": None,
        },
    }


def ensure_connection(org):
    """Register org's database if absent and return its alias."""
    alias = alias_for_org(org.id)
    with _lock:
        if alias in connections.databases:
            _lru.move_to_end(alias, last=True)
            return alias

        connections.databases[alias] = build_config(org.db_connection)
        _lru[alias] = True
        while len(_lru) > MAX_TENANT_CONNECTIONS:
            oldest, _ = _lru.popitem(last=False)
            _close_and_forget(oldest)
    return alias


def drop_connection(alias):
    with _lock:
        _lru.pop(alias, None)
        _close_and_forget(alias)


def _close_and_forget(alias):
    """Close the connection and remove Django's memory of the alias."""
    if alias not in connections.databases:
        return
    try:
        connections[alias].close()
    except Exception:
        pass  # a dead connection is exactly what we are discarding
    connections.databases.pop(alias, None)
    try:
        delattr(connections._connections, alias)
    except AttributeError:
        pass
```

- [ ] **Step 5: Run the tests**

Run: `python backend/manage.py test tests.test_org_connections --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 15 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orgs/context.py backend/apps/orgs/connections.py backend/tests/test_org_connections.py
git commit -m "Add the tenant contextvar and runtime connection registry

Connections are registered into Django's ConnectionHandler on demand and capped
by an LRU so a large number of active orgs cannot exhaust the process. Configs
are fully populated because runtime entries bypass configure_settings.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The database router

**Files:**
- Create: `backend/apps/orgs/router.py`
- Modify: `backend/fundvault_backend/settings.py` (append `DATABASE_ROUTERS`)
- Create: `backend/tests/test_router.py`
- Modify: `backend/tests/test_schema_changes.py`, `backend/tests/test_balance_concurrency.py` (wrap in `org_context`)

**Interfaces:**
- Consumes: `current_org_alias()` from Task 8.
- Produces:
  - `apps.orgs.router.TenantRouter`
  - `apps.orgs.router.NoOrgContext` — raised when a tenant model is queried with no org set
  - `apps.orgs.router.TENANT_APPS = {"accounts", "ledger"}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_router.py`:

```python
from django.test import TestCase

from apps.accounts.models import User
from apps.ledger.models import TransactionFund
from apps.orgs.context import org_context
from apps.orgs.models import Org
from apps.orgs.router import NoOrgContext, TenantRouter


class RouterReadWriteTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.router = TenantRouter()

    def test_tenant_model_routes_to_the_current_org(self):
        with org_context("tenant_dev"):
            self.assertEqual(self.router.db_for_read(User), "tenant_dev")
            self.assertEqual(self.router.db_for_write(TransactionFund), "tenant_dev")

    def test_control_plane_model_always_routes_to_default(self):
        self.assertEqual(self.router.db_for_read(Org), "default")
        with org_context("tenant_dev"):
            self.assertEqual(self.router.db_for_write(Org), "default")

    def test_tenant_model_without_context_raises(self):
        with self.assertRaises(NoOrgContext):
            self.router.db_for_read(User)

    def test_the_raise_names_the_model(self):
        with self.assertRaises(NoOrgContext) as caught:
            self.router.db_for_write(TransactionFund)
        self.assertIn("TransactionFund", str(caught.exception))


class RouterMigrateTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.router = TenantRouter()

    def test_ledger_migrates_only_on_tenant_aliases(self):
        self.assertFalse(self.router.allow_migrate("default", "ledger"))
        self.assertTrue(self.router.allow_migrate("tenant_dev", "ledger"))
        self.assertTrue(self.router.allow_migrate("org_abc", "ledger"))

    def test_accounts_migrates_only_on_tenant_aliases(self):
        self.assertFalse(self.router.allow_migrate("default", "accounts"))
        self.assertTrue(self.router.allow_migrate("org_abc", "accounts"))

    def test_orgs_migrates_only_on_default(self):
        self.assertTrue(self.router.allow_migrate("default", "orgs"))
        self.assertFalse(self.router.allow_migrate("org_abc", "orgs"))

    def test_contenttypes_stays_on_default(self):
        self.assertTrue(self.router.allow_migrate("default", "contenttypes"))
        self.assertFalse(self.router.allow_migrate("org_abc", "contenttypes"))


class RouterRelationTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.router = TenantRouter()

    def test_same_database_relations_allowed(self):
        user = User(id="u", username="u", email="u@example.com", password_hash="x")
        user._state.db = "tenant_dev"
        other = User(id="v", username="v", email="v@example.com", password_hash="x")
        other._state.db = "tenant_dev"
        self.assertTrue(self.router.allow_relation(user, other))

    def test_cross_database_relations_refused(self):
        user = User(id="u", username="u", email="u@example.com", password_hash="x")
        user._state.db = "tenant_dev"
        org = Org(id="o", name="O", slug="o", owner_email="o@example.com", db_connection="x")
        org._state.db = "default"
        self.assertFalse(self.router.allow_relation(user, org))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_router --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orgs.router'`.

- [ ] **Step 3: Write the router**

Create `backend/apps/orgs/router.py`:

```python
"""Route tenant models to the current org's database.

The router raises rather than falling back to `default` when no org is set.
Falling back would mean a bug in context propagation silently reads or writes
the wrong organisation's ledger; raising turns that into a visible 500 instead
of a quiet data leak.
"""

from apps.orgs.context import current_org_alias

TENANT_APPS = {"accounts", "ledger"}


class NoOrgContext(RuntimeError):
    """A tenant model was queried with no organisation in context."""


class TenantRouter:
    def _route(self, model, operation):
        app_label = model._meta.app_label
        if app_label in TENANT_APPS:
            alias = current_org_alias()
            if alias is None:
                raise NoOrgContext(
                    f"{model.__name__} ({app_label}) was queried for {operation} with no "
                    "organisation in context. Tenant models require OrgContextMiddleware "
                    "or an explicit org_context() block."
                )
            return alias
        return "default"

    def db_for_read(self, model, **hints):
        return self._route(model, "read")

    def db_for_write(self, model, **hints):
        return self._route(model, "write")

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in TENANT_APPS:
            return db != "default"
        return db == "default"
```

`allow_migrate` returns `db != "default"` for tenant apps rather than testing for an `org_` prefix, so the `tenant_dev` alias and every future org alias are covered by one rule.

**On the two URL parsers.** `settings._parse_database_url` (Task 1) and `connections.build_config` (Task 8) both parse a `postgres://` URL, and that duplication is deliberate. `settings.py` is imported before the app registry is populated, so it cannot import from `apps.orgs` without a circular import at startup. Do not merge them. If one changes, change both — the tests in `test_org_connections.py` cover `build_config` only.

- [ ] **Step 4: Register the router**

Append to `backend/fundvault_backend/settings.py`:

```python
DATABASE_ROUTERS = ["apps.orgs.router.TenantRouter"]
```

- [ ] **Step 5: Run the router tests**

Run: `python backend/manage.py test tests.test_router --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 10 tests.

- [ ] **Step 6: Run the whole suite and expect breakage**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL. Tests from Tasks 4 and 5 reach tenant models through `.using("tenant_dev")`, which the router now overrides for reads it is asked to route.

Fix each by wrapping the body in `with org_context("tenant_dev"):` and removing the `.using("tenant_dev")` calls — the router does that job now. Add `from apps.orgs.context import org_context` to both files. In `test_balance_concurrency.py` the threaded helper needs its own context, because contextvars do not inherit into threads started with `threading.Thread`: wrap the body of `_post_debit` in `with org_context("tenant_dev"):` and change the ORM calls to drop `.using("tenant_dev")`, keeping `transaction.atomic(using="tenant_dev")` as-is since that names a connection rather than routing a model.

This breakage is the router working: a test that used to reach a database by accident now cannot.

- [ ] **Step 7: Re-run until green**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/orgs/router.py backend/fundvault_backend/settings.py backend/tests
git commit -m "Add the tenant database router

Tenant models resolve to the current org's connection and raise NoOrgContext
when none is set, rather than falling back to default. A context-propagation
bug becomes a visible failure instead of a silent cross-tenant read.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: JWT org claim and the middleware

**Files:**
- Create: `backend/apps/orgs/middleware.py`
- Modify: `backend/apps/common/auth.py:18-27` (`create_session_token`)
- Modify: `backend/apps/accounts/views.py` (two `create_session_token` call sites)
- Modify: `backend/fundvault_backend/settings.py:20-23` (MIDDLEWARE)
- Create: `backend/tests/test_org_middleware.py`

**Interfaces:**
- Consumes: `ensure_connection`, `InvalidConnectionString` (Task 8).
- Produces:
  - `create_session_token(user_id, org_id)` — **signature change**, `org_id` now required
  - `apps.orgs.middleware.OrgContextMiddleware`
  - `apps.orgs.middleware.PUBLIC_PREFIXES` — tuple of paths that bypass org resolution
  - `request.fv_org` — the resolved `Org`, set by the middleware
  - HTTP 503 `{"error": "..."}` when a tenant database is unreachable

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_org_middleware.py`:

```python
import json
from datetime import timedelta

import jwt
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.context import current_org_alias, org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class TokenTests(TestCase):
    databases = {"default"}

    def test_token_carries_the_org_claim(self):
        token = create_session_token("u1", "o1")
        decoded = jwt.decode(token, settings.FUNDVAULT_JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(decoded["id"], "u1")
        self.assertEqual(decoded["org_id"], "o1")


class MiddlewareTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.token = create_session_token("u1", "o1")
        with org_context("tenant_dev"):
            User.objects.create(
                id="u1", username="alice", email="alice@example.com",
                password_hash="x", role=User.Role.OWNER,
            )
            Session.objects.create(
                id="s1", user_id="u1", token=self.token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        self.client = Client()

    def test_context_is_clear_after_a_request(self):
        self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertIsNone(
            current_org_alias(), "middleware leaked org context past the response"
        )

    def test_unknown_org_is_rejected(self):
        stray = create_session_token("u1", "does-not-exist")
        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {stray}")
        self.assertEqual(response.status_code, 401)

    def test_token_without_an_org_claim_is_rejected(self):
        legacy = jwt.encode(
            {"id": "u1", "exp": int((timezone.now() + timedelta(hours=1)).timestamp())},
            settings.FUNDVAULT_JWT_SECRET,
            algorithm="HS256",
        )
        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {legacy}")
        self.assertEqual(response.status_code, 401)

    def test_request_without_a_token_is_rejected(self):
        response = self.client.get("/api/databases")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content)["error"], "No token provided")

    def test_unreachable_tenant_returns_503_naming_the_org(self):
        Org.objects.filter(id="o1").update(
            db_connection="postgres://fundvault:devpassword@127.0.0.1:9/nothing"
        )
        response = self.client.get("/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Acme", json.loads(response.content)["error"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_org_middleware --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `create_session_token() missing 1 required positional argument: 'org_id'`.

- [ ] **Step 3: Add the org claim to tokens**

In `backend/apps/common/auth.py`, replace `create_session_token`:

```python
def create_session_token(user_id, org_id):
    now = timezone.now()
    payload = {
        "id": user_id,
        "org_id": org_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.FUNDVAULT_SESSION_HOURS)).timestamp()),
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.FUNDVAULT_JWT_SECRET, algorithm="HS256")
```

The org travels in a signed claim rather than a header, so a client cannot swap it.

- [ ] **Step 4: Write the middleware**

Create `backend/apps/orgs/middleware.py`:

```python
"""Resolve the request's organisation and open its database connection."""

import jwt
from django.conf import settings
from django.db import OperationalError
from django.http import JsonResponse

from apps.orgs.connections import InvalidConnectionString, ensure_connection
from apps.orgs.context import reset_current_org, set_current_org
from apps.orgs.models import Org

# Paths that must work before an organisation is known.
PUBLIC_PREFIXES = (
    "/api/auth/orgs",
    "/api/orgs/create",
    "/api/orgs/join",
    "/api/orgs/validate-connection",
)


class OrgContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(PUBLIC_PREFIXES):
            return self.get_response(request)

        org = self._resolve_org(request)
        if isinstance(org, JsonResponse):
            return org
        if org is None:
            return self.get_response(request)  # no usable token; the view answers 401

        try:
            alias = ensure_connection(org)
        except InvalidConnectionString as exc:
            return JsonResponse(
                {"error": f"{org.name} has an invalid database connection: {exc}"},
                status=503,
            )

        request.fv_org = org
        token = set_current_org(alias)
        try:
            return self.get_response(request)
        except OperationalError as exc:
            return JsonResponse(
                {"error": f"{org.name}'s database is unreachable. {exc}"}, status=503
            )
        finally:
            reset_current_org(token)

    def _resolve_org(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return self._org_from_body(request)

        raw = header.split(" ", 1)[1].strip()
        try:
            decoded = jwt.decode(raw, settings.FUNDVAULT_JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None  # auth_required produces the 401 with its own wording

        org_id = decoded.get("org_id")
        if not org_id:
            return JsonResponse(
                {"error": "Token has no organisation. Log in again."}, status=401
            )
        org = Org.objects.filter(id=org_id).first()
        if not org:
            return JsonResponse({"error": "Organisation no longer exists."}, status=401)
        return org

    def _org_from_body(self, request):
        """Login and signup carry orgId in the body — there is no token yet."""
        if request.path not in ("/api/auth/login", "/api/auth/signup"):
            return None
        import json as _json

        try:
            payload = _json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return None
        org_id = str(payload.get("orgId", "")).strip()
        if not org_id:
            return JsonResponse({"error": "Choose an organisation first"}, status=400)
        org = Org.objects.filter(id=org_id).first()
        if not org:
            return JsonResponse({"error": "Organisation not found"}, status=404)
        return org
```

Login and signup are the one case where the org cannot come from a token, because no token exists yet. They carry `orgId` in the body, and the middleware resolves it there so the views themselves stay unchanged in shape.

- [ ] **Step 5: Register the middleware**

In `backend/fundvault_backend/settings.py`, replace the `MIDDLEWARE` list:

```python
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.orgs.middleware.OrgContextMiddleware",
]
```

- [ ] **Step 6: Update the two token call sites**

In `backend/apps/accounts/views.py`, both `signup` and `login` call `create_session_token(user.id)`. Change each to:

```python
    token = create_session_token(user.id, request.fv_org.id)
```

and immediately after each function's method check, add:

```python
    if not getattr(request, "fv_org", None):
        return json_error("Choose an organisation first", 400)
```

- [ ] **Step 7: Run the middleware tests**

Run: `python backend/manage.py test tests.test_org_middleware --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 6 tests.

- [ ] **Step 8: Run the full suite**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 9: Commit**

```bash
git add backend/apps backend/fundvault_backend/settings.py backend/tests/test_org_middleware.py
git commit -m "Resolve the org from a signed JWT claim in middleware

The org travels as a signed claim so a client cannot swap it. The middleware
always resets the contextvar in a finally, because a leaked contextvar on a
reused worker would serve the next request another org's database.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Prove isolation end to end

**Files:**
- Create: `backend/apps/orgs/management/__init__.py`
- Create: `backend/apps/orgs/management/commands/__init__.py`
- Create: `backend/apps/orgs/management/commands/register_org.py`
- Create: `backend/tests/test_tenant_isolation.py`

**Interfaces:**
- Consumes: everything from Tasks 7-10.
- Produces: `python backend/manage.py register_org --name NAME --url URL --owner-email EMAIL [--id ID]`

- [ ] **Step 1: Write the isolation test**

Create `backend/tests/test_tenant_isolation.py`:

```python
"""The load-bearing test of the design: two orgs cannot see each other."""

import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund
from apps.orgs.context import org_context
from apps.orgs.models import Org
from apps.orgs.router import NoOrgContext

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class NoContextTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_tenant_query_outside_a_request_raises(self):
        with self.assertRaises(NoOrgContext):
            list(DatabaseFund.objects.all())

    def test_control_plane_query_outside_a_request_is_fine(self):
        self.assertEqual(Org.objects.count(), 0)


class TwoOrgTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        # Both orgs point at the same physical development database on purpose.
        # If isolation held only because the databases differed, this would
        # prove nothing about routing. Here the alias is the only separation,
        # so a routing bug shows up as a visible cross-org read.
        self.client = Client()
        Org.objects.create(
            id="orga", name="Alpha Funds", slug="alpha",
            owner_email="a@example.com", db_connection=TENANT_URL,
        )
        Org.objects.create(
            id="orgb", name="Beta Funds", slug="beta",
            owner_email="b@example.com", db_connection=TENANT_URL,
        )
        self.token_a = create_session_token("u1", "orga")
        with org_context("tenant_dev"):
            user = User.objects.create(
                id="u1", username="alice", email="alice@example.com",
                password_hash="x", role=User.Role.OWNER,
            )
            DatabaseFund.objects.create(id="f1", created_by=user, name="Alpha Fund")
            Session.objects.create(
                id="s1", user_id="u1", token=self.token_a,
                expires_at=timezone.now() + timedelta(hours=1),
            )

    def test_authenticated_request_reads_its_own_org(self):
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token_a}"
        )
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in json.loads(response.content)]
        self.assertEqual(names, ["Alpha Fund"])

    def test_tampered_org_claim_is_rejected(self):
        head, payload, _sig = self.token_a.split(".")
        tampered = f"{head}.{payload}.deadbeefsignature"
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {tampered}"
        )
        self.assertEqual(response.status_code, 401)

    def test_every_tenant_response_omits_credentials(self):
        response = self.client.get(
            "/api/databases", HTTP_AUTHORIZATION=f"Bearer {self.token_a}"
        )
        body = response.content.decode("utf-8")
        for secret in ("postgres://", "devpassword", "db_connection"):
            self.assertNotIn(secret, body, f"{secret} leaked into an API response")
```

- [ ] **Step 2: Run it**

Run: `python backend/manage.py test tests.test_tenant_isolation --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 5 tests. If `test_tenant_query_outside_a_request_raises` fails, the router is falling back to `default` — the single most important defect this phase can have. Fix it before continuing.

- [ ] **Step 3: Write the org registration command**

```bash
mkdir -p backend/apps/orgs/management/commands
touch backend/apps/orgs/management/__init__.py backend/apps/orgs/management/commands/__init__.py
```

Create `backend/apps/orgs/management/commands/register_org.py`:

```python
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
```

The org row is written only after the connection, the write probe, and the migration all succeed — a failure leaves no half-created org.

- [ ] **Step 4: Register your development org**

```bash
python backend/manage.py register_org --name "Dev Org" --owner-email "you@example.com" --url "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"
```

Expected: a version line, "Write access confirmed.", migration output, and an org id. Record that id — Phase 3 uses it.

- [ ] **Step 5: Verify the running application**

Start the backend: `python backend/manage.py runserver 127.0.0.1:8000`

In another terminal:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/databases
```

Expected: `401` — refused cleanly, not a 500 from a missing org context.

- [ ] **Step 6: Run the full suite**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orgs/management backend/tests/test_tenant_isolation.py
git commit -m "Prove tenant isolation and add register_org

Both test orgs point at the same physical database on purpose: the alias is
then the only thing separating them, so a routing bug surfaces as a visible
cross-org read rather than being masked by the databases differing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Phase 2 is complete.** The routing layer works and is proven. The application is reachable only with a token minted against a registered org; Phase 3 gives it a front door.

---

## Phase 3 — Onboarding, join codes, login routing

Phase 3 gives the platform a front door: create an org, join one with a code, and pick which org to sign in to.

### Task 12: Connection validation and org creation over HTTP

**Files:**
- Create: `backend/apps/orgs/provisioning.py`
- Create: `backend/apps/orgs/serializers.py`
- Create: `backend/apps/orgs/views.py`
- Create: `backend/apps/orgs/urls.py`
- Modify: `backend/fundvault_backend/urls.py`
- Create: `backend/tests/test_org_provisioning.py`

**Interfaces:**
- Consumes: `build_config`, `drop_connection` (Task 8); `Org` (Task 7).
- Produces:
  - `apps.orgs.provisioning.ConnectionCheck` — dataclass `ok: bool`, `message: str`, `version: str`
  - `apps.orgs.provisioning.check_connection(url)` → `ConnectionCheck`
  - `apps.orgs.provisioning.provision_org(name, url, owner_email)` → `Org` (raises `ProvisioningError`)
  - `apps.orgs.provisioning.ProvisioningError`
  - `apps.orgs.serializers.serialize_org(org)` → `{"id", "name", "slug", "created_at"}` — **never** credentials
  - `POST /api/orgs/validate-connection` → `{"ok": bool, "message": str}`
  - `POST /api/orgs/create` → `{"org": {...}, "token": str, "user": {...}}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_org_provisioning.py`:

```python
import json

from django.test import Client, TestCase

from apps.orgs.models import EmailIndex, Org
from apps.orgs.provisioning import ProvisioningError, check_connection, provision_org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"
DEAD_URL = "postgres://fundvault:devpassword@127.0.0.1:9/nothing"


class ConnectionCheckTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_reachable_database_passes(self):
        result = check_connection(TENANT_URL)
        self.assertTrue(result.ok, result.message)
        self.assertIn("PostgreSQL", result.version)

    def test_unreachable_database_fails_with_a_readable_message(self):
        result = check_connection(DEAD_URL)
        self.assertFalse(result.ok)
        self.assertTrue(result.message, "a failure must explain itself")

    def test_malformed_url_fails_without_raising(self):
        result = check_connection("not-a-url")
        self.assertFalse(result.ok)
        self.assertIn("postgres", result.message.lower())

    def test_check_leaves_no_alias_behind(self):
        from django.db import connections

        before = set(connections.databases)
        check_connection(TENANT_URL)
        self.assertEqual(set(connections.databases), before)


class ProvisionTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_provision_creates_the_org(self):
        org = provision_org("Acme Funds", TENANT_URL, "owner@example.com")
        self.assertEqual(org.name, "Acme Funds")
        self.assertEqual(org.slug, "acme-funds")
        self.assertTrue(Org.objects.filter(id=org.id).exists())

    def test_provision_failure_leaves_no_org_row(self):
        with self.assertRaises(ProvisioningError):
            provision_org("Broken", DEAD_URL, "owner@example.com")
        self.assertEqual(Org.objects.count(), 0)

    def test_slugs_do_not_collide(self):
        first = provision_org("Acme Funds", TENANT_URL, "a@example.com")
        second = provision_org("Acme Funds", TENANT_URL, "b@example.com")
        self.assertNotEqual(first.slug, second.slug)


class CreateOrgEndpointTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()

    def _post(self, payload):
        return self.client.post(
            "/api/orgs/create", data=json.dumps(payload), content_type="application/json"
        )

    def test_creates_org_owner_and_session(self):
        response = self._post({
            "name": "Acme Funds",
            "databaseUrl": TENANT_URL,
            "username": "alice",
            "email": "alice@example.com",
            "password": "hunter22",
        })
        self.assertEqual(response.status_code, 200, response.content)
        body = json.loads(response.content)
        self.assertEqual(body["user"]["role"], "owner")
        self.assertTrue(body["token"])
        self.assertEqual(body["org"]["name"], "Acme Funds")

    def test_response_never_contains_the_connection_string(self):
        response = self._post({
            "name": "Acme Funds", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })
        text = response.content.decode("utf-8")
        self.assertNotIn("devpassword", text)
        self.assertNotIn("postgres://", text)

    def test_writes_the_email_index(self):
        self._post({
            "name": "Acme Funds", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })
        self.assertTrue(EmailIndex.objects.filter(email="alice@example.com").exists())

    def test_short_password_is_refused(self):
        response = self._post({
            "name": "Acme", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "short",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("6 characters", json.loads(response.content)["error"])

    def test_bad_connection_is_refused_before_anything_is_created(self):
        response = self._post({
            "name": "Acme", "databaseUrl": DEAD_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Org.objects.count(), 0)
        self.assertEqual(EmailIndex.objects.count(), 0)

    def test_missing_fields_are_refused(self):
        response = self._post({"name": "Acme"})
        self.assertEqual(response.status_code, 400)


class ValidateConnectionEndpointTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_reports_success_without_creating_anything(self):
        response = Client().post(
            "/api/orgs/validate-connection",
            data=json.dumps({"databaseUrl": TENANT_URL}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["ok"])
        self.assertEqual(Org.objects.count(), 0)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_org_provisioning --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orgs.provisioning'`.

- [ ] **Step 3: Write the provisioning module**

Create `backend/apps/orgs/provisioning.py`:

```python
"""Validate an organisation's Postgres and build its schema.

Nothing is written to the control plane until the connection works, the
credentials can create tables, and migrations apply. A failure at any point
leaves no org row, so there is never a half-created org whose database is in
an unknown state.
"""

from dataclasses import dataclass

from django.core.management import call_command
from django.db import connections
from django.utils.text import slugify

from apps.common.utils import uid
from apps.orgs.connections import (
    InvalidConnectionString,
    alias_for_org,
    build_config,
    drop_connection,
)
from apps.orgs.models import Org


class ProvisioningError(Exception):
    """The org's database could not be prepared."""


@dataclass
class ConnectionCheck:
    ok: bool
    message: str
    version: str = ""


def _friendly(exc):
    """Turn a psycopg failure into something an operator can act on."""
    text = str(exc)
    lowered = text.lower()
    if "password authentication failed" in lowered:
        return "Authentication failed — check the username and password."
    if "could not translate host name" in lowered or "name or service not known" in lowered:
        return "Host not found — check the hostname in the connection string."
    if "connection refused" in lowered:
        return "Connection refused — check the host and port, and that the server allows external connections."
    if "does not exist" in lowered and "database" in lowered:
        return "That database does not exist on the server."
    if "ssl" in lowered:
        return f"TLS problem: {text}"
    if "timeout" in lowered or "timed out" in lowered:
        return "Timed out connecting — the host may be firewalled."
    return text


def check_connection(url):
    """Probe a candidate database without registering it permanently."""
    probe_alias = f"probe_{uid()}"
    try:
        config = build_config(url)
    except InvalidConnectionString as exc:
        return ConnectionCheck(ok=False, message=str(exc))

    connections.databases[probe_alias] = config
    try:
        with connections[probe_alias].cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
            cursor.execute("CREATE TABLE _fundvault_probe (id integer)")
            cursor.execute("DROP TABLE _fundvault_probe")
        return ConnectionCheck(ok=True, message="Connected, and able to create tables.", version=version)
    except Exception as exc:
        return ConnectionCheck(ok=False, message=_friendly(exc))
    finally:
        drop_connection(probe_alias)


def _unique_slug(name, org_id):
    base = slugify(name)[:70] or "org"
    if not Org.objects.filter(slug=base).exists():
        return base
    return f"{base}-{org_id[:6]}"


def provision_org(name, url, owner_email):
    """Verify the database, migrate it, then record the org. Raises ProvisioningError."""
    check = check_connection(url)
    if not check.ok:
        raise ProvisioningError(check.message)

    org_id = uid()
    alias = alias_for_org(org_id)
    connections.databases[alias] = build_config(url)
    try:
        call_command("migrate", database=alias, verbosity=0)
    except Exception as exc:
        drop_connection(alias)
        raise ProvisioningError(f"Could not build the schema: {_friendly(exc)}")

    return Org.objects.create(
        id=org_id,
        name=name,
        slug=_unique_slug(name, org_id),
        owner_email=owner_email,
        db_connection=url,
    )
```

- [ ] **Step 4: Write the org serializer**

Create `backend/apps/orgs/serializers.py`:

```python
"""Org serialisers.

Credentials are never serialised. `serialize_org` lists its keys explicitly
rather than iterating model fields, so adding a secret column later cannot
silently start exposing it.
"""


def serialize_org(org):
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


def serialize_org_summary(org):
    """The shape shown on the org picker before anyone has authenticated."""
    return {"id": org.id, "name": org.name, "slug": org.slug}
```

- [ ] **Step 5: Write the views**

Create `backend/apps/orgs/views.py`:

```python
import bcrypt
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import User
from apps.accounts.serializers import serialize_user
from apps.common.audit import add_audit
from apps.common.auth import create_session, create_session_token
from apps.common.utils import json_error, parse_body, uid
from apps.orgs.connections import ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, Org
from apps.orgs.provisioning import ProvisioningError, check_connection, provision_org
from apps.orgs.serializers import serialize_org


@csrf_exempt
def validate_connection(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    url = str(parse_body(request).get("databaseUrl", "")).strip()
    if not url:
        return json_error("Database URL required", 400)
    result = check_connection(url)
    return JsonResponse({"ok": result.ok, "message": result.message})


@csrf_exempt
def create_org(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    body = parse_body(request)
    name = str(body.get("name", "")).strip()
    url = str(body.get("databaseUrl", "")).strip()
    username = str(body.get("username", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not name or not url or not username or not email or not password:
        return json_error("All fields required", 400)
    if len(password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    try:
        org = provision_org(name, url, email)
    except ProvisioningError as exc:
        return json_error(str(exc), 400)

    alias = ensure_connection(org)
    try:
        with org_context(alias):
            user = User.objects.create(
                id=uid(),
                username=username,
                email=email,
                password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
                role=User.Role.OWNER,
                is_active=True,
                updated_at=timezone.now(),
            )
            token = create_session_token(user.id, org.id)
            create_session(user.id, token)
            add_audit(user.id, "create", "org", org.id, f'Organisation "{org.name}" created')
    except Exception as exc:
        # The database is migrated but has no owner: an org nobody can enter.
        # Remove the registration so the person can simply try again.
        Org.objects.filter(id=org.id).delete()
        return json_error(f"Could not create the owner account: {exc}", 500)

    EmailIndex.objects.create(email=email, org=org)
    return JsonResponse({"org": serialize_org(org), "token": token, "user": serialize_user(user)})
```

The rollback on owner-creation failure matters: a migrated database with no owner is an org nobody can ever enter, and the person would be blocked from retrying with the same connection string because the org already exists.

- [ ] **Step 6: Wire the URLs**

Create `backend/apps/orgs/urls.py`:

```python
from django.urls import path

from apps.orgs import views


urlpatterns = [
    path("orgs/validate-connection", views.validate_connection),
    path("orgs/create", views.create_org),
]
```

Replace `backend/fundvault_backend/urls.py`:

```python
from django.urls import include, path


urlpatterns = [
    path("api/", include("apps.orgs.urls")),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.ledger.urls")),
]
```

`apps.orgs.urls` is included first so its concrete paths win over any later pattern.

- [ ] **Step 7: Run the tests**

Run: `python backend/manage.py test tests.test_org_provisioning --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 14 tests.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/orgs backend/fundvault_backend/urls.py backend/tests/test_org_provisioning.py
git commit -m "Add org creation with connection validation

The org row is written only after the database connects, proves it can create
tables, and accepts migrations. If owner creation then fails, the registration
is rolled back rather than leaving an org nobody can enter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Join codes

**Files:**
- Modify: `backend/apps/orgs/views.py` (add `join_org`, `mint_join_code`, `list_join_codes`, `revoke_join_code`)
- Modify: `backend/apps/orgs/urls.py`
- Create: `backend/tests/test_join_codes.py`

**Interfaces:**
- Consumes: `JoinCode`, `new_join_code` (Task 7); `provision_org` patterns (Task 12).
- Produces:
  - `POST /api/orgs/join/preview` → `{"org": {"id", "name", "slug"}}` — resolves a code to an org name only
  - `POST /api/orgs/join` → `{"org", "token", "user"}` — creates the user and consumes the code
  - `POST /api/orgs/codes` (authenticated) → `{"code", "grants_role", "expires_at", "max_uses"}`
  - `GET /api/orgs/codes` (authenticated) → list
  - `DELETE /api/orgs/codes/<code>` (authenticated) → `{"success": true}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_join_codes.py`:

```python
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, JoinCode, Org, new_join_code

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class JoinFlowTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme Funds", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.code = JoinCode.objects.create(
            code=new_join_code(), org=self.org, grants_role="member",
            expires_at=timezone.now() + timedelta(days=7), max_uses=3,
        )

    def _join(self, **overrides):
        payload = {
            "code": self.code.code,
            "username": "bob",
            "email": "bob@example.com",
            "password": "hunter22",
        }
        payload.update(overrides)
        return self.client.post(
            "/api/orgs/join", data=json.dumps(payload), content_type="application/json"
        )

    def test_preview_returns_the_org_name_only(self):
        response = self.client.post(
            "/api/orgs/join/preview",
            data=json.dumps({"code": self.code.code}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body["org"]["name"], "Acme Funds")
        self.assertNotIn("db_connection", json.dumps(body))
        self.assertNotIn("postgres://", response.content.decode("utf-8"))

    def test_join_creates_a_user_with_the_granted_role(self):
        response = self._join()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)["user"]["role"], "member")

    def test_join_consumes_one_use(self):
        self._join()
        self.code.refresh_from_db()
        self.assertEqual(self.code.uses, 1)

    def test_join_writes_the_email_index(self):
        self._join()
        self.assertTrue(
            EmailIndex.objects.filter(email="bob@example.com", org=self.org).exists()
        )

    def test_expired_code_is_refused(self):
        JoinCode.objects.filter(code=self.code.code).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self._join()
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", json.loads(response.content)["error"].lower())

    def test_revoked_code_is_refused(self):
        JoinCode.objects.filter(code=self.code.code).update(revoked=True)
        self.assertEqual(self._join().status_code, 400)

    def test_unknown_code_is_refused(self):
        self.assertEqual(self._join(code="FUNDVAULT-ZZZZ-ZZZZ").status_code, 404)

    def test_duplicate_email_in_the_same_org_is_refused(self):
        self._join()
        response = self._join(username="bob2")
        self.assertEqual(response.status_code, 400)

    def test_exhausted_code_is_refused(self):
        JoinCode.objects.filter(code=self.code.code).update(max_uses=1, uses=1)
        self.assertEqual(self._join().status_code, 400)


class CodeManagementTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner_token = self._user("u1", "owner", User.Role.OWNER)
        self.member_token = self._user("u2", "bob", User.Role.MEMBER)

    def _user(self, user_id, username, role):
        token = create_session_token(user_id, "o1")
        with org_context("tenant_dev"):
            User.objects.create(
                id=user_id, username=username, email=f"{username}@example.com",
                password_hash="x", role=role,
            )
            Session.objects.create(
                id=f"s{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def _mint(self, token, role="member"):
        return self.client.post(
            "/api/orgs/codes",
            data=json.dumps({"role": role}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_owner_can_mint_an_admin_code(self):
        self.assertEqual(self._mint(self.owner_token, "admin").status_code, 200)

    def test_member_cannot_mint_any_code(self):
        self.assertEqual(self._mint(self.member_token).status_code, 403)

    def test_minted_code_is_shaped_correctly(self):
        response = self._mint(self.owner_token)
        self.assertRegex(json.loads(response.content)["code"], r"^FUNDVAULT-")

    def test_owner_role_cannot_be_granted_by_a_code(self):
        response = self._mint(self.owner_token, "owner")
        self.assertEqual(response.status_code, 400)

    def test_revoking_a_code_prevents_its_use(self):
        code = json.loads(self._mint(self.owner_token).content)["code"]
        response = self.client.delete(
            f"/api/orgs/codes/{code}", HTTP_AUTHORIZATION=f"Bearer {self.owner_token}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(JoinCode.objects.get(code=code).revoked)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_join_codes --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — 404s, because none of the join routes exist yet.

- [ ] **Step 3: Add the join views**

Append to `backend/apps/orgs/views.py`:

```python
from datetime import timedelta

from apps.common.auth import auth_required
from apps.orgs.models import JoinCode, new_join_code
from apps.orgs.serializers import serialize_org_summary

# Which roles each role may hand out. Owner is absent from every list:
# ownership transfers explicitly, never through an invite.
MINTABLE = {
    User.Role.OWNER: {User.Role.ADMIN, User.Role.MEMBER, User.Role.VIEWER},
    User.Role.ADMIN: {User.Role.MEMBER, User.Role.VIEWER},
}


def _usable_code_or_error(raw_code):
    code = JoinCode.objects.select_related("org").filter(code=raw_code).first()
    if not code:
        return None, json_error("That join code does not exist", 404)
    if code.revoked:
        return None, json_error("That join code has been revoked", 400)
    if code.expires_at <= timezone.now():
        return None, json_error("That join code has expired", 400)
    if code.uses >= code.max_uses:
        return None, json_error("That join code has already been used the maximum number of times", 400)
    return code, None


@csrf_exempt
def join_preview(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    raw = str(parse_body(request).get("code", "")).strip().upper()
    code, error = _usable_code_or_error(raw)
    if error:
        return error
    return JsonResponse({"org": serialize_org_summary(code.org), "role": code.grants_role})


@csrf_exempt
def join_org(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    body = parse_body(request)
    raw = str(body.get("code", "")).strip().upper()
    username = str(body.get("username", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not raw or not username or not email or not password:
        return json_error("All fields required", 400)
    if len(password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    code, error = _usable_code_or_error(raw)
    if error:
        return error

    org = code.org
    alias = ensure_connection(org)
    with org_context(alias):
        clash = User.objects.filter(username=username).exists() or User.objects.filter(email=email).exists()
        if clash:
            return json_error("That username or email is already used in this organisation", 400)

        if not code.consume():
            return json_error("That join code is no longer usable", 400)

        user = User.objects.create(
            id=uid(),
            username=username,
            email=email,
            password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
            role=code.grants_role,
            is_active=True,
            updated_at=timezone.now(),
        )
        token = create_session_token(user.id, org.id)
        create_session(user.id, token)
        add_audit(user.id, "create", "user", user.id, f'{username} joined as {code.grants_role}')

    EmailIndex.objects.get_or_create(email=email, org=org)
    return JsonResponse({"org": serialize_org(org), "token": token, "user": serialize_user(user)})


@csrf_exempt
@auth_required
def join_codes(request):
    org = request.fv_org
    actor = request.fv_user

    if request.method == "GET":
        if actor.role not in MINTABLE:
            return json_error("Admin access required", 403)
        rows = JoinCode.objects.filter(org=org).order_by("-created_at")
        return JsonResponse(
            [
                {
                    "code": row.code,
                    "grants_role": row.grants_role,
                    "expires_at": row.expires_at.isoformat(),
                    "max_uses": row.max_uses,
                    "uses": row.uses,
                    "revoked": row.revoked,
                }
                for row in rows
            ],
            safe=False,
        )

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    allowed = MINTABLE.get(actor.role)
    if not allowed:
        return json_error("Admin access required", 403)

    body = parse_body(request)
    role = str(body.get("role", User.Role.MEMBER)).strip()
    if role not in allowed:
        return json_error(f"You cannot create a join code granting {role!r}", 400)

    try:
        max_uses = max(1, min(int(body.get("maxUses", 1)), 100))
        days = max(1, min(int(body.get("expiresInDays", 14)), 90))
    except (TypeError, ValueError):
        return json_error("maxUses and expiresInDays must be numbers", 400)

    code = JoinCode.objects.create(
        code=new_join_code(),
        org=org,
        grants_role=role,
        expires_at=timezone.now() + timedelta(days=days),
        max_uses=max_uses,
    )
    add_audit(actor.id, "create", "join_code", code.code, f"Join code created granting {role}")
    return JsonResponse(
        {
            "code": code.code,
            "grants_role": code.grants_role,
            "expires_at": code.expires_at.isoformat(),
            "max_uses": code.max_uses,
            "uses": 0,
            "revoked": False,
        }
    )


@csrf_exempt
@auth_required
def revoke_join_code(request, code):
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)
    if request.fv_user.role not in MINTABLE:
        return json_error("Admin access required", 403)
    updated = JoinCode.objects.filter(code=code, org=request.fv_org).update(revoked=True)
    if not updated:
        return json_error("Join code not found", 404)
    add_audit(request.fv_user.id, "delete", "join_code", code, "Join code revoked")
    return JsonResponse({"success": True})
```

`join_org` checks for a username clash *before* consuming the code, so a failed signup does not burn a use.

- [ ] **Step 4: Add the routes**

Replace `backend/apps/orgs/urls.py`:

```python
from django.urls import path

from apps.orgs import views


urlpatterns = [
    path("orgs/validate-connection", views.validate_connection),
    path("orgs/create", views.create_org),
    path("orgs/join/preview", views.join_preview),
    path("orgs/join", views.join_org),
    path("orgs/codes", views.join_codes),
    path("orgs/codes/<str:code>", views.revoke_join_code),
]
```

Add the two public join paths to `PUBLIC_PREFIXES` in `backend/apps/orgs/middleware.py` — `/api/orgs/join` already covers `/api/orgs/join/preview` by prefix, so no change is needed there. Confirm by reading the tuple; it must contain `"/api/orgs/join"`.

- [ ] **Step 5: Run the tests**

Run: `python backend/manage.py test tests.test_join_codes --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 14 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orgs backend/tests/test_join_codes.py
git commit -m "Add join codes

Members join with a code that maps to an org, never with the org's database
credentials. A username clash is checked before the code is consumed so a
failed signup does not burn a use, and no role may ever grant ownership.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: Login routing by email

**Files:**
- Modify: `backend/apps/accounts/views.py` (`login`, `signup`, `logout`)
- Modify: `backend/apps/accounts/urls.py`
- Create: `backend/tests/test_login_routing.py`

**Interfaces:**
- Consumes: `EmailIndex` (Task 7), middleware body-resolution (Task 10).
- Produces:
  - `POST /api/auth/orgs` body `{"email": "..."}` → `{"orgs": [{"id", "name", "slug"}]}`
  - `POST /api/auth/login` body gains required `orgId`
  - `signup` is removed from the public API — accounts are created only through `/api/orgs/create` or `/api/orgs/join`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_login_routing.py`:

```python
import json
from datetime import timedelta

import bcrypt
from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class OrgDiscoveryTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        self.first = Org.objects.create(
            id="o1", name="Acme Funds", slug="acme",
            owner_email="x@example.com", db_connection=TENANT_URL,
        )
        self.second = Org.objects.create(
            id="o2", name="Personal", slug="personal",
            owner_email="x@example.com", db_connection=TENANT_URL,
        )
        EmailIndex.objects.create(email="x@example.com", org=self.first)
        EmailIndex.objects.create(email="x@example.com", org=self.second)

    def _lookup(self, email):
        return self.client.post(
            "/api/auth/orgs",
            data=json.dumps({"email": email}),
            content_type="application/json",
        )

    def test_lists_every_org_for_the_email(self):
        response = self._lookup("x@example.com")
        self.assertEqual(response.status_code, 200)
        names = sorted(org["name"] for org in json.loads(response.content)["orgs"])
        self.assertEqual(names, ["Acme Funds", "Personal"])

    def test_unknown_email_returns_an_empty_list_not_an_error(self):
        response = self._lookup("nobody@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["orgs"], [])

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(len(json.loads(self._lookup("X@Example.com").content)["orgs"]), 2)

    def test_no_credentials_are_returned(self):
        text = self._lookup("x@example.com").content.decode("utf-8")
        self.assertNotIn("postgres://", text)
        self.assertNotIn("devpassword", text)


class LoginTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="a@example.com", db_connection=TENANT_URL,
        )
        EmailIndex.objects.create(email="alice@example.com", org=self.org)
        with org_context("tenant_dev"):
            User.objects.create(
                id="u1", username="alice", email="alice@example.com",
                password_hash=bcrypt.hashpw(b"hunter22", bcrypt.gensalt()).decode("utf-8"),
                role=User.Role.OWNER, is_active=True,
            )

    def _login(self, **overrides):
        payload = {"orgId": "o1", "username": "alice", "password": "hunter22"}
        payload.update(overrides)
        return self.client.post(
            "/api/auth/login", data=json.dumps(payload), content_type="application/json"
        )

    def test_login_with_an_org_succeeds(self):
        response = self._login()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)["token"])

    def test_login_without_an_org_is_refused(self):
        response = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": "alice", "password": "hunter22"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_login_with_an_unknown_org_is_refused(self):
        self.assertEqual(self._login(orgId="nope").status_code, 404)

    def test_wrong_password_is_refused(self):
        self.assertEqual(self._login(password="wrong").status_code, 401)

    def test_login_refreshes_the_email_index(self):
        before = EmailIndex.objects.get(email="alice@example.com").last_seen_at
        self._login()
        after = EmailIndex.objects.get(email="alice@example.com").last_seen_at
        self.assertGreater(after, before)

    def test_public_signup_is_gone(self):
        response = self.client.post(
            "/api/auth/signup",
            data=json.dumps({"username": "x", "email": "x@example.com", "password": "hunter22"}),
            content_type="application/json",
        )
        self.assertIn(response.status_code, (404, 405))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_login_routing --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `/api/auth/orgs` returns 404.

- [ ] **Step 3: Add the org lookup view**

In `backend/apps/accounts/views.py`, add after the imports:

```python
from apps.orgs.models import EmailIndex
from apps.orgs.serializers import serialize_org_summary


@csrf_exempt
def orgs_for_email(request):
    """Which organisations does this email belong to? Discovery only."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    email = str(parse_body(request).get("email", "")).strip().lower()
    if not email:
        return json_error("Email required", 400)

    rows = EmailIndex.objects.select_related("org").filter(email=email).order_by("-last_seen_at")
    return JsonResponse({"orgs": [serialize_org_summary(row.org) for row in rows]})
```

An unknown email returns an empty list rather than an error, so the endpoint cannot be used to enumerate which addresses are registered.

- [ ] **Step 4: Update login and remove public signup**

In `login`, after the successful password check and before returning, refresh the index:

```python
    EmailIndex.objects.filter(email=user.email, org=request.fv_org).update(
        last_seen_at=timezone.now()
    )
```

Delete the entire `signup` function from `backend/apps/accounts/views.py`. Accounts are now created only by `/api/orgs/create` (owner) or `/api/orgs/join` (everyone else). Leaving it would create users with no org and no email-index row — accounts that can never log in.

Replace `backend/apps/accounts/urls.py`:

```python
from django.urls import path

from apps.accounts import views


urlpatterns = [
    path("auth/orgs", views.orgs_for_email),
    path("auth/login", views.login),
    path("auth/logout", views.logout),
    path("auth/me", views.me),
    path("admin/users", views.admin_users),
    path("admin/users/<str:user_id>", views.admin_user_detail),
    path("admin/users/<str:user_id>/reset-password", views.admin_reset_password),
]
```

- [ ] **Step 5: Remove the signup path from the middleware's public list**

In `backend/apps/orgs/middleware.py`, `_org_from_body` currently allows `/api/auth/signup`. Change the tuple to just login:

```python
        if request.path != "/api/auth/login":
            return None
```

and add `"/api/auth/orgs"` to `PUBLIC_PREFIXES` if it is not already present.

- [ ] **Step 6: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests. Any test still calling `/api/auth/signup` must be updated to use `/api/orgs/create` or `/api/orgs/join`.

- [ ] **Step 7: Commit**

```bash
git add backend/apps backend/tests/test_login_routing.py
git commit -m "Route login through organisation selection

An email resolves to its orgs, the person picks one, and the password is
checked against that org's own users table. Public signup is removed: accounts
are created only by creating an org or redeeming a join code, so no account can
exist without an org.

Unknown emails return an empty list rather than an error, so the lookup cannot
enumerate registered addresses.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: The org gateway in the frontend

**Files:**
- Create: `frontend/src/components/auth/OrgGateway.jsx`
- Create: `frontend/src/components/auth/CreateOrgForm.jsx`
- Create: `frontend/src/components/auth/JoinOrgForm.jsx`
- Modify: `frontend/src/components/auth/AuthView.jsx`
- Modify: `frontend/src/components/FundVaultApp.jsx:238-300` (login/signup handlers), `:869-890` (auth branch)
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/app/globals.css`

**Interfaces:**
- Consumes: `/api/auth/orgs`, `/api/orgs/create`, `/api/orgs/join`, `/api/orgs/join/preview`, `/api/orgs/validate-connection`.
- Produces:
  - `localStorage` key `fundvault_org` holding `{id, name, slug}`
  - `OrgGateway` props: `onAuthenticated({token, user, org})`
  - `apiRequest` unchanged; new helpers `lookupOrgs(email)`, `createOrg(payload)`, `joinOrg(payload)`, `previewJoinCode(code)`, `validateConnection(url)`

- [ ] **Step 1: Add the API helpers**

Append to `frontend/src/lib/api.js`:

```javascript
export const lookupOrgs = email =>
  apiRequest("/api/auth/orgs", { method: "POST", body: JSON.stringify({ email }) });

export const previewJoinCode = code =>
  apiRequest("/api/orgs/join/preview", { method: "POST", body: JSON.stringify({ code }) });

export const validateConnection = databaseUrl =>
  apiRequest("/api/orgs/validate-connection", {
    method: "POST",
    body: JSON.stringify({ databaseUrl })
  });

export const createOrg = payload =>
  apiRequest("/api/orgs/create", { method: "POST", body: JSON.stringify(payload) });

export const joinOrg = payload =>
  apiRequest("/api/orgs/join", { method: "POST", body: JSON.stringify(payload) });
```

- [ ] **Step 2: Build the create-org form**

Create `frontend/src/components/auth/CreateOrgForm.jsx`:

```jsx
"use client";

import { useState } from "react";

import { createOrg, validateConnection } from "lib/api";

const PRESETS = [
  { id: "supabase", label: "Supabase", hint: "Project settings → Database → Connection string → URI. Use the pooled string on port 6543." },
  { id: "neon", label: "Neon", hint: "Dashboard → Connection Details → Connection string." },
  { id: "railway", label: "Railway", hint: "Postgres service → Variables → DATABASE_URL." },
  { id: "other", label: "Other Postgres", hint: "Any postgres:// URL your server can reach." }
];

export default function CreateOrgForm({ onCreated, onError }) {
  const [preset, setPreset] = useState("supabase");
  const [form, setForm] = useState({
    name: "",
    databaseUrl: "",
    username: "",
    email: "",
    password: ""
  });
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  const runCheck = async () => {
    if (!form.databaseUrl.trim()) return;
    setChecking(true);
    setCheckResult(null);
    try {
      setCheckResult(await validateConnection(form.databaseUrl.trim()));
    } catch (err) {
      setCheckResult({ ok: false, message: err.message });
    } finally {
      setChecking(false);
    }
  };

  const submit = async () => {
    if (!form.name.trim() || !form.databaseUrl.trim()) {
      onError("Organisation name and database URL are required");
      return;
    }
    if (form.password.length < 6) {
      onError("Password must be at least 6 characters");
      return;
    }
    setBusy(true);
    try {
      const result = await createOrg({
        name: form.name.trim(),
        databaseUrl: form.databaseUrl.trim(),
        username: form.username.trim(),
        email: form.email.trim(),
        password: form.password
      });
      onCreated(result);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const active = PRESETS.find(item => item.id === preset);

  return (
    <div className="org-form">
      <label>Organisation name</label>
      <input value={form.name} onChange={e => set("name", e.target.value)} placeholder="Acme Funds" />

      <label>Database provider</label>
      <div className="preset-row">
        {PRESETS.map(item => (
          <button
            key={item.id}
            type="button"
            className={`preset ${preset === item.id ? "active" : ""}`}
            onClick={() => setPreset(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <p className="hint">{active.hint}</p>

      <label>Connection string</label>
      <input
        value={form.databaseUrl}
        onChange={e => {
          set("databaseUrl", e.target.value);
          setCheckResult(null);
        }}
        placeholder="postgres://user:password@host:5432/database"
        autoComplete="off"
        spellCheck={false}
      />
      <button type="button" className="btn-secondary" onClick={runCheck} disabled={checking}>
        {checking ? "Testing…" : "Test connection"}
      </button>
      {checkResult && (
        <p className={checkResult.ok ? "check-ok" : "check-bad"}>{checkResult.message}</p>
      )}

      <hr />
      <p className="hint">You become the Owner of this organisation.</p>

      <label>Your username</label>
      <input value={form.username} onChange={e => set("username", e.target.value)} />

      <label>Your email</label>
      <input type="email" value={form.email} onChange={e => set("email", e.target.value)} />

      <label>Password</label>
      <input type="password" value={form.password} onChange={e => set("password", e.target.value)} />

      <button type="button" className="btn-primary" onClick={submit} disabled={busy}>
        {busy ? "Creating organisation…" : "Create organisation"}
      </button>
      <p className="hint">
        Creating the organisation builds its tables in your database. Nothing is saved if it fails.
      </p>
    </div>
  );
}
```

- [ ] **Step 3: Build the join form**

Create `frontend/src/components/auth/JoinOrgForm.jsx`:

```jsx
"use client";

import { useState } from "react";

import { joinOrg, previewJoinCode } from "lib/api";

export default function JoinOrgForm({ onJoined, onError }) {
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState(null);
  const [form, setForm] = useState({ username: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);

  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  const lookup = async () => {
    if (!code.trim()) return;
    try {
      setPreview(await previewJoinCode(code.trim().toUpperCase()));
    } catch (err) {
      setPreview(null);
      onError(err.message);
    }
  };

  const submit = async () => {
    if (form.password.length < 6) {
      onError("Password must be at least 6 characters");
      return;
    }
    setBusy(true);
    try {
      onJoined(
        await joinOrg({
          code: code.trim().toUpperCase(),
          username: form.username.trim(),
          email: form.email.trim(),
          password: form.password
        })
      );
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="org-form">
      <label>Join code</label>
      <input
        value={code}
        onChange={e => {
          setCode(e.target.value);
          setPreview(null);
        }}
        placeholder="FUNDVAULT-XXXX-XXXX"
        autoComplete="off"
        spellCheck={false}
      />
      <button type="button" className="btn-secondary" onClick={lookup}>
        Look up
      </button>

      {preview && (
        <>
          <p className="check-ok">
            Joining <strong>{preview.org.name}</strong> as {preview.role}
          </p>

          <label>Your username</label>
          <input value={form.username} onChange={e => set("username", e.target.value)} />

          <label>Your email</label>
          <input type="email" value={form.email} onChange={e => set("email", e.target.value)} />

          <label>Password</label>
          <input
            type="password"
            value={form.password}
            onChange={e => set("password", e.target.value)}
          />

          <button type="button" className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? "Joining…" : `Join ${preview.org.name}`}
          </button>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Build the gateway**

Create `frontend/src/components/auth/OrgGateway.jsx`:

```jsx
"use client";

import { useState } from "react";

import CreateOrgForm from "components/auth/CreateOrgForm";
import JoinOrgForm from "components/auth/JoinOrgForm";
import { apiRequest, lookupOrgs } from "lib/api";

export default function OrgGateway({ onAuthenticated, onError }) {
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [orgs, setOrgs] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [credentials, setCredentials] = useState({ username: "", password: "" });

  const findOrgs = async () => {
    if (!email.trim()) {
      onError("Enter your email");
      return;
    }
    try {
      const result = await lookupOrgs(email.trim());
      setOrgs(result.orgs);
      if (result.orgs.length === 1) setChosen(result.orgs[0]);
    } catch (err) {
      onError(err.message);
    }
  };

  const signIn = async () => {
    try {
      const response = await apiRequest("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          orgId: chosen.id,
          username: credentials.username || email.trim(),
          password: credentials.password
        })
      });
      onAuthenticated({ ...response, org: chosen });
    } catch (err) {
      onError(err.message);
    }
  };

  return (
    <div className="auth-shell">
      <h1>FundVault</h1>
      <div className="auth-tabs">
        <button className={mode === "signin" ? "active" : ""} onClick={() => setMode("signin")}>
          Sign in
        </button>
        <button className={mode === "join" ? "active" : ""} onClick={() => setMode("join")}>
          Join with a code
        </button>
        <button className={mode === "create" ? "active" : ""} onClick={() => setMode("create")}>
          Create an organisation
        </button>
      </div>

      {mode === "signin" && (
        <div className="org-form">
          <label>Email</label>
          <input
            type="email"
            value={email}
            onChange={e => {
              setEmail(e.target.value);
              setOrgs(null);
              setChosen(null);
            }}
          />
          {!orgs && (
            <button type="button" className="btn-primary" onClick={findOrgs}>
              Continue
            </button>
          )}

          {orgs && orgs.length === 0 && (
            <p className="check-bad">
              No organisations found for that email. Create one, or ask an admin for a join code.
            </p>
          )}

          {orgs && orgs.length > 0 && !chosen && (
            <>
              <p className="hint">Choose an organisation</p>
              {orgs.map(org => (
                <button key={org.id} type="button" className="org-choice" onClick={() => setChosen(org)}>
                  {org.name}
                </button>
              ))}
            </>
          )}

          {chosen && (
            <>
              <p className="hint">
                Signing in to <strong>{chosen.name}</strong>{" "}
                {orgs.length > 1 && (
                  <button type="button" className="linkish" onClick={() => setChosen(null)}>
                    change
                  </button>
                )}
              </p>
              <label>Username</label>
              <input
                value={credentials.username}
                onChange={e => setCredentials(prev => ({ ...prev, username: e.target.value }))}
                placeholder={email}
              />
              <label>Password</label>
              <input
                type="password"
                value={credentials.password}
                onChange={e => setCredentials(prev => ({ ...prev, password: e.target.value }))}
                onKeyDown={e => e.key === "Enter" && signIn()}
              />
              <button type="button" className="btn-primary" onClick={signIn}>
                Sign in
              </button>
            </>
          )}
        </div>
      )}

      {mode === "join" && <JoinOrgForm onJoined={onAuthenticated} onError={onError} />}
      {mode === "create" && <CreateOrgForm onCreated={onAuthenticated} onError={onError} />}
    </div>
  );
}
```

- [ ] **Step 5: Wire the gateway into the app**

In `frontend/src/components/FundVaultApp.jsx`:

Add to the imports:

```javascript
import OrgGateway from "components/auth/OrgGateway";
```

Add an org state variable beside `currentUser`:

```javascript
  const [currentOrg, setCurrentOrg] = useState(null);
```

Replace `handleLogin` and `handleSignup` with a single handler:

```javascript
  const handleAuthenticated = ({ token: nextToken, user, org }) => {
    setToken(nextToken);
    setCurrentUser({ ...user, token: nextToken });
    setCurrentOrg(org);
    localStorage.setItem("fundvault_token", nextToken);
    localStorage.setItem("fundvault_currentUser", JSON.stringify(user));
    localStorage.setItem("fundvault_org", JSON.stringify(org));
    toast(`Welcome to ${org.name}, ${user.username}!`, "success");
  };
```

In `logout`, add the org cleanup beside the existing removals:

```javascript
    setCurrentOrg(null);
    localStorage.removeItem("fundvault_org");
```

In the restore-session effect, restore the org too:

```javascript
    const savedOrg = localStorage.getItem("fundvault_org");
    if (savedOrg) setCurrentOrg(JSON.parse(savedOrg));
```

Replace the whole `if (!currentUser || !token)` block's `<AuthView .../>` element with:

```jsx
        <OrgGateway onAuthenticated={handleAuthenticated} onError={message => toast(message, "error")} />
```

Delete the now-unused `authTab`, `setAuthTab`, `loginForm`, `setLoginForm`, `signupForm`, `setSignupForm` state declarations and the `AuthView` import. Delete `frontend/src/components/auth/AuthView.jsx`.

- [ ] **Step 6: Style the gateway**

Append to `frontend/src/app/globals.css`:

```css
.auth-shell { max-width: 460px; margin: 8vh auto; padding: 0 20px; }
.auth-tabs { display: flex; gap: 4px; margin: 20px 0; }
.auth-tabs button {
  flex: 1; padding: 10px; border: 1px solid var(--border, #333);
  background: transparent; color: inherit; cursor: pointer; border-radius: 6px;
  font-size: 13px;
}
.auth-tabs button.active { background: var(--accent, #4f8cff); color: #fff; border-color: transparent; }
.org-form { display: flex; flex-direction: column; gap: 8px; }
.org-form label { font-size: 12px; opacity: 0.75; margin-top: 8px; }
.org-form input {
  padding: 10px; border-radius: 6px; border: 1px solid var(--border, #333);
  background: var(--input-bg, transparent); color: inherit; font-size: 14px;
}
.preset-row { display: flex; gap: 6px; flex-wrap: wrap; }
.preset {
  padding: 6px 12px; border-radius: 999px; font-size: 12px; cursor: pointer;
  border: 1px solid var(--border, #333); background: transparent; color: inherit;
}
.preset.active { background: var(--accent, #4f8cff); color: #fff; border-color: transparent; }
.hint { font-size: 12px; opacity: 0.7; line-height: 1.5; margin: 4px 0; }
.check-ok { font-size: 13px; color: #34c759; }
.check-bad { font-size: 13px; color: #ff6b6b; }
.org-choice {
  text-align: left; padding: 12px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--border, #333); background: transparent; color: inherit;
  font-size: 14px;
}
.org-choice:hover { border-color: var(--accent, #4f8cff); }
.linkish {
  background: none; border: none; color: var(--accent, #4f8cff);
  cursor: pointer; font-size: 12px; padding: 0; text-decoration: underline;
}
```

- [ ] **Step 7: Verify by hand**

Run the stack: `npm run dev`

Then, in the browser at `http://localhost:3001`:

1. **Create an organisation** — use `postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev`, press "Test connection", and confirm it reports success. Complete the form; you should land in the app as Owner.
2. **Check the network tab** — confirm no response body contains `postgres://` or `devpassword`.
3. **Sign out, then sign in** — enter your email, confirm the org appears by name, pick it, and sign in.
4. **Wrong org** — confirm an email with no orgs shows the "No organisations found" message rather than an error toast.

- [ ] **Step 8: Commit**

```bash
git add frontend/src backend/tests
git commit -m "Add the organisation gateway to the frontend

Create an org with a connection string, join one with a code, or sign in by
email and pick from the orgs that email belongs to. The join screen shows only
the org's name — a member never sees or types database credentials.

Replaces AuthView, which assumed one implicit database.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Phase 3 is complete.** Anyone can create an organisation, invite people to it, and sign in to it.

---

## Phase 4 — Roles and the approval rule

### Task 16: The permission module

**Files:**
- Create: `backend/apps/accounts/permissions.py`
- Create: `backend/tests/test_permissions.py`

**Interfaces:**
- Consumes: `User.Role` (Task 4).
- Produces:
  - `apps.accounts.permissions.Action` — string constants `VIEW`, `CREATE_TXN`, `APPROVE`, `MODIFY_TXN`, `MANAGE_FUNDS`, `MANAGE_MEMBERS`, `MINT_ADMIN_CODE`, `CHANGE_ROLE`, `MANAGE_ORG_CONFIG`, `TRANSFER_OWNERSHIP`
  - `apps.accounts.permissions.can(user, action)` → `bool`
  - `apps.accounts.permissions.require(user, action)` → `None` or `JsonResponse` 403
  - `apps.accounts.permissions.needs_approval(user, amount, threshold)` → `bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_permissions.py`:

```python
from django.test import SimpleTestCase

from apps.accounts.models import User
from apps.accounts.permissions import Action, can, needs_approval

OWNER, ADMIN, MEMBER, VIEWER = "owner", "admin", "member", "viewer"


def _user(role):
    return User(id="u", username="u", email="u@example.com", password_hash="x", role=role)


class CapabilityTests(SimpleTestCase):
    def test_everyone_can_view(self):
        for role in (OWNER, ADMIN, MEMBER, VIEWER):
            self.assertTrue(can(_user(role), Action.VIEW), role)

    def test_viewer_cannot_create_transactions(self):
        self.assertFalse(can(_user(VIEWER), Action.CREATE_TXN))

    def test_members_and_above_can_create_transactions(self):
        for role in (OWNER, ADMIN, MEMBER):
            self.assertTrue(can(_user(role), Action.CREATE_TXN), role)

    def test_only_admin_and_owner_approve(self):
        self.assertTrue(can(_user(OWNER), Action.APPROVE))
        self.assertTrue(can(_user(ADMIN), Action.APPROVE))
        self.assertFalse(can(_user(MEMBER), Action.APPROVE))
        self.assertFalse(can(_user(VIEWER), Action.APPROVE))

    def test_only_admin_and_owner_modify_or_void(self):
        for action in (Action.MODIFY_TXN, Action.MANAGE_FUNDS, Action.MANAGE_MEMBERS):
            self.assertTrue(can(_user(ADMIN), action), action)
            self.assertFalse(can(_user(MEMBER), action), action)

    def test_owner_only_capabilities(self):
        for action in (
            Action.MINT_ADMIN_CODE,
            Action.CHANGE_ROLE,
            Action.MANAGE_ORG_CONFIG,
            Action.TRANSFER_OWNERSHIP,
        ):
            self.assertTrue(can(_user(OWNER), action), action)
            self.assertFalse(can(_user(ADMIN), action), action)

    def test_unknown_role_can_do_nothing(self):
        self.assertFalse(can(_user("wizard"), Action.VIEW))

    def test_inactive_user_can_do_nothing(self):
        user = _user(OWNER)
        user.is_active = False
        self.assertFalse(can(user, Action.VIEW))


class ApprovalRuleTests(SimpleTestCase):
    def test_member_over_threshold_needs_approval(self):
        self.assertTrue(needs_approval(_user(MEMBER), 5000, 1000))

    def test_member_under_threshold_does_not(self):
        self.assertFalse(needs_approval(_user(MEMBER), 500, 1000))

    def test_member_exactly_at_threshold_needs_approval(self):
        self.assertTrue(needs_approval(_user(MEMBER), 1000, 1000))

    def test_admin_never_needs_approval(self):
        self.assertFalse(needs_approval(_user(ADMIN), 999999, 1000))

    def test_owner_never_needs_approval(self):
        self.assertFalse(needs_approval(_user(OWNER), 999999, 1000))

    def test_zero_threshold_disables_approval_entirely(self):
        self.assertFalse(needs_approval(_user(MEMBER), 999999, 0))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_permissions --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `No module named 'apps.accounts.permissions'`.

- [ ] **Step 3: Write the module**

Create `backend/apps/accounts/permissions.py`:

```python
"""Who may do what, in one place.

Every capability check in the application resolves through this table, so the
spec's roles table has exactly one implementation rather than a scatter of
inline role comparisons.
"""

from apps.accounts.models import User
from apps.common.utils import json_error


class Action:
    VIEW = "view"
    CREATE_TXN = "create_txn"
    APPROVE = "approve"
    MODIFY_TXN = "modify_txn"
    MANAGE_FUNDS = "manage_funds"
    MANAGE_MEMBERS = "manage_members"
    MINT_ADMIN_CODE = "mint_admin_code"
    CHANGE_ROLE = "change_role"
    MANAGE_ORG_CONFIG = "manage_org_config"
    TRANSFER_OWNERSHIP = "transfer_ownership"


_VIEWER = {Action.VIEW}
_MEMBER = _VIEWER | {Action.CREATE_TXN}
_ADMIN = _MEMBER | {
    Action.APPROVE,
    Action.MODIFY_TXN,
    Action.MANAGE_FUNDS,
    Action.MANAGE_MEMBERS,
}
_OWNER = _ADMIN | {
    Action.MINT_ADMIN_CODE,
    Action.CHANGE_ROLE,
    Action.MANAGE_ORG_CONFIG,
    Action.TRANSFER_OWNERSHIP,
}

CAPABILITIES = {
    User.Role.OWNER: _OWNER,
    User.Role.ADMIN: _ADMIN,
    User.Role.MEMBER: _MEMBER,
    User.Role.VIEWER: _VIEWER,
}


def can(user, action):
    if user is None or not getattr(user, "is_active", False):
        return False
    return action in CAPABILITIES.get(user.role, frozenset())


def require(user, action):
    """Return a 403 JsonResponse if the user may not perform action, else None."""
    if can(user, action):
        return None
    return json_error("You do not have permission to do that", 403)


def needs_approval(user, amount, threshold):
    """Approval gates Member-created transactions only, per the spec."""
    if not threshold or threshold <= 0:
        return False
    if user.role != User.Role.MEMBER:
        return False
    return amount >= threshold
```

- [ ] **Step 4: Run the tests**

Run: `python backend/manage.py test tests.test_permissions --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/accounts/permissions.py backend/tests/test_permissions.py
git commit -m "Add the permission table

One implementation of the spec's roles table, so capability checks cannot drift
apart across views. Approval gates Member-created transactions only; Admins and
Owners post directly and may approve their own.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 17: Enforce permissions in the ledger

**Files:**
- Modify: `backend/apps/ledger/views.py` (every mutating view)
- Create: `backend/tests/test_ledger_permissions.py`

**Interfaces:**
- Consumes: `can`, `require`, `needs_approval`, `Action` (Task 16).
- Produces: HTTP 403 on every mutating ledger endpoint for roles that lack the capability.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_permissions.py`:

```python
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class LedgerPermissionTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.tokens = {
            role: self._user(f"u_{role}", role) for role in ("owner", "admin", "member", "viewer")
        }
        with org_context("tenant_dev"):
            DatabaseFund.objects.create(
                id="f1", name="Fund", balance=1000.0, approval_threshold=500.0,
                created_by_id="u_owner",
            )

    def _user(self, user_id, role):
        token = create_session_token(user_id, "o1")
        with org_context("tenant_dev"):
            User.objects.create(
                id=user_id, username=user_id, email=f"{user_id}@example.com",
                password_hash="x", role=role, is_active=True,
            )
            Session.objects.create(
                id=f"s_{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def _auth(self, role):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tokens[role]}"}

    def _post_txn(self, role, amount=100.0):
        return self.client.post(
            "/api/databases/f1/transactions",
            data=json.dumps({
                "type": "credit", "amount": amount,
                "date": timezone.now().isoformat(), "mode": "cash",
                "sender": "x", "receiver": "y", "modeData": {},
            }),
            content_type="application/json",
            **self._auth(role),
        )

    def test_viewer_can_read_funds(self):
        self.assertEqual(self.client.get("/api/databases", **self._auth("viewer")).status_code, 200)

    def test_viewer_cannot_create_a_transaction(self):
        self.assertEqual(self._post_txn("viewer").status_code, 403)

    def test_member_can_create_a_transaction(self):
        self.assertEqual(self._post_txn("member").status_code, 200)

    def test_viewer_cannot_create_a_fund(self):
        response = self.client.post(
            "/api/databases",
            data=json.dumps({"name": "New"}),
            content_type="application/json",
            **self._auth("viewer"),
        )
        self.assertEqual(response.status_code, 403)

    def test_member_cannot_create_a_fund(self):
        response = self.client.post(
            "/api/databases",
            data=json.dumps({"name": "New"}),
            content_type="application/json",
            **self._auth("member"),
        )
        self.assertEqual(response.status_code, 403)

    def test_member_cannot_void(self):
        with org_context("tenant_dev"):
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
        response = self.client.post(
            "/api/transactions/t1/void",
            data=json.dumps({"reason": "mistake"}),
            content_type="application/json",
            **self._auth("member"),
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_void(self):
        with org_context("tenant_dev"):
            TransactionFund.objects.create(
                id="t2", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0,
            )
        response = self.client.post(
            "/api/transactions/t2/void",
            data=json.dumps({"reason": "mistake"}),
            content_type="application/json",
            **self._auth("admin"),
        )
        self.assertEqual(response.status_code, 200)


class ApprovalRuleIntegrationTests(LedgerPermissionTests):
    def test_member_transaction_over_threshold_awaits_approval(self):
        response = self._post_txn("member", amount=600.0)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["requiresApproval"])

    def test_admin_transaction_over_threshold_posts_directly(self):
        response = self._post_txn("admin", amount=600.0)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.content)["requiresApproval"])

    def test_admin_can_approve_a_members_transaction(self):
        created = json.loads(self._post_txn("member", amount=600.0).content)
        txn_id = created["transaction"]["id"]
        response = self.client.post(f"/api/transactions/{txn_id}/approve", **self._auth("admin"))
        self.assertEqual(response.status_code, 200)

    def test_member_cannot_approve(self):
        created = json.loads(self._post_txn("member", amount=600.0).content)
        txn_id = created["transaction"]["id"]
        response = self.client.post(f"/api/transactions/{txn_id}/approve", **self._auth("member"))
        self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_ledger_permissions --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — viewers and members are currently allowed to do everything.

- [ ] **Step 3: Add the guards**

In `backend/apps/ledger/views.py`, add to the imports:

```python
from apps.accounts.permissions import Action, needs_approval, require
```

Insert a guard as the first statement after each method check, matching this table:

| View | Guard |
|---|---|
| `databases_list_create` (POST branch) | `Action.MANAGE_FUNDS` |
| `databases_merge` | `Action.MANAGE_FUNDS` |
| `database_detail` (PUT and DELETE branches) | `Action.MANAGE_FUNDS` |
| `database_archive` | `Action.MANAGE_FUNDS` |
| `database_transactions` (POST branch) | `Action.CREATE_TXN` |
| `transaction_void` | `Action.MODIFY_TXN` |
| `transaction_delete_voided` | `Action.MODIFY_TXN` |
| `transaction_update` | `Action.MODIFY_TXN` |
| `transaction_approve` | `Action.APPROVE` |
| `recurring_list_create` (POST branch) | `Action.MANAGE_FUNDS` |
| `recurring_delete` | `Action.MANAGE_FUNDS` |
| `trash_restore`, `trash_delete`, `trash_list` (DELETE branch) | `Action.MANAGE_FUNDS` |

Each guard is two lines, for example in `transaction_void`:

```python
    denied = require(request.fv_user, Action.MODIFY_TXN)
    if denied:
        return denied
```

GET branches need no guard: every role has `Action.VIEW`, and the org boundary is the connection.

- [ ] **Step 4: Switch the approval rule to the role-aware version**

In `database_transactions`, inside the atomic block, replace:

```python
        requires_approval = locked.approval_threshold > 0 and amount >= locked.approval_threshold
```

with:

```python
        requires_approval = needs_approval(request.fv_user, amount, locked.approval_threshold)
```

- [ ] **Step 5: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 6: Verify no unguarded mutation remains**

Run: `grep -n "def \|require(request.fv_user" backend/apps/ledger/views.py`

Read the output top to bottom: every view that writes must have a `require(...)` line before its first write. A mutating view with no guard is a permission hole.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ledger/views.py backend/tests/test_ledger_permissions.py
git commit -m "Enforce roles across the ledger

Viewers read only, Members post transactions, Admins approve and void, Owners
manage the org. Approval now gates Member-created transactions rather than all
transactions over the threshold.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 18: Member management

**Files:**
- Modify: `backend/apps/accounts/views.py` (`admin_users`, `admin_user_detail`, `admin_reset_password`)
- Create: `backend/tests/test_member_management.py`

**Interfaces:**
- Consumes: `permissions` (Task 16).
- Produces:
  - `GET /api/admin/users` — org members; requires `MANAGE_MEMBERS`
  - `PUT /api/admin/users/<id>` — role change requires `CHANGE_ROLE`; activation requires `MANAGE_MEMBERS`
  - `DELETE /api/admin/users/<id>` — requires `MANAGE_MEMBERS`
  - `POST /api/admin/users/<id>/transfer-ownership` — requires `TRANSFER_OWNERSHIP`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_member_management.py`:

```python
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class MemberManagementTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner = self._user("u_owner", "owner")
        self.admin = self._user("u_admin", "admin")
        self._user("u_member", "member")

    def _user(self, user_id, role):
        token = create_session_token(user_id, "o1")
        with org_context("tenant_dev"):
            User.objects.create(
                id=user_id, username=user_id, email=f"{user_id}@example.com",
                password_hash="x", role=role, is_active=True,
            )
            Session.objects.create(
                id=f"s_{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def _put(self, token, user_id, payload):
        return self.client.put(
            f"/api/admin/users/{user_id}",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_owner_can_change_a_role(self):
        self.assertEqual(self._put(self.owner, "u_member", {"role": "admin"}).status_code, 200)

    def test_admin_cannot_change_a_role(self):
        self.assertEqual(self._put(self.admin, "u_member", {"role": "admin"}).status_code, 403)

    def test_admin_can_deactivate_a_member(self):
        response = self._put(self.admin, "u_member", {"is_active": False})
        self.assertEqual(response.status_code, 200)

    def test_nobody_can_demote_the_only_owner(self):
        response = self._put(self.owner, "u_owner", {"role": "member"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("owner", json.loads(response.content)["error"].lower())

    def test_owner_cannot_deactivate_themselves(self):
        self.assertEqual(self._put(self.owner, "u_owner", {"is_active": False}).status_code, 400)

    def test_a_role_cannot_be_set_to_owner_directly(self):
        response = self._put(self.owner, "u_member", {"role": "owner"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("transfer", json.loads(response.content)["error"].lower())

    def test_ownership_transfer_swaps_both_roles(self):
        response = self.client.post(
            "/api/admin/users/u_admin/transfer-ownership",
            HTTP_AUTHORIZATION=f"Bearer {self.owner}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        with org_context("tenant_dev"):
            self.assertEqual(User.objects.get(id="u_admin").role, "owner")
            self.assertEqual(User.objects.get(id="u_owner").role, "admin")

    def test_admin_cannot_transfer_ownership(self):
        response = self.client.post(
            "/api/admin/users/u_admin/transfer-ownership",
            HTTP_AUTHORIZATION=f"Bearer {self.admin}",
        )
        self.assertEqual(response.status_code, 403)

    def test_member_list_requires_manage_members(self):
        member_token = create_session_token("u_member", "o1")
        with org_context("tenant_dev"):
            Session.objects.create(
                id="s_extra", user_id="u_member", token=member_token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        response = self.client.get(
            "/api/admin/users", HTTP_AUTHORIZATION=f"Bearer {member_token}"
        )
        self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_member_management --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `admin_required` still allows any admin to change roles, and there is no transfer endpoint.

- [ ] **Step 3: Replace admin_required with capability checks**

In `backend/apps/accounts/views.py`:

Change the import line from

```python
from apps.common.auth import admin_required, auth_required, create_session, create_session_token
```

to

```python
from apps.common.auth import auth_required, create_session, create_session_token
from apps.accounts.permissions import Action, require
```

Replace `@admin_required` with `@auth_required` on `admin_users`, `admin_user_detail`, and `admin_reset_password`, and add the appropriate guard as each function's first statement:

```python
    denied = require(request.fv_user, Action.MANAGE_MEMBERS)
    if denied:
        return denied
```

Replace `_other_active_admin_count` with an owner-aware version:

```python
def _other_active_owner_count(user_id):
    return (
        User.objects.filter(role=User.Role.OWNER, is_active=True).exclude(id=user_id).count()
    )
```

In `admin_user_detail`'s PUT branch, replace the role validation block with:

```python
        if next_role != target.role:
            denied = require(request.fv_user, Action.CHANGE_ROLE)
            if denied:
                return denied
        if next_role == User.Role.OWNER:
            return json_error(
                "Use transfer-ownership to make someone the Owner", 400
            )
        if next_role not in (User.Role.ADMIN, User.Role.MEMBER, User.Role.VIEWER):
            return json_error("Invalid role", 400)
        if request.fv_user.id == target.id and not next_is_active:
            return json_error("You cannot deactivate your own account", 400)

        losing_owner = target.role == User.Role.OWNER and (
            next_role != User.Role.OWNER or not next_is_active
        )
        if losing_owner and _other_active_owner_count(target.id) == 0:
            return json_error("An organisation must always have an active Owner", 400)
```

In the DELETE branch, replace the `_other_active_admin_count` guard with:

```python
        if target.role == User.Role.OWNER and _other_active_owner_count(target.id) == 0:
            return json_error("An organisation must always have an active Owner", 400)
```

- [ ] **Step 4: Add the ownership transfer view**

Append to `backend/apps/accounts/views.py`:

```python
@csrf_exempt
@auth_required
def transfer_ownership(request, user_id):
    """Hand Owner to another member. The previous Owner becomes an Admin."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.TRANSFER_OWNERSHIP)
    if denied:
        return denied

    target = User.objects.filter(id=user_id, is_active=True).first()
    if not target:
        return json_error("User not found", 404)
    if target.id == request.fv_user.id:
        return json_error("You are already the Owner", 400)

    with transaction.atomic():
        target.role = User.Role.OWNER
        target.updated_at = timezone.now()
        target.save(update_fields=["role", "updated_at"])

        previous = User.objects.filter(id=request.fv_user.id).first()
        previous.role = User.Role.ADMIN
        previous.updated_at = timezone.now()
        previous.save(update_fields=["role", "updated_at"])

    add_audit(
        request.fv_user.id,
        "update",
        "user",
        target.id,
        f'Ownership transferred to "{target.username}"',
    )
    return JsonResponse({"success": True, "owner": serialize_user(target)})
```

Both writes happen in one transaction so the org can never momentarily have two Owners or none.

- [ ] **Step 5: Add the route**

In `backend/apps/accounts/urls.py`, add:

```python
    path("admin/users/<str:user_id>/transfer-ownership", views.transfer_ownership),
```

- [ ] **Step 6: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/accounts backend/tests/test_member_management.py
git commit -m "Add org member management and ownership transfer

Role changes are Owner-only; activation and removal are Admin-and-above. Owner
cannot be assigned directly — it transfers, in one transaction, so the org
never has two Owners or none.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 19: Role-aware frontend

**Files:**
- Modify: `frontend/src/components/FundVaultApp.jsx`
- Modify: `frontend/src/components/views/DatabaseView.jsx`
- Modify: `frontend/src/components/views/HomeView.jsx`
- Modify: `frontend/src/components/layout/HeaderBar.jsx`
- Create: `frontend/src/lib/permissions.js`

**Interfaces:**
- Consumes: `user.role` from the login response.
- Produces: `frontend/src/lib/permissions.js` exporting `can(user, action)` and the `ACTIONS` constants, mirroring the backend table.

- [ ] **Step 1: Mirror the permission table**

Create `frontend/src/lib/permissions.js`:

```javascript
// Mirrors backend/apps/accounts/permissions.py. The backend is authoritative —
// this exists only so the UI does not offer buttons that would 403.
export const ACTIONS = {
  VIEW: "view",
  CREATE_TXN: "create_txn",
  APPROVE: "approve",
  MODIFY_TXN: "modify_txn",
  MANAGE_FUNDS: "manage_funds",
  MANAGE_MEMBERS: "manage_members",
  CHANGE_ROLE: "change_role",
  MANAGE_ORG_CONFIG: "manage_org_config",
  TRANSFER_OWNERSHIP: "transfer_ownership"
};

const VIEWER = [ACTIONS.VIEW];
const MEMBER = [...VIEWER, ACTIONS.CREATE_TXN];
const ADMIN = [...MEMBER, ACTIONS.APPROVE, ACTIONS.MODIFY_TXN, ACTIONS.MANAGE_FUNDS, ACTIONS.MANAGE_MEMBERS];
const OWNER = [...ADMIN, ACTIONS.CHANGE_ROLE, ACTIONS.MANAGE_ORG_CONFIG, ACTIONS.TRANSFER_OWNERSHIP];

const TABLE = { owner: OWNER, admin: ADMIN, member: MEMBER, viewer: VIEWER };

export const can = (user, action) =>
  Boolean(user && user.is_active !== false && (TABLE[user.role] || []).includes(action));
```

- [ ] **Step 2: Gate the fund actions**

In `frontend/src/components/FundVaultApp.jsx`, import the helper:

```javascript
import { ACTIONS, can } from "lib/permissions";
```

Pass a permissions object into the views. In the `DatabaseView` element, add:

```jsx
          permissions={{
            createTxn: can(currentUser, ACTIONS.CREATE_TXN),
            modifyTxn: can(currentUser, ACTIONS.MODIFY_TXN),
            approve: can(currentUser, ACTIONS.APPROVE),
            manageFunds: can(currentUser, ACTIONS.MANAGE_FUNDS)
          }}
```

In the `HomeView` element, add:

```jsx
          canManageFunds={can(currentUser, ACTIONS.MANAGE_FUNDS)}
```

- [ ] **Step 3: Use the flags in DatabaseView**

In `frontend/src/components/views/DatabaseView.jsx`, add `permissions = {}` to the destructured props, then wrap each control:

- The "New Transaction" button: `{permissions.createTxn && (...)}`
- "Edit" and "Void" buttons in each transaction row: `{permissions.modifyTxn && (...)}`
- The "Approve" button: `{permissions.approve && (...)}`
- "Edit database", "Archive", and the recurring button: `{permissions.manageFunds && (...)}`

Export and Print stay visible for every role — Viewers are explicitly allowed to export.

- [ ] **Step 4: Use the flag in HomeView**

In `frontend/src/components/views/HomeView.jsx`, add `canManageFunds = false` to the props, and wrap the "Create Database", "Merge", per-card "Edit" and "Delete" controls in `{canManageFunds && (...)}`.

- [ ] **Step 5: Show the role and org in the header**

In `frontend/src/components/layout/HeaderBar.jsx`, add `currentOrg` to the props and render it beside the username:

```jsx
      <div className="org-badge">
        <span className="org-name">{currentOrg?.name || "—"}</span>
        <span className="role-chip">{currentUser?.role}</span>
      </div>
```

Pass `currentOrg={currentOrg}` from `FundVaultApp.jsx`, and hide the "User Management" menu entry unless `can(currentUser, ACTIONS.MANAGE_MEMBERS)`.

Append to `frontend/src/app/globals.css`:

```css
.org-badge { display: flex; align-items: center; gap: 8px; }
.org-name { font-weight: 600; font-size: 13px; }
.role-chip {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
  padding: 2px 8px; border-radius: 999px;
  background: var(--chip-bg, rgba(127, 127, 127, 0.18));
}
```

- [ ] **Step 6: Verify by hand**

Run `npm run dev`. Create a Viewer join code as the Owner, redeem it in a private window, and confirm: funds and transactions are visible, Export and Print work, and no New Transaction, Edit, Void, Approve, or Create Database control is rendered.

Then confirm the backend is independently enforcing it — with the Viewer's token:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/api/databases \
  -H "Authorization: Bearer <viewer-token>" -H "Content-Type: application/json" \
  -d '{"name":"Should Fail"}'
```

Expected: `403`. The hidden button is a courtesy; the 403 is the actual control.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "Gate the UI by role

Mirrors the backend permission table so the interface does not offer controls
that would 403. The backend remains authoritative; this is presentation only.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Phase 4 is complete.**

---

## Phase 5 — Object storage and per-org AI

### Task 20: Storage backend

**Files:**
- Create: `backend/apps/ledger/storage.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/test_storage.py`

**Interfaces:**
- Consumes: `Org.storage_config` (Task 7).
- Produces:
  - `apps.ledger.storage.StorageConfig` — dataclass `endpoint_url`, `region`, `bucket`, `access_key`, `secret_key`
  - `apps.ledger.storage.parse_storage_config(raw_json)` → `StorageConfig | None`
  - `apps.ledger.storage.put_object(config, key, data, content_type)` → key
  - `apps.ledger.storage.signed_url(config, key, expires=3600)` → URL string
  - `apps.ledger.storage.check_storage(config)` → `(ok: bool, message: str)`
  - `apps.ledger.storage.StorageNotConfigured`

- [ ] **Step 1: Add boto3**

Append to `backend/requirements.txt`:

```text
boto3==1.35.76
```

Run: `pip install -r backend/requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_storage.py`:

```python
import json

from django.test import SimpleTestCase

from apps.ledger.storage import StorageNotConfigured, parse_storage_config, receipt_key_for

VALID = json.dumps({
    "endpoint_url": "https://abc.supabase.co/storage/v1/s3",
    "region": "us-east-1",
    "bucket": "receipts",
    "access_key": "key",
    "secret_key": "secret",
})


class ParseTests(SimpleTestCase):
    def test_parses_a_complete_config(self):
        config = parse_storage_config(VALID)
        self.assertEqual(config.bucket, "receipts")
        self.assertEqual(config.region, "us-east-1")

    def test_blank_config_is_none(self):
        self.assertIsNone(parse_storage_config(""))
        self.assertIsNone(parse_storage_config(None))

    def test_malformed_json_is_none(self):
        self.assertIsNone(parse_storage_config("{not json"))

    def test_missing_bucket_raises(self):
        incomplete = json.dumps({"endpoint_url": "https://x", "access_key": "k", "secret_key": "s"})
        with self.assertRaises(StorageNotConfigured):
            parse_storage_config(incomplete)


class KeyTests(SimpleTestCase):
    def test_key_is_namespaced_by_fund_and_transaction(self):
        self.assertEqual(receipt_key_for("f1", "t1"), "receipts/f1/t1.jpg")

    def test_key_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            receipt_key_for("../../etc", "t1")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_storage --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `No module named 'apps.ledger.storage'`.

- [ ] **Step 4: Write the storage module**

Create `backend/apps/ledger/storage.py`:

```python
"""S3-compatible object storage for receipt images.

Buckets are private. Reads go through a short-lived signed URL generated at
serialisation time, so a receipt is never fetchable by anyone who guesses a
path, and a URL that leaks stops working within the hour.
"""

import json
import re
from dataclasses import dataclass

REQUIRED = ("endpoint_url", "bucket", "access_key", "secret_key")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


class StorageNotConfigured(Exception):
    """This organisation has no usable storage configuration."""


@dataclass
class StorageConfig:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "auto"


def parse_storage_config(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    missing = [key for key in REQUIRED if not data.get(key)]
    if missing:
        raise StorageNotConfigured(f"Storage config is missing: {', '.join(missing)}")
    return StorageConfig(
        endpoint_url=data["endpoint_url"],
        bucket=data["bucket"],
        access_key=data["access_key"],
        secret_key=data["secret_key"],
        region=data.get("region") or "auto",
    )


def receipt_key_for(fund_id, transaction_id):
    for segment in (fund_id, transaction_id):
        if not _SAFE_SEGMENT.match(str(segment)):
            raise ValueError(f"Unsafe object key segment: {segment!r}")
    return f"receipts/{fund_id}/{transaction_id}.jpg"


def _client(config):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
    )


def put_object(config, key, data, content_type="image/jpeg"):
    _client(config).put_object(
        Bucket=config.bucket, Key=key, Body=data, ContentType=content_type
    )
    return key


def signed_url(config, key, expires=3600):
    return _client(config).generate_presigned_url(
        "get_object",
        Params={"Bucket": config.bucket, "Key": key},
        ExpiresIn=expires,
    )


def delete_object(config, key):
    _client(config).delete_object(Bucket=config.bucket, Key=key)


def check_storage(config):
    """Round-trip a tiny object to prove the credentials work."""
    probe = "receipts/_fundvault_probe"
    try:
        client = _client(config)
        client.put_object(Bucket=config.bucket, Key=probe, Body=b"ok", ContentType="text/plain")
        client.get_object(Bucket=config.bucket, Key=probe)
        client.delete_object(Bucket=config.bucket, Key=probe)
        return True, "Storage is reachable and writable."
    except Exception as exc:
        return False, str(exc)
```

- [ ] **Step 5: Run the tests**

Run: `python backend/manage.py test tests.test_storage --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/ledger/storage.py backend/requirements.txt backend/tests/test_storage.py
git commit -m "Add S3-compatible receipt storage

Buckets stay private; reads use a one-hour signed URL generated at read time,
so the column holds an object key rather than a permanent URL. Key segments are
validated to prevent path traversal into another fund's namespace.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 21: Receipt upload and signed reads

**Files:**
- Modify: `backend/apps/ledger/views.py` (add `transaction_receipt`)
- Modify: `backend/apps/ledger/serializers.py` (`serialize_transaction` gains `receipt_url`)
- Modify: `backend/apps/ledger/urls.py`
- Create: `backend/tests/test_receipt_upload.py`

**Interfaces:**
- Consumes: `storage` (Task 20); `Org.storage_config`.
- Produces:
  - `POST /api/transactions/<id>/receipt` — multipart `image`; returns `{"receipt_url": "..."}`
  - `serialize_transaction(txn, storage_config=None)` — **signature change**; emits `receipt_url` when a config is supplied, otherwise `None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_receipt_upload.py`:

```python
import io
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


def _png(size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    buf.seek(0)
    buf.name = "receipt.png"
    return buf


class ReceiptUploadTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )  # storage_config deliberately blank
        self.token = create_session_token("u1", "o1")
        with org_context("tenant_dev"):
            User.objects.create(
                id="u1", username="alice", email="a@example.com",
                password_hash="x", role=User.Role.ADMIN, is_active=True,
            )
            Session.objects.create(
                id="s1", user_id="u1", token=self.token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
            DatabaseFund.objects.create(id="f1", name="Fund", balance=100.0, created_by_id="u1")
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0, created_by_id="u1",
            )

    def _upload(self, payload):
        return self.client.post(
            "/api/transactions/t1/receipt",
            data={"image": payload},
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_upload_without_storage_configured_explains_itself(self):
        response = self._upload(_png())
        self.assertEqual(response.status_code, 503)
        self.assertIn("storage", response.json()["error"].lower())

    def test_non_image_is_refused(self):
        text = io.BytesIO(b"this is not an image")
        text.name = "notes.txt"
        response = self._upload(text)
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_is_refused(self):
        big = io.BytesIO(b"\0" * (5 * 1024 * 1024 + 1))
        big.name = "big.png"
        response = self._upload(big)
        self.assertEqual(response.status_code, 400)
        self.assertIn("5", response.json()["error"])

    def test_missing_file_is_refused(self):
        response = self.client.post(
            "/api/transactions/t1/receipt", HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )
        self.assertEqual(response.status_code, 400)

    def test_serializer_returns_null_url_when_no_storage(self):
        from apps.ledger.serializers import serialize_transaction

        with org_context("tenant_dev"):
            txn = TransactionFund.objects.get(id="t1")
        self.assertIsNone(serialize_transaction(txn)["receipt_url"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_receipt_upload --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — the receipt route returns 404.

- [ ] **Step 3: Add the upload view**

Append to `backend/apps/ledger/views.py`:

```python
@csrf_exempt
@auth_required
def transaction_receipt(request, transaction_id):
    """Attach a receipt image to a transaction."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.CREATE_TXN)
    if denied:
        return denied

    from apps.ledger.receipt_extractor import _compress_image
    from apps.ledger.storage import (
        StorageNotConfigured,
        parse_storage_config,
        put_object,
        receipt_key_for,
    )

    try:
        storage = parse_storage_config(request.fv_org.storage_config)
    except StorageNotConfigured as exc:
        return json_error(str(exc), 503)
    if storage is None:
        return json_error(
            "Receipt storage is not configured for this organisation. "
            "An Owner can add it in organisation settings.",
            503,
        )

    upload = request.FILES.get("image")
    if not upload:
        return json_error("No image file provided", 400)
    if upload.size > 5 * 1024 * 1024:
        return json_error("Image must be less than 5 MB", 400)

    txn = TransactionFund.objects.filter(id=transaction_id).first()
    if not txn:
        return json_error("Transaction not found", 404)

    raw = upload.read()
    try:
        compressed = _compress_image(raw)
    except Exception:
        return json_error("That file is not a readable image", 400)

    key = receipt_key_for(txn.database_id, txn.id)
    try:
        put_object(storage, key, compressed, "image/jpeg")
    except Exception as exc:
        return json_error(f"Could not upload the receipt: {exc}", 502)

    txn.receipt_key = key
    txn.save(update_fields=["receipt_key"])
    add_audit(request.fv_user.id, "update", "transaction", txn.id, "Receipt attached")

    from apps.ledger.storage import signed_url

    return JsonResponse({"receipt_url": signed_url(storage, key)})
```

The image is decoded by Pillow before it is stored, so a file that merely claims an image content type is rejected.

- [ ] **Step 4: Emit signed URLs from the serializer**

In `backend/apps/ledger/serializers.py`, replace `serialize_transaction` entirely and add the helper above it:

```python
def _receipt_url(txn, storage_config):
    """A one-hour signed URL, or None when storage is unconfigured."""
    if not txn.receipt_key or storage_config is None:
        return None
    from apps.ledger.storage import signed_url

    try:
        return signed_url(storage_config, txn.receipt_key)
    except Exception:
        # A storage outage degrades to a missing image rather than a failed
        # ledger request. The ledger is readable without its receipts.
        return None


def serialize_transaction(txn, storage_config=None):
    base = {
        "id": txn.id,
        "database_id": txn.database_id,
        "type": txn.type,
        "amount": float(txn.amount),
        "date": txn.date.isoformat() if txn.date else None,
        "sender": txn.sender or "",
        "receiver": txn.receiver or "",
        "mode": txn.mode,
        "mode_data": _parse_mode_data(txn.mode_data),
        "location": txn.location or "",
        "notes": txn.notes or "",
        "running_balance": float(txn.running_balance),
        "receipt_key": txn.receipt_key,
        "receipt_url": _receipt_url(txn, storage_config),
        "requires_approval": bool(txn.requires_approval),
        "approved": bool(txn.approved),
        "approved_by": txn.approved_by,
        "approved_at": txn.approved_at.isoformat() if txn.approved_at else None,
        "is_voided": bool(txn.is_voided),
        "void_reason": txn.void_reason,
        "voided_by": txn.voided_by,
        "voided_at": txn.voided_at.isoformat() if txn.voided_at else None,
        "created_by": txn.created_by_id,
        "created_at": txn.created_at.isoformat() if txn.created_at else None,
    }
    mode_data = base["mode_data"]
    if txn.mode == "electronic" and mode_data.get("elecId"):
        base["elecId"] = mode_data["elecId"]
    if txn.mode == "cheque":
        for key in ("chequeNo", "chequeDate", "chequeBank"):
            if mode_data.get(key):
                base[key] = mode_data[key]
    return base
```

In `backend/apps/ledger/views.py`, add a helper near `_get_user_database`:

```python
def _storage_for(request):
    from apps.ledger.storage import StorageNotConfigured, parse_storage_config

    try:
        return parse_storage_config(request.fv_org.storage_config)
    except StorageNotConfigured:
        return None
```

Then pass it at all four serialisation sites. In `database_detail`'s GET branch:

```python
    if request.method == "GET":
        txns = TransactionFund.objects.filter(database_id=db.id).order_by("-date")
        storage = _storage_for(request)
        payload = serialize_database(db)
        payload["transactions"] = [serialize_transaction(txn, storage) for txn in txns]
        return JsonResponse(payload)
```

In `database_transactions`'s GET branch:

```python
    if request.method == "GET":
        rows = TransactionFund.objects.filter(database_id=database_id).order_by("-date", "-created_at")
        storage = _storage_for(request)
        return JsonResponse([serialize_transaction(row, storage) for row in rows], safe=False)
```

In `database_transactions`'s POST branch, the final return:

```python
    return JsonResponse(
        {
            "transaction": serialize_transaction(txn, _storage_for(request)),
            "requiresApproval": requires_approval,
            "newBalance": new_balance,
        }
    )
```

And in `transaction_update`'s final return:

```python
    return JsonResponse(serialize_transaction(txn, _storage_for(request)))
```

- [ ] **Step 5: Add the route**

In `backend/apps/ledger/urls.py`, add:

```python
    path("transactions/<str:transaction_id>/receipt", views.transaction_receipt),
```

- [ ] **Step 6: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/ledger backend/tests/test_receipt_upload.py
git commit -m "Store receipts as objects and serve them by signed URL

Transactions carry a key; the API returns a one-hour signed URL generated at
read time. Uploads are decoded by Pillow before storage, so a file that merely
claims an image content type is refused.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 22: Per-org AI configuration

**Files:**
- Modify: `backend/apps/ledger/receipt_extractor.py`
- Modify: `backend/apps/ledger/views.py` (`extract_receipt`)
- Create: `backend/tests/test_ai_config.py`

**Interfaces:**
- Consumes: `Org.ai_config`.
- Produces:
  - `apps.ledger.receipt_extractor.AIConfig` — dataclass `provider` (`"openai_compatible"` or `"gemini"`), `base_url`, `model`, `api_key`
  - `parse_ai_config(raw_json)` → `{"primary": AIConfig|None, "fallback": AIConfig|None}`
  - `extract_from_receipt_image(image_bytes, mime_type, config)` — **signature change**, `config` required
  - `check_ai_config(config)` → `(ok, message)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ai_config.py`:

```python
import json

from django.test import SimpleTestCase

from apps.ledger.receipt_extractor import AIConfig, parse_ai_config

CONFIG = json.dumps({
    "primary": {
        "provider": "openai_compatible",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/nemotron-nano-12b-v2-vl",
        "api_key": "nvapi-secret",
    },
    "fallback": {"provider": "gemini", "model": "gemini-3.6-flash", "api_key": "gem-secret"},
})


class ParseTests(SimpleTestCase):
    def test_parses_both_providers(self):
        parsed = parse_ai_config(CONFIG)
        self.assertIsInstance(parsed["primary"], AIConfig)
        self.assertEqual(parsed["primary"].model, "nvidia/nemotron-nano-12b-v2-vl")
        self.assertEqual(parsed["fallback"].provider, "gemini")

    def test_blank_config_yields_no_providers(self):
        parsed = parse_ai_config("")
        self.assertIsNone(parsed["primary"])
        self.assertIsNone(parsed["fallback"])

    def test_fallback_is_optional(self):
        only_primary = json.dumps({
            "primary": {
                "provider": "openai_compatible",
                "base_url": "https://x/v1",
                "model": "m",
                "api_key": "k",
            }
        })
        self.assertIsNone(parse_ai_config(only_primary)["fallback"])

    def test_unknown_provider_is_dropped(self):
        bogus = json.dumps({"primary": {"provider": "mystery", "model": "m", "api_key": "k"}})
        self.assertIsNone(parse_ai_config(bogus)["primary"])

    def test_openai_compatible_without_base_url_is_dropped(self):
        bad = json.dumps({
            "primary": {"provider": "openai_compatible", "model": "m", "api_key": "k"}
        })
        self.assertIsNone(parse_ai_config(bad)["primary"])


class ExtractionContractTests(SimpleTestCase):
    def test_no_configured_provider_returns_an_error_not_an_exception(self):
        from apps.ledger.receipt_extractor import extract_from_receipt_image

        result = extract_from_receipt_image(b"", "image/png", {"primary": None, "fallback": None})
        self.assertIn("error", result)
        self.assertIn("not configured", result["error"].lower())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_ai_config --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `cannot import name 'AIConfig'`.

- [ ] **Step 3: Rework the extractor**

In `backend/apps/ledger/receipt_extractor.py`, replace the `from django.conf import settings` import with:

```python
from dataclasses import dataclass
```

Add after the `_PROMPT` definition:

```python
@dataclass
class AIConfig:
    provider: str          # "openai_compatible" | "gemini"
    model: str
    api_key: str
    base_url: str = ""


def _one(entry):
    if not isinstance(entry, dict):
        return None
    provider = entry.get("provider")
    model = entry.get("model")
    api_key = entry.get("api_key")
    if not model or not api_key:
        return None
    if provider == "openai_compatible":
        if not entry.get("base_url"):
            return None
        return AIConfig("openai_compatible", model, api_key, entry["base_url"])
    if provider == "gemini":
        return AIConfig("gemini", model, api_key)
    return None


def parse_ai_config(raw):
    """{'primary': AIConfig|None, 'fallback': AIConfig|None} from stored JSON."""
    if not raw:
        return {"primary": None, "fallback": None}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"primary": None, "fallback": None}
    return {"primary": _one(data.get("primary")), "fallback": _one(data.get("fallback"))}
```

Change `_extract_nvidia` to take the config, and rename it:

```python
def _extract_openai_compatible(compressed: bytes, config: AIConfig) -> dict:
    from openai import OpenAI

    b64 = base64.b64encode(compressed).decode("utf-8")
    client = OpenAI(base_url=config.base_url, api_key=config.api_key)

    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        temperature=0.1,
        top_p=0.95,
        max_tokens=2048,
        stream=False,
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    if not raw:
        raw = (getattr(choice.message, "reasoning_content", None) or "").strip()
    if not raw:
        raise json.JSONDecodeError("Empty response from model", "", 0)

    result = _parse_json_from_text(raw)
    result["_provider"] = config.model
    return result
```

Change `_extract_gemini` similarly:

```python
def _extract_gemini(compressed: bytes, config: AIConfig) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.api_key)
    response = client.models.generate_content(
        model=config.model,
        contents=[
            types.Content(
                parts=[
                    types.Part.from_text(text=_PROMPT),
                    types.Part.from_bytes(data=compressed, mime_type="image/jpeg"),
                ]
            )
        ],
    )
    result = _parse_json_from_text(response.text.strip())
    result["_provider"] = config.model
    return result
```

Replace `extract_from_receipt_image` entirely:

```python
def _run(config, compressed):
    if config.provider == "gemini":
        return _extract_gemini(compressed, config)
    return _extract_openai_compatible(compressed, config)


def extract_from_receipt_image(image_bytes, mime_type, config):
    """Extract payment details using the org's own providers.

    `config` is the dict returned by parse_ai_config. The primary is tried
    first; any failure falls through to the fallback if one is configured.
    """
    primary = config.get("primary")
    fallback = config.get("fallback")
    if not primary and not fallback:
        return {
            "error": "Receipt extraction is not configured for this organisation. "
                     "An Owner can add an AI provider in organisation settings."
        }

    try:
        compressed = _compress_image(image_bytes)
    except Exception as exc:
        return {"error": f"Image processing failed: {exc}"}

    errors = []
    for label, candidate in (("primary", primary), ("fallback", fallback)):
        if not candidate:
            continue
        try:
            return _run(candidate, compressed)
        except json.JSONDecodeError:
            errors.append(f"{label} ({candidate.model}): could not parse the model response")
        except Exception as exc:
            errors.append(f"{label} ({candidate.model}): {exc}")

    return {"error": "Extraction failed. " + " | ".join(errors)}


def check_ai_config(config):
    """Probe a provider cheaply so a bad key surfaces in settings, not at first use."""
    target = config.get("primary") or config.get("fallback")
    if not target:
        return False, "No provider configured."
    try:
        if target.provider == "gemini":
            from google import genai

            genai.Client(api_key=target.api_key).models.list()
        else:
            from openai import OpenAI

            OpenAI(base_url=target.base_url, api_key=target.api_key).models.list()
        return True, f"{target.model} is reachable."
    except Exception as exc:
        return False, str(exc)
```

Delete `_MOCK_RESPONSE` and every reference to `NVIDIA_RECEIPT_MOCK` / `GEMINI_RECEIPT_MOCK`. Mock mode existed because keys lived in server config; each org now brings its own, and an unconfigured org gets a clear message instead.

- [ ] **Step 4: Update the view**

In `backend/apps/ledger/views.py`, replace the body of `extract_receipt` after the method check with:

```python
    denied = require(request.fv_user, Action.CREATE_TXN)
    if denied:
        return denied

    image_file = request.FILES.get("image")
    if not image_file:
        return json_error("No image file provided", 400)
    if image_file.size > 5 * 1024 * 1024:
        return json_error("Image must be less than 5 MB", 400)

    from apps.ledger.receipt_extractor import extract_from_receipt_image, parse_ai_config

    config = parse_ai_config(request.fv_org.ai_config)
    result = extract_from_receipt_image(image_file.read(), image_file.content_type or "", config)
    status = 503 if result.get("error", "").endswith("organisation settings.") else 200
    return JsonResponse(result, status=status)
```

Remove the settings-based configuration checks at the top of the old implementation.

- [ ] **Step 5: Clean the settings**

In `backend/fundvault_backend/settings.py`, delete the four AI lines (`GEMINI_API_KEY`, `GEMINI_RECEIPT_MOCK`, `NVIDIA_API_KEY`, `NVIDIA_RECEIPT_MOCK`). Delete the corresponding block from `backend/.env.example`. Keys are per-org now, and a server-wide key would silently subsidise every organisation.

- [ ] **Step 6: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add backend/apps backend/fundvault_backend backend/.env.example backend/tests/test_ai_config.py
git commit -m "Move AI credentials from server config to each organisation

Any OpenAI-compatible endpoint works via base URL, model, and key; Gemini keeps
its own path. Removes the server-wide keys and mock mode, which existed only
because credentials used to live in the environment.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 23: Organisation settings

**Files:**
- Modify: `backend/apps/orgs/views.py` (add `org_settings`)
- Modify: `backend/apps/orgs/urls.py`
- Create: `frontend/src/components/modals/OrgSettingsModal.jsx`
- Modify: `frontend/src/components/modals/AppModals.jsx`
- Create: `backend/tests/test_org_settings.py`

**Interfaces:**
- Consumes: `check_storage` (Task 20), `check_ai_config` (Task 22), `MANAGE_ORG_CONFIG` (Task 16).
- Produces:
  - `GET /api/orgs/settings` → masked view of what is configured
  - `PUT /api/orgs/settings` → accepts `storage` and/or `ai` objects, validates before saving

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_org_settings.py`:

```python
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class OrgSettingsTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme", owner_email="o@example.com",
            db_connection=TENANT_URL,
            ai_config=json.dumps({
                "primary": {
                    "provider": "openai_compatible",
                    "base_url": "https://x/v1",
                    "model": "m",
                    "api_key": "nvapi-abcdef123456",
                }
            }),
        )
        self.owner = self._user("u_owner", "owner")
        self.admin = self._user("u_admin", "admin")

    def _user(self, user_id, role):
        token = create_session_token(user_id, "o1")
        with org_context("tenant_dev"):
            User.objects.create(
                id=user_id, username=user_id, email=f"{user_id}@example.com",
                password_hash="x", role=role, is_active=True,
            )
            Session.objects.create(
                id=f"s_{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def test_owner_sees_masked_keys_only(self):
        response = self.client.get(
            "/api/orgs/settings", HTTP_AUTHORIZATION=f"Bearer {self.owner}"
        )
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        self.assertNotIn("nvapi-abcdef123456", text)
        self.assertIn("••••", text)

    def test_admin_cannot_read_settings(self):
        response = self.client.get(
            "/api/orgs/settings", HTTP_AUTHORIZATION=f"Bearer {self.admin}"
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_write_settings(self):
        response = self.client.put(
            "/api/orgs/settings",
            data=json.dumps({"ai": {"primary": {"provider": "gemini", "model": "g", "api_key": "k"}}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.admin}",
        )
        self.assertEqual(response.status_code, 403)

    def test_connection_string_is_never_returned(self):
        response = self.client.get(
            "/api/orgs/settings", HTTP_AUTHORIZATION=f"Bearer {self.owner}"
        )
        self.assertNotIn("postgres://", response.content.decode("utf-8"))
        self.assertNotIn("devpassword", response.content.decode("utf-8"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_org_settings --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — the settings route returns 404.

- [ ] **Step 3: Add the settings view**

Append to `backend/apps/orgs/views.py`:

```python
from apps.accounts.permissions import Action, require


def _mask(secret):
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) > 8 else ""
    return f"••••{tail}"


@csrf_exempt
@auth_required
def org_settings(request):
    denied = require(request.fv_user, Action.MANAGE_ORG_CONFIG)
    if denied:
        return denied

    org = request.fv_org

    if request.method == "GET":
        import json as _json

        from apps.ledger.receipt_extractor import parse_ai_config

        ai = parse_ai_config(org.ai_config)
        try:
            storage = _json.loads(org.storage_config) if org.storage_config else None
        except ValueError:
            storage = None

        return JsonResponse({
            "org": serialize_org(org),
            "storage": None if not storage else {
                "endpoint_url": storage.get("endpoint_url", ""),
                "bucket": storage.get("bucket", ""),
                "region": storage.get("region", ""),
                "access_key": _mask(storage.get("access_key", "")),
                "secret_key": _mask(storage.get("secret_key", "")),
            },
            "ai": {
                slot: None if not cfg else {
                    "provider": cfg.provider,
                    "base_url": cfg.base_url,
                    "model": cfg.model,
                    "api_key": _mask(cfg.api_key),
                }
                for slot, cfg in ai.items()
            },
        })

    if request.method != "PUT":
        return json_error("Method not allowed", 405)

    import json as _json

    body = parse_body(request)
    updates = []

    if "storage" in body:
        from apps.ledger.storage import (
            StorageNotConfigured,
            check_storage,
            parse_storage_config,
        )

        raw = _json.dumps(body["storage"])
        try:
            config = parse_storage_config(raw)
        except StorageNotConfigured as exc:
            return json_error(str(exc), 400)
        if config is not None:
            ok, message = check_storage(config)
            if not ok:
                return json_error(f"Storage check failed: {message}", 400)
        org.storage_config = raw
        updates.append("storage_config")

    if "ai" in body:
        from apps.ledger.receipt_extractor import check_ai_config, parse_ai_config

        raw = _json.dumps(body["ai"])
        parsed = parse_ai_config(raw)
        if not parsed["primary"] and not parsed["fallback"]:
            return json_error("No usable AI provider in that configuration", 400)
        ok, message = check_ai_config(parsed)
        if not ok:
            return json_error(f"AI provider check failed: {message}", 400)
        org.ai_config = raw
        updates.append("ai_config")

    if not updates:
        return json_error("Nothing to update", 400)

    org.save(update_fields=updates)
    add_audit(
        request.fv_user.id, "update", "org", org.id,
        f"Organisation settings updated: {', '.join(updates)}",
    )
    return JsonResponse({"success": True, "updated": updates})
```

Both credentials are probed before they are stored, so a typo is refused at the settings screen rather than surfacing later as a broken feature.

- [ ] **Step 4: Add the route**

In `backend/apps/orgs/urls.py`, add:

```python
    path("orgs/settings", views.org_settings),
```

- [ ] **Step 5: Build the settings modal**

Create `frontend/src/components/modals/OrgSettingsModal.jsx`:

```jsx
"use client";

import { useEffect, useState } from "react";

const BLANK_STORAGE = { endpoint_url: "", bucket: "", region: "auto", access_key: "", secret_key: "" };
const BLANK_AI = { provider: "openai_compatible", base_url: "", model: "", api_key: "" };

export default function OrgSettingsModal({ open, onClose, request, toast }) {
  const [current, setCurrent] = useState(null);
  const [storage, setStorage] = useState(BLANK_STORAGE);
  const [ai, setAi] = useState(BLANK_AI);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    request("/orgs/settings")
      .then(setCurrent)
      .catch(err => toast(err.message, "error"));
  }, [open]);

  if (!open) return null;

  const save = async (section, payload) => {
    setBusy(true);
    try {
      await request("/orgs/settings", { method: "PUT", body: JSON.stringify({ [section]: payload }) });
      toast(`${section === "ai" ? "AI provider" : "Storage"} saved and verified`, "success");
      setCurrent(await request("/orgs/settings"));
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal large" onClick={e => e.stopPropagation()}>
        <h2>Organisation settings</h2>

        <h3>Receipt storage</h3>
        {current?.storage ? (
          <p className="hint">
            Configured: {current.storage.bucket} at {current.storage.endpoint_url} (key{" "}
            {current.storage.access_key})
          </p>
        ) : (
          <p className="hint">Not configured — receipt upload is disabled.</p>
        )}
        <input placeholder="S3 endpoint URL" value={storage.endpoint_url}
          onChange={e => setStorage({ ...storage, endpoint_url: e.target.value })} />
        <input placeholder="Bucket" value={storage.bucket}
          onChange={e => setStorage({ ...storage, bucket: e.target.value })} />
        <input placeholder="Region (auto)" value={storage.region}
          onChange={e => setStorage({ ...storage, region: e.target.value })} />
        <input placeholder="Access key" value={storage.access_key} autoComplete="off"
          onChange={e => setStorage({ ...storage, access_key: e.target.value })} />
        <input placeholder="Secret key" type="password" value={storage.secret_key} autoComplete="off"
          onChange={e => setStorage({ ...storage, secret_key: e.target.value })} />
        <button className="btn-primary" disabled={busy} onClick={() => save("storage", storage)}>
          {busy ? "Verifying…" : "Save and verify storage"}
        </button>

        <h3>Receipt extraction</h3>
        {current?.ai?.primary ? (
          <p className="hint">
            Primary: {current.ai.primary.model} (key {current.ai.primary.api_key})
          </p>
        ) : (
          <p className="hint">Not configured — receipt extraction is disabled.</p>
        )}
        <select value={ai.provider} onChange={e => setAi({ ...ai, provider: e.target.value })}>
          <option value="openai_compatible">OpenAI-compatible (NVIDIA, OpenRouter, Groq, vLLM)</option>
          <option value="gemini">Google Gemini</option>
        </select>
        {ai.provider === "openai_compatible" && (
          <input placeholder="Base URL, e.g. https://integrate.api.nvidia.com/v1"
            value={ai.base_url} onChange={e => setAi({ ...ai, base_url: e.target.value })} />
        )}
        <input placeholder="Model name" value={ai.model}
          onChange={e => setAi({ ...ai, model: e.target.value })} />
        <input placeholder="API key" type="password" value={ai.api_key} autoComplete="off"
          onChange={e => setAi({ ...ai, api_key: e.target.value })} />
        <button className="btn-primary" disabled={busy}
          onClick={() => save("ai", { primary: ai })}>
          {busy ? "Verifying…" : "Save and verify provider"}
        </button>

        <button className="btn-secondary" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Mount the modal**

In `frontend/src/components/modals/AppModals.jsx`, import and render it, adding `orgSettings: false` to the `modals` object in `FundVaultApp.jsx` and an "Organisation settings" entry to the header menu shown only when `can(currentUser, ACTIONS.MANAGE_ORG_CONFIG)`.

- [ ] **Step 7: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/orgs frontend/src backend/tests/test_org_settings.py
git commit -m "Add organisation settings for storage and AI

Owner-only. Credentials are probed before they are stored, so a typo is caught
at the settings screen. Existing keys are shown masked and can be replaced but
never read back.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 24: Receipt UX — no auto-retry, no duplicate extraction

**Files:**
- Modify: `frontend/src/components/modals/AppModals.jsx:97-145` (receipt extraction block)
- Modify: `frontend/src/lib/api.js`

**Interfaces:**
- Consumes: `/api/extract-receipt`, `/api/transactions/<id>/receipt`.
- Produces: `uploadReceipt(transactionId, file, token)` in `lib/api.js`.

- [ ] **Step 1: Add the upload helper**

Append to `frontend/src/lib/api.js`:

```javascript
export async function uploadReceipt(transactionId, file, token) {
  const form = new FormData();
  form.append("image", file);
  const response = await fetch(`${API_BASE}/api/transactions/${transactionId}/receipt`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form
  });
  let payload = null;
  try { payload = await response.json(); } catch (_) { payload = null; }
  if (!response.ok) throw new Error(payload?.error || `Upload failed (${response.status})`);
  return payload;
}
```

- [ ] **Step 2: Add hashing and remove any retry**

In `frontend/src/components/modals/AppModals.jsx`, add above the component:

```javascript
// Extraction costs the organisation money, so an identical image submitted
// twice in one session reuses its result rather than paying again.
const extractionCache = new Map();

async function hashFile(file) {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, "0"))
    .join("");
}
```

Replace the body of the extraction handler with:

```javascript
    setExtracting(true);
    setExtractError("");
    try {
      const key = await hashFile(rawFile);
      let data = extractionCache.get(key);
      if (!data) {
        data = await extractReceipt(rawFile, state.currentUser.token);
        if (data.error) throw new Error(data.error);
        extractionCache.set(key, data);
      }
      const updates = {};
      if (data.amount) updates.amount = String(data.amount);
      if (data.date) updates.date = String(data.date).slice(0, 16);
      if (data.sender) updates.sender = data.sender;
      if (data.receiver) updates.receiver = data.receiver;
      if (data.mode) updates.mode = data.mode;
      if (data.reference_id) updates.modeData = { elecId: data.reference_id || "" };
      actions.setTxnForm(prev => ({ ...prev, ...updates }));
      actions.toast(`Extracted with ${data._provider || "your provider"}`, "success");
    } catch (err) {
      // Deliberately no retry: a failed extraction shows an error and a manual
      // button. Silent retries are what actually burn an org's API credits.
      setExtractError(err.message);
    } finally {
      setExtracting(false);
    }
```

Ensure the error state renders a visible message with a "Try again" button that calls the same handler, and that no `setTimeout`, loop, or `useEffect` re-invokes extraction automatically.

- [ ] **Step 3: Verify by hand**

Run `npm run dev`, open the transaction modal, and upload the same receipt twice. The second attempt should populate instantly with no network request in the browser's network tab. Then configure an invalid API key and confirm a failure shows one error with a manual retry button, and issues exactly one request.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "Cache identical extractions and never auto-retry

Extraction spends the organisation's own credits, so a repeated image reuses
its result and a failure waits for a person to press try again.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Phase 5 is complete.**

---

## Phase 6 — Deployment

### Task 25: Production settings

**Files:**
- Create: `backend/fundvault_backend/settings_production.py`
- Modify: `backend/requirements.txt`
- Create: `backend/tests/test_production_settings.py`

**Interfaces:**
- Produces: `DJANGO_SETTINGS_MODULE=fundvault_backend.settings_production` for deployed environments.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_production_settings.py`:

```python
import importlib
import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

REQUIRED = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "JWT_SECRET": "y" * 50,
    "FUNDVAULT_SECRET_KEY": "cP7mHqLxKcVfJhTgYnWzRbNdSaQeUiOpAsDfGhJkLmM=",
    "DATABASE_URL": "postgres://u:p@h:5432/d",
    "DJANGO_ALLOWED_HOSTS": "fundvault.example.com",
    "CORS_ALLOWED_ORIGINS": "https://fundvault.example.com",
}


def _load():
    module = importlib.import_module("fundvault_backend.settings_production")
    return importlib.reload(module)


class ProductionSettingsTests(SimpleTestCase):
    def test_debug_is_off(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=False):
            self.assertFalse(_load().DEBUG)

    def test_wildcard_hosts_are_refused(self):
        env = dict(REQUIRED, DJANGO_ALLOWED_HOSTS="*")
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_cors_is_not_open_to_everything(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=False):
            settings = _load()
            self.assertFalse(getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False))
            self.assertEqual(settings.CORS_ALLOWED_ORIGINS, ["https://fundvault.example.com"])

    def test_missing_secret_is_refused(self):
        env = dict(REQUIRED)
        env.pop("JWT_SECRET")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_development_default_secrets_are_refused(self):
        env = dict(REQUIRED, JWT_SECRET="fundvault-secret-key-change-in-production")
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_security_headers_are_on(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=False):
            settings = _load()
            self.assertTrue(settings.SECURE_SSL_REDIRECT)
            self.assertTrue(settings.SECURE_HSTS_SECONDS >= 31536000)
            self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python backend/manage.py test tests.test_production_settings --settings=fundvault_backend.settings_test -v 2`
Expected: FAIL — `No module named 'fundvault_backend.settings_production'`.

- [ ] **Step 3: Write the production settings**

Create `backend/fundvault_backend/settings_production.py`:

```python
"""Production settings. Refuses to start when a required secret is missing.

Failing at boot is the point: a deployment running on a default JWT secret
would issue forgeable tokens for every organisation.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from fundvault_backend.settings import *  # noqa: F401,F403
from fundvault_backend.settings import _parse_database_url

_DEV_DEFAULTS = {
    "fundvault-django-secret-change-in-production",
    "fundvault-secret-key-change-in-production",
    "change-me-in-production",
}


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} must be set in production.")
    if value in _DEV_DEFAULTS:
        raise ImproperlyConfigured(f"{name} is still the development default. Generate a real one.")
    return value


DEBUG = False
SECRET_KEY = _required("DJANGO_SECRET_KEY")
FUNDVAULT_JWT_SECRET = _required("JWT_SECRET")
FUNDVAULT_SECRET_KEY = _required("FUNDVAULT_SECRET_KEY")

ALLOWED_HOSTS = [h.strip() for h in _required("DJANGO_ALLOWED_HOSTS").split(",") if h.strip()]
if "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must name real hosts in production, not '*'.")

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in _required("CORS_ALLOWED_ORIGINS").split(",") if o.strip()
]
CORS_ALLOW_CREDENTIALS = False

DATABASES = {"default": _parse_database_url(_required("DATABASE_URL"))}

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024  # receipts are capped at 5 MB

STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.orgs.middleware.OrgContextMiddleware",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
```

Note that the tenant database entry is absent: production tenants are registered at runtime by the middleware, and `tenant_dev` must never exist on a deployed server.

- [ ] **Step 4: Add the production dependencies**

Append to `backend/requirements.txt`:

```text
gunicorn==23.0.0
whitenoise==6.8.2
```

Run: `pip install -r backend/requirements.txt`

- [ ] **Step 5: Run the tests**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, all tests.

- [ ] **Step 6: Commit**

```bash
git add backend/fundvault_backend/settings_production.py backend/requirements.txt backend/tests/test_production_settings.py
git commit -m "Add production settings that refuse to boot on defaults

A deployment running on the development JWT secret would issue forgeable tokens
for every organisation, so a missing or default secret is a startup failure
rather than a warning. CORS is an explicit allowlist and hosts cannot be '*'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 26: Deploy the backend to Render

**Files:**
- Create: `render.yaml`
- Create: `backend/build.sh`
- Modify: `backend/fundvault_backend/urls.py` (health check)

**Interfaces:**
- Produces: a deployed API at `https://<service>.onrender.com`, and `GET /api/health` returning `{"status": "ok"}`.

- [ ] **Step 1: Add a health check**

In `backend/apps/orgs/views.py`, append:

```python
@csrf_exempt
def health(request):
    """Liveness probe. Touches the control plane only — never a tenant."""
    Org.objects.exists()
    return JsonResponse({"status": "ok"})
```

Add to `backend/apps/orgs/urls.py`:

```python
    path("health", views.health),
```

and add `"/api/health"` to `PUBLIC_PREFIXES` in `backend/apps/orgs/middleware.py`.

- [ ] **Step 2: Write the build script**

Create `backend/build.sh`:

```bash
#!/usr/bin/env bash
# Render build step. Migrates the control plane only — tenant databases are
# migrated when their org is created, and on demand thereafter.
set -o errexit

pip install -r backend/requirements.txt
python backend/manage.py collectstatic --no-input
python backend/manage.py migrate --database=default --no-input
```

Run: `chmod +x backend/build.sh`

- [ ] **Step 3: Write the Render blueprint**

Create `render.yaml`:

```yaml
services:
  - type: web
    name: fundvault-api
    runtime: python
    plan: free
    buildCommand: "./backend/build.sh"
    startCommand: "gunicorn fundvault_backend.wsgi:application --chdir backend --bind 0.0.0.0:$PORT --workers 2 --timeout 60"
    healthCheckPath: /api/health
    envVars:
      - key: DJANGO_SETTINGS_MODULE
        value: fundvault_backend.settings_production
      - key: PYTHON_VERSION
        value: "3.13.1"
      - key: DATABASE_URL
        fromDatabase:
          name: fundvault-control
          property: connectionString
      - key: DJANGO_SECRET_KEY
        generateValue: true
      - key: JWT_SECRET
        generateValue: true
      # Generate locally and paste — it must never change once orgs exist:
      #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
      - key: FUNDVAULT_SECRET_KEY
        sync: false
      - key: DJANGO_ALLOWED_HOSTS
        sync: false
      - key: CORS_ALLOWED_ORIGINS
        sync: false

databases:
  - name: fundvault-control
    plan: free
    postgresMajorVersion: "16"
```

`FUNDVAULT_SECRET_KEY` uses `sync: false` rather than `generateValue: true` deliberately: a regenerated key would make every stored org connection undecryptable, locking every organisation out of its own data permanently.

- [ ] **Step 4: Deploy**

Push the branch, then in the Render dashboard: **New → Blueprint**, select the repository, and let it read `render.yaml`. When prompted, supply:
- `FUNDVAULT_SECRET_KEY` — generated locally with the command in the comment above; **save a copy offline**
- `DJANGO_ALLOWED_HOSTS` — the Render hostname, e.g. `fundvault-api.onrender.com`
- `CORS_ALLOWED_ORIGINS` — the Vercel URL from Task 27; set it to a placeholder now and correct it after that task

- [ ] **Step 5: Verify the deployment**

```bash
curl -s https://fundvault-api.onrender.com/api/health
```

Expected: `{"status": "ok"}`. A 500 here almost always means a missing environment variable — check the Render logs for `ImproperlyConfigured`, which names the exact variable.

- [ ] **Step 6: Commit**

```bash
git add render.yaml backend/build.sh backend/apps/orgs
git commit -m "Add Render deployment for the API and control plane

FUNDVAULT_SECRET_KEY is sync:false rather than generated: a regenerated key
would make every stored org connection undecryptable and lock every
organisation out of its own data permanently.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 27: Deploy the frontend to Vercel

**Files:**
- Create: `frontend/vercel.json`
- Modify: `frontend/next.config.js`
- Create: `frontend/.env.example`

- [ ] **Step 1: Document the API base variable**

Create `frontend/.env.example`:

```text
# Backend API origin. Local default is http://localhost:8000.
NEXT_PUBLIC_API_BASE=https://fundvault-api.onrender.com
```

- [ ] **Step 2: Add the Vercel configuration**

Create `frontend/vercel.json`:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "nextjs",
  "buildCommand": "next build",
  "installCommand": "npm install",
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Content-Type-Options", "value": "nosniff" },
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" }
      ]
    }
  ]
}
```

- [ ] **Step 3: Deploy**

In the Vercel dashboard: **Add New → Project**, import the repository, set the **Root Directory** to `frontend`, and add the environment variable `NEXT_PUBLIC_API_BASE` with the Render URL from Task 26. Deploy.

- [ ] **Step 4: Close the CORS loop**

Copy the Vercel production URL and set it as `CORS_ALLOWED_ORIGINS` in Render, then redeploy the Render service. Until this is done every browser request fails CORS, which presents as "Failed to fetch" with no useful error in the UI.

- [ ] **Step 5: Verify end to end**

Open the Vercel URL and create an organisation using a real Postgres (a free Neon or Supabase project). Confirm the connection test succeeds, the org is created, and you land in the app as Owner. Then check the browser network tab and confirm no response body contains `postgres://`.

- [ ] **Step 6: Commit**

```bash
git add frontend/vercel.json frontend/.env.example frontend/next.config.js
git commit -m "Add Vercel deployment for the frontend

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 28: Rewrite the documentation

**Files:**
- Modify: `README.md`
- Modify: `API_DOCUMENTATION.md`
- Modify: `install.bat`, `run.bat`

- [ ] **Step 1: Rewrite the README**

Replace `README.md` entirely. It must cover, in this order: what FundVault is now (a multi-tenant platform where each org brings its own Postgres); a "Get started" section pointing at the deployed URL with the three entry paths (create an org, join with a code, sign in); a "Run it yourself" section covering `docker compose up -d`, `backend/.env` from `.env.example` including generating `FUNDVAULT_SECRET_KEY`, `migrate --database=default`, and `npm run dev`; the roles table copied from the spec; a "Bring your own" section listing supported Postgres providers, S3-compatible storage, and AI providers; and a deployment section describing the Render + Vercel split.

Delete the banner added when the repository was created — it describes a transition that has finished.

Every reference to `admin login.txt`, a shipped SQLite database, or server-wide `GEMINI_API_KEY`/`NVIDIA_API_KEY` must be gone. Verify:

```bash
grep -rn "admin login\|fundvault.db\|GEMINI_API_KEY\|NVIDIA_API_KEY\|signup" README.md
```

Expected: no output.

- [ ] **Step 2: Update the API documentation**

In `API_DOCUMENTATION.md`:

- Add an **Organisations** section documenting `POST /api/orgs/validate-connection`, `POST /api/orgs/create`, `POST /api/orgs/join/preview`, `POST /api/orgs/join`, `GET|POST /api/orgs/codes`, `DELETE /api/orgs/codes/<code>`, `GET|PUT /api/orgs/settings`, and `GET /api/health`
- Add `POST /api/auth/orgs` and document that `POST /api/auth/login` now requires `orgId`
- Delete the `POST /api/auth/signup` section — the endpoint no longer exists
- Add `POST /api/transactions/<id>/receipt`
- Change every transaction example: `receipt_image` becomes `receipt_key` plus a `receipt_url` that is a signed URL valid for one hour
- Change `POST /api/databases/<id>/transactions` — it no longer accepts `receiptImage`
- Add a note to every endpoint that authentication is per-organisation, and that a token is only valid for the org it was minted for
- Add a **Permissions** section with the roles table
- Correct the analytics response: the non-empty branch returns only `totalDatabases`, `totalBalance`, `totalCredits`, and `totalDebits`, and the documented `monthlyData`/`modeData` keys are absent

- [ ] **Step 3: Update the Windows scripts**

In `install.bat`, change the closing summary to describe the new flow: databases via `docker compose up -d`, the generated `FUNDVAULT_SECRET_KEY`, and that AI keys are configured per organisation in the app rather than in a file.

In `run.bat`, keep the Docker startup added in Phase 1 Task 6 and confirm the printed URL is `http://localhost:3001`.

- [ ] **Step 4: Verify the whole suite one last time**

Run: `python backend/manage.py test tests --settings=fundvault_backend.settings_test -v 2`
Expected: PASS, every test.

Run: `npm --prefix frontend run build`
Expected: a successful production build with no errors.

- [ ] **Step 5: Commit**

```bash
git add README.md API_DOCUMENTATION.md install.bat run.bat
git commit -m "Rewrite the documentation for the multi-tenant platform

Documents organisation creation, join codes, per-org login, roles, and the
Render plus Vercel deployment. Removes signup, the shipped database, and
server-wide AI keys, none of which exist any more.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**Phase 6 is complete. The platform is deployed.**

---

## Deferred (not in this plan)

These are recorded in the spec and deliberately excluded:

- **`import_sqlite`** — importing an existing SQLite ledger into an org's Postgres. Must run a `--check` dry run comparing row counts per table, `SUM(amount)` per fund, and recomputed running balances against the source before writing anything, must read from a copy rather than the original file, and must convert base64 `receipt_image` rows to uploaded objects on the way through.
- **Per-fund access lists** — funds are visible org-wide here.
- **Bulk receipt entry** — several images at once producing draft transactions.
- **`DecimalField` for money** — the codebase uses `FloatField` throughout; converting is its own migration project across every tenant database.

## Self-review notes

Checked against the spec on completion:

| Spec section | Covered by |
|---|---|
| 1 — Control plane, encrypted credentials | Task 7 |
| 1 — Tenant schema changes | Task 4 |
| 1 — Roles table | Tasks 16, 17, 18, 19 |
| 1 — Self-approval and its audit trail | Tasks 16, 17 |
| 2 — Create an organisation | Tasks 12, 15 |
| 2 — Join with a code | Tasks 13, 15 |
| 2 — `email_index` writes | Tasks 12, 13, 14 |
| 2 — Login by email then org | Tasks 14, 15 |
| 2 — Unreachable tenant isolated to one org | Task 10 |
| 3 — JWT org claim, middleware, contextvar reset | Task 10 |
| 3 — Router, `NoOrgContext`, `allow_migrate` | Task 9 |
| 3 — Connection LRU, pooled connection strings | Tasks 8, 15 |
| 3 — `select_for_update` concurrency fix | Task 5 |
| 4 — Private buckets, object keys, signed URLs | Tasks 20, 21 |
| 4 — Per-org AI, OpenAI-compatible plus Gemini | Task 22 |
| 4 — Validation on save, masked keys | Task 23 |
| 4 — No rate limit, no auto-retry, hash dedupe | Task 24 |
| 5 — Migrations replace the legacy schema | Tasks 2, 3 |
| 5 — Rollout order | Phase structure |
| 5 — Three required tests | Tasks 5 (concurrency), 11 (isolation), plus balance coverage in 5 |
| 5 — Housekeeping (`install.bat`, secrets) | Task 6, plus the repository split already done |
