# FundVault - Fund Management System

FundVault is a multi-tenant fund/ledger management platform. Each organisation
brings its own Postgres database, invites members with roles, and supplies its
own object storage and AI provider credentials — there is no shared tenant
database and no server-wide API keys. A small control-plane database (run by
the operator) tracks organisations, join codes, and which orgs an email
belongs to; every organisation's actual ledger data lives only in the
Postgres it connected.

The design behind this is in
[`docs/superpowers/specs/2026-09-09-fundvault-multitenant-design.md`](docs/superpowers/specs/2026-09-09-fundvault-multitenant-design.md).

## Features

- **Multi-tenant by database** — each org supplies its own `postgres://` connection; there is no shared tenant table and no `org_id` column to get wrong
- **Organisation onboarding** — create an org (validates the connection, migrates it, makes you Owner) or join one with a code
- **Role-based access control** — Owner / Admin / Member / Viewer, see [Roles](#roles) below
- **Fund management** — create, archive, merge, and manage multiple fund accounts per org
- **Ledger management** — transactions with running balances, void/edit, and an approval workflow for Member-created transactions over a threshold
- **Receipts** — upload a receipt image to an org's own S3-compatible bucket; read access is via a signed URL valid for one hour, never a public link
- **AI-powered receipt extraction** — any OpenAI-compatible endpoint (NVIDIA NIM, OpenRouter, Groq, a local vLLM, ...) or Google Gemini, configured per organisation with an optional fallback provider
- **Audit logging** — a trail of user actions per organisation
- **Trash management** — soft delete with recovery capability
- **Recurring transactions** — scheduled transactions processed on demand
- **Analytics** — dashboard totals across an org's funds

## Project structure

```text
FundVault/
├── backend/                         # Django API (plain function views)
│   ├── apps/
│   │   ├── accounts/                # Users, login, roles, admin member management
│   │   ├── common/                  # JWT auth, audit logging, shared helpers
│   │   ├── orgs/                    # Control plane: org provisioning, join codes,
│   │   │   │                        # per-org connection routing, org settings
│   │   └── ledger/                  # Funds, transactions, receipts, AI extraction
│   ├── fundvault_backend/
│   │   ├── settings.py              # Local/dev settings
│   │   ├── settings_production.py   # Production settings (refuses to boot on defaults)
│   │   ├── settings_test.py         # Isolated test settings — see Running the tests
│   │   ├── test_runner.py           # Private per-alias test databases; blocks real DB access
│   │   └── urls.py
│   ├── tests/                       # support.py plus one module per feature area
│   ├── manage.py
│   └── requirements.txt
├── frontend/                        # Next.js web application
│   ├── src/
│   ├── vercel.json
│   └── .env.example
├── docker-compose.yml                # Local control-plane + dev-tenant Postgres
├── render.yaml                       # Render Blueprint for the backend web service
├── API_DOCUMENTATION.md              # Complete API reference
├── install.bat                       # Windows installation script
├── run.bat                           # Windows application launcher
└── README.md                         # This file
```

### Tech stack

**Backend:** Django 5.2 (plain function views, no REST framework), JWT auth (PyJWT),
bcrypt password hashing, `cryptography` (Fernet) for encrypting stored org
credentials, django-cors-headers, psycopg 3 (Postgres only — this is a
Postgres-only application, no SQLite), boto3 for S3-compatible storage,
`openai` client + `google-genai` for AI receipt extraction, Pillow for image
processing, gunicorn + whitenoise for production.

**Frontend:** Next.js 16, React 19, Chart.js, jsPDF for PDF export.

## Get started

FundVault is deployed as a Django API (Render) behind a Next.js frontend
(Vercel) — see [Deployment](#deployment) for how to stand up your own copy.
Once it's running, opening the site presents three entry paths:

1. **Create an organisation** — name it, paste a `postgres://` connection
   string for a database you control, and pick a username/email/password.
   FundVault verifies the connection, runs migrations against it, and makes
   you the Owner. Nothing is written to the control plane until all of that
   succeeds.
2. **Join with a code** — an Owner or Admin mints a join code that grants a
   specific role (Admin, Member, or Viewer) and shows you which org you're
   joining before you create your account.
3. **Sign in** — enter your email, pick from the organisation(s) it belongs
   to, then your password (checked against that org's own database).

There is no self-service "sign up" without either creating an org or holding
a join code — that endpoint doesn't exist.

## Run it yourself

Prerequisites: Python 3.11+, Node.js 20.9+, Docker (for local Postgres), Git.
`python` must resolve to 3.11+ — activate a virtualenv first if your system
Python is older or missing.

1. **Start local Postgres** — one database for the control plane, one for a
   development tenant:

   ```bash
   docker compose up -d
   ```

2. **Install dependencies:**

   ```bash
   npm run install:all
   ```

3. **Configure the backend** — copy the example env file and fill it in:

   ```bash
   cp backend/.env.example backend/.env
   ```

   Generate `FUNDVAULT_SECRET_KEY` (encrypts every org's stored connection
   string, storage credentials, and AI key — losing it makes them
   unrecoverable):

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Paste the result into `backend/.env` as `FUNDVAULT_SECRET_KEY`. The other
   defaults in `.env.example` (`DATABASE_URL`, `DEV_TENANT_DATABASE_URL`)
   already match the `docker-compose.yml` ports.

4. **Migrate the control plane:**

   ```bash
   python backend/manage.py migrate --database=default
   ```

5. **Run it:**

   ```bash
   npm run dev
   ```

   This starts the backend at `http://127.0.0.1:8000` and the frontend at
   `http://localhost:3001`.

Or, on Windows, run `install.bat` once and `run.bat` every time after —
`run.bat` also brings up the two Docker databases automatically.

6. **Create your first organisation** — open `http://localhost:3001`, choose
   **Create an organisation**, and paste the dev tenant's connection string:

   ```text
   postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev
   ```

   Use `127.0.0.1`, not `localhost` — the dev SSRF allowlist (active only with
   `DJANGO_DEBUG=true`, the dev default) matches exact `127.0.0.1` host:port pairs, not hostnames.

AI keys and receipt storage credentials are **not** environment variables —
each organisation configures its own from its org settings page after
signing in (see [Bring your own](#bring-your-own)).

### Running the tests
With the dev Postgres containers up (`docker compose up -d`):

    cd backend
    python manage.py test --settings=fundvault_backend.settings_test

The test runner creates private `test_*` copies of every database and refuses to
connect to anything else, so tests never touch your dev data. To run two suites
at once, give each its own suffix: `FUNDVAULT_TEST_DB_SUFFIX=_mine`.

## Roles

Every member of an organisation holds exactly one role:

| Capability | Owner | Admin | Member | Viewer |
|---|---|---|---|---|
| View all funds and transactions | ✓ | ✓ | ✓ | ✓ |
| Export / print reports and receipts | ✓ | ✓ | ✓ | ✓ |
| Create transactions | ✓ | ✓ | ✓ | — |
| Transaction requires approval when over threshold | — | — | ✓ | n/a |
| Approve transactions (including own) | ✓ | ✓ | — | — |
| Void / edit / delete transactions | ✓ | ✓ | — | — |
| Create, edit, archive, delete funds | ✓ | ✓ | — | — |
| Mint Member / Viewer join codes · manage members | ✓ | ✓ | — | — |
| Mint Admin join codes | ✓ | — | — | — |
| Change role of a member | ✓ | — | — | — |
| Set database connection, storage, AI config | ✓ | — | — | — |
| Transfer ownership | ✓ | — | — | — |

An org always has exactly one Owner. The Owner cannot be demoted or removed
while they are the only one — every org must keep an active Owner.
Self-approval is intentionally allowed (an Admin may approve their own
transaction); `created_by` and `approved_by` are both recorded, so it is
always visible in the audit log.

## Bring your own

FundVault has no shared infrastructure to bring your own data to — you supply:

- **A Postgres database**, one per organisation. Any `postgres://` connection
  string works: [Supabase](https://supabase.com), [Neon](https://neon.tech),
  [Railway](https://railway.app), your own server, anything Postgres-compatible.
  Supabase and Neon are used through their **pooled** connection string
  (on Supabase, the **Session pooler** string, `…pooler.supabase.com:5432`)
  — their direct-connection limits are too low for several active orgs
  sharing the process.
- **S3-compatible object storage**, for receipt images. Supabase Storage or
  any S3-compatible endpoint (Cloudflare R2, MinIO, AWS S3, ...) — an
  endpoint URL, bucket name, region, and an access key pair. The bucket must
  be private: FundVault never writes a public URL, only short-lived (one
  hour) signed URLs generated on read. Without storage configured, receipt
  upload is disabled but everything else works.
- **An AI provider**, for receipt extraction. Either any OpenAI-compatible
  endpoint (base URL + model name + key — this is how NVIDIA NIM,
  OpenRouter, Groq, and a local vLLM server all work) or Google Gemini (its
  own SDK, so it's a separate option). You can configure a primary and an
  optional fallback. Keys are validated with a cheap probe call when you
  save them, and are shown masked afterward — never re-displayed in full.
  Without an AI provider configured, extraction is disabled but manual
  transaction entry works normally.

All three are configured per-organisation from the org settings page — see
`GET|PUT /api/orgs/settings` in [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

## Deployment

FundVault runs as two services plus databases you own:

| Piece | Where | Notes |
|---|---|---|
| API (Django + gunicorn) | Render web service from `render.yaml` | each deploy runs `backend/build.sh` |
| Control-plane Postgres | any Postgres that doesn't expire — e.g. a Neon project, or a paid Render Postgres | holds organisations, join codes, the email index, and every org's encrypted connection string |
| Frontend (Next.js) | Vercel, Root Directory `frontend` | a static page that talks to the API |
| Each organisation's data | that organisation's own Postgres (Neon, or Supabase's **Session pooler** string) | entered when the organisation is created |

### 1. Control-plane database
Create a Postgres database that will not expire and copy its connection string
(keep `?sslmode=require`). Render's free Postgres is deleted 30 days after
creation, which would orphan every organisation, so `render.yaml` does not
create one.

### 2. Encryption key
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Keep it in a password manager. It encrypts every stored organisation
credential; if it changes or is lost, every organisation becomes unreachable.
The API refuses to start with a missing or malformed key.

### 3. Backend on Render
New → Blueprint → pick this repository (branch `main`). Render asks for:
- `DATABASE_URL` — the connection string from step 1
- `FUNDVAULT_SECRET_KEY` — the key from step 2
- `CORS_ALLOWED_ORIGINS` — your Vercel URL, e.g. `https://fundvault.vercel.app`
  (it must be a full URL with a scheme — corsheaders' system check fails the
  build otherwise; a placeholder like `https://example.com` is fine now, fix
  it to the real URL after step 4)

`DJANGO_SECRET_KEY` and `JWT_SECRET` are generated for you. The service's own
`*.onrender.com` hostname is allowed automatically; set `DJANGO_ALLOWED_HOSTS`
only if you add a custom domain.

Every deploy installs dependencies, migrates the control plane, then runs
`python backend/manage.py migrate_tenants` to bring each organisation's database
up to date. An organisation whose database can't be reached is logged as
`FAILED` in the build log without failing the deploy; it stays on the old schema
until a later deploy reaches it.

Check it: `curl https://<service>.onrender.com/api/health` → `{"status": "ok"}`.
On the free plan the service sleeps after 15 idle minutes and the next request
takes about a minute.

### 4. Frontend on Vercel
Add New → Project → import this repository → **Root Directory: `frontend`** →
environment variable `NEXT_PUBLIC_API_BASE=https://<service>.onrender.com`
(no trailing slash) → Deploy. The build fails on purpose if the variable is
missing. Then put the Vercel production URL into Render's
`CORS_ALLOWED_ORIGINS` (Render redeploys by itself).

### 5. First organisation
Open the Vercel URL → **Create organisation** → paste the organisation's
Postgres connection string: any Neon string, or Supabase's *Session pooler*
string (`…pooler.supabase.com:5432`). Supabase's direct `db.<ref>.supabase.co`
host is IPv6-only and Render can't reach it — FundVault tells you so if you try.
Invite people from the user menu → **Invite members**.

Receipt storage (any S3-compatible bucket) and AI receipt extraction (any
OpenAI-compatible provider) are optional; the Owner sets them up under
**Organisation settings**.

### Known limits
- Rate limits count requests per client IP as the API sees it. Behind Render's
  proxy that may be the proxy's address, so limits could be shared between users;
  check one request's `X-Forwarded-For` after deploying before relying on them.
- Rate-limit counters live in each worker's memory and reset when it restarts.
  `render.yaml` restarts each gunicorn worker every ~500 requests
  (`--max-requests`), which also resets its rate-limit counters.

## Troubleshooting

### Port already in use

If port 8000 or 3001 is already in use, change the port in the relevant npm
script, or stop the process using it.

### Backend can't reach Postgres

Make sure `docker compose up -d` succeeded (`docker compose ps`) and that
`backend/.env`'s `DATABASE_URL` / `DEV_TENANT_DATABASE_URL` match the ports in
`docker-compose.yml` (5433 and 5434 by default).

### Dependency issues

```bash
npm cache clean --force
rm -rf node_modules frontend/node_modules
npm run install:all
```

Note: this repo intentionally keeps one lockfile at the root for workspace
tooling and one inside `frontend/` for the Next.js app. The frontend is
pinned to its own folder in `frontend/next.config.js`, so Next.js will not
treat the root lockfile as the app root.

### AI receipt extraction failing

Extraction is per-organisation, not environment-configured. Check that an
Owner has added a working provider under the org's settings page, and that
the key hasn't been rotated on the provider's side. A failed extraction shows
its error and does not auto-retry.

### Next.js build issues

```bash
cd frontend
npm cache clean --force
rm -rf node_modules .next
npm install
npm run build
```

## License

This project is licensed under the MIT License — see the LICENSE file for
details.

## Authors

- **Arachno-Sapien - Syed Junaid K**

## Contact & support

For issues and questions, please create an issue in the repository.
