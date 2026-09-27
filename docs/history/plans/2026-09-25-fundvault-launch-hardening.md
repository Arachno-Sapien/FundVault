# FundVault Launch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the fixes found by the deploy audit and the architecture/performance review: correct money handling, cheaper requests, a usable invite flow, cleaner tests and accurate deploy docs. Then merge `worktree-fundvault-multitenant` into `main` and push it to GitHub, ready for Render and Vercel.

**Architecture:** No new services or layers. The ledger's money rules move into `apps/ledger/services.py` as three small functions: `lock_fund`, `post_transaction` and the existing `recalculate_running_balances`. The views keep HTTP concerns only. The frontend gains one self-contained modal (`JoinCodesModal`) following the existing `OrgSettingsModal` pattern. Tests share one helper module instead of repeating the same setup in each module.

**Tech Stack:** Django 5.2 (plain function views, JsonResponse), Postgres 16 via psycopg 3, Next.js 16.3 / React 19, ESLint 9, gunicorn on Render, Vercel.

**Spec:** There is no single spec document. This plan argues from three verified sources:
- the deploy audit and its adversarial refutation (2026-09-23)
- the backend and frontend architecture reviews (2026-09-24), with probes run in scratch copies
- the multi-tenant design spec `docs/superpowers/specs/2026-09-09-fundvault-multitenant-design.md`, whose Global Constraints still apply

The findings relevant to each task are restated inside that task, so an executor never needs the review files.

## Global Constraints

- The org boundary is the database connection. No `org_id` column, no per-user ownership filters on tenant data.
- `TenantRouter` raises `NoOrgContext` rather than falling back. Every `transaction.atomic` touching tenant models uses `using=current_org_alias()`, and never the decorator form.
- Secrets never reach an HTTP response or a log unredacted (`_redact` helpers exist in `apps/ledger/storage.py` and `receipt_extractor.py`).
- Money stays `FloatField`, a spec constraint. Every computed amount and balance is rounded to 2 decimal places (`round(x, 2)`). A DecimalField migration is out of scope; it is the user's decision.
- User-facing currency strings use `₹`.
- API field casing stays as it is: responses mix snake_case (`grants_role`, `max_uses`) and camelCase (`requiresApproval`, `newBalance`, `maxUses` in request bodies). Do not "fix" it.
- Lock order for any money write: the fund row first (`select_for_update`), then transaction rows.
- Tests run only through the isolated runner, from `backend/`:
  `FUNDVAULT_TEST_DB_SUFFIX=_<yourtask> python manage.py test --settings=fundvault_backend.settings_test --noinput [labels]`
  It gives every alias a private test database and raises `RealDatabaseAccess` on any non-test database. Never touch `fundvault_control`, `fundvault_tenant_dev` or `fundvault_tenant_dev_orgb`.
- Commit messages: a short imperative subject; a blank line; plain prose on what was wrong and why the change fixes it; the last line exactly `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Git hygiene, since several agents share one worktree: never `git add -A`, `git add .`, `git commit -a`, `git stash`, `git reset` or a branch switch. Commit with `git add <new files>` then `git commit -m "..." -- <exact paths>`, and retry on `index.lock`.
- Ponytail: the smallest diff that fully solves the item. No speculative abstractions, no new runtime dependencies. Match the surrounding comment density.

## Environment facts every executor needs

- Worktree: `C:/Users/SYED/Documents/Junaid/FundVault/.claude/worktrees/fundvault-multitenant`, branch `worktree-fundvault-multitenant`, based on `main` (fast-forwardable).
- Dev Postgres runs in Docker: control plane `127.0.0.1:5433`, tenant server `127.0.0.1:5434`, user `fundvault`, password `devpassword`.
- Windows 11. The Bash tool is Git Bash. Python is global with all backend deps. Node 22.
- A hook may block the first Bash/Write/Edit and ask you to state facts first. State them in two lines and retry.
- The full backend suite takes about 3 minutes; run it in the background and wait.
- The frontend has no unit tests. Its gates are `npm run lint` (0 errors), `npm run build`, and a manual browser pass done by the supervisor in Task 9.

## Model assignment and supervision

| Task | What | Implementer | Why | Gate |
|---|---|---|---|---|
| 1 | Finish ledger money fixes (validation, locking, backdating) | **Sonnet 5**, high effort | Money-critical and concurrency-sensitive; partial work to audit | Opus review |
| 2 | Finish backend perf and cleanup (auth writes, admin_users, join-code gating, DRF, middleware) | **Sonnet 5** | Mostly done; auth-adjacent, needs careful verification | Opus review |
| 3 | Consolidate ledger write rules; analytics query trim; receipt 503; `_get_fund` rename | **Sonnet 5**, high effort | Refactor touches every money path | Opus review |
| 4 | Frontend: join-code manager and profile photo downscale | **Sonnet 5** | New UI feature against a real API | Opus review |
| 5 | Frontend: CSS hygiene, onboarding buttons, dead HomeView code | **Haiku 4.5** | Mechanical deletions verified by grep | Opus review |
| 6 | Shared tenant-test helper, converting test modules | **Sonnet 5** | Wide but mechanical; test counts must not change | Opus review |
| 7 | Repo tooling and docs (README, API docs, root scripts) | **Sonnet 5** | Accuracy against the final code matters | Opus review |
| 8 | Final whole-branch review, two lenses | **Opus 5.5** ×2 | Supervisory | Supervisor triage; Sonnet fixer |
| 9 | Full suite, API E2E, browser smoke, dev DB migrate, merge, push | **Haiku 4.5** (E2E run) plus supervisor | Mostly running things | Supervisor |

Each implementer task runs: **implement → Opus review → (if findings) fix by the same model → Opus re-review**. There are at most 2 fix rounds; after that, residual findings are ruled on by the supervisor.

**Waves**, grouped by file ownership (no two concurrent tasks share a file):
- Wave 1: Tasks 1, 2, 4, 5 in parallel.
- Wave 2: Task 3, after Task 1 has landed.
- Wave 3: Tasks 6 and 7 in parallel, after Tasks 1–5 have landed.
- Wave 4: Task 8, then fixes.
- Wave 5: Task 9.

## File structure (what changes, and who owns it)

| File | Task | Responsibility after this plan |
|---|---|---|
| `backend/apps/common/utils.py` | 1 | `parse_body` (always a dict), `parse_number` (finite float or None) |
| `backend/apps/ledger/services.py` | 1, then 3 | money rules: `lock_fund`, `post_transaction`, `InsufficientBalance`, `recalculate_running_balances` (locked, bulk), recurring |
| `backend/apps/ledger/views.py` | 1, then 3 | HTTP glue: parse → `require()` → service → serialize |
| `backend/tests/test_ledger_money.py` | 1 | money regression tests (already written, untracked) |
| `backend/tests/test_balance_concurrency.py` | 1 | real-endpoint concurrency tests (partially rewritten, uncommitted) |
| `backend/tests/test_ledger_services.py` | 3 (new) | tests for overview query count, receipt 503 |
| `backend/apps/common/auth.py` | 2 | no writes on authenticated reads |
| `backend/apps/accounts/views.py` | 2 | aggregate `admin_users`, role-ordered |
| `backend/apps/orgs/views.py` | 2 | join codes gated by the permissions table |
| `backend/fundvault_backend/settings.py`, `settings_production.py`, `backend/requirements.txt` | 2 | no DRF; production middleware extends the base list |
| `backend/tests/test_login_routing.py`, `test_member_management.py`, `test_join_codes.py`, `test_org_middleware.py`, `test_production_settings.py` | 2 | tests for the above (partially edited, uncommitted) |
| `frontend/src/components/modals/JoinCodesModal.jsx` | 4 (new) | list, mint, revoke and copy join codes |
| `frontend/src/components/FundVaultApp.jsx`, `modals/AppModals.jsx`, `layout/HeaderBar.jsx` | 4 | wire the modal; downscale profile photos |
| `frontend/src/app/globals.css`, `auth/CreateOrgForm.jsx`, `auth/JoinOrgForm.jsx`, `views/HomeView.jsx` | 5 | dead CSS removed; real button classes |
| `backend/tests/support.py` | 6 (new) | shared org-"o1" test setup |
| `README.md`, `API_DOCUMENTATION.md`, `package.json`, `install.bat`, `.gitignore`, `frontend/package.json` (engines only), `frontend/.env.example`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`, `data/.gitkeep` | 7 | accurate docs and cross-platform tooling |

---

### Task 1: Finish and land the ledger money fixes

**Model:** Sonnet 5, high effort. **Suffix:** `_t1`.

**Current state** (uncommitted, left by an interrupted agent; audit it, don't trust it):
- `backend/apps/common/utils.py`: `parse_body` returns `{}` for non-dict JSON; new `parse_number(value)` returns a finite float or `None`, and refuses `bool`.
- `backend/apps/ledger/views.py`:
  - thresholds are parsed with `parse_number` (400 "Thresholds must be numbers");
  - create, update and recurring amounts use `round(parse_number(x) or 0, 2)`, so `nan`/`inf`/garbage becomes 0 and hits the existing `amount <= 0` → 400;
  - balances are rounded;
  - void, update and delete-voided lock the fund first;
  - merge uses `bulk_create`.
- `backend/apps/ledger/services.py`: `recalculate_running_balances` locks the fund, uses `.only()`, rounds, and does a `bulk_update(batch_size=500)`; recurring rounds the balance.
- `backend/tests/test_ledger_money.py` (untracked): 6 tests covering garbage numbers, JSON array bodies, float drift, paise rounding, backdated create, and void query count.
- `backend/tests/test_balance_concurrency.py` (modified): rewritten toward real-endpoint threads. Unverified.

**What is still missing:** the backdated-create fix, verification, and commits.

**Files:**
- Modify: `backend/apps/ledger/views.py` (`database_transactions` POST branch, lines ~324–376)
- Modify/verify: `backend/apps/common/utils.py`, `backend/apps/ledger/services.py`, `backend/tests/test_balance_concurrency.py`
- Test: `backend/tests/test_ledger_money.py` (keep it), plus `backend/tests/test_balance_concurrency.py`

**Interfaces:**
- Produces: `apps.common.utils.parse_number(value) -> float | None`; `apps.common.utils.parse_body(request) -> dict`.
- Produces: `recalculate_running_balances(database_id) -> float`. It returns the fund's new balance, rebuilt from its approved, non-voided rows, and takes the fund row lock itself.

- [ ] **Step 1: Read the partial work.** Run `git diff -- backend/apps/common/utils.py backend/apps/ledger/services.py backend/apps/ledger/views.py backend/tests/test_balance_concurrency.py` and `cat backend/tests/test_ledger_money.py`. Note anything wrong against the Global Constraints.

- [ ] **Step 2: Run the money tests and confirm that exactly the backdated test fails.**
  Run: `FUNDVAULT_TEST_DB_SUFFIX=_t1 python manage.py test tests.test_ledger_money --settings=fundvault_backend.settings_test --noinput`
  Expected: `test_backdated_create_fixes_every_running_balance` FAILS, with rows like `[(1000, 1000), (10, 10), ...]` instead of `[(1000, 1000), (10, 1010), (10, 1020), (10, 1030)]`. The other five pass. If any other test fails, fix the partial work first.

- [ ] **Step 3: Fix backdated creates.** In `database_transactions`, inside the existing `with transaction.atomic(using=current_org_alias()):` block, replace the tail that saves the fund balance:

```python
        if not requires_approval:
            locked.balance = new_balance
            locked.save(update_fields=["balance"])
```

  with:

```python
        if not requires_approval:
            # The row above was stamped with a running balance as if it were the
            # latest entry; a backdated one isn't, so rebuild the fund's rows in
            # date order (one SELECT and no row updates when it is the latest).
            new_balance = recalculate_running_balances(database_id)
            txn.refresh_from_db(fields=["running_balance"])
```

  `recalculate_running_balances` also writes the fund's `balance`, so the explicit save is no longer needed. The response still returns `"newBalance": new_balance`.

- [ ] **Step 4: Re-run the money tests.** Same command as Step 2. Expected: 6/6 PASS.

- [ ] **Step 5: Fix fixtures that assume an unbacked balance.** Recalculation rebuilds a fund's balance from its approved rows. That is the real invariant, so a test fixture that sets `DatabaseFund.balance` with no rows behind it and then creates a transaction will now see a different balance.
  Run: `FUNDVAULT_TEST_DB_SUFFIX=_t1 python manage.py test tests.test_ledger_permissions tests.test_atomic_alias_regression tests.test_receipt_upload tests.test_recurring_concurrency --settings=fundvault_backend.settings_test --noinput`
  For each failure caused by this, change the fixture to create an opening credit row: a `TransactionFund` with `type="credit"`, the same amount, `approved=True` and `running_balance` equal to that amount. Do NOT change production code to preserve unbacked balances. If a failure has another cause, stop and report it.

- [ ] **Step 6: Verify the concurrency tests prove what they claim.** Read `tests/test_balance_concurrency.py`. It must drive the real endpoints (`/api/databases/<id>/transactions` and `/api/transactions/<id>/void`) from two threads, using `TransactionTestCase` and the barrier-before-lock pattern of `tests/test_recurring_concurrency.py`. It must include a void-vs-create race asserting `fund.balance == sum of approved non-voided rows`. Then prove it bites:
  - temporarily delete the line `DatabaseFund.objects.select_for_update().filter(id=txn.database_id).first()` from `transaction_void`;
  - run `FUNDVAULT_TEST_DB_SUFFIX=_t1 python manage.py test tests.test_balance_concurrency --settings=fundvault_backend.settings_test --noinput` several times. Expected: the void-vs-create test FAILS at least once in 5 runs. If it never fails, strengthen the test (for example, widen the window by patching `apps.ledger.views.add_audit` or `recalculate_running_balances` to sleep 0.3 s inside the transaction, as `test_recurring_concurrency.py` does) until it does;
  - restore the line with an editor and confirm `git diff` shows it back;
  - run again. Expected: PASS 5/5.

- [ ] **Step 7: Run the full suite.**
  Run: `FUNDVAULT_TEST_DB_SUFFIX=_t1 python manage.py test --settings=fundvault_backend.settings_test --noinput`
  Expected: `OK`, with 0 failures and 0 errors. Record the test count.

- [ ] **Step 8: Commit.** Preferably make two commits: (1) number validation and rounding, and (2) locking, bulk recalculation and backdated creates, each with its own tests. If separating hunks inside `views.py` is awkward, make ONE commit covering `utils.py`, `views.py`, `services.py` and both test files, with a body explaining all three bugs:
  - **non-finite input:** `float()` accepted `"nan"`/`"inf"`, which passed `amount <= 0` and turned a fund's balance (and the org's JSON) into NaN; a JSON array body 500'd every view; `0.3 - 0.1` left `0.19999999999999998`, so a later `0.2` debit was refused.
  - **void-vs-create lost update:** a void recomputed the balance without the fund lock, overwriting a concurrent create. It was reproduced at 200 against 250, and a void of the earliest of 500 rows took 511 queries, now under 30.
  - **backdated creates:** they left every later running balance wrong.

```bash
git add backend/tests/test_ledger_money.py
git commit -F - -- backend/apps/common/utils.py backend/apps/ledger/views.py backend/apps/ledger/services.py backend/tests/test_ledger_money.py backend/tests/test_balance_concurrency.py <any fixture files from Step 5> <<'MSG'
Keep fund balances right under bad input, races and backdated entries

<body as described above>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
```

**Acceptance:**
- `test_ledger_money` passes 6/6;
- the concurrency test fails with the fund lock removed and passes with it;
- the full suite is OK;
- the worktree has no uncommitted changes to the Task 1 files.

---

### Task 2: Finish and land backend performance and cleanup

**Model:** Sonnet 5. **Suffix:** `_t2`.

**Current state** (uncommitted, from an interrupted agent):
- `apps/common/auth.py`: expired-session cleanup moved into `create_session()`; the `last_activity` write removed.
- `apps/accounts/views.py`: `admin_users` uses two aggregate queries instead of 3 COUNTs per user.
- `apps/orgs/views.py`: `join_codes`/`revoke_join_code` are gated by `require(actor, Action.MANAGE_MEMBERS)`. `MINTABLE` now only decides which roles may be granted, and there is a 405 check before the permission check.
- `settings.py`: `rest_framework` removed from `INSTALLED_APPS`; `SecurityMiddleware` and `XFrameOptionsMiddleware` added to the base `MIDDLEWARE`.
- `settings_production.py`: builds `MIDDLEWARE` by inserting WhiteNoise after `CorsMiddleware` instead of redefining the list.
- `requirements.txt`: `djangorestframework` removed.
- Tests edited: `test_login_routing.py` (+3 session tests), `test_member_management.py` (query bound), `test_join_codes.py`, `test_org_middleware.py` (1 line), `test_production_settings.py` (middleware parity).

**Files:** exactly those listed above.

**Interfaces:**
- Consumes: `apps.accounts.permissions.require(user, action)`, `Action.MANAGE_MEMBERS`.
- Produces: `create_session(user_id, token)` (unchanged signature) now also deletes expired sessions. `auth_required` performs no writes.

- [ ] **Step 1: Read the partial work.** Run `git diff -- backend/apps/common/auth.py backend/apps/accounts/views.py backend/apps/orgs/views.py backend/fundvault_backend/settings.py backend/fundvault_backend/settings_production.py backend/requirements.txt backend/tests/test_login_routing.py backend/tests/test_member_management.py backend/tests/test_join_codes.py backend/tests/test_org_middleware.py backend/tests/test_production_settings.py`. Explain the one-line `test_org_middleware.py` change in your report.

- [ ] **Step 2: Prove the auth change with a failing test first.** `test_login_routing.py` should contain these three tests (write them if missing):
  - `test_an_expired_session_is_refused` — a session row with `expires_at` in the past returns 401 on `GET /api/auth/me`;
  - `test_login_clears_expired_sessions` — after a successful login, the expired row is gone;
  - `test_an_authenticated_read_does_not_write` — `GET /api/auth/me` with a live session, wrapped in `CaptureQueriesContext(connections[ORG_ALIAS])`, issues no `INSERT`, `UPDATE` or `DELETE`:

```python
    def test_an_authenticated_read_does_not_write(self):
        token = self._session("s_live", timedelta(hours=1))
        with CaptureQueriesContext(connections[ORG_ALIAS]) as tenant:
            response = self.client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200)
        writes = [q["sql"] for q in tenant.captured_queries
                  if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(writes, [])
```
  Temporarily re-add `session.last_activity = timezone.now(); session.save(update_fields=["last_activity"])` to `auth_required` and confirm the third test FAILS, then remove it again.

- [ ] **Step 3: Order the `admin_users` rows by role rank.** The existing sort puts "admin" first and everything else second, so the Owner lands among Members. Replace it:

```python
    rank = {User.Role.OWNER: 0, User.Role.ADMIN: 1, User.Role.MEMBER: 2, User.Role.VIEWER: 3}
    data.sort(key=lambda x: (rank.get(x["role"], 4), x["created_at"] or ""))
```
  Add an assertion to the existing `admin_users` query-count test in `test_member_management.py`: the first row's role is `"owner"`. The test must bound tenant queries (e.g. `assertLessEqual(len(q), 6)` with about 20 users).

- [ ] **Step 4: Check the join-code gate's error text.** Run `grep -rn "Admin access required" backend/tests backend/apps`. Any test still asserting the old text must now assert status 403 only. The message is the generic permission message from `require()`.

- [ ] **Step 5: Confirm DRF is truly unused, and check production settings.**
  - Run: `git grep -n "rest_framework" -- backend` → expect no matches.
  - Run: `python backend/manage.py check` → "System check identified no issues".
  - Confirm `tests/test_production_settings.py` has a test asserting that every entry of the base `settings.MIDDLEWARE` appears in the production `MIDDLEWARE`, in the same relative order, with WhiteNoise immediately after `CorsMiddleware`.

- [ ] **Step 6: Run the full suite.**
  `FUNDVAULT_TEST_DB_SUFFIX=_t2 python manage.py test --settings=fundvault_backend.settings_test --noinput` → OK.

- [ ] **Step 7: Commit in four commits, each with only its own paths:**
  1. `apps/common/auth.py` + `tests/test_login_routing.py` + `tests/test_org_middleware.py`: "Stop writing to the tenant database on every authenticated request". State the numbers: 2 writes per request before, 0 after; the post-login load of F+5 requests was paying 2(F+5) writes.
  2. `apps/accounts/views.py` + `tests/test_member_management.py`: "Count members' funds and transactions in two queries, and list the Owner first" (was 3 queries per user; 67 with 21 users).
  3. `apps/orgs/views.py` + `tests/test_join_codes.py`: "Gate join codes through the permissions table".
  4. `settings.py` + `settings_production.py` + `requirements.txt` + `tests/test_production_settings.py`: "Drop the unused REST framework and run production's middleware in tests".

**Acceptance:** all three session tests pass, and the no-write test fails when the write is re-added; the `admin_users` query bound holds; the full suite is OK; 4 commits.

---

### Task 3: Put the ledger's money rules in one place

**Model:** Sonnet 5, high effort. **Suffix:** `_t3`. **Depends on:** Task 1 committed.

**Why:** the lock → check → insert → move-balance sequence is implemented three times, slightly differently each time: in the create view, in `process_due_recurring`, and partly in approve. That drift is exactly how the backdated and void races happened. After this task, each invariant lives once, in `services.py`.

**Files:**
- Modify: `backend/apps/ledger/services.py`
- Modify: `backend/apps/ledger/views.py`
- Create: `backend/tests/test_ledger_services.py`

**Interfaces:**
- Produces, in `apps.ledger.services`:
  - `class InsufficientBalance(Exception)`
  - `lock_fund(database_id) -> DatabaseFund | None`: must be called inside `transaction.atomic(using=current_org_alias())`.
  - `post_transaction(fund, *, tx_type, amount, date, requires_approval, created_by_id, **fields) -> TransactionFund`: `fund` must come from `lock_fund` in the same atomic block; raises `InsufficientBalance`.
  - `recalculate_running_balances(database_id) -> float` (unchanged from Task 1).

- [ ] **Step 1: Add the service functions.** In `services.py`, after `recalculate_running_balances`, add:

```python
class InsufficientBalance(Exception):
    """A debit larger than the fund's current balance."""


def lock_fund(database_id):
    """Row-lock a fund inside the caller's tenant atomic block and return it.

    Every money write takes this first -- the fund, then its transaction rows
    -- so writes to one fund run one at a time and never deadlock each other.
    """
    return DatabaseFund.objects.select_for_update().filter(id=database_id).first()


def post_transaction(fund, *, tx_type, amount, date, requires_approval, created_by_id, **fields):
    """Insert a transaction into a fund locked with lock_fund().

    A debit larger than the balance is refused whether or not it needs
    approval. One that needs approval is stored pending and leaves the balance
    alone (transaction_approve moves it later); an approved one moves the
    balance, and running balances are rebuilt so a backdated entry leaves every
    later row right.
    """
    if tx_type == "debit" and amount > fund.balance:
        raise InsufficientBalance()
    running = fund.balance if requires_approval else round(
        fund.balance + amount if tx_type == "credit" else fund.balance - amount, 2
    )
    txn = TransactionFund.objects.create(
        id=uid(),
        database_id=fund.id,
        type=tx_type,
        amount=amount,
        date=date,
        running_balance=running,
        requires_approval=requires_approval,
        approved=not requires_approval,
        created_by_id=created_by_id,
        **fields,
    )
    if not requires_approval:
        fund.balance = recalculate_running_balances(fund.id)
        txn.refresh_from_db(fields=["running_balance"])
    return txn
```

- [ ] **Step 2: Use them in `process_due_recurring`.** Replace the block from `db = DatabaseFund.objects.select_for_update().get(id=rec.database_id)` through `db.save(update_fields=["balance"])` (the "Re-read the fund…" comment, the explicit insufficient-debit check, the `requires_approval` computation, the `new_balance` computation, the `TransactionFund.objects.create(...)` call and the conditional balance save) with:

```python
            db = lock_fund(rec.database_id)
            # Re-evaluated against the creator's *current* standing every run
            # (not frozen at creation time). Fail closed: only a creator who
            # still exists, is active, and could still create this rule
            # (MANAGE_FUNDS: Admin/Owner) posts straight through. Anyone else --
            # demoted, deactivated, deleted (SET_NULL), or a legacy row with no
            # creator -- is gated exactly as a Member's transaction would be.
            requires_approval = not can(rec.created_by, Action.MANAGE_FUNDS) and needs_approval(
                User(role=User.Role.MEMBER), rec.amount, db.approval_threshold
            )
            try:
                txn = post_transaction(
                    db,
                    tx_type=rec.type,
                    amount=rec.amount,
                    date=timezone.now(),
                    requires_approval=requires_approval,
                    created_by_id=rec.created_by_id,
                    sender="Recurring",
                    receiver=rec.description or "",
                    mode=TransactionFund.TxnMode.ELECTRONIC,
                    mode_data=json.dumps({"elecId": f"REC-{rec.id}"}),
                    location="Auto",
                    notes=f"Recurring {rec.frequency} transaction",
                    is_voided=False,
                )
            except InsufficientBalance:
                rec.next_run = next_recurring_date(rec.next_run, rec.frequency)
                rec.save(update_fields=["next_run"])
                continue
```
  Keep the lines that follow unchanged: the `rec.next_run` advance, `add_audit(...)` and `created.append(txn)`. Behaviour is identical: an over-balance debit skips this run, which is what the old explicit check did before computing approval.

- [ ] **Step 3: Use them in the create view.** In `views.py`, import `InsufficientBalance, lock_fund, post_transaction` from `apps.ledger.services`. Replace the whole `with transaction.atomic(using=current_org_alias()):` block of the `database_transactions` POST branch, and its `new_balance` bookkeeping, with:

```python
    # The org's own alias: select_for_update() needs a transaction on the alias it queries.
    with transaction.atomic(using=current_org_alias()):
        fund = lock_fund(database_id)
        if not fund:
            return json_error("Database not found", 404)
        requires_approval = needs_approval(request.fv_user, amount, fund.approval_threshold)
        try:
            txn = post_transaction(
                fund,
                tx_type=tx_type,
                amount=amount,
                date=tx_date,
                requires_approval=requires_approval,
                created_by_id=request.fv_user.id,
                sender=sender or None,
                receiver=receiver or None,
                mode=mode,
                mode_data=json.dumps(mode_data),
                location=location or None,
                notes=notes or None,
                receipt_key=None,
            )
        except InsufficientBalance:
            return json_error("Insufficient balance", 400)
    new_balance = fund.balance
```
  The `add_audit(...)` call and the JSON response below stay as they are.

- [ ] **Step 4: Use `lock_fund` in approve, void, edit and delete-voided.**
  - In `transaction_approve`, replace `locked = DatabaseFund.objects.select_for_update().filter(id=txn.database_id).first()` with `locked = lock_fund(txn.database_id)`. Then replace the manual balance arithmetic (`new_balance = round(...)` through `recalculate_running_balances(locked.id)`) with:

```python
        txn.approved = True
        txn.approved_by = request.fv_user.username
        txn.approved_at = timezone.now()
        txn.save(update_fields=["approved", "approved_by", "approved_at"])
        # Rebuilds this row's running balance (it may be backdated) and the fund's.
        new_balance = recalculate_running_balances(locked.id)
```
    The insufficient-balance check above it stays unchanged.
  - In `transaction_void`, `transaction_update` and `transaction_delete_voided`, replace `DatabaseFund.objects.select_for_update().filter(id=...).first()` with `lock_fund(...)`, and delete the three-line comment above each. `lock_fund`'s docstring says it once.

- [ ] **Step 5: Trim `analytics_overview` to two queries.** Replace its body after the method check with:

```python
    funds = DatabaseFund.objects.filter(is_deleted=False).aggregate(count=Count("id"), balance=Sum("balance"))
    totals = TransactionFund.objects.filter(database__is_deleted=False, is_voided=False).aggregate(
        credits=Sum("amount", filter=Q(type="credit")),
        debits=Sum("amount", filter=Q(type="debit")),
    )
    return JsonResponse(
        {
            "totalDatabases": funds["count"],
            "totalBalance": funds["balance"] or 0,
            "totalCredits": totals["credits"] or 0,
            "totalDebits": totals["debits"] or 0,
        }
    )
```
  Import `Count` and `Q` from `django.db.models`. Before deleting the old zero-funds branch, run `grep -rn "monthlyData\|modeData" frontend/src`. Only the zero-funds branch returned those two keys; if any frontend code reads them from the overview response, keep returning `[]` for both and say so.

- [ ] **Step 6: Decide the receipt-extraction 503 from config, not message text.** In `extract_receipt`, today's code picks 503 when `result.get("error","").endswith("organisation settings.")`. Read `backend/apps/ledger/receipt_extractor.py` around lines 240–255 to get the exact dict `extract_from_receipt_image` returns when no provider is configured. Then change the view to:

```python
    config = parse_ai_config(request.fv_org.ai_config)
    if not config["primary"] and not config["fallback"]:
        return JsonResponse(<that exact dict, copied>, status=503)
    result = extract_from_receipt_image(image_file.read(), image_file.content_type or "", config)
    return JsonResponse(result)
```
  If the extractor's own no-config branch becomes unreachable from this view, leave it in place: it guards direct callers.

- [ ] **Step 7: Rename `_get_user_database`.** Its `user` parameter is unused ("retained for Phase 4", which has shipped). Rename it to `_get_fund(database_id, include_deleted=False)`, drop the parameter, and update every caller (`grep -rn "_get_user_database" backend`). Replace its docstring with one line: `"""Look up a fund in the caller's org (the tenant connection is the org boundary)."""`

- [ ] **Step 8: Write the new tests** in `backend/tests/test_ledger_services.py`, reusing the module-level setup pattern of `tests/test_ledger_money.py` (org "o1", owner token, fund "f1"):
  - `test_overview_counts_in_two_queries` — create 3 funds and 10 transactions (including a voided one and one in a deleted fund). With `CaptureQueriesContext(connections[ORG_ALIAS])`, `GET /api/analytics/overview` → 200, and the totals equal hand-computed sums that exclude voided rows and deleted funds. Count only captured queries whose SQL references the ledger tables (`"databases"` / `"transactions"`; check `db_table` in `apps/ledger/models.py`) and assert that count ≤ 2.
  - `test_overview_with_no_funds_has_the_same_four_keys` → `set(body) == {"totalDatabases","totalBalance","totalCredits","totalDebits"}` and all values 0 (adjust if Step 5 had to keep the extra keys).
  - `test_extract_receipt_without_ai_config_is_503_and_calls_nothing`: with `mock.patch("apps.ledger.receipt_extractor.extract_from_receipt_image")`, POST a small valid PNG (build one with Pillow into `SimpleUploadedFile`) to the extract route (find it in `backend/apps/ledger/urls.py`) → 503, and the mock is not called.
  - `test_recurring_and_manual_posts_share_rules`: a Member's manual transaction at or above the threshold and a recurring rule whose creator was demoted to Member both end up `requires_approval=True` with the balance unchanged. Call `process_due_recurring` directly inside `org_context(ORG_ALIAS)`.

- [ ] **Step 9: Run the full suite.**
  `FUNDVAULT_TEST_DB_SUFFIX=_t3 python manage.py test --settings=fundvault_backend.settings_test --noinput` → OK. The test count is Task 1's count plus the new tests.

- [ ] **Step 10: Commit.** Two commits if the hunks separate cleanly, otherwise one whose body covers both:
  1. `services.py` + `views.py` (Steps 1–4, 7) + the recurring/manual test: "Put the ledger's money rules in one place".
  2. `views.py` (Steps 5–6) + the remaining tests: "Count the overview in two queries and decide the receipt 503 from config".

**Acceptance:**
- `git grep -n "select_for_update" backend/apps/ledger` shows no `DatabaseFund` lock outside `lock_fund` (the recurring rule lock stays);
- `post_transaction` is the only place a `TransactionFund` is created together with a balance move;
- the full suite is OK.

---

### Task 4: Frontend — join-code manager and profile photo downscale

**Model:** Sonnet 5. **Files owned:** `frontend/src/components/modals/JoinCodesModal.jsx` (new), `frontend/src/components/modals/AppModals.jsx`, `frontend/src/components/layout/HeaderBar.jsx`, `frontend/src/components/FundVaultApp.jsx`.

**Why:** the backend has tested join-code endpoints, but nothing in the UI calls them. After deploy, an Owner cannot invite anyone without curl. Separately, profile photos are stored as raw base64 data URLs: a 3 MB phone photo becomes about 4 MB in every login and `/auth/me` response.

**API facts** (from `backend/apps/orgs/views.py` and `urls.py`; all need a bearer token and the `MANAGE_MEMBERS` capability, which Owner and Admin have):
- `GET /api/orgs/codes` → `[{code, grants_role, expires_at (ISO), max_uses, uses, revoked}]`, newest first.
- `POST /api/orgs/codes` with body `{role, maxUses, expiresInDays}` → one row of the same shape. `maxUses` is clamped to 1–100 and `expiresInDays` to 1–90. The Owner may grant `admin`, `member` or `viewer`; an Admin may grant `member` or `viewer`. Anything else → 400.
- `DELETE /api/orgs/codes/<code>` → `{success: true}`, or 404.
- In the frontend, `request(endpoint, options)` is `authedRequest` (it prefixes the API base and throws an `Error` with `.message` and `.status`), and `toast(message, type)` shows a toast. `AppModals` receives them as `actions.request`/`actions.toast`, and the current user as `state.currentUser`.

- [ ] **Step 1: Create `frontend/src/components/modals/JoinCodesModal.jsx`:**

```jsx
"use client";

import { useEffect, useState } from "react";

import Modal from "components/modals/Modal";
import { formatDate } from "lib/format";
import { ACTIONS, can } from "lib/permissions";

const LABEL = { admin: "Admin", member: "Member", viewer: "Viewer" };

const statusOf = row => {
  if (row.revoked) return "Revoked";
  if (new Date(row.expires_at) < new Date()) return "Expired";
  if (row.uses >= row.max_uses) return "Used up";
  return "Active";
};

export default function JoinCodesModal({ open, onClose, request, toast, currentUser }) {
  const roles = can(currentUser, ACTIONS.MINT_ADMIN_CODE) ? ["member", "viewer", "admin"] : ["member", "viewer"];
  const [codes, setCodes] = useState([]);
  const [form, setForm] = useState({ role: "member", maxUses: 1, expiresInDays: 14 });
  const [busy, setBusy] = useState(false);

  const load = () =>
    request("/orgs/codes")
      .then(setCodes)
      .catch(err => toast(err.message, "error"));

  useEffect(() => {
    if (open) load();
  }, [open]);

  if (!open) return null;

  const copy = async code => {
    try {
      await navigator.clipboard.writeText(code);
      toast("Join code copied", "success");
    } catch {
      toast("Couldn't copy — select the code and copy it by hand", "error");
    }
  };

  const create = async () => {
    setBusy(true);
    try {
      const row = await request("/orgs/codes", {
        method: "POST",
        body: JSON.stringify({
          role: form.role,
          maxUses: Number(form.maxUses),
          expiresInDays: Number(form.expiresInDays)
        })
      });
      setCodes(prev => [row, ...prev]);
      await copy(row.code);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async code => {
    if (!window.confirm("Revoke this join code? Anyone who hasn't used it yet won't be able to.")) return;
    try {
      await request(`/orgs/codes/${encodeURIComponent(code)}`, { method: "DELETE" });
      setCodes(prev => prev.map(row => (row.code === code ? { ...row, revoked: true } : row)));
      toast("Join code revoked", "success");
    } catch (err) {
      toast(err.message, "error");
    }
  };

  return (
    <Modal open={open} id="joinCodesModal" title="Invite members" onClose={onClose} large>
      <p className="hint">
        Share a code with the person you're inviting. They choose "Join an organisation" on the sign-in screen and
        paste it.
      </p>
      <div className="form-row">
        <div className="form-group">
          <label>Role</label>
          <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>
            {roles.map(role => (
              <option key={role} value={role}>
                {LABEL[role]}
              </option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label>Uses</label>
          <input
            type="number"
            min="1"
            max="100"
            value={form.maxUses}
            onChange={e => setForm({ ...form, maxUses: e.target.value })}
          />
        </div>
        <div className="form-group">
          <label>Expires in (days)</label>
          <input
            type="number"
            min="1"
            max="90"
            value={form.expiresInDays}
            onChange={e => setForm({ ...form, expiresInDays: e.target.value })}
          />
        </div>
      </div>
      <button className="btn btn-primary" onClick={create} disabled={busy}>
        {busy ? "Creating…" : "Create code"}
      </button>

      <div className="section-label" style={{ margin: "20px 0 8px" }}>
        Codes
      </div>
      {codes.length === 0 ? (
        <p className="hint">No join codes yet.</p>
      ) : (
        <table className="txn-table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Role</th>
              <th>Uses</th>
              <th>Expires</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {codes.map(row => {
              const status = statusOf(row);
              return (
                <tr key={row.code}>
                  <td>
                    <code>{row.code}</code>
                  </td>
                  <td>{LABEL[row.grants_role] || row.grants_role}</td>
                  <td>
                    {row.uses} / {row.max_uses}
                  </td>
                  <td>{formatDate(row.expires_at)}</td>
                  <td>{status}</td>
                  <td>
                    {status === "Active" && (
                      <>
                        <button className="btn btn-sm btn-outline" onClick={() => copy(row.code)}>
                          Copy
                        </button>{" "}
                        <button className="btn btn-sm btn-ghost" onClick={() => revoke(row.code)}>
                          Revoke
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Modal>
  );
}
```
  Check that `txn-table`, `hint`, `section-label`, `form-row`, `form-group`, `btn-sm`, `btn-outline` and `btn-ghost` exist in `frontend/src/app/globals.css` (`grep -n "\.txn-table\|\.hint\b\|\.section-label" frontend/src/app/globals.css`). If the table class differs, use the class `DatabaseView.jsx` uses for its ledger table.

- [ ] **Step 2: Wire it up.**
  - `AppModals.jsx`: add `import JoinCodesModal from "components/modals/JoinCodesModal";`, and render it after `<OrgSettingsModal ... />`:

```jsx
      <JoinCodesModal
        open={modals.joinCodes}
        onClose={() => close("joinCodes")}
        request={actions.request}
        toast={actions.toast}
        currentUser={state.currentUser}
      />
```
  - `FundVaultApp.jsx`: add `joinCodes: false` to the `modals` initial state, after `orgSettings: false`. Pass `onOpenJoinCodes={() => setModals(prev => ({ ...prev, joinCodes: true }))}` to `<HeaderBar>`.
  - `HeaderBar.jsx`: add `onOpenJoinCodes` to the destructured props, and add this menu item directly after the "Manage Users" item:

```jsx
              {canManageUsers && (
                <button
                  className="user-dropdown-item"
                  onClick={() => {
                    setUserDropdownOpen(false);
                    onOpenJoinCodes();
                  }}
                >
                  🎟️ Invite members
                </button>
              )}
```

- [ ] **Step 3: Downscale profile photos.** In `FundVaultApp.jsx`, replace `updateProfileImage` with:

```js
  const updateProfileImage = file => {
    if (!file) {
      setProfileForm(prev => ({ ...prev, profileImage: "" }));
      return;
    }
    // Stored on the user row and sent with every login and /auth/me response,
    // so shrink it to avatar size first (~20 KB instead of megabytes).
    const url = URL.createObjectURL(file);
    const img = new window.Image();
    img.onload = () => {
      const scale = Math.min(1, 256 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      setProfileForm(prev => ({ ...prev, profileImage: canvas.toDataURL("image/jpeg", 0.85) }));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      toast("That file is not a readable image", "error");
    };
    img.src = url;
  };
```

- [ ] **Step 4: Lint and build.** From `frontend/`:
  - `npm run lint` → 0 errors. Warnings may remain; do not add new ones beyond the `[open]` exhaustive-deps warning, which mirrors `OrgSettingsModal`.
  - `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run build` → success.

- [ ] **Step 5: Commit.**
  1. `git add frontend/src/components/modals/JoinCodesModal.jsx`, then commit it with `AppModals.jsx`, `HeaderBar.jsx` and `FundVaultApp.jsx`: "Add an Invite members screen for join codes". If the `FundVaultApp.jsx` hunks can't be separated from Step 3, include Step 3 here too and mention it in the body.
  2. Otherwise, `FundVaultApp.jsx` alone: "Shrink profile photos to avatar size before saving them".

**Acceptance:** lint shows 0 errors; the build succeeds; the supervisor's Task 9 browser pass shows an Owner minting a Member code and a second browser joining with it.

---

### Task 5: Frontend — CSS hygiene and onboarding buttons

**Model:** Haiku 4.5. **Files owned:** `frontend/src/app/globals.css`, `frontend/src/components/auth/CreateOrgForm.jsx`, `frontend/src/components/auth/JoinOrgForm.jsx`, `frontend/src/components/views/HomeView.jsx`.

**Facts:**
- These selectors belong to components that no longer exist (the old AuthView and the activity feed) or to the theme toggle, which moved into the user menu: `.auth-container`, `.auth-card`, `.auth-tab` (singular), `.auth-form`, `.activity-feed`, `.activity-row`, `.activity-row-time`, `.theme-toggle`.
- `.auth-tabs` (plural) is still used by `OrgGateway.jsx`. `globals.css` has TWO `.auth-tabs` blocks: an old one around line 883 (`display: grid`) and an appended one near the end of the file (`display: flex`). The old one is dead but still applies, and is only masked by the later block.
- `CreateOrgForm.jsx:101` and `JoinOrgForm.jsx:60` use `className="btn-secondary"`, which has no CSS rule at all. The existing button classes are `.btn` plus `.btn-primary`, `.btn-outline` or `.btn-ghost`.
- `HomeView.jsx` computes `const recent = auditLogs.slice(0, 5);` and never renders it.

- [ ] **Step 1: Prove each selector is unreferenced before deleting it.** For each name in `auth-container auth-card auth-form activity-feed activity-row activity-row-time theme-toggle`, run
  `grep -rn "<name>" frontend/src --include=*.jsx --include=*.js`
  Expected: no matches. For `auth-tab`, run `grep -rnw "auth-tab" frontend/src --include=*.jsx`, which must not match `auth-tabs`. If any selector has a match, keep it and report it.

- [ ] **Step 2: Delete the dead rules** from `globals.css`: every rule whose selector list consists only of the unreferenced selectors above, plus the OLD `.auth-tabs` block (the one with `display: grid`; keep the later `display: flex` one). Where a selector sits in a comma list with live selectors, remove only that selector from the list. Record `wc -l frontend/src/app/globals.css` before and after.

- [ ] **Step 3: Give the onboarding buttons real classes.** In `CreateOrgForm.jsx` and `JoinOrgForm.jsx`, change `className="btn-secondary"` to `className="btn btn-outline"`. Run `grep -n "className=" frontend/src/components/auth/*.jsx`; any button class with no CSS rule (check with `grep -n "\.<class>" frontend/src/app/globals.css`) becomes `btn btn-primary` for a primary action or `btn btn-outline` for a secondary one.

- [ ] **Step 4: Remove the dead code in HomeView.** Delete `const recent = auditLogs.slice(0, 5);` and remove `auditLogs` from HomeView's destructured props. `FundVaultApp.jsx` still passes it; that is harmless, and it is Task 4's file, so do not edit it.

- [ ] **Step 5: Lint and build.** From `frontend/`: `npm run lint` shows 0 errors, and `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run build` succeeds.

- [ ] **Step 6: Commit** with message "Remove dead styles and give the onboarding buttons real classes", paths: the four files.

**Acceptance:** each deleted selector has no references; lint shows 0 errors and the build succeeds; the supervisor's Task 9 visual check of the sign-in gateway, the header and the home screen shows no styling regressions.

---

### Task 6: One shared helper for tenant tests

**Model:** Sonnet 5. **Suffix:** `_t6`. **Depends on:** Tasks 1–3 committed, since it edits their test files.

**Why:** 16 test modules repeat the same 25–35 lines:
- a `TENANT_URL` constant;
- `ensure_connection(Org(id="o1", ...))` at import time, because Django computes each TestCase's database allowlist before `setUp`;
- the same call again in `setUpClass`;
- `databases = {"default", ORG_ALIAS}`;
- a local `_user()` helper that creates a `User` and a `Session` and returns a token.

A new tenant test currently needs all of that, exactly right.

**Files:**
- Create: `backend/tests/support.py`
- Modify: the test modules listed in Step 3

**Interfaces:**
- Produces, in `tests.support`: `TENANT_URL`, `ORG_ID = "o1"`, `ORG_ALIAS`, and `class OrgTestMixin` with class attribute `databases`, and methods `make_org(**fields) -> Org`, `make_user(user_id, role, *, username=None, email=None, is_active=True) -> str` (returns the bearer token), and `auth(token) -> dict` (static).

- [ ] **Step 1: Record the per-module baseline.** For every module you will convert, run
  `FUNDVAULT_TEST_DB_SUFFIX=_t6 python manage.py test tests.<module> --settings=fundvault_backend.settings_test --noinput 2>&1 | grep "^Ran"`
  and write the counts into your report.

- [ ] **Step 2: Create `backend/tests/support.py`:**

```python
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
```

- [ ] **Step 3: Convert modules one at a time.** Candidates: `test_member_management`, `test_login_routing`, `test_join_codes`, `test_join_code_audit_privacy`, `test_ledger_permissions`, `test_ledger_money`, `test_ledger_services`, `test_org_settings`, `test_receipt_upload`, `test_atomic_alias_regression`, `test_balance_concurrency`, `test_ownership_transfer_concurrency`, `test_org_middleware`.
  For each module:
  - replace the module-level `TENANT_URL`, `ORG_ALIAS` and import-time `ensure_connection` with `from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL` (import only what is used);
  - change `class X(TestCase)` to `class X(OrgTestMixin, TestCase)`;
  - delete the now-duplicated `databases =` line and `setUpClass` override, unless the class adds more aliases or other `setUpClass` work (then keep it and call `super()`);
  - replace local `_user`/`_token` helpers with `self.make_user(...)` only where the semantics are identical (same `password_hash`, `role` and `is_active`). Login tests that need a real bcrypt hash keep their own helper;
  - keep every assertion byte-for-byte;
  - run the module; the `Ran N` count must equal the baseline and the result must be OK;
  - commit that module alone: "Use the shared tenant-test helper in <module>". Include `backend/tests/support.py` (after `git add`) in the first such commit.
  Do NOT convert `test_org_connections`, `test_org_provisioning`, `test_tenant_isolation` or `test_migrate_tenants`: they deliberately manage connections themselves.
  Skip any module where the conversion would not remove at least ~10 lines, and list it in your report.

- [ ] **Step 4: Run the full suite.**
  `FUNDVAULT_TEST_DB_SUFFIX=_t6 python manage.py test --settings=fundvault_backend.settings_test --noinput` → OK, with the same total count as before Task 6.

**Acceptance:** per-module test counts are unchanged; the full suite is OK; `grep -c "TENANT_URL = " backend/tests/*.py` shows the constant defined only in `support.py`, the four excluded modules and any skipped module.

---

### Task 7: Accurate docs and cross-platform tooling

**Model:** Sonnet 5. **Depends on:** Tasks 1–5 committed; read their commits with `git log 0fe1467..HEAD --stat` before writing.

**Files:** `README.md`, `API_DOCUMENTATION.md`, `package.json` (root), `install.bat`, `.gitignore`, `frontend/package.json` (the `engines` field only), `frontend/.env.example`, `frontend/AGENTS.md` and `frontend/CLAUDE.md` (commit as-is), `data/.gitkeep` (delete).

- [ ] **Step 1: Fix the root `package.json`.** Change `backend\\manage.py` → `backend/manage.py` and `backend\\requirements.txt` → `backend/requirements.txt` in all four scripts. On macOS and Linux, npm runs scripts through `sh`, which drops the backslash; cmd.exe accepts forward slashes. In `keywords`, replace `"sqlite"` with `"postgres"`. Verify from Git Bash with `npm run check:backend` → "System check identified no issues".

- [ ] **Step 2: Fix `frontend/package.json`.** Add `"engines": { "node": ">=20.9" }`. Next 16.3 requires Node ≥ 20.9; the README says 18+.

- [ ] **Step 3: Replace `frontend/.env.example`** with:

```
# Backend API origin, no trailing slash. Local development uses the default below.
# In production set it in Vercel's environment variables, e.g.
#   NEXT_PUBLIC_API_BASE=https://fundvault-api.onrender.com
NEXT_PUBLIC_API_BASE=http://localhost:8000
```

- [ ] **Step 4: Fix `install.bat`.** Replace the whole `( echo ... ) > backend\.env` block with `copy /Y backend\.env.example backend\.env >nul`, so the installer and `.env.example` can't drift again. Keep the surrounding `if not exist` check and messages. `backend/.env.example` leaves `FUNDVAULT_SECRET_KEY=` empty; add a line to the installer's closing message telling the user to generate one with the `python -c "from cryptography.fernet ..."` command that `.env.example` shows. Do not attempt automatic key injection.

- [ ] **Step 5: Tidy the repo and commit.**
  - Add `.claude/` to `.gitignore`. It holds local launch configs and worktrees.
  - Delete `data/.gitkeep`, a SQLite-era leftover. First confirm with `git grep -n "data/" -- backend frontend run.bat install.bat`; if anything uses `data/`, keep it and say so.
  - `git add frontend/AGENTS.md frontend/CLAUDE.md`. Next.js 16.3's `next dev` writes these and re-adds them on every run, so committing them keeps the tree clean.
  - Commit `package.json`, `frontend/package.json`, `frontend/.env.example`, `install.bat`, `.gitignore`, `data/.gitkeep`, `frontend/AGENTS.md` and `frontend/CLAUDE.md`: "Make the root scripts cross-platform and stop the installer's env drifting".

- [ ] **Step 6: Rewrite the README's `## Deployment` section** (currently lines ~197–220) with this content, adjusting only where the code says otherwise:

```markdown
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
  (a placeholder is fine now; fix it after step 4)

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
```

  Also update:
  - the README's Node requirement to **Node.js 20.9+**;
  - its project-structure tree: remove `data/` if listed; add `backend/tests/` (with `support.py`) and `backend/fundvault_backend/test_runner.py` if the tree lists that level of detail;
  - its testing instructions, adding a short **Running the tests** subsection under "Run it yourself":

```markdown
### Running the tests
With the dev Postgres containers up (`docker compose up -d`):

    cd backend
    python manage.py test --settings=fundvault_backend.settings_test

The test runner creates private `test_*` copies of every database and refuses to
connect to anything else, so tests never touch your dev data. To run two suites
at once, give each its own suffix: `FUNDVAULT_TEST_DB_SUFFIX=_mine`.
```

- [ ] **Step 7: Update `API_DOCUMENTATION.md`.** Change only what the code changed; verify each item against the code before writing it.
  - **Recurring rules:** the response includes `created_by` (user id or null). `POST /api/recurring/process` may create transactions pending approval (`requires_approval: true`, `approved: false`, balance unchanged) when the rule's creator is no longer an active Admin or Owner and the amount is at or above the fund's `approval_threshold`. The audit text reads "pending approval" instead of "auto-posted". Overlapping calls never post a rule twice.
  - **Money input:** `amount`, `lowBalanceThreshold` and `approvalThreshold` must be finite numbers; `nan`, `inf` and non-numeric values → 400. Amounts and balances are rounded to 2 decimal places. A non-object JSON body → 400 through the missing-field checks.
  - **Receipts:** permanently deleting a voided transaction, deleting or emptying trashed funds, and replacing a receipt under a different key remove the image from the org's bucket after commit, unless another row still references it. This is best-effort.
  - **`PUT /api/orgs/settings`:** a `storage` or `ai` value that is not a JSON object (including `null`) → 400. There is no clear operation.
  - **Join codes:** a caller without `MANAGE_MEMBERS` gets the generic 403 "You do not have permission to do that".
  - **`GET /api/admin/users`:** ordered Owner, Admin, Member, Viewer, then by creation date.
  - **`GET /api/analytics/overview`:** always returns exactly `totalDatabases`, `totalBalance`, `totalCredits`, `totalDebits` (check Task 3's commit in case it kept the extra keys).
  - **Sessions:** `last_activity` is no longer updated per request.
  - **Receipt extraction:** 503 when the org has no AI provider configured.
  - **Org creation / connection validation:** an IPv6-only database host → 400 with the connection-pooler hint.
  - **Operations:** the `migrate_tenants` management command, run by every deploy.

- [ ] **Step 8: Commit** with message "Document the deploy path and this round's API changes", paths `README.md` and `API_DOCUMENTATION.md`.

**Acceptance:**
- `npm run check:backend` works from Git Bash;
- every claim added to the docs is traceable to a line of code (the Opus reviewer spot-checks 10);
- `git status` shows no untracked `frontend/AGENTS.md` or `frontend/CLAUDE.md`.

---

### Task 8: Final supervisory review (Opus)

Two Opus reviewers run in parallel over `git diff 0fe1467..HEAD` plus the full current files they need. Each returns findings as `{severity: critical|important|minor, file, line, claim, evidence, fix}`, having tried to refute each finding before reporting it.

- **Lens A — money, security, tenancy:**
  - money invariants: `fund.balance` equals the sum of approved non-voided rows after every endpoint, lock order, and rounding;
  - no secret in responses or logs;
  - tenant isolation: no ownership filters reintroduced, and no `atomic()` without `using=`;
  - permission gates matching `accounts/permissions.py`;
  - the frontend XSS fix covering every user-controlled interpolation in `printLedger`;
  - `JoinCodesModal` rendering values only as text.
- **Lens B — deploy readiness and docs accuracy:**
  - `render.yaml`, `build.sh`, `settings_production.py` and `migrate_tenants`, against current Render docs;
  - `vercel.json` and `next.config.js`, against Vercel;
  - the README Deployment steps, followed literally, would work;
  - `API_DOCUMENTATION.md` claims match the code;
  - `install.bat` and root scripts behave as documented.

The supervisor triages. Critical and important findings go to ONE Sonnet fixer dispatch carrying the complete list, with the owning files named. Minor findings are ruled on (fix now, or park with a reason). One Opus re-review is scoped to the fix diff.

### Task 9: Verify, migrate, merge, push (supervisor)

- [ ] **Full suite, twice concurrently** with suffixes `_final_a` and `_final_b` → both OK.
- [ ] **API E2E (Haiku runs it):** copy the 2026-09-23 E2E script (scratchpad `verify/e2e.py`, 41 checks) and update it for the new behaviours:
  - `nan` amount → 400;
  - a backdated entry gives correct running balances;
  - join codes: a Viewer is denied and an Admin can't mint an Admin code;
  - the analytics overview has 4 keys;
  - a Member triggers recurring processing and the demoted-creator rule is pending.
  Run it against fresh scratch databases (`pv9_cp` on :5433, `pv9_tenant` on :5434) with dev settings on port 8795, then drop them. All checks must pass.
- [ ] **Browser smoke (supervisor, built-in browser pane):** backend on scratch DBs, `next dev` with `NEXT_PUBLIC_API_BASE`. Check:
  - create an org;
  - fund → transaction with sender `<img src=x onerror=alert(1)>` → Print ledger shows the literal text;
  - the Dashboard opens (lazy chunk);
  - Invite members → mint a Member code → join in a second tab → change that user's role;
  - corrupt the token → the session-expired flow;
  - the header shows the real role;
  - onboarding buttons are styled.
  Take screenshots.
- [ ] **Dev database:** `python backend/manage.py migrate_tenants` (dev settings) to apply ledger `0003` to the user's local org database(s). Report the per-org results.
- [ ] **Secret scan** of the tree to be pushed: `git grep -nE "(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY)" HEAD`, and `git ls-files | grep -E "(^|/)\.env$"` → nothing.
- [ ] **Merge:** from the main checkout, `git merge --ff-only worktree-fundvault-multitenant` (main is an ancestor).
- [ ] **Push:** `git push -u origin main`, which the user approved on 2026-09-24. Confirm with `git ls-remote origin`.
- [ ] **Update the memory note** `fundvault-deploy-status.md`.

## Deferred (recorded, not in this plan)

- **Sign receipt URLs on demand** instead of for every row on every fund load, and make the home cards stop loading every fund's full ledger. Login cost grows with total receipts and transactions. Needs a backend count on `GET /databases` plus frontend changes.
- **DecimalField money.** Cheapest before real data exists; a spec-level decision for the user.
- **Split `AppModals.jsx`** into one self-contained file per modal (the `OrgSettingsModal`/`JoinCodesModal` pattern), one modal at a time, with a manual pass each.
- **Rate-limit client IP behind Render's proxy.** Needs one live request to learn Render's `X-Forwarded-For` contract.
- **Retire WhiteNoise and collectstatic.** Once DRF is gone, there are no static files to serve.

## Self-review

- **Coverage:**
  - audit findings F2/F3/F4/F8/F9/F11/F14/F13(a,b) landed earlier (commits `53513f3`, `b6100ae`, `62d55cc`, `9dadf3a`, `4121a2b`);
  - F5 and preview CORS are deferred;
  - backend review items 1, 2, 3 → Task 1; 4, 5(accounts), 8, 11b → Task 2; 5(ledger), 6, 11a, 11c → Task 3; 7 → Task 6; 9 and 10 → deferred;
  - frontend review items 1, 2, 3, 5, 6, 7 and 9-step-1 are committed (`dc9af5a`..`7cdd284`); 8 and 12 → Task 4; 11 → Task 5; 10 → Task 7; 4 and 9-full → deferred.
- **Placeholders:** Task 3 Step 6 deliberately tells the executor to copy a dict from a named location instead of guessing it. Everything else is concrete.
- **Name consistency:** `lock_fund`, `post_transaction`, `InsufficientBalance`, `recalculate_running_balances`, `_get_fund`, `OrgTestMixin.make_user/make_org/auth`, `JoinCodesModal` props `{open, onClose, request, toast, currentUser}` and the `modals.joinCodes` key are used identically everywhere.
