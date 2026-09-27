# Backend reference

Module-by-module reference for the Django backend. See
[architecture.md](./architecture.md) for the system-level picture (control
plane vs tenant DBs, request lifecycle, security model), and
[api.md](./api.md) for endpoint request/response shapes. This document
covers what each file does and the invariants a contributor must respect —
not a copy of the code.

There is no Django REST Framework here: every view is a plain function that
parses a JSON body with `apps.common.utils.parse_body` and returns a
`JsonResponse`. There's no `django.contrib.auth` either — see
[apps/common](#appscommon).

## The one invariant that matters everywhere

`accounts` and `ledger` are **tenant apps** (`TenantRouter.TENANT_APPS` in
`../backend/apps/orgs/router.py`): every query against their models is routed
to the current org's own database, taken from a `ContextVar`
(`../backend/apps/orgs/context.py`). Two consequences:

- **Any `transaction.atomic()` around a tenant model must be
  `transaction.atomic(using=current_org_alias())`.** A bare `atomic()` opens
  on `default`, and `select_for_update()` on a tenant model inside it raises
  `TransactionManagementError`. Never use `@transaction.atomic` as a
  decorator on a tenant-writing view — the alias would need to be evaluated
  at import time, before any request (and org) exists.
- **A tenant query with no org in context raises `NoOrgContext`** (not a
  silent fallback to `default`). Code that runs outside a request — a
  management command, a script — must wrap tenant access in
  `apps.orgs.context.org_context(alias)`.

The test runner also expects `apps/orgs/connections.py`'s `build_config` to
be referenced **through the module** (`tenant_connections.build_config(...)`),
not via a `from ... import build_config`, because
`fundvault_backend/test_runner.py` monkeypatches the module attribute to
redirect every tenant connection to a `test_*` database during a test run.
`provisioning.py` and `register_org.py` both do this correctly.

## `backend/apps/orgs` — control plane and tenant routing

The control plane: which orgs exist, how to reach their databases, join
codes, and email-based login discovery.

| File | Purpose |
|---|---|
| `models.py` | `Org` (id, slug, encrypted `db_connection`/`storage_config`/`ai_config`), `JoinCode` (`FUNDVAULT-XXXX-XXXX`, `consume()` is an atomic conditional `UPDATE` so concurrent redemptions can't over-consume), `EmailIndex` (`(email, org)` unique, drives login discovery) |
| `fields.py` | `EncryptedTextField`: Fernet encrypt/decrypt using `FUNDVAULT_SECRET_KEY` (`lru_cache`d). Empty string and `None` pass through unencrypted; a bad key raises `ValueError` on decrypt, never leaking ciphertext |
| `connections.py` | Runtime tenant-alias registry — see [architecture.md](./architecture.md#request-lifecycle) for the flow. `ensure_connection` is per-process (an LRU capped at `MAX_TENANT_CONNECTIONS=50`); `_close_and_forget` only closes the *calling thread's* connection wrapper, so under a threaded worker eviction can leave another thread's socket open until `CONN_MAX_AGE` (60s) |
| `router.py` | `TenantRouter` — see [the invariant above](#the-one-invariant-that-matters-everywhere) |
| `context.py` | The `ContextVar` holding the current org alias, plus `org_context(alias)` for non-request code |
| `middleware.py` | `OrgContextMiddleware` — resolves the org from the JWT (or, for `/api/auth/login` only, from the request body's `orgId`), calls `ensure_connection`, sets the `ContextVar` for the request. `PUBLIC_PREFIXES` skip resolution entirely. `process_exception` turns a tenant `OperationalError` into a generic 503 — the exception text (which can include host/port/user) is logged, never returned to the client |
| `provisioning.py` | `resolve_target`/`blocked_https_url_message` (the SSRF guard, shared by DB, storage and AI targets — see [architecture.md](./architecture.md#security-model)), `check_connection` (probe with a pinned IP), `provision_org` (probe → register alias → migrate → create `Org` in a savepoint, dropping the alias on failure), `migrate_org_database` (repoint: probe → drop old alias → register+migrate new one → save) |
| `views.py` | Org HTTP endpoints — see [Endpoints of note](#endpoints-of-note) below |
| `serializers.py` | `serialize_org`/`serialize_org_summary` — explicit-key, never include credentials |
| `urls.py` | Routes under `/api/orgs/...` and `/api/health` |
| `management/commands/migrate_tenants.py` | Deploy step (`build.sh`): migrates every org's tenant DB, in `created_at` order. Per-org failures are logged and skipped — **the command's own exit code is always 0**, so a failing tenant migration does not fail the Render build |
| `management/commands/register_org.py` | Operator CLI to create an org from a URL, bypassing the self-service SSRF guard (this path is operator-trusted, not attacker-facing) |

### Endpoints of note (`views.py`)

- `validate_connection` and `create_org` are **public and rate-limited**
  (this is unauthenticated, self-service signup — treat every input as
  hostile). `create_org` provisions the tenant DB, creates the Owner
  user/session/audit row inside it, and deletes the just-created `Org` row
  on any failure.
- `join_preview`/`join_org` are public and rate-limited; `join_org` checks
  for a username/email clash in the target org, then `JoinCode.consume()`,
  then creates the user.
- `join_codes` GET/POST and `revoke_join_code` are scoped to the caller's
  org and to the roles they're allowed to mint (`_mintable_roles(actor)`:
  Member and Viewer always, Admin only when `can(actor,
  Action.MINT_ADMIN_CODE)`; Owner never). An unmintable code answers
  like a missing one, so as not to leak which roles exist.
- `org_settings`: GET masks secrets (`_mask`) and returns DB host/port/
  name/user but never the password. PUT validates the `storage`/`ai`
  sections (write-time SSRF + credential probe) before saving, and repoints
  the database (if a `database` section is present) **last**, after the
  other sections have already validated — so a bad storage/AI config never
  leaves the org mid-repoint. Omitting `ai.fallback` from the payload keeps
  the existing fallback provider; sending `ai.fallback: null` clears it
  explicitly.
- `DELETE` on `org_settings` requires the caller to type the org's `name`
  to confirm, and removes only the control-plane `Org` row — see
  [architecture.md](./architecture.md#org-lifecycle) for why the tenant DB
  and its deletion audit entry are left behind.
- `health` only touches the control plane (`Org.objects.exists()`); it is
  the Render health-check target and needs no org context.

## `backend/apps/accounts` — users, sessions, permissions

A **tenant app**: `User` and `Session` live in each org's own database, not
the control plane.

| File | Purpose |
|---|---|
| `models.py` | `User` (`users` table): bcrypt `password_hash`, `username` unique+case-sensitive, `email` unique and always lowercased on write, `role` in `owner`/`admin`/`member`/`viewer` (default `member`). `Session` (`sessions` table): `token` is the full JWT string (unique), `last_activity` is set once at creation and never updated afterwards — it means "login time", not "last active" |
| `permissions.py` | The single capability table — see [Permissions](#permissions) below |
| `serializers.py` | `serialize_user` — never includes `password_hash` |
| `views.py` | Login, discovery, self-profile, member management — see [Endpoints of note](#endpoints-of-note-1) below |
| `urls.py` | `/api/auth/*` and `/api/admin/users/*` |

### Permissions

`permissions.py`'s `CAPABILITIES` table nests: `VIEWER = {VIEW}`, `MEMBER =
VIEWER + {CREATE_TXN}`, `ADMIN = MEMBER + {APPROVE, MODIFY_TXN, MANAGE_FUNDS,
MANAGE_MEMBERS}`, `OWNER = ADMIN + {MINT_ADMIN_CODE, CHANGE_ROLE,
MANAGE_ORG_CONFIG, TRANSFER_OWNERSHIP}`. `can(user, action)` is `False` for
`None`, inactive users, and unknown roles. `require(user, action)` returns a
403 `JsonResponse` or `None` — every write-gating view returns it directly.

One check sits outside this table: `needs_approval(user, amount, threshold)`
compares `user.role` directly (`True` only for `role == "member"`,
`threshold > 0`, `amount >= threshold`) rather than calling into
`CAPABILITIES`.

`frontend/src/lib/permissions.js` mirrors this table by hand — the two must
be kept in sync manually; there's no shared source of truth across the
language boundary.

### Endpoints of note (`views.py`)

- `login` (rate-limited, org from `OrgContextMiddleware`'s body-based
  resolution): matches candidates on **username OR email** and only accepts
  the one whose bcrypt hash verifies — this is deliberate, so that a user
  who has set their own username to someone else's email address can't
  shadow that person's email login (at most two rows match, so at most two
  bcrypt checks).
- `me` PUT: a password change requires `currentPassword` and, on success,
  deletes every *other* session (not the current one). A wrong
  `currentPassword` returns **400**, not 401 — the frontend treats any 401
  on an authenticated call as "session expired" and signs the user out, so
  401 here would silently and incorrectly log the user out instead of
  showing "Current password is incorrect". `profile_image` is validated
  server-side: must be `None`/empty or a `data:image/...` string of at most
  300,000 characters, else 400.
- `admin_user_detail` PUT (`MANAGE_MEMBERS`): a role change needs
  `CHANGE_ROLE`; role can never be set to `owner` here (use
  `transfer_ownership`); the sole active Owner can't be demoted, deactivated,
  or self-deactivated. Only fields that actually changed relative to the
  freshly-read row are written — this matters because it's the fix for a
  race where an Admin editing an unrelated field (e.g. a typo in someone's
  username) could otherwise stomp a concurrent `transfer_ownership`'s role
  change back to its old value. Editing the Owner's own non-role fields (as
  themselves, or as an Admin without `TRANSFER_OWNERSHIP`) is allowed; only
  an actual promotion-to-owner attempt is refused.
- `admin_user_detail` DELETE: reassigns the target's `TrashItem.deleted_by`
  and nulls `AuditLog.user_id` **before** deleting the user, because both
  FKs are `CASCADE` at the model level — get the order wrong and a user
  delete silently wipes audit history and trash provenance.
- `admin_reset_password`: resetting the Owner's own password additionally
  requires `TRANSFER_OWNERSHIP` (an Admin can't reset the Owner out from
  under them).
- `transfer_ownership`: locks the caller's row with `select_for_update` and
  re-checks they're still Owner before promoting the target and demoting
  the caller — closes the window for two concurrent transfers to both
  succeed.

## `backend/apps/common` — shared plumbing, no models

`common` is not an installed Django app with models; it's where every other
app's shared helpers live.

| File | Purpose |
|---|---|
| `auth.py` | `create_session_token` (HS256 JWT: `id`, `org_id`, `iat`, `exp`, `jti`). `auth_required` decorator: verifies the JWT, loads the matching `Session` row in the tenant DB, checks the claim's user id matches, checks expiry, checks the user is active (deleting **all** of an inactive user's sessions if not) — sets `request.fv_user`/`request.fv_token` on success. It does not re-check `org_id`; `OrgContextMiddleware` already established `request.fv_org` from the same token |
| `ratelimit.py` | `rate_limit(key_prefix, max_attempts, window_seconds)`: fixed-window counter keyed on `REMOTE_ADDR`, on Django's default in-memory cache — per-worker, resets on restart. Behind Render's proxy this may collapse many clients onto one bucket (unverified; see [architecture.md](./architecture.md#security-model)) |
| `audit.py` | `add_audit(user_id, action, entity_type, entity_id, details)` — writes an `AuditLog` row to the **current tenant DB**; needs org context |
| `utils.py` | `uid()` (8 random `[a-z0-9]` chars + hex millisecond timestamp — used for every model PK; not a secret, not cryptographically random), `parse_body` (non-object JSON becomes `{}`), `parse_number` (finite float or `None`; rejects bool/NaN/inf), `json_error`, `libpq_options` (allowlists `sslmode`/`options`/`application_name`/`channel_binding`/`connect_timeout` out of a Postgres URL's query string; defaults `sslmode=require` unless the host is loopback) |

## `backend/apps/ledger` — funds, transactions, receipts

A **tenant app**. This is the money core; read
[architecture.md](./architecture.md#money-correctness) first for the
locking and balance-derivation model before touching anything here.

| File | Purpose |
|---|---|
| `models.py` | `DatabaseFund` ("databases" table — funds are called databases in the schema for historical reasons), `TransactionFund`, `AuditLog`, `RecurringTransaction`, `TrashItem`. All FloatField money, rounded to 2dp on every write. `DatabaseFund.merged_into` is a self-FK (`SET_NULL`) used by fund merges |
| `services.py` | `lock_fund(id)` (`SELECT ... FOR UPDATE`; **must** run inside `transaction.atomic(using=current_org_alias())`), `recalculate_running_balances(id)` (rebuilds every approved+non-voided row's `running_balance`, ordered `(date, created_at, id)` — the `id` tie-break gives deterministic order when two rows share a timestamp — writes via `bulk_update`/`update()`, so any in-memory copy of the fund/txn is stale afterwards; call sites that need the fresh value call `refresh_from_db`), `post_transaction` (raises `InsufficientBalance` if a debit would exceed the current balance, even while pending), `next_recurring_date` (monthly clamps the day to `min(day, 28)` permanently; yearly Feb 29 becomes Feb 28 and stays there), `process_due_recurring` — see [architecture.md](./architecture.md#money-correctness) |
| `serializers.py` | `serialize_transaction` mints a fresh presigned receipt URL per row at read time (`None` if storage isn't configured or signing fails); `mode_data` is parsed defensively — anything that isn't a JSON object becomes `{}` |
| `storage.py` | Per-org S3-compatible client. `parse_storage_config` raises `StorageNotConfigured` if any required key is missing. `receipt_key_for(fund_id, txn_id)` builds the deterministic key `receipts/<fund>/<txn>.jpg` (each segment validated against `[A-Za-z0-9_-]+`) — re-uploading a receipt overwrites the object in place. `check_storage` is the write-time SSRF+credential gate, called only from `orgs.views.org_settings` PUT |
| `receipt_extractor.py` | Per-org AI extraction (OpenAI-compatible or Gemini, primary with fallback). Both OpenAI-compatible clients are built with `DefaultHttpxClient(follow_redirects=False)` — see [architecture.md](./architecture.md#security-model). `_compress_image` (also used directly by `views.transaction_receipt`) converts to RGB, thumbnails to 1024px, re-encodes as JPEG q75. `check_ai_config` is the write-time gate, called from `org_settings` PUT |
| `urls.py` | Route order matters: `databases/merge` must be registered before `databases/<id>`, and `recurring/process` before `recurring/<id>` |

### Endpoints of note (`views.py`)

Every write endpoint gates on `permissions.require` per the table in
[api.md](./api.md#permissions) — see that document for the exact
role-to-endpoint mapping; this section only covers behaviour worth knowing
beyond "which role."

- Money-affecting writes (`database_transactions` POST, `transaction_void`,
  `transaction_delete_voided`, `transaction_approve`, `transaction_update`)
  each open `transaction.atomic(using=current_org_alias())`, call
  `lock_fund`, **re-check** deleted/archived/already-voided state under the
  lock (a pre-lock check is only a fast path), do the write, and audit
  inside the same atomic block — a failure partway through rolls back the
  whole thing rather than leaving, say, a voided transaction with no audit
  entry.
- `transaction_void` and `transaction_delete_voided` re-check the row's
  current state as part of their locked `UPDATE`/`DELETE` (via a
  conditional queryset filter, not a plain save/delete on an
  already-fetched object) so that two concurrent requests against the same
  transaction can't both apply — the second one gets the normal "already
  voided" 400 / "not found" 404 instead of double-voiding or
  double-deleting.
- `transaction_receipt` (any `CREATE_TXN` user, i.e. any role): if the
  caller didn't create the transaction, the endpoint additionally requires
  `MODIFY_TXN` — otherwise a Member could silently overwrite the receipt
  image on another user's (including an Admin's, already-approved)
  transaction, since the storage key is deterministic. Also refuses to
  upload to a transaction whose fund is archived (400 "This fund is
  archived").
- `database_detail` GET and `database_transactions` GET order transactions
  by `(-date, -created_at, -id)` — the same tie-break `id` order (reversed)
  that `recalculate_running_balances` uses, so rows with an identical
  `date`+`created_at` display in the same order the running balance was
  computed in.
- String fields read from the request body (`name`, `reason`, `sender`,
  etc.) treat an explicit JSON `null` as clearing the field (empty
  string/`None`), not as the literal text `"None"` — and an empty required
  field (e.g. a void `reason` of `null`) correctly fails validation instead
  of "succeeding" with the string `"None"` stored.
- `database_detail` DELETE (soft delete, creates a `TrashItem`) and
  `trash_restore` POST each run their multi-row write (fund flag +
  `TrashItem` + audit) inside one `transaction.atomic(using=
  current_org_alias())`, so a failure partway through can't leave a fund
  that's neither listed nor in the trash.
- Permanently purging a fund (`trash_delete`, or "empty trash") and
  deactivating a recurring rule (`recurring_delete`) now write an audit
  entry — these are irreversible actions that previously left no trace
  beyond the earlier soft-delete/create entries.
- The trash-restore audit entry's `entity_id`/message reference the
  **restored fund's** id/name, not the (now-deleted) `TrashItem`'s id.
- `extract_receipt` returns HTTP **502** (not 200) when the configured AI
  provider(s) fail to extract — success still returns 200, and "no provider
  configured at all" still returns 503. The response body shape
  (`{"error": ...}`) is unchanged; only the status code moved.

## Deploy-relevant files

| File | Role |
|---|---|
| `backend/build.sh` | Render build: pip install → `migrate --database=default` → `migrate_tenants` |
| `backend/fundvault_backend/settings.py` | Dev/base settings; every other settings module effectively layers on top of it |
| `backend/fundvault_backend/settings_production.py` | Refuses to boot on a missing or default-looking secret (`DJANGO_SECRET_KEY`, `JWT_SECRET`, `FUNDVAULT_SECRET_KEY`); forces `DEBUG=False` and an empty SSRF allowlist |
| `backend/fundvault_backend/test_runner.py` | `IsolatedDatabaseRunner` — redirects every tenant connection (including ones registered at runtime by `provisioning.py`) onto `test_*` databases, and hard-fails any real-database connection attempt outside that set |
| `backend/manage.py` | Loads `backend/.env` (gunicorn does not — only local/dev tooling reads it) |

See [deployment.md](./deployment.md) for the full Render/Vercel walkthrough,
and [development.md](./development.md) for running the test suite locally
(`manage.py test --settings=fundvault_backend.settings_test`).
