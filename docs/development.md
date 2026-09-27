# Development guide

## Prerequisites

- Python 3.11+ (`python` must resolve to 3.11+; activate a virtualenv first if your system Python is older)
- Node.js 20.9+
- Docker (for local Postgres)
- Git

## Local setup

### 1. Start the local databases

```bash
docker compose up -d --wait
```

`--wait` returns once both containers pass their `pg_isready` healthcheck:

- control plane at `127.0.0.1:5433` (database `fundvault_control`)
- development tenant at `127.0.0.1:5434` (database `fundvault_tenant_dev`)

### 2. Install dependencies

```bash
npm run install:all
```

Installs the root npm packages (`concurrently`), the frontend's packages and `backend/requirements.txt`.

### 3. Configure the backend

```bash
cp backend/.env.example backend/.env
```

Generate `FUNDVAULT_SECRET_KEY`. It encrypts every org's stored connection string, storage
credentials and AI keys; losing or changing it makes them unrecoverable.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste it into `backend/.env` as `FUNDVAULT_SECRET_KEY=...`. The other defaults match
`docker-compose.yml`.

### 4. Migrate the control plane

```bash
python backend/manage.py migrate --database=default
```

Only the control plane is migrated here. An organisation's own database is migrated when the
organisation is created (and on deploy by `manage.py migrate_tenants`).

### 5. Run it

```bash
npm run dev
```

Starts the API at `http://127.0.0.1:8000` and the web app at `http://localhost:3001`.

### 6. Create your first organisation

Open `http://localhost:3001`, choose **Create an organisation**, and paste the dev tenant's
connection string:

```text
postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev
```

Use `127.0.0.1`, not `localhost`. The SSRF guard refuses private and loopback addresses; in
development (`DJANGO_DEBUG=true`) it allows exactly `127.0.0.1:5433` and `127.0.0.1:5434`
(`FUNDVAULT_TENANT_HOST_ALLOWLIST` in `backend/fundvault_backend/settings.py`), matched by
address, not hostname.

### Windows

Run `install.bat` once and `run.bat` every time after. `run.bat` starts the Docker databases
(`docker compose up -d --wait`), migrates the control plane, opens the browser and runs
`npm run dev`.

## Root npm scripts

| Script | What it does |
|---|---|
| `npm run dev` | API and web app together (`concurrently`) |
| `npm run dev:backend` / `dev:frontend` | one side only |
| `npm run check:backend` | `manage.py check` |
| `npm run build` | backend check, then the Next.js production build |
| `npm run install:all` | all npm and pip dependencies |

## Tests

### Backend

With the dev databases up:

```bash
cd backend
python manage.py test --settings=fundvault_backend.settings_test
```

One module: `python manage.py test tests.test_permissions --settings=fundvault_backend.settings_test`.

`fundvault_backend/test_runner.py` (`IsolatedDatabaseRunner`) keeps tests off real data:

1. every database alias, including tenant aliases registered at runtime, gets a private
   `test_<name>` copy;
2. after setup, `apps.orgs.connections.build_config` is redirected so any config rebuilt from a
   URL points at the test copy (which is why code must call it through the module,
   `tenant_connections.build_config`);
3. for the whole run, `psycopg.connect` refuses any non-test database on the configured servers
   and raises `RealDatabaseAccess`.

To run two test suites at the same time (two terminals, two agents), give each its own
database names with `FUNDVAULT_TEST_DB_SUFFIX`:

```bash
FUNDVAULT_TEST_DB_SUFFIX=_alice python manage.py test --settings=fundvault_backend.settings_test
```

The runner supports serial runs only; `--parallel` workers would not inherit its patches.

### Frontend

```bash
cd frontend
npx eslint src      # lint
npm run build       # production build
```

If results differ from CI, reinstall exactly what the lockfile pins with `npm ci`.

## Conventions

### Backend

- **Views** are plain Django function views returning `JsonResponse`; there is no REST framework.
- **Errors** use `json_error(message, status)` from `apps/common/utils.py`, which returns `{"error": message}`.
- **Request bodies** go through `parse_body(request)` (same module).
- **Audit** every user-visible write with `add_audit(user_id, action, entity_type, entity_id, details)` from `apps/common/audit.py`, inside the same atomic block as the write.
- **Tenant writes** use `transaction.atomic(using=current_org_alias())`. Tenant models (`accounts`, `ledger`) are routed by `TenantRouter` (`apps/orgs/router.py`) to the alias `OrgContextMiddleware` sets for the request; outside a request, wrap tenant queries in `org_context(alias)`.
- **Money writes** lock the fund row first (`lock_fund` in `apps/ledger/services.py`).
- **Permissions** are checked with `require(user, Action.X)` / `can(...)` from `apps/accounts/permissions.py`. `frontend/src/lib/permissions.js` mirrors that table and must be updated with it.
- **Tests** go in `backend/tests/`, one module per feature area, reusing the helpers in `tests/support.py` (`ORG_ALIAS`, `TENANT_URL`, `OrgTestMixin`).

### Frontend

- **API calls** go through `apiRequest` in `lib/api.js` (inside the app shell, through `authedRequest`, which signs the user out on a 401).
- **Permission checks** use `can(currentUser, ACTIONS.X)` from `lib/permissions.js` to hide buttons the API would reject.
- **Dates:** the server stores UTC. Fill `datetime-local` inputs with `toLocalInput(value)` (or `nowInput()`) from `lib/format.js`; convert back with `new Date(input).toISOString()`.

## Troubleshooting

### Port already in use

Stop the process holding port 8000, 3001, 5433 or 5434, or change the port in the npm script or
`docker-compose.yml`. If 5433/5434 are held by containers from another compose project (for
example an old worktree), stop that project first.

### Backend can't reach Postgres

Check `docker compose ps` shows both services healthy, and that `DATABASE_URL` and
`DEV_TENANT_DATABASE_URL` in `backend/.env` match the ports in `docker-compose.yml`.

### Dependency issues

```bash
npm cache clean --force
rm -rf node_modules frontend/node_modules
npm run install:all
```

The repo keeps one lockfile at the root (for `concurrently`) and one in `frontend/`. The
frontend is pinned to its own folder in `frontend/next.config.js` (`turbopack.root`), so Next.js
does not treat the root lockfile as the app root.

### AI receipt extraction failing

Extraction is configured per organisation, not by environment variables. Check that an Owner
has added a working provider under **Organisation settings** and that the key hasn't been
rotated. A failed extraction shows its error and does not retry.

### Next.js build issues

```bash
cd frontend
rm -rf .next
npm ci
npm run build
```
