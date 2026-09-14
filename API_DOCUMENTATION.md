# FundVault - API Documentation

**Base URL:** `/api/`

## Authentication model

FundVault is multi-tenant: every organisation has its own database, and a
session token is minted **for one specific organisation**. The token carries
an `org_id` claim signed by the server; presenting it to a different
organisation's endpoints does not grant access there — there is nothing to
resolve, since each org's data lives in a separate database the token's
claimed org doesn't point at. There is no cross-org session.

Most endpoints require the header:

```text
Authorization: Bearer <token>
```

A handful of endpoints are unauthenticated by necessity, because they run
before any session exists: `POST /api/orgs/validate-connection`,
`POST /api/orgs/create`, `POST /api/orgs/join/preview`, `POST /api/orgs/join`,
`POST /api/auth/orgs`, `POST /api/auth/login`, and `GET /api/health`. Every
other endpoint below is marked **Authentication: Required**, and is further
gated by role where noted (see [Permissions](#permissions)).

`POST /api/auth/login` is also unauthenticated but still org-scoped: it takes
an `orgId` in the request body (there is no token yet to carry one).

---

## Table of Contents

1. [Organisation Endpoints](#organisation-endpoints)
2. [Authentication Endpoints](#authentication-endpoints)
3. [User Management Endpoints (Admin)](#user-management-endpoints-admin)
4. [Database Endpoints](#database-endpoints)
5. [Transaction Endpoints](#transaction-endpoints)
6. [Receipt Endpoints](#receipt-endpoints)
7. [Recurring Transaction Endpoints](#recurring-transaction-endpoints)
8. [Audit Log Endpoints](#audit-log-endpoints)
9. [Trash Management Endpoints](#trash-management-endpoints)
10. [Analytics Endpoints](#analytics-endpoints)
11. [Permissions](#permissions)
12. [Data Types & Enums](#data-types--enums)

---

## Organisation Endpoints

These back org creation, joining, and org-level settings. Except for
`GET|PUT /api/orgs/settings`, none of these require a bearer token — that's
the point of them.

### 1. Validate a database connection

**POST** `/orgs/validate-connection`

Probes a candidate Postgres connection string without registering anything —
used to check a connection before committing to it. Connects directly,
checks the server version, and verifies it can `CREATE TABLE`.

**Request Body:**

```json
{ "databaseUrl": "string (required, postgres:// or postgresql://)" }
```

**Response (200):**

```json
{ "ok": "boolean", "message": "string" }
```

Note this always returns `200` — a bad connection string is reported via
`"ok": false` and a human-readable `message`, not an HTTP error status.

**Error Responses:**

- `400`: Database URL required
- `405`: Method not allowed

---

### 2. Create an organisation

**POST** `/orgs/create`

Creates a new organisation. The server validates the connection, migrates it,
creates the caller as Owner, and writes the `orgs` row only if every step
succeeds — a failed attempt leaves nothing behind to retry against.

**Request Body:**

```json
{
  "name": "string (required, org name)",
  "databaseUrl": "string (required, postgres:// connection string)",
  "username": "string (required)",
  "email": "string (required)",
  "password": "string (required, min 6 characters)"
}
```

**Response (200):**

```json
{
  "org": { "id": "string", "name": "string", "slug": "string", "created_at": "ISO 8601 datetime" },
  "token": "string",
  "user": { "...": "see serialize_user, Authentication Endpoints" }
}
```

**Error Responses:**

- `400`: All fields required / Password must be at least 6 characters / a
  connection or migration failure message (e.g. "Authentication failed —
  check the username and password.", "That host cannot be used for a tenant
  database connection.", "An organisation with a similar name already
  exists — try a different name.")
- `405`: Method not allowed
- `500`: Could not create the owner account. Please try again.

---

### 3. Preview a join code

**POST** `/orgs/join/preview`

Resolves a join code to the organisation's name and the role it grants,
without creating an account — the connection string is never sent to the
client.

**Request Body:**

```json
{ "code": "string (required, e.g. FUNDVAULT-AB3D-9KLM)" }
```

**Response (200):**

```json
{
  "org": { "id": "string", "name": "string", "slug": "string" },
  "role": "admin, member, or viewer"
}
```

**Error Responses:**

- `404`: That join code does not exist
- `400`: That join code has been revoked / has expired / has already been
  used the maximum number of times
- `405`: Method not allowed

---

### 4. Join an organisation

**POST** `/orgs/join`

Consumes a join code and creates a new user in that organisation's database,
with the role the code grants.

**Request Body:**

```json
{
  "code": "string (required)",
  "username": "string (required)",
  "email": "string (required)",
  "password": "string (required, min 6 characters)"
}
```

**Response (200):**

```json
{
  "org": { "id": "string", "name": "string", "slug": "string", "created_at": "ISO 8601 datetime" },
  "token": "string",
  "user": { "...": "see serialize_user, Authentication Endpoints" }
}
```

**Error Responses:**

- `400`: All fields required / Password must be at least 6 characters / That
  username or email is already used in this organisation / That join code is
  no longer usable / (code validity errors, as in join/preview)
- `404`: That join code does not exist
- `405`: Method not allowed
- `503`: `<org>`'s database connection is misconfigured. Contact your
  organisation's admin.

---

### 5. List / create join codes

**GET|POST** `/orgs/codes`

**Authentication:** Required. `GET` requires Admin or Owner. `POST` requires
Admin or Owner, and an Admin may only mint codes granting Member or Viewer —
only the Owner may mint an Admin-granting code.

**GET Response (200):**

```json
[
  {
    "code": "string",
    "grants_role": "admin, member, or viewer",
    "expires_at": "ISO 8601 datetime",
    "max_uses": "integer",
    "uses": "integer",
    "revoked": "boolean"
  }
]
```

**POST Request Body:**

```json
{
  "role": "admin, member, or viewer (default: member)",
  "maxUses": "integer (optional, default 1, clamped 1-100)",
  "expiresInDays": "integer (optional, default 14, clamped 1-90)"
}
```

**POST Response (200):** same shape as one item of the GET array above
(`uses` starts at `0`, `revoked` at `false`).

**Error Responses:**

- `400`: You cannot create a join code granting `<role>` / maxUses and
  expiresInDays must be numbers
- `401`: Unauthorized
- `403`: Admin access required
- `405`: Method not allowed

---

### 6. Revoke a join code

**DELETE** `/orgs/codes/<code>`

**Authentication:** Required (Admin or Owner).

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: Admin access required
- `404`: Join code not found
- `405`: Method not allowed

---

### 7. Get / update organisation settings

**GET|PUT** `/orgs/settings`

**Authentication:** Required (Owner only — this is the one place database,
storage, and AI credentials can be viewed or changed).

**GET Response (200):**

```json
{
  "org": { "id": "string", "name": "string", "slug": "string", "created_at": "ISO 8601 datetime" },
  "storage": null,
  "ai": {
    "primary": null,
    "fallback": null
  }
}
```

When storage is configured, `"storage"` is instead:

```json
{
  "endpoint_url": "string",
  "bucket": "string",
  "region": "string",
  "access_key": "string (masked, e.g. ••••3f2a)",
  "secret_key": "string (masked)"
}
```

When a provider slot is configured, `"primary"`/`"fallback"` is instead:

```json
{
  "provider": "openai_compatible or gemini",
  "base_url": "string (empty for gemini)",
  "model": "string",
  "api_key": "string (masked)"
}
```

Masked values never round-trip a usable secret — a secret 8 characters or
shorter is masked to `""`. Neither `secret_key` nor `api_key` can be read
back in full through this endpoint by anyone, including another Owner.

**PUT Request Body:** either or both of:

```json
{
  "storage": {
    "endpoint_url": "string",
    "bucket": "string",
    "region": "string (optional, default \"auto\")",
    "access_key": "string",
    "secret_key": "string"
  },
  "ai": {
    "primary": { "provider": "openai_compatible", "base_url": "string", "model": "string", "api_key": "string" },
    "fallback": { "provider": "gemini", "model": "string", "api_key": "string" }
  }
}
```

Both `storage` and `ai` are validated with a live probe call before being
saved (a small object round-trip for storage, a cheap list/models call for
AI) — a bad key or endpoint is rejected here rather than at first use.

**PUT Response (200):**

```json
{ "success": true, "updated": ["storage_config", "ai_config"] }
```

**Error Responses:**

- `400`: Storage config is missing: ... / Storage check failed: ... / No
  usable AI provider in that configuration / AI provider check failed: ... /
  Nothing to update
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `405`: Method not allowed

---

### 8. Health check

**GET** `/health`

Liveness probe. Touches only the control-plane database, never a tenant's.

**Response (200):**

```json
{ "status": "ok" }
```

---

## Authentication Endpoints

There is no `POST /auth/signup` — the only ways to get an account are
[Create an organisation](#2-create-an-organisation) and
[Join an organisation](#4-join-an-organisation), above.

### 1. Which organisations does this email belong to?

**POST** `/auth/orgs`

Discovery step for returning users — does not authenticate anything.

**Request Body:**

```json
{ "email": "string (required)" }
```

**Response (200):**

```json
{ "orgs": [{ "id": "string", "name": "string", "slug": "string" }] }
```

An email with no matching organisation returns `{"orgs": []}`, not an error —
the frontend is expected to show "create one, or ask an admin for a join
code."

**Error Responses:**

- `400`: Email required
- `405`: Method not allowed

---

### 2. Login

**POST** `/auth/login`

**Request Body:**

```json
{
  "orgId": "string (required — from the org picker after auth/orgs)",
  "username": "string (username or email, required)",
  "password": "string (required)"
}
```

**Response (200):**

```json
{ "token": "string", "user": { "id": "string", "username": "string", "email": "string", "profile_image": "string or null", "role": "owner, admin, member, or viewer", "is_active": "boolean", "created_at": "ISO 8601 datetime", "updated_at": "ISO 8601 datetime or null" } }
```

The returned token is minted for `orgId` only — it will not authenticate
against any other organisation.

**Error Responses:**

- `400`: Choose an organisation first (missing `orgId`)
- `404`: Organisation not found
- `401`: Invalid credentials
- `403`: Account is inactive
- `405`: Method not allowed
- `503`: `<org>`'s database connection is misconfigured. Contact your
  organisation's admin.

---

### 3. Logout

**POST** `/auth/logout`

**Authentication:** Required

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

### 4. Get current user profile

**GET** `/auth/me`

**Authentication:** Required

**Response (200):**

```json
{
  "id": "string",
  "username": "string",
  "email": "string",
  "profile_image": "string or null",
  "role": "owner, admin, member, or viewer",
  "is_active": "boolean",
  "created_at": "ISO 8601 datetime",
  "updated_at": "ISO 8601 datetime or null"
}
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

### 5. Update current user profile

**PUT** `/auth/me`

**Authentication:** Required

**Request Body:**

```json
{
  "username": "string (optional)",
  "email": "string (optional)",
  "profile_image": "string or null (optional, base64 encoded image)",
  "currentPassword": "string (required if changing password)",
  "newPassword": "string (optional, min 6 characters)",
  "confirmPassword": "string (optional, must match newPassword)"
}
```

**Response (200):** the updated user object, same shape as `GET /auth/me`.
Changing the password invalidates every other session for this user (the
current one is kept).

**Error Responses:**

- `400`: Username and email are required / Password must be at least 6
  characters / Passwords do not match / Username or email already exists
- `401`: Current password is incorrect / Unauthorized
- `405`: Method not allowed

---

## User Management Endpoints (Admin)

**Authentication:** Required on all of these. Listing, updating, and deleting
require Admin or Owner (`MANAGE_MEMBERS`); changing a user's `role` requires
Owner specifically (`CHANGE_ROLE`); transferring ownership requires Owner
(`TRANSFER_OWNERSHIP`).

### 1. List all users

**GET** `/admin/users`

**Response (200):**

```json
[
  {
    "id": "string",
    "username": "string",
    "email": "string",
    "profile_image": "string or null",
    "role": "owner, admin, member, or viewer",
    "is_active": "boolean",
    "created_at": "ISO 8601 datetime",
    "updated_at": "ISO 8601 datetime or null",
    "database_count": "integer",
    "active_database_count": "integer",
    "transaction_count": "integer"
  }
]
```

Sorted admins/owners first, then by `created_at`.

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `405`: Method not allowed

---

### 2. Update a user

**PUT** `/admin/users/<user_id>`

There is no `GET /admin/users/<user_id>` — only `PUT` and `DELETE` exist on
this path; a `GET` here returns `405`.

**Request Body:**

```json
{
  "username": "string (optional)",
  "email": "string (optional)",
  "role": "admin, member, or viewer (optional — owner is rejected, see below)",
  "is_active": "boolean (optional)"
}
```

**Response (200):** the updated user object (same shape as `GET /auth/me`).

**Behavior:**

- Setting `role` to anything other than the target's current role requires
  Owner; setting it to `owner` is always rejected — use
  [transfer-ownership](#5-transfer-ownership) instead.
- You cannot deactivate your own account.
- An organisation must always have an active Owner — demoting or
  deactivating the sole Owner is rejected.

**Error Responses:**

- `400`: Username and email are required / Use transfer-ownership to make
  someone the Owner / Invalid role / You cannot deactivate your own account /
  An organisation must always have an active Owner / Username or email
  already exists
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: User not found
- `405`: Method not allowed

---

### 3. Delete a user

**DELETE** `/admin/users/<user_id>`

Deletes the user and their sessions; their trash items are deleted and their
audit log entries are kept with `user_id` cleared to `null` rather than
deleted.

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `400`: You cannot delete your own account / An organisation must always
  have an active Owner
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: User not found
- `405`: Method not allowed

---

### 4. Reset a user's password

**POST** `/admin/users/<user_id>/reset-password`

Resets the password and deletes all of that user's sessions.

**Request Body:**

```json
{ "newPassword": "string (required, min 6 characters)" }
```

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `400`: Password must be at least 6 characters
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: User not found
- `405`: Method not allowed

---

### 5. Transfer ownership

**POST** `/admin/users/<user_id>/transfer-ownership`

Hands Owner to another active member; the caller becomes Admin. Requires
Owner. Guarded against a race between two concurrent transfer attempts from
the same Owner with `select_for_update()` — the second request re-reads the
caller's role after the first commits and is rejected if it's no longer
Owner.

**Response (200):**

```json
{ "success": true, "owner": { "...": "the promoted user, same shape as GET /auth/me" } }
```

**Error Responses:**

- `400`: You are already the Owner / You are no longer the Owner
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: User not found
- `405`: Method not allowed

---

## Database Endpoints

"Database" here means a fund/ledger, not a Postgres instance. Funds are
visible to the whole organisation — there is no per-fund access list.

**Authentication:** Required on all of these. Creating, updating, deleting,
archiving, and merging require Admin or Owner (`MANAGE_FUNDS`); reading is
open to any role, including Viewer.

### 1. List databases

**GET** `/databases`

**Response (200):**

```json
[
  {
    "id": "string",
    "created_by": "string (user id)",
    "name": "string",
    "description": "string",
    "balance": "float",
    "low_balance_threshold": "float",
    "approval_threshold": "float",
    "is_archived": "boolean",
    "is_deleted": "boolean",
    "created_at": "ISO 8601 datetime"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

### 2. Create a database

**POST** `/databases`

**Request Body:**

```json
{
  "name": "string (required)",
  "description": "string (optional)",
  "lowBalanceThreshold": "float (optional, default 0)",
  "approvalThreshold": "float (optional, default 0)"
}
```

**Response (200):** the created database, same shape as one item of the
`GET /databases` array above.

**Error Responses:**

- `400`: Name required
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `405`: Method not allowed

---

### 3. Get database detail

**GET** `/databases/<database_id>`

**Response (200):** the database object plus its transactions:

```json
{
  "id": "string",
  "created_by": "string",
  "name": "string",
  "description": "string",
  "balance": "float",
  "low_balance_threshold": "float",
  "approval_threshold": "float",
  "is_archived": "boolean",
  "is_deleted": "boolean",
  "created_at": "ISO 8601 datetime",
  "transactions": [ "see Transaction Endpoints for the shape" ]
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 4. Update a database

**PUT** `/databases/<database_id>`

**Request Body:**

```json
{
  "name": "string (required)",
  "description": "string (optional)",
  "lowBalanceThreshold": "float (optional)",
  "approvalThreshold": "float (optional)"
}
```

**Response (200):** the updated database (same shape as `POST /databases`).

**Error Responses:**

- `400`: Name required
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Database not found
- `405`: Method not allowed

---

### 5. Delete a database

**DELETE** `/databases/<database_id>`

Soft-deletes (moves to trash). Use the trash endpoints to permanently delete
or restore.

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Database not found
- `405`: Method not allowed

---

### 6. Archive/unarchive a database

**POST** `/databases/<database_id>/archive`

Toggles the archived flag.

**Response (200):**

```json
{ "success": true, "is_archived": "boolean" }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Database not found
- `405`: Method not allowed

---

### 7. Merge databases

**POST** `/databases/merge`

Creates a new database containing both source databases' transactions,
archives the sources, and recalculates running balances.

**Request Body:**

```json
{
  "sourceId": "string (required)",
  "targetId": "string (required)",
  "name": "string (required, name for the merged database)"
}
```

**Response (200):** the newly created merged database.

**Error Responses:**

- `400`: Source, target, and name are required / Cannot merge a database
  with itself
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Database not found
- `405`: Method not allowed

---

## Transaction Endpoints

**Authentication:** Required on all of these. Creating a transaction
requires Member or above (`CREATE_TXN` — Viewer cannot create); editing,
voiding, and permanently deleting a voided transaction require Admin or
Owner (`MODIFY_TXN`); approving requires Admin or Owner (`APPROVE`, and an
Admin/Owner may approve their own transaction).

### 1. List a database's transactions

**GET** `/databases/<database_id>/transactions`

**Response (200):**

```json
[
  {
    "id": "string",
    "database_id": "string",
    "type": "credit or debit",
    "amount": "float",
    "date": "ISO 8601 datetime",
    "sender": "string",
    "receiver": "string",
    "mode": "electronic, cheque, or cash",
    "mode_data": {
      "elecId": "string (electronic mode)",
      "chequeNo": "string (cheque mode)",
      "chequeDate": "string (cheque mode)",
      "chequeBank": "string (cheque mode)"
    },
    "location": "string",
    "notes": "string",
    "running_balance": "float",
    "receipt_key": "string or null (object storage key, not a URL)",
    "receipt_url": "string or null (signed URL, valid ~1 hour, only present when storage is configured and receipt_key is set)",
    "requires_approval": "boolean",
    "approved": "boolean",
    "approved_by": "string or null",
    "approved_at": "ISO 8601 datetime or null",
    "is_voided": "boolean",
    "void_reason": "string or null",
    "voided_by": "string or null",
    "voided_at": "ISO 8601 datetime or null",
    "created_by": "string (user id)",
    "created_at": "ISO 8601 datetime"
  }
]
```

There is no `receipt_image` field any more — receipts are objects in the
org's own storage, referenced by `receipt_key` and read through a freshly
signed `receipt_url` (never a permanent link). See
[Receipt Endpoints](#receipt-endpoints) to attach one.

For `electronic` and `cheque` transactions, the same values nested under
`mode_data` are also duplicated as flattened top-level keys (`elecId`, or
`chequeNo`/`chequeDate`/`chequeBank`) alongside the fields above, present
only when set. `mode_data` is the canonical field; the flattened keys exist
for convenience and always mirror it.

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 2. Create a transaction

**POST** `/databases/<database_id>/transactions`

**Request Body:**

```json
{
  "type": "credit or debit (required)",
  "amount": "float (required, must be > 0)",
  "date": "ISO 8601 datetime (required)",
  "sender": "string (optional)",
  "receiver": "string (optional)",
  "mode": "electronic, cheque, or cash (required)",
  "modeData": {
    "elecId": "string (for electronic mode)",
    "chequeNo": "string (for cheque mode)",
    "chequeDate": "string (for cheque mode)",
    "chequeBank": "string (for cheque mode)"
  },
  "location": "string (optional)",
  "notes": "string (optional)"
}
```

This no longer accepts `receiptImage` — a transaction is created without a
receipt, then a receipt is attached separately with
`POST /transactions/<id>/receipt`.

**Response (200):**

```json
{
  "transaction": { "...": "see List a database's transactions above" },
  "requiresApproval": "boolean",
  "newBalance": "float"
}
```

**Behavior:**

- Approval is required only when the creator is a Member and
  `amount >= approvalThreshold > 0` — Owners and Admins never require
  approval regardless of amount.
- The fund's balance row is locked (`select_for_update`) before the balance
  check and write, so two concurrent transactions against the same fund
  cannot both pass an insufficient-balance check.
- For approved-on-creation transactions the balance updates immediately; for
  transactions pending approval it does not, until
  [approved](#5-approve-a-transaction).
- A debit cannot take the balance negative.

**Error Responses:**

- `400`: Invalid transaction type / Invalid transaction mode / Amount must be
  greater than 0 / Transaction date is required / Insufficient balance
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Database not found
- `405`: Method not allowed

---

### 3. Update a transaction

**PUT** `/transactions/<transaction_id>`

Cannot edit a voided transaction. Editing the amount or date recalculates
running balances for the whole fund.

**Request Body:**

```json
{
  "amount": "float (optional)",
  "date": "ISO 8601 datetime (optional)",
  "sender": "string (optional)",
  "receiver": "string (optional)",
  "location": "string (optional)",
  "notes": "string (optional)"
}
```

**Response (200):** the updated transaction (same shape as in the list
endpoint above).

**Error Responses:**

- `400`: Enter a valid amount / Cannot edit a voided transaction /
  Transaction date is required
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Transaction not found
- `405`: Method not allowed

---

### 4. Void a transaction

**POST** `/transactions/<transaction_id>/void`

**Request Body:**

```json
{ "reason": "string (required)" }
```

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `400`: Void reason required / Transaction is already voided
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Transaction not found
- `405`: Method not allowed

---

### 5. Approve a transaction

**POST** `/transactions/<transaction_id>/approve`

Only valid for a transaction with `requires_approval: true` and
`approved: false`. The approver's own transaction may be approved — the
compensating control for that is the audit trail (`created_by` and
`approved_by` are both recorded), not a block.

**Response (200):**

```json
{ "success": true, "newBalance": "float" }
```

**Error Responses:**

- `400`: Cannot approve a voided transaction / Transaction is already
  approved / Transaction does not require approval / Insufficient balance to
  approve this debit transaction
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Transaction not found
- `405`: Method not allowed

---

### 6. Permanently delete a voided transaction

**DELETE** `/transactions/<transaction_id>/delete`

Only valid for a transaction that is already voided; recalculates running
balances for the fund afterward.

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `400`: Only voided transactions can be deleted
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Transaction not found
- `405`: Method not allowed

---

## Receipt Endpoints

Two separate endpoints: one extracts structured data from an image with AI
(does not save anything), the other attaches an image to a transaction as
its receipt (no AI involved). Both require `CREATE_TXN` (Member or above).

### 1. Extract details from a receipt image

**POST** `/extract-receipt`

Uses the calling organisation's own configured AI provider(s) — see
`GET|PUT /api/orgs/settings` — trying the primary first, then the fallback
on any failure. There is no server-wide provider any more.

**Request Body:**

```text
Content-Type: multipart/form-data

image: <image file, up to 5 MB>
```

Note the field name is `image`, not `file`.

**Response (200):** on success —

```json
{
  "amount": "float or null",
  "date": "ISO 8601 datetime string or null",
  "sender": "string or null",
  "receiver": "string or null",
  "reference_id": "string or null",
  "mode": "electronic, cheque, or cash, or null",
  "confidence": "float, 0.0-1.0",
  "_provider": "string (the configured model name that produced this result)"
}
```

On failure, the response is `{"error": "string"}` — and note this is
returned with HTTP `200`, *not* an error status, whenever extraction was
attempted and failed (both providers errored, or the response couldn't be
parsed as JSON). The only case that gets a non-200 status is no AI provider
being configured at all for this organisation, which returns `503`.

**Error Responses:**

- `400`: No image file provided / Image must be less than 5 MB
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `405`: Method not allowed
- `503`: Receipt extraction is not configured for this organisation. An
  Owner can add an AI provider in organisation settings. (returned inside the
  normal `{"error": ...}` body, not a separate shape)

---

### 2. Attach a receipt to a transaction

**POST** `/transactions/<transaction_id>/receipt`

Compresses and uploads the image to the organisation's configured object
storage (max 1024px, JPEG quality 75) at
`receipts/<database_id>/<transaction_id>.jpg`, and stores the object key on
the transaction.

**Request Body:**

```text
Content-Type: multipart/form-data

image: <image file, up to 5 MB>
```

**Response (200):**

```json
{ "receipt_url": "string (freshly signed URL, valid ~1 hour)" }
```

**Error Responses:**

- `400`: No image file provided / Image must be less than 5 MB / That file
  is not a readable image
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Transaction not found
- `405`: Method not allowed
- `502`: Could not upload the receipt: `<reason>`
- `503`: Receipt storage is not configured for this organisation. An Owner
  can add it in organisation settings. / a storage misconfiguration message

---

## Recurring Transaction Endpoints

**Authentication:** Required on all of these. Reading is open to any role;
creating and deleting require Admin or Owner (`MANAGE_FUNDS`); processing due
transactions requires Member or above (`CREATE_TXN`, since it posts real
transactions).

### 1. List recurring transactions

**GET** `/databases/<database_id>/recurring`

**Response (200):**

```json
[
  {
    "id": "string",
    "database_id": "string",
    "type": "credit or debit",
    "amount": "float",
    "frequency": "daily, weekly, monthly, or yearly",
    "description": "string",
    "next_run": "ISO 8601 date (YYYY-MM-DD)",
    "is_active": "boolean",
    "created_by": "string (user id)",
    "created_at": "ISO 8601 datetime"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 2. Create a recurring transaction

**POST** `/databases/<database_id>/recurring`

**Request Body:**

```json
{
  "type": "credit or debit (required)",
  "amount": "float (required, must be > 0)",
  "frequency": "daily, weekly, monthly, or yearly (required)",
  "description": "string (required)",
  "nextRun": "ISO 8601 date string (required, format: YYYY-MM-DD)"
}
```

**Response (200):** the created recurring transaction (same shape as above).

**Error Responses:**

- `400`: Invalid transaction type / Amount must be greater than 0 / Invalid
  frequency / Description required / Invalid next run date
- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Database not found
- `405`: Method not allowed

---

### 3. Delete a recurring transaction

**DELETE** `/recurring/<recurring_id>`

Deactivates it (`is_active` becomes `false`) rather than removing the row.

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Recurring transaction not found
- `405`: Method not allowed

---

### 4. Process due recurring transactions

**POST** `/recurring/process`

Creates a real transaction for every active recurring transaction whose
`next_run` has passed, and advances `next_run`.

**Response (200):**

```json
{ "success": true, "processed": "integer" }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `405`: Method not allowed

---

## Audit Log Endpoints

**Authentication:** Required. No role gate.

### 1. List audit logs

**GET** `/audit`

Returns the **whole organisation's** audit trail (not just the calling
user's own actions), most recent 500 first. The tenant database connection
is already the org boundary, so no additional per-user filter is applied —
every member, including Viewer, can see every other member's actions.

**Response (200):**

```json
[
  {
    "id": "string",
    "user_id": "string or null",
    "action": "string (create, update, delete, login, logout, void, etc.)",
    "entity_type": "string (org, user, join_code, database, transaction, recurring, etc.)",
    "entity_id": "string or null",
    "details": "string",
    "timestamp": "ISO 8601 datetime"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

## Trash Management Endpoints

**Authentication:** Required. Reading is open to any role; restoring,
permanently deleting, and emptying trash require Admin or Owner
(`MANAGE_FUNDS`). All of these operate on the **whole organisation's** trash
— an Admin or Owner can restore or delete any member's deleted item, not
just their own.

### 1. List trash items

**GET** `/trash`

**Response (200):**

```json
[
  {
    "id": "string",
    "entity_type": "string (database)",
    "entity_data": "JSON string (original data of the deleted item)",
    "deleted_at": "ISO 8601 datetime",
    "deleted_by": "string (user id)"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

### 2. Restore an item from trash

**POST** `/trash/<item_id>/restore`

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Item not found
- `405`: Method not allowed

---

### 3. Permanently delete an item

**DELETE** `/trash/<item_id>`

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `404`: Item not found
- `405`: Method not allowed

---

### 4. Empty trash

**DELETE** `/trash`

Permanently deletes every item in the organisation's trash.

**Response (200):**

```json
{ "success": true }
```

**Error Responses:**

- `401`: Unauthorized
- `403`: You do not have permission to do that
- `405`: Method not allowed

---

## Analytics Endpoints

**Authentication:** Required. No role gate.

### 1. Get overview analytics

**GET** `/analytics/overview`

Totals across every non-deleted fund in the organisation.

**Response (200):**

```json
{
  "totalDatabases": "integer",
  "totalBalance": "float (sum of all non-deleted funds' balances)",
  "totalCredits": "float (sum of all non-voided credit transactions)",
  "totalDebits": "float (sum of all non-voided debit transactions)"
}
```

When the organisation has no funds yet, the response instead has six keys —
the four above (all zero) plus two empty placeholder arrays, `monthlyData`
and `modeData`. Neither key is populated in the normal (non-zero) response
above, and neither is currently consumed by the frontend.

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

## Permissions

Every member of an organisation holds exactly one role, checked centrally
(`apps.accounts.permissions`) rather than scattered per view:

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

Notes:

- An organisation always has exactly one Owner; the sole Owner cannot be
  demoted, deactivated, or deleted.
- Self-approval is allowed by design — an Admin/Owner may approve their own
  transaction. `created_by` and `approved_by` are both recorded on every
  transaction, so a self-approval is always visible in the audit trail even
  though it isn't blocked.
- A `403` from any gated endpoint returns `{"error": "You do not have
  permission to do that"}`, except the join-code endpoints, which return
  `{"error": "Admin access required"}`.

---

## Data Types & Enums

### User roles

- `owner` — full control, including database/storage/AI configuration and
  ownership transfer; there is exactly one per organisation
- `admin` — manages funds, transactions, and members, but not org
  configuration or ownership
- `member` — creates transactions; subject to the approval threshold
- `viewer` — read-only

### Transaction types

- `credit` — money in
- `debit` — money out

### Transaction modes

- `electronic` — electronic transfer (carries `elecId`)
- `cheque` — cheque payment (carries `chequeNo`, `chequeDate`, `chequeBank`)
- `cash` — cash transaction

### AI provider types

- `openai_compatible` — any endpoint speaking the OpenAI chat-completions API
  (NVIDIA NIM, OpenRouter, Groq, a local vLLM server, ...): configured with a
  base URL, model name, and key
- `gemini` — Google Gemini, via its own SDK: configured with a model name and
  key, no base URL

Each organisation configures its own primary and optional fallback provider
independently; there is no shared or default provider.

### Recurring frequencies

- `daily`, `weekly`, `monthly`, `yearly`

### Common response codes

- `200` — Success
- `400` — Bad request (validation error)
- `401` — Unauthorized (missing/invalid/expired token)
- `403` — Forbidden (insufficient role, or wrong permission for the action)
- `404` — Not found
- `405` — Method not allowed
- `500` — Unexpected server-side failure (e.g. owner-account creation during
  org creation)
- `502` — Upstream failure (receipt storage upload)
- `503` — This organisation's dependency isn't reachable or configured
  (tenant database, receipt storage, or AI provider)

### Timestamp format

ISO 8601 with timezone information, e.g. `2026-06-19T10:16:48.756+00:00`.

---

## Request/response format

All requests and responses use JSON (`Content-Type: application/json`)
except the two multipart file uploads noted above. Every error response has
the shape:

```json
{ "error": "string" }
```

## Rate limiting

There is none. Each organisation supplies and pays for its own AI key, so
usage is that organisation's decision — see the design spec for the
reasoning. Two client-side behaviors substitute for a server-side limit:
a failed extraction does not auto-retry, and an identical image hash within
a session reuses its previous result rather than calling the provider again.

## Notes

- All timestamps are stored in UTC with timezone offset information.
- Fund balances and transaction amounts are stored as floats, not fixed-point
  decimals.
- Receipts are never stored inline as base64 — only an object key
  (`receipt_key`) plus a signed URL minted at read time (`receipt_url`,
  ~1 hour validity). An org without storage configured simply has no
  `receipt_url` on any transaction and cannot attach new receipts, but
  everything else works.
- Audit logs and trash listings are scoped to the organisation (the tenant
  database connection is already the org boundary), not to the calling
  user — every member sees every other member's actions and deletions. This
  means `entity_id`/`details` on an audit entry must never carry a raw
  secret: join codes are never written to the audit trail at all
  (`entity_id=None`), for example.
