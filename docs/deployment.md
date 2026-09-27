# Deployment Guide

FundVault runs as two services plus databases you own:

| Piece | Where | Notes |
|---|---|---|
| API (Django + gunicorn) | Render web service from `render.yaml` | each deploy runs `backend/build.sh` |
| Control-plane Postgres | any Postgres that doesn't expire — e.g. a Neon project, or a paid Render Postgres | holds organisations, join codes, the email index, and every org's encrypted connection string |
| Frontend (Next.js) | Vercel, Root Directory `frontend` | a static page that talks to the API |
| Each organisation's data | that organisation's own Postgres (Neon, or Supabase's **Session pooler** string) | entered when the organisation is created |

## 1. Control-plane database

Create a Postgres database that **will not expire**. Render's free Postgres is deleted 30 days after creation, which would orphan every organisation; use Neon, a paid Render Postgres, or your own server.

Copy its connection string (keep `?sslmode=require` if present).

## 2. Encryption key

Generate a `FUNDVAULT_SECRET_KEY` that encrypts every stored organisation credential:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**This key must never change once organisations exist.** If it changes or is lost, every organisation becomes unreachable. Store it in a password manager immediately. The API refuses to start with a missing or malformed key.

## 3. Deploy the backend to Render

1. Go to Render → **New** → **Blueprint**
2. Choose this repository (branch `main`)
3. Render asks for environment variables:
   - `DATABASE_URL` — the control-plane connection string from step 1
   - `FUNDVAULT_SECRET_KEY` — the key from step 2
   - `CORS_ALLOWED_ORIGINS` — your Vercel URL, e.g. `https://fundvault.vercel.app`
     - Must be a full URL with a scheme; `corsheaders` system check fails otherwise
     - A placeholder like `https://example.com` is fine now; fix it to the real URL after deploying the frontend
   - `DJANGO_SECRET_KEY` and `JWT_SECRET` are generated for you
   - `DJANGO_ALLOWED_HOSTS` — only set if you add a custom domain (the service's `*.onrender.com` hostname is allowed automatically)

**Build behaviour:**

Every deploy runs `backend/build.sh`, which:
1. Installs dependencies
2. Migrates the control plane (`python manage.py migrate --database=default`)
3. Migrates every registered organisation's database (`python manage.py migrate_tenants`)

An organisation whose database can't be reached is logged as `FAILED` in the build log without failing the deploy — it stays on the old schema until a later deploy reaches it.

**Health check:**

```bash
curl https://<service>.onrender.com/api/health
# → {"status": "ok"}
```

On the free plan, the service sleeps after 15 idle minutes; the next request takes about a minute to wake it.

## 4. Deploy the frontend to Vercel

1. Go to Vercel → **Add New** → **Project** → import this repository
2. Set **Root Directory: `frontend`**
3. Add environment variable `NEXT_PUBLIC_API_BASE` = your backend URL (e.g. `https://<service>.onrender.com`, no trailing slash)
   - The build fails on purpose if this is missing or ends with a slash
4. Deploy

After the frontend deploys, update Render's `CORS_ALLOWED_ORIGINS` to the Vercel production URL. Render will redeploy automatically.

## 5. Create your first organisation

1. Open the Vercel URL
2. Click **Create organisation**
3. Paste the organisation's Postgres connection string (any Neon string, or Supabase's **Session pooler** string)
   - **Important:** Use Supabase's pooled connection (`…pooler.supabase.com:5432`), not the direct connection (`db.<ref>.supabase.co`)
   - The direct connection is IPv6-only and Render can't reach it; FundVault tells you if you try
4. Create a username/email/password — you become the Owner

Receipt storage (S3-compatible bucket) and AI receipt extraction (OpenAI-compatible provider) are optional. The Owner sets them up later under **Organisation settings**.

## Important notes

### Production boot checks

`backend/fundvault_backend/settings_production.py` refuses to start if:
- `DJANGO_SECRET_KEY`, `JWT_SECRET`, `FUNDVAULT_SECRET_KEY`, `CORS_ALLOWED_ORIGINS` or `DATABASE_URL` is missing
- any of those secrets is still a development default (including the values shipped in `backend/.env.example`)
- `FUNDVAULT_SECRET_KEY` is not a valid Fernet key
- `DJANGO_ALLOWED_HOSTS` is missing when not on Render, or is `*`

### Control-plane database stability

The control-plane database must **never expire**. Losing it orphans every organisation whose data cannot be accessed:
- Render free Postgres expires after 30 days — do not use it
- Neon, paid Render Postgres, or your own server are suitable
- Once set, do not change the connection string without notifying users

### FUNDVAULT_SECRET_KEY immutability

Every organisation's database credentials (connection string, storage keys, AI keys) are encrypted with `FUNDVAULT_SECRET_KEY`. If the key changes:
- All stored credentials become unrecoverable
- Organisations cannot connect to their databases
- The application must be rolled back or credentials re-entered

### Tenant database migrations

`python manage.py migrate_tenants` (run every deploy) brings each registered organisation's database up to the current schema. It:
- Iterates every org in the control plane
- Connects to each org's database
- Runs pending migrations
- Logs failures but does not fail the overall build

An unreachable org stays on its current schema until it becomes reachable again (e.g., if the database was temporarily down).

### Rate limiting

Rate limits count requests per client IP as the API sees it. Behind Render's proxy, this may be the proxy's address, so limits could be shared between users. Check one request's `X-Forwarded-For` header after deploying before relying on them.

Rate-limit counters live in each gunicorn worker's memory. `render.yaml` restarts each worker every ~500 requests (`--max-requests`), which also resets its rate-limit counters.

### SSRF allowlist in production

The SSRF guard in `apps/orgs/provisioning.py` blocks private/loopback addresses by default. In production, when creating or updating an organisation's database connection, the host is:
- Resolved via DNS once and pinned to the IP during the initial connection test
- The pinned IP is discarded after migration, so subsequent runtime connections re-resolve DNS
- Private and loopback addresses are rejected (production has no exceptions; development adds `127.0.0.1:5433` and `:5434` for local testing)

### Static files

Production does not serve static files — whitenoise and Django staticfiles were removed. The frontend is served from Vercel; there is no `/static/` path or `STATIC_URL`.
