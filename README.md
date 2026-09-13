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
├── backend/                         # Django REST API
│   ├── apps/
│   │   ├── accounts/                # Users, login, roles, admin member management
│   │   ├── common/                  # JWT auth, audit logging, shared helpers
│   │   ├── orgs/                    # Control plane: org provisioning, join codes,
│   │   │   │                        # per-org connection routing, org settings
│   │   └── ledger/                  # Funds, transactions, receipts, AI extraction
│   ├── fundvault_backend/
│   │   ├── settings.py              # Local/dev settings
│   │   ├── settings_production.py   # Production settings (refuses to boot on defaults)
│   │   └── urls.py
│   ├── manage.py
│   └── requirements.txt
├── frontend/                        # Next.js web application
│   ├── src/
│   ├── vercel.json
│   └── .env.example
├── docker-compose.yml                # Local control-plane + dev-tenant Postgres
├── render.yaml                       # Render deployment for the backend + control-plane DB
├── API_DOCUMENTATION.md              # Complete API reference
├── install.bat                       # Windows installation script
├── run.bat                           # Windows application launcher
└── README.md                         # This file
```

### Tech stack

**Backend:** Django 5.2, Django REST Framework, JWT auth (PyJWT), bcrypt password hashing,
`cryptography` (Fernet) for encrypting stored org credentials, django-cors-headers,
psycopg 3 (Postgres only — this is a Postgres-only application, no SQLite), boto3
for S3-compatible storage, `openai` client + `google-genai` for AI receipt
extraction, Pillow for image processing, gunicorn + whitenoise for production.

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

Prerequisites: Python 3.11+, Node.js 18+, Docker (for local Postgres), Git.

1. **Start local Postgres** — one database for the control plane, one for a
   development tenant:

   ```bash
   docker compose up -d
   ```

2. **Configure the backend** — copy the example env file and fill it in:

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

3. **Install dependencies:**

   ```bash
   npm run install:all
   ```

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

AI keys and receipt storage credentials are **not** environment variables —
each organisation configures its own from its org settings page after
signing in (see [Bring your own](#bring-your-own)).

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
  (Supabase port 6543) — their direct-connection limits are too low for
  several active orgs sharing the process.
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

FundVault deploys as two independent services:

- **Backend + control-plane database → [Render](https://render.com).**
  `render.yaml` defines a Python web service (`gunicorn`, `settings_production`)
  and a managed Postgres database for the control plane. Render generates
  `DJANGO_SECRET_KEY` and `JWT_SECRET` for you; `FUNDVAULT_SECRET_KEY` you
  must generate yourself (same command as above) and paste into Render's
  environment — **it must never change once an organisation exists**, since
  every stored connection string, storage config, and AI key is encrypted
  under it. You'll also need to set `DJANGO_ALLOWED_HOSTS` and
  `CORS_ALLOWED_ORIGINS` to your actual Render/Vercel hostnames.
  `settings_production.py` refuses to boot if any required secret is missing
  or left at its development default.
- **Frontend → [Vercel](https://vercel.com).** `frontend/vercel.json` sets the
  Next.js build/install commands and security headers. Point
  `NEXT_PUBLIC_API_BASE` (see `frontend/.env.example`) at your deployed
  Render backend's URL.

Deploying is: push the backend to Render (it reads `render.yaml`), push the
frontend to Vercel with `NEXT_PUBLIC_API_BASE` set to the Render service's
URL, and set the Render env vars above. There's no live instance to point at
here — the steps above are how to stand up your own.

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
