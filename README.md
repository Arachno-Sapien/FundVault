# FundVault - Fund Management System

FundVault is a multi-tenant fund/ledger management platform. Each organisation
brings its own Postgres database, invites members with roles, and supplies its
own object storage and AI provider credentials — there is no shared tenant
database and no server-wide API keys. A small control-plane database (run by
the operator) tracks organisations, join codes, and which orgs an email
belongs to; every organisation's actual ledger data lives only in the
Postgres it connected.

## Features

- **Multi-tenant by database** — each org supplies its own `postgres://` connection; there is no shared tenant table
- **Organisation onboarding** — create an org (validates the connection, migrates it, makes you Owner) or join one with a code
- **Role-based access control** — Owner / Admin / Member / Viewer, see [Roles](#roles) below
- **Fund management** — create, archive, merge, and manage multiple fund accounts per org
- **Ledger management** — transactions with running balances, void/edit, and an approval workflow for Member-created transactions over a threshold
- **Receipts** — upload a receipt image to an org's own S3-compatible bucket; read access is via a signed URL valid for one hour
- **AI-powered receipt extraction** — any OpenAI-compatible endpoint (NVIDIA NIM, OpenRouter, Groq, a local vLLM, ...) or Google Gemini, configured per organisation with an optional fallback provider
- **Audit logging** — a trail of user actions per organisation
- **Trash management** — soft delete with recovery capability
- **Recurring transactions** — scheduled transactions processed on demand
- **Analytics** — dashboard totals across an org's funds

## Tech Stack

**Backend:** Django 5.2 (plain function views), JWT auth, bcrypt password hashing, `cryptography` for encrypting stored org credentials, django-cors-headers, psycopg 3 (Postgres only), boto3 for S3-compatible storage, `openai` and `google-genai` for AI receipt extraction, Pillow for image processing, gunicorn for production.

**Frontend:** Next.js 16, React 19, Chart.js, jsPDF for PDF export.

## Quick Start

Prerequisites: Python 3.11+, Node.js 20.9+, Docker, Git.

```bash
# Start local Postgres
docker compose up -d --wait

# Install dependencies
npm run install:all

# Configure the backend
cp backend/.env.example backend/.env
# Generate FUNDVAULT_SECRET_KEY and paste it into backend/.env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Migrate the control plane
python backend/manage.py migrate --database=default

# Run the app
npm run dev
```

Open `http://localhost:3001` and create an organisation with the dev tenant's connection string:
```
postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev
```

Use `127.0.0.1`, not `localhost`: in development the SSRF guard allows exactly
`127.0.0.1:5433` and `127.0.0.1:5434`.

For full setup instructions, see [docs/development.md](docs/development.md).

## Project Structure

```text
FundVault/
├── docs/
│   ├── README.md                 # Documentation index
│   ├── development.md            # Local setup, tests, conventions
│   ├── deployment.md             # Deploy to Render/Vercel
│   ├── api.md                    # HTTP API reference
│   ├── architecture.md           # System design
│   ├── backend.md                # Django app reference
│   ├── frontend.md               # Next.js app guide
│   ├── codebase-audit-*.md       # Audit results
│   └── history/                  # Design spec and plans
├── backend/
│   ├── apps/
│   │   ├── accounts/             # Users, login, roles, member management
│   │   ├── common/               # JWT auth, audit logging, shared helpers
│   │   ├── orgs/                 # Control plane, org provisioning, routing
│   │   └── ledger/               # Funds, transactions, receipts, AI extraction
│   ├── fundvault_backend/
│   │   ├── settings.py           # Dev settings
│   │   ├── settings_production.py # Production settings (refuses unsafe defaults)
│   │   ├── settings_test.py      # Test settings with isolated databases
│   │   ├── test_runner.py        # Private per-alias test databases
│   │   └── urls.py
│   ├── tests/                    # Test suite (one module per feature area)
│   ├── manage.py
│   ├── build.sh                  # Render build script
│   ├── .env.example              # Environment variables template
│   └── requirements.txt
├── frontend/
│   ├── src/
│   ├── next.config.js
│   ├── vercel.json
│   └── .env.example
├── docker-compose.yml            # Local control-plane + dev-tenant Postgres
├── render.yaml                   # Render Blueprint for backend
├── package.json                  # Root workspace scripts
├── install.bat                   # Windows installation
├── run.bat                       # Windows launcher
├── LICENSE
└── README.md
```

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

## Bring Your Own

FundVault has no shared infrastructure — you supply:

- **A Postgres database** for each organisation (Supabase, Neon, Railway, your own, etc.)
  - Use Supabase's **Session pooler** string, not the direct connection (IPv6-only)
- **S3-compatible object storage** for receipt images (Supabase Storage, Cloudflare R2, MinIO, AWS S3, etc.)
  - Optional; without it, receipt upload is disabled
- **An AI provider** for receipt extraction (any OpenAI-compatible endpoint or Google Gemini)
  - Optional; without it, manual transaction entry works normally
  - Configurable per-organisation with an optional fallback provider

All three are configured per-organisation from the org settings page.

## Documentation

The [documentation index](docs/README.md) lists every document.

| Document | Purpose |
|---|---|
| [docs/development.md](docs/development.md) | Local setup, running tests, project conventions |
| [docs/deployment.md](docs/deployment.md) | Deploying to Render and Vercel |
| [docs/api.md](docs/api.md) | HTTP API reference for every endpoint |
| [docs/architecture.md](docs/architecture.md) | System design and security model |
| [docs/backend.md](docs/backend.md) | Django app structure and invariants |
| [docs/frontend.md](docs/frontend.md) | Next.js app structure and conventions |
| [docs/codebase-audit-2026-09-27.md](docs/codebase-audit-2026-09-27.md) | 2026-09-27 audit: fixes, deferred items, unnecessary files |
| [docs/history/specs/2026-09-09-fundvault-multitenant-design.md](docs/history/specs/2026-09-09-fundvault-multitenant-design.md) | Multi-tenant design spec |

## License

This project is licensed under the MIT License — see the LICENSE file for
details.

## Authors

- **Arachno-Sapien - Syed Junaid K**

## Contact & Support

For issues and questions, please create an issue in the repository.
