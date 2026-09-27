# Frontend

Next.js 16 + React 19 single-page app (App Router, one route) in `frontend/`. See
[`../frontend/AGENTS.md`](../frontend/AGENTS.md): this Next.js version has breaking
changes from what most training data assumes — check `node_modules/next/dist/docs/`
before relying on remembered APIs.

## Structure

```
src/app/            layout.js, page.js, globals.css — one route, renders FundVaultApp
src/components/
  auth/             OrgGateway, CreateOrgForm, JoinOrgForm (signed-out flow)
  layout/           HeaderBar, NavTabs
  modals/           Modal (dialog shell), AppModals (all modal bodies), OrgSettingsModal, JoinCodesModal
  views/            HomeView, DashboardView, DatabaseView, AuditView, TrashView
lib/
  api.js            apiRequest/uploadReceipt/extractReceipt + org-auth endpoints
  format.js         fmt, formatDate(Short), relativeTime, toLocalInput/nowInput
  permissions.js    ACTIONS + can() — mirrors backend/apps/accounts/permissions.py
```

`page.js` renders `FundVaultApp` client-side; there is no server component tree to
speak of — all state and data fetching lives in `FundVaultApp.jsx`, with views and
modals as (mostly) presentational children driven by props.

## App shell — `FundVaultApp.jsx`

Owns essentially all app state: `token`, `currentUser`, `currentOrg`, `databases`
(each hydrated with its `transactions`), `currentDbId`/`transactions` (the open
fund's ledger), `auditLogs`, `trashItems`, `recurringItems`, `managedUsers`,
`overview` (dashboard summary), every form/modal-visibility object, and `toasts`.

**Session restore.** On mount, `fundvault_token`/`fundvault_currentUser`/
`fundvault_org` are read from `localStorage` into state, then `restored` flips
true (nothing renders before that, so a signed-in user never flashes the sign-in
gateway). A second effect, keyed on `token`, runs `runPostLoginLoad()`: it tries
`POST /api/recurring/process` (ignored unless it 401s — a Viewer isn't allowed to
post, and a missed run is retried next login), then always refetches
`GET /api/auth/me` and overwrites `currentUser` with it. This is deliberate:
`currentUser` is restored from localStorage and can be stale after a role change
made elsewhere, and every `can()` permission gate reads `currentUser.role`, so the
UI's permission state is only as fresh as this refresh. `loading` is
`Boolean(token) && loadedToken !== token` — true from sign-in until this load
finishes.

**`authedRequest(endpoint, options)`** wraps `apiRequest` with the current token
and centralizes 401 handling: on a 401 it only calls `resetSession()` (clears all
state and the three localStorage keys) and toasts "Session expired" if the failing
request's token still matches the one currently in localStorage — so a stale
in-flight request from a *previous* session can't sign out a session that has
since re-authenticated, and a burst of parallel 401s only resets once.

**`refreshAfter(scope)`** is the one place that decides how much to refetch after
a mutation:
- `"txn"` (recording/editing/voiding/approving/deleting a transaction, receipt
  upload) → `loadCurrentDb(currentDbId)` + audit + overview. Cheap: refetches only
  the one fund whose ledger changed.
- `"fund"` (create/edit/delete/archive/merge a fund, trash restore/delete, initial
  load) → `hydrateDatabases()` + audit + trash + overview.

`hydrateDatabases()` is the expensive path: it calls `GET /api/databases` for the
list, then issues one `GET /api/databases/:id` per fund in parallel
(`Promise.all`) to attach each fund's full transaction list, because the Dashboard
(monthly/mode/balance charts, summary chips) and Home cards need every fund's
ledger, not just its own page. This means the app pulls every transaction of
every fund on every fund-level refresh — the cost scales with total fund count ×
average ledger size, not with what's on screen. There is no pagination or
per-fund lazy loading; a fund whose detail fetch fails is kept in the list with
an empty `transactions: []` rather than dropped.

## Permissions mirror

`lib/permissions.js` (`ACTIONS`, `can(user, action)`) is a hand-kept copy of
[`../backend/apps/accounts/permissions.py`](../backend/apps/accounts/permissions.py)'s
action names and the viewer ⊂ member ⊂ admin ⊂ owner capability sets. The comment
in the file says it plainly: the backend is authoritative and enforces every
check server-side; this copy exists only so the UI doesn't render a button that
would 403. `can()` also requires `user.is_active !== false` — a deactivated user's
token still exists but every gate here (and every backend check) treats them as
having no permissions. Keep the two files' role→action tables identical when
either changes; nothing detects drift automatically.

## Dates

The server stores and returns timestamps in UTC. The UI only ever asks for local
wall-clock time (a `datetime-local` input never carries a timezone), so every
input that reads or writes a date goes through
`toLocalInput`/`nowInput` (`lib/format.js`):

```js
export const toLocalInput = (value = Date.now()) => {
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
```

Two places rely on this to avoid corrupting data:
- **Editing a transaction** (`openEditTransactionModal`/`saveTransactionEdit` in
  `FundVaultApp.jsx`) tracks the input's original local value (`originalDate`) and
  only includes `date` in the `PUT` body when it actually changed. Re-sending an
  unchanged local value through `new Date(...).toISOString()` on every unrelated
  edit would round-trip it through the browser's offset each time.
- **Dashboard month buckets** (`DashboardView.jsx`'s `getMonthlyData`) build the
  `YYYY-MM` key from `date.getFullYear()`/`getMonth()` (local), not from the UTC
  ISO string, so a transaction near midnight buckets into the month the user sees
  it in, not UTC's.

`formatDate`/`formatDateShort` are read-only display formatting (`en-IN` locale)
and don't need this treatment.

## Keyboard shortcuts

Registered once in `FundVaultApp.jsx` via a `document.addEventListener("keydown",
...)` effect with no dependency array (it re-subscribes every render rather than
risk a stale closure over `modals`/`currentDb`/`transactions`/`activeTab`). Gates,
checked in order:
- Ignored entirely if signed out, or if Ctrl/Cmd/Alt is held.
- `Escape` closes all modals — checked *before* the `INPUT`/`TEXTAREA`/`SELECT`
  early-return, so it works even while focus is inside a form field.
- Any other shortcut is ignored while focus is in an input/textarea/select, or
  while any modal is open.
- `n` — new transaction, only when `activeTab === "db"`, a fund is open, it's not
  archived, and `can(currentUser, ACTIONS.CREATE_TXN)`.
- `d` — open "create fund", only with `ACTIONS.MANAGE_FUNDS`.
- `h`/`1`, `2`, `3`, `4` — go to Home/Dashboard/Audit/Trash (`goToTab`, which also
  clears `currentDbId`/`transactions`).
- `t` — toggle theme. `?` — open the shortcuts help modal.

## Theming

CSS custom properties on `:root` in `globals.css`, overridden under
`[data-theme="light"]` (the app defaults to dark). `FundVaultApp.jsx` sets
`document.documentElement.dataset.theme` and persists the choice to
`localStorage["fundvault_theme"]`; `DashboardView` reads the `theme` prop
directly (not the CSS variables) to pick Chart.js label/grid colors, since
Chart.js draws to canvas and can't read CSS custom properties itself.

## Charts

`DashboardView` is lazy-loaded from `FundVaultApp.jsx` via `next/dynamic` with
`ssr: false`, so `chart.js` (and its several registered controllers/elements) is
only fetched when the Dashboard tab is actually opened. Within the view, only the
Chart.js pieces it uses are imported and registered (`BarController`,
`PieController`, `LineController`, etc.) rather than the full bundle. Charts are
built from `databases` filtered to `!is_deleted && !is_archived` — the same set
the Home/summary chips use — so an archived fund's balance isn't silently double
counted into the org-wide charts while its own page still shows it. Only
`approved && !is_voided` transactions feed the charts.

## Exports and print

Both CSV and PDF export (`AppModals.jsx`, the "Export" modal) and print
(`printLedger` in `FundVaultApp.jsx`) start from `filterExportTxns()`/an
equivalent filter over `!is_voided` transactions bounded by the export date
range, parsed as local wall-clock (`` `${from}T00:00:00` `` /
`` `${to}T23:59:59` ``, not UTC midnight).

- **CSV** (`exportCSV`): built as a plain string and downloaded via a `Blob` +
  object URL. Each cell that isn't a plain number and starts with `= + - @` or a
  tab/CR is prefixed with `'` before quoting, so a note field like
  `=HYPERLINK(...)` opens as text in Excel/Sheets instead of executing as a
  formula.
- **PDF** (`exportPDF`): `jspdf`/`jspdf-autotable` are dynamically `import()`ed on
  first use (large libraries most sessions never touch), then rendered as a table
  with a totals header. Totals in both exports only sum `approved` transactions,
  matching how the running balance is computed, even though the row list itself
  still includes pending ones.
- **Print** (`printLedger`, `FundVaultApp.jsx`): opens a blank `window.open("",
  "_blank")` and writes an HTML table into it via `document.write`. Because that
  window shares the app's origin, every piece of user-supplied text (fund name,
  sender/receiver, notes, mode) is passed through a local `esc()` helper
  (`&<>"'` entity-escaped) before being interpolated — otherwise a Member's
  transaction field could inject a script that runs with whoever printed it (an
  Admin/Owner)'s session.

## Networking

`lib/api.js`'s `apiRequest` reads `NEXT_PUBLIC_API_BASE` (fallback
`http://localhost:8000`), attaches `Authorization: Bearer <token>` when given one,
JSON-encodes the body, and on a non-2xx response throws an `Error` carrying
`.status` and a message from the JSON body's `error` field (or a generic
fallback) — this is what lets `authedRequest` distinguish a 401 from other
failures. `postImage` (backing `extractReceipt`/`uploadReceipt`) sends
`multipart/form-data` and deliberately does not set a `Content-Type` header so
the browser can add the multipart boundary itself.

`next.config.js` fails the Vercel build outright if `NEXT_PUBLIC_API_BASE` is
unset (`process.env.VERCEL` truthy) or if it ends with a trailing slash — both
would otherwise build successfully and only fail at request time in the browser,
since `api.js` blindly prefixes `/api/...` onto the base.

## Auth entry point

Signed-out users see `OrgGateway` (`components/auth/OrgGateway.jsx`), which looks
up an email's orgs (`POST /api/auth/orgs`), then hands off to sign-in,
`CreateOrgForm`, or `JoinOrgForm`. On success it calls `onAuthenticated({ token,
user, org })`, which `FundVaultApp.jsx` uses to seed state and localStorage
(`handleAuthenticated`).
