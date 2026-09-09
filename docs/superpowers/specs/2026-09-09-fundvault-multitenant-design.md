# FundVault Multi-Tenant Design

**Date:** 2026-09-09
**Status:** Approved, pending implementation plan

## Problem

FundVault today is a single-user local application: Django + Next.js over one
SQLite file, with every fund owned by one user (`DatabaseFund.user_id`) and one
set of AI credentials in `backend/.env`. Running it for two purposes means
running two copies of the codebase, which have already drifted — the personal
copy uses `nemotron-nano-12b-v2-vl` and `gemini-3.6-flash`, the other is two
model generations behind.

The goal is one deployed application where any organisation can sign up, connect
its own database, invite colleagues with roles, and use its own AI provider — so
that separate ledgers no longer require separate codebases.

## Decisions

| Question | Decision |
|---|---|
| Where tenant data lives | Each org supplies its own Postgres (BYO database) |
| Supported services | Postgres-compatible only — Supabase, Neon, Railway, any `postgres://` |
| Identity | Users live in the tenant DB; control plane keeps an email→org index |
| Permissions | Org-wide funds, roles Owner/Admin/Member |
| Approval rule | Applies only to Member-created transactions; any Admin may approve, including their own |
| Receipts | Org's own object storage, private bucket, signed URLs on read |
| AI providers | Per-org: OpenAI-compatible (base URL + model + key) or Gemini |
| Multi-DB mechanism | Django database router driven by a contextvar |
| Hosting | Next.js on Vercel, Django + control-plane Postgres on Render |

Rejected: a shared multi-tenant database (the org's data sovereignty was the
point); Airtable / Sheets / Firestore adapters (no SQL, no transactions — a
ledger cannot be built safely on them); per-org AI rate limits (the org pays for
its own key, and bulk receipt entry is a normal session).

## 1. Data model & control plane

**Control plane** — one small Postgres on Render, owned by the operator. Three
tables, no financial data:

```
orgs          id · name · slug · owner_email · created_at
              db_connection    (encrypted)   postgres://…
              storage_config   (encrypted)   bucket endpoint + key
              ai_config        (encrypted)   base_url + model + key, or gemini key
join_codes    code · org_id · grants_role · expires_at · max_uses · uses · revoked
email_index   email · org_id · last_seen_at
```

Secrets are encrypted at rest with Fernet under `FUNDVAULT_SECRET_KEY`.
**Losing that key makes every org's connection unrecoverable.** It lives in
Render's environment with an offline copy held by the operator.

`email_index` exists only so a returning user can be shown which orgs they
belong to. It holds no password data.

**Tenant DB** — the org's own Postgres. The existing seven tables, with four
changes:

- `users.role` gains `owner` alongside `admin` and `member`
- `databases.user_id` becomes `created_by`; funds belong to the org, and
  `_get_user_database()` drops its `user_id` filter
- `transactions.receipt_image` (base64 TEXT) becomes `receipt_key` (TEXT)
- `transactions` gains `created_by`, so the approval rule can read the
  creator's role

`requires_approval` is computed once at insert — `creator.role == member AND
amount >= threshold` — and stored, as it is today. No recomputation, no join at
read time.

**Roles.** Every member of an org holds exactly one:

| Capability | Owner | Admin | Member |
|---|---|---|---|
| View all funds and transactions | ✓ | ✓ | ✓ |
| Create transactions | ✓ | ✓ | ✓ |
| Transaction requires approval when over threshold | — | — | ✓ |
| Approve transactions (including own) | ✓ | ✓ | — |
| Void / edit / delete transactions | ✓ | ✓ | — |
| Create, edit, archive, delete funds | ✓ | ✓ | — |
| Mint Member join codes · manage members | ✓ | ✓ | — |
| Mint Admin join codes | ✓ | — | — |
| Change role of a member | ✓ | — | — |
| Set database connection, storage, AI config | ✓ | — | — |
| Transfer ownership | ✓ | — | — |

An org always has exactly one Owner — the creator, until ownership is
transferred. Existing guards carry over in org form: the Owner cannot be
demoted or removed while they are the only one, mirroring today's "at least one
active admin" rule.

**There is no `org_id` column anywhere.** The database is the tenant boundary,
so the classic multi-tenant failure — a query missing its `WHERE org_id = ?` —
is structurally impossible. The only way to reach the wrong org's data is to
open the wrong connection, which makes connection resolution the single place
that needs defending.

## 2. Onboarding & join flow

**Create an organisation (Owner).** Name the org, pick a service, paste the
`postgres://` URL. Before persisting anything the server connects, checks the
server version, verifies it can `CREATE TABLE`, runs `migrate
--database=<alias>`, and creates the first user as Owner. The `orgs` row is
written only if all four succeed, so a failure leaves no half-created org whose
database is in an unknown state. Storage and AI config are skippable and
configurable later.

**Join an organisation (Member).** A join code resolves to an org and the
browser is shown only the org's name — the connection string is never sent to
the client in any form. The normal signup form follows, creating the user row in
the tenant DB with the role the code grants. Codes carry `grants_role`, an
expiry, a max-use count, and a revoked flag. Admins mint Member codes; only the
Owner mints Admin codes.

Creating an org and joining one both write an `email_index` row, which is what
makes the org appear at that person's next login. Removing a member deletes
theirs.

**Returning login.** Email → `email_index` → the user picks from their orgs →
password, checked against that org's `users` table by the existing bcrypt path,
unchanged. An unknown email gets "no organisations found — create one, or ask an
admin for a join code."

**A tenant database being unreachable** fails that org's login with a specific
message and leaves every other org unaffected. The blast radius of a bad
connection string is exactly one org.

## 3. Request lifecycle & isolation

The JWT gains an `org_id` claim alongside `id`/`iat`/`exp`/`jti`, signed by the
control-plane secret, so a request's claimed org is tamper-proof.

```
request → CorsMiddleware → OrgContextMiddleware → view
                              ├ decode JWT → org_id
                              ├ load org (cached) → decrypt connection
                              ├ register/reuse alias  org_<id>
                              ├ set contextvar current_org
                              └ finally: reset contextvar
```

Resetting the contextvar in a `finally` is mandatory — contextvars otherwise
leak across requests on reused workers.

**The router** has three jobs:

- `db_for_read` / `db_for_write`: tenant models (`accounts`, `ledger`) resolve to
  `current_org`, and **raise `NoOrgContext` when it is unset** rather than
  falling back to `default`. Control-plane models always resolve to `default`.
- `allow_migrate`: tenant apps migrate only on tenant aliases, control-plane app
  only on `default`, so a stray `migrate` cannot scatter ledger tables into the
  orgs registry.
- `allow_relation`: same-alias only.

`@auth_required` runs unchanged — its session lookup and user load land in the
tenant DB because the context is already set. `views.py`, `services.py`, and
`serializers.py` need no edits for tenancy.

**Connections** are cached per org in an LRU capped at ~50 aliases, closing on
eviction. Supabase and Neon default to their **pooled** connection strings
(Supabase port 6543); their direct-connection limits are low enough that a
handful of active orgs would exhaust them.

**Concurrency fix.** `database_transactions` currently reads `db.balance`,
computes the new balance, and only then opens its atomic block. With one user
that race is theoretical; with colleagues posting to one fund it is a live
corruption path — two concurrent debits both read the old balance and the fund
passes its own sufficiency check while going negative. The fund row is locked
with `select_for_update()` inside the transaction. This belongs to the tenancy
work because multi-user is what makes it real.

## 4. Receipts & AI configuration

**Storage.** An org supplies bucket credentials — Supabase (project URL +
service key + bucket) or any S3-compatible endpoint — encrypted into
`storage_config`. Upload path: browser → Django → validate (≤5 MB, `image/*`,
and an actual Pillow decode, because content-type is client-supplied) → the
existing `_compress_image` 1024px JPEG q75 pass → PUT to
`receipts/<fund_id>/<txn_id>.jpg`.

The column stores the **object key, not a URL**. Buckets stay private and the
serializer mints a short-lived signed URL (60 minutes) at read time. A public
bucket would make receipt images — names, account numbers, amounts — fetchable
by anyone who guesses a path; storing a permanent URL would bake that in.

An org without storage configured has receipt upload disabled with a clear
message. Everything else works normally.

**AI.** `extract_from_receipt_image(image_bytes, mime_type, config)` takes its
provider config as an argument instead of reading `settings`. The provider
functions, the fallback chain, and `_parse_json_from_text` are untouched. An org
configures a primary and an optional secondary; the existing
try-primary-then-fallback logic becomes config-driven rather than hardcoded
NVIDIA→Gemini.

Rather than a fixed provider dropdown, the form takes **base URL + model name +
key** for any OpenAI-compatible endpoint — which is how the NVIDIA path already
works — with Gemini as its own option because it needs its own SDK. Orgs can
point at NVIDIA, OpenRouter, Groq, or a local vLLM without a release.

Keys are validated on save with one cheap probe call, so a typo surfaces in
settings rather than at someone's first receipt. They are shown masked
(`nvapi-••••3f2a`), replaceable but never revealed — an Admin should not be able
to exfiltrate the Owner's key through the settings page.

**No rate limiting.** The org pays for its own key, so usage is its decision, and
entering a month of receipts in one sitting is a normal session. Two client
behaviours cover the actual risk: extraction failures do not auto-retry (they
surface an error and a manual retry button), and an identical image hash within a
session reuses its previous result.

## 5. Migration, rollout & testing

**Migrations.** `makemigrations` generates the initial set from the existing
models; current SQLite files already have those tables, courtesy of
`legacy/server.js`, so they take `migrate --fake-initial`. A second migration
carries the section 1 changes. `ensure_profile_schema()` — the hand-rolled
`ALTER TABLE` running on every authenticated request — is deleted, because a
real migration is what it was standing in for. The control plane becomes a new
`apps/orgs`, migrated only on `default`.

The `receipt_image` → `receipt_key` change is a plain schema change with no data
step. New receipts are written as object keys from day one; converting old
base64 rows belongs to the deferred importer.

**Rollout order**, each step leaving a working application:

1. Migrations · `select_for_update` fix · secrets cleanup — still single-tenant,
   still local
2. Control plane + router + middleware, with one hardcoded org pointing at the
   repo's own development database. Behaviour identical, plumbing proven, no UI
   changes to confuse diagnosis
3. Org creation, join codes, login routing
4. Roles and the Member-only approval rule
5. Storage and per-org AI config
6. Deploy to Render + Vercel

Step 2 carries the most risk, which is why it ships alone.

**Testing.** The project has no tests today. Three are not optional here:

- **Tenant isolation** — querying a tenant model with no context or the wrong
  context raises, and never returns another org's rows
- **Balance recalculation** — the money path in
  `recalculate_running_balances()`
- **Concurrent debit** — two simultaneous debits against one fund, proving
  `select_for_update` holds the invariant

**Housekeeping folded into step 1:** `data/fundvault.db` is untracked
(`git rm --cached`), `admin login.txt` is deleted and its password rotated, and
`install.bat`'s `.env` path bug is fixed — it writes to the repository root
while `manage.py` loads `backend/.env`, so every key it sets is silently
ignored. `admin login.txt` then has no reason to exist: org creation makes its
creator the Owner, which is what the file was working around.

## Deferred

**`import_sqlite` management command.** Importing an existing SQLite ledger into
an org's Postgres is out of scope until the platform is deployed and working.
When it is built, it must run `--check` first: a dry run that compares row counts
per table, `SUM(amount)` per fund, and recomputed running balances against the
source, writing nothing. A ledger import that silently drops one transaction is
worse than one that refuses to run. It reads from a copy, never the original
file, and converts base64 `receipt_image` rows to uploaded objects on the way
through.

**Per-fund access lists.** Funds are visible org-wide in this design. Restricting
a fund to named members is a reasonable future need but costs a membership table,
an access check on every ledger query, and a management UI — worth building when
an org asks, not before.

**Bulk receipt entry.** Multiple images uploaded at once producing several draft
transactions would fit real usage better than today's one-modal-per-receipt, but
it is a separate feature layered on this design.
