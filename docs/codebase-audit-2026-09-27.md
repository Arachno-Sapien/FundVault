# Codebase audit — 2026-09-27

A full read of the backend, frontend, tests, configuration and docs, done by twelve independent
readers (one per subsystem, plus cross-cutting API-contract, security and repo-hygiene passes).
Findings reported by several readers independently, or confirmed by reading the code, were fixed
when the fix preserved existing behaviour. Everything else is listed under
[Deliberately not changed](#deliberately-not-changed) with the reason.

## Verification

| Check | Before | After |
|---|---|---|
| Backend tests (`manage.py test --settings=fundvault_backend.settings_test`) | 405 passed | 403 passed (25 duplicate test runs removed, 23 new tests added) |
| `manage.py check` (dev and production settings) | clean | clean |
| Frontend `npm run build` | passes | passes |
| Frontend `eslint src` | 0 errors, 10 warnings | 0 errors, 9 warnings |

## Fixes

### Security

| Area | Problem | Fix |
|---|---|---|
| `orgs/provisioning.py` | A connection string whose host does not resolve (including a libpq multi-host string such as `nohost.invalid,127.0.0.1`) skipped the internal-address check, reachable without authentication through `/api/orgs/validate-connection` and `/api/orgs/create`. | Resolution failure now fails closed ("Host not found"), and the probe is always pinned to the validated IP. `blocked_https_url_message` fails closed the same way. |
| `orgs/provisioning.py` | DNS rebinding: the probe was pinned to the validated IP but `migrate` re-resolved the host. | `migrate` during create and DB repoint is pinned to the validated IP; the pin is removed afterwards so runtime reconnects still follow provider IP changes. |
| `ledger/receipt_extractor.py` | The OpenAI SDK follows redirects, so an org-supplied `base_url` could bounce requests to internal addresses, with the response body echoed back in errors. | OpenAI-compatible clients are built with `follow_redirects=False`. |
| `ledger/receipt_extractor.py` | A small PNG with huge dimensions (decompression bomb) could exhaust a worker's memory. | Images over 25 MP are refused before decoding; JPEGs decode at reduced scale. |
| `ledger/views.py` | Any Member could overwrite the receipt image of any transaction (others', approved, voided, archived). | Only the creator, or an Admin/Owner, may upload; archived funds are refused. |
| `accounts/views.py` | Login matched username before email, so a member could set their username to a colleague's email and lock them out of email login. | Both matches are tried and the account whose password verifies wins. |
| `accounts/views.py` | `profile_image` was stored unchecked (any type, up to the request limit) and returned in every member list. | Only `data:image/…` strings up to 300 000 characters are accepted. |
| `settings_production.py` | The production secret guard accepted the `JWT_SECRET` value shipped in `.env.example`. | That value is now refused at boot. |
| `settings_production.py` | `DATA_UPLOAD_MAX_MEMORY_SIZE = 6 MB` did not cap receipts (Django excludes file uploads) and only raised the JSON body limit. | Removed; the views enforce the 5 MB receipt cap. |

### Correctness

| Area | Problem | Fix |
|---|---|---|
| Frontend dates | Date inputs were filled with UTC wall-clock time and parsed back as local time, so new transactions were stored early by the UTC offset (5h30 in IST) and every edit moved the transaction again. | `lib/format.js` `toLocalInput`; the edit form sends `date` only when changed; export ranges, ledger filters, recurring start date and dashboard months use local dates. |
| `accounts/views.py` | Every edit of the Owner's row (rename, email) failed with "Use transfer-ownership…". | Role checks run only when the role changes; only the Owner may edit the Owner's row. Saves write only changed fields. |
| `accounts/views.py` | A wrong current password returned 401, which the frontend treats as an expired session and signs the user out. | Returns 400. |
| `orgs/views.py` | Saving the AI provider from the settings modal silently deleted a configured fallback provider. | An omitted `fallback` keeps the stored one; `"fallback": null` still clears it. |
| `ledger/views.py` | `PUT /transactions/<id>` returned the pre-edit `running_balance`. | Refreshed before responding. |
| `ledger/views.py` | Fund soft-delete, trash restore and the money endpoints wrote their audit rows outside the transaction; a failure could leave a fund in neither the list nor the trash, or a committed write with a 5xx response. | Those writes and their audit rows share one atomic block. |
| `ledger/views.py` | Void and delete-voided re-checked state only before taking the lock, so a double submit double-applied. | Conditional update/delete under the lock. |
| `ledger/views.py` | JSON `null` in text fields was stored as the string `"None"` and passed required-field checks. | Null reads as empty; an explicit null clears optional fields on edit. |
| `ledger/views.py` | Fund detail ordered by date only, so same-minute rows came back in arbitrary order relative to their running balances. | Both ledger endpoints order by `(-date, -created_at, -id)`. |
| `ledger/views.py` | Permanently purging a fund and deleting a recurring rule left no audit entry; the restore entry pointed at a deleted row id. | Audited, and the restore entry names the fund. |
| `ledger/views.py` | `POST /extract-receipt` reported failures as HTTP 200. | 502 (the UI message is unchanged). |
| `ledger/receipt_extractor.py` | AI calls had no timeout (OpenAI: 600 s with retries; Gemini: none), so gunicorn killed the worker at 60 s before the fallback provider could run. | 25 s per provider, no retries. |
| `ledger/receipt_extractor.py` | Portrait phone photos were stored rotated (EXIF ignored); 16-bit and other image modes failed to encode; a `None` Gemini response crashed. | Fixed. |
| `ledger/storage.py`, `receipt_extractor.py` | Non-string config values produced a 500 instead of a 400. | Treated as missing. |
| Frontend | Dashboard charts included archived funds, double-counting merged money and disagreeing with the summary chips. | Archived funds excluded, matching the backend overview. |
| Frontend | "Record Transaction" could be double-clicked into two postings. | Disabled while the request is in flight. |
| Frontend | The role used to show or hide buttons came from `localStorage` and never refreshed. | Refreshed from `GET /api/auth/me` on load. |
| Frontend | An open modal survived sign-out and reappeared, broken, after the next sign-in. | Sign-out closes all modals. |
| Frontend | Keyboard shortcuts read stale state, ignored permissions and archived funds, fired with Ctrl/Cmd, and Esc did nothing inside a form field. | Fixed. |
| Frontend | Buttons the API always rejects were shown: trash actions to Members/Viewers, Owner-row actions to Admins, "click + New Database" to users without that button. | Hidden to match the API. |
| Frontend | "Clear Cache" left the server session alive and the org in `localStorage`. | Signs out properly. |
| Frontend | Org settings kept typed storage/AI secrets in state after saving. | Cleared on save. |
| Tests | `test_no_legacy_schema_hacks` matched its own source on Linux/macOS (Windows-only path split). | Uses `pathlib`, scans project code only. |
| `run.bat`, `docker-compose.yml` | `run.bat` migrated before Postgres was ready on a fresh volume. | Healthchecks plus `docker compose up -d --wait`. |

### Accessibility

Fund cards and the user menu are keyboard-operable; modals have dialog semantics and a labelled
close button.

### Performance and simplification

- Removed the unused static-files stack (WhiteNoise, `django.contrib.staticfiles`, `collectstatic`): no installed app ships static files and the frontend is on Vercel.
- Archive and fund edit no longer re-fetch the open fund's ledger twice.
- Removed 25 duplicate test executions (a test class inherited by two others, and spot-checks subsumed by the full capability matrix).
- Dead code removed: `User.is_authenticated`, `JoinCode.is_usable`/`default_expiry`, the unused `mime_type` parameter, `export { API_BASE }`, an unused prop and imports, an unused hidden input, no-op `useCallback`s, and an inline `<style>` tag.
- Duplication removed: password hashing (now `common.auth.hash_password`/`check_password`), transaction lookup, fund-field parsing and upload checks in `ledger/views.py`, the two multipart helpers in `lib/api.js`, export filtering, blank-form literals and tab navigation in the frontend.
- Join-code minting rules now derive from the capability table (`Action.MINT_ADMIN_CODE`) instead of a parallel role map.

## Deliberately not changed

| Item | Why it was left |
|---|---|
| Rate limits key on `REMOTE_ADDR`; behind Render's proxy all clients may share one bucket. | The fix depends on the exact `X-Forwarded-For` shape Render sends, which needs one live request to confirm. Check it after the first deploy (see [deployment.md](./deployment.md)). |
| Every fund-level refresh downloads every fund's full ledger (one request per fund). | Fixing it needs a transaction count on `GET /api/databases` plus lazy loading on the Dashboard: a feature change, not a fix. |
| `process_due_recurring` locks the rule before the fund, the opposite order from purge/merge, so a deadlock is possible under a rare race. | A lock-order change in money code; the collision needs a purge or merge racing the per-login recurring run. Worth doing with a dedicated concurrency test. |
| Duplicate index on `sessions.user_id`; unused `Session.last_activity` column. | Both need a tenant migration rolled out to every org database; the benefit is small. |
| Flattened `elecId`/`cheque*` keys in transaction responses duplicate `mode_data`. | Documented API surface; external callers may rely on them. |
| `@csrf_exempt` on views is a no-op (`CsrfViewMiddleware` is not installed). | Harmless, and keeps the views safe if CSRF middleware is ever added. |
| Test fixtures hand-roll the org setup that `tests/support.py` provides; `DEAD_URL` tests exercise the SSRF branch rather than a real connection failure; no tests yet for saving storage settings or for org-deletion side effects. | Test-suite refactors worth doing, but broad and unrelated to behaviour. |
| Form labels are not tied to their inputs (`htmlFor`/`id`); sortable table headers are not keyboard-reachable. | Touches every form; tracked here as the next accessibility step. |
| The transaction amount column has `txn-amount credit/debit` classes with no CSS rule. | Colouring it is a visual design decision. |
| Remaining lint warnings: `setState` in effects, `<img>` instead of `next/image`, two `exhaustive-deps` warnings. | Behaviour-neutral; `next/image` does not suit data-URL avatars and receipts. |
| The org-deletion audit row is written only to the org's (now orphaned) tenant database. | Documented in code; recording it in the control plane would need a new table. |

## File organisation

- `API_DOCUMENTATION.md` (repo root) → [`docs/api.md`](./api.md)
- `docs/superpowers/specs`, `docs/superpowers/plans` → [`docs/history/`](./history/) (historical planning documents)
- New: [`docs/README.md`](./README.md), [architecture.md](./architecture.md), [backend.md](./backend.md), [frontend.md](./frontend.md), [development.md](./development.md), [deployment.md](./deployment.md), this report.

## Unnecessary files and folders

None of these are tracked by git; all are local to this machine. Nothing was deleted by the audit.

| Path | Size | What it is | Safe to remove? |
|---|---|---|---|
| `.claude/worktrees/` (4 worktrees) | 644 MB | Old Claude Code worktrees. All four branches (`claude/awesome-bhabha-e1aca3`, `claude/happy-visvesvaraya-0b3fac`, `claude/hungry-booth-0ed257`, `worktree-fundvault-multitenant`) are fully merged into `main` and have no uncommitted changes. | Yes, with `git worktree remove <path>` then `git branch -d <branch>`. The Docker containers currently serving ports 5433/5434 belong to the `fundvault-multitenant` compose project; removing the worktree does not stop them or delete their volumes. |
| `data/fundvault.db` | 0 bytes | Empty leftover from the old SQLite version; the app is Postgres-only. | Yes |
| `frontend/.next/` | 166 MB | Next.js build output, regenerated by `npm run dev`/`build`. | Yes (regenerates) |
| `backend/**/__pycache__/` | small | Python bytecode caches. | Yes (regenerates) |
| `.serena/` | 18 KB | Cache of the Serena code-navigation tool. | Yes, if you don't use Serena |
| Docker containers `fundvault-controlplane-1`, `fundvault-tenant_dev-1` (state "Created") and volumes `fundvault_controlplane_data`, `fundvault_tenant_dev_data` | empty | Created by this audit's `docker compose up` from `main`, which could not start because the `fundvault-multitenant` project already holds the ports. | Yes: `docker compose down -v` from the repo root (touches only these empty ones) |

Tracked files that look removable but are needed: root `package.json`/`package-lock.json`
(provides `concurrently` for `npm run dev`), `frontend/AGENTS.md`/`CLAUDE.md` (re-created by
`next dev`), `install.bat`/`run.bat` (Windows setup), `docs/history/` (design record).

Local environment note: `frontend/node_modules` was out of sync with the committed lockfile
(Next 16.2.6 installed vs 16.3.6 locked, ESLint missing); `npm ci` fixed it. If a build or lint
behaves differently from CI, run `npm ci` in `frontend/`.
