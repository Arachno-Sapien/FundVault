# Fund Management System - API Documentation

**Base URL:** `/api/`

**Authentication:** Most endpoints require a valid session token passed in the `Authorization` header: `Authorization: Bearer <token>`

---

## Table of Contents

1. [Authentication Endpoints](#authentication-endpoints)
2. [User Management Endpoints](#user-management-endpoints)
3. [Database Endpoints](#database-endpoints)
4. [Transaction Endpoints](#transaction-endpoints)
5. [Receipt Extraction Endpoints](#receipt-extraction-endpoints)
6. [Recurring Transaction Endpoints](#recurring-transaction-endpoints)
7. [Audit Log Endpoints](#audit-log-endpoints)
8. [Trash Management Endpoints](#trash-management-endpoints)
9. [Analytics Endpoints](#analytics-endpoints)
10. [Data Types & Enums](#data-types--enums)

---

## Authentication Endpoints

### 1. Sign Up

**POST** `/auth/signup`

Creates a new user account. The first user registered becomes an admin; subsequent users are members.

**Request Body:**

```json
{
  "username": "string (required, unique)",
  "email": "string (required, valid email, unique)",
  "password": "string (required, min 6 characters)"
}
```

**Response (200):**

```json
{
  "token": "string (session token for authentication)",
  "user": {
    "id": "string",
    "username": "string",
    "email": "string",
    "profile_image": "string or null",
    "role": "admin or member",
    "is_active": "boolean",
    "created_at": "ISO 8601 datetime",
    "updated_at": "ISO 8601 datetime or null"
  }
}
```

**Error Responses:**

- `400`: All fields required / Username or email already exists / Password must be at least 6 characters
- `405`: Method not allowed

---

### 2. Login

**POST** `/auth/login`

Authenticates a user and returns a session token.

**Request Body:**

```json
{
  "username": "string (username or email, required)",
  "password": "string (required)"
}
```

**Response (200):**

```json
{
  "token": "string (session token for authentication)",
  "user": { }
}
```

**Error Responses:**

- `401`: Invalid credentials
- `403`: Account is inactive
- `405`: Method not allowed

---

### 3. Logout

**POST** `/auth/logout`

Logs out the authenticated user and invalidates their session token.

**Authentication:** Required

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `401`: Unauthorized (invalid or missing token)
- `405`: Method not allowed

---

### 4. Get Current User Profile

**GET** `/auth/me`

Retrieves the profile of the currently authenticated user.

**Authentication:** Required

**Response (200):**

```json
{
  "id": "string",
  "username": "string",
  "email": "string",
  "profile_image": "string or null",
  "role": "admin or member",
  "is_active": "boolean",
  "created_at": "ISO 8601 datetime",
  "updated_at": "ISO 8601 datetime or null"
}
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

### 5. Update Current User Profile

**PUT** `/auth/me`

Updates the profile of the currently authenticated user.

**Authentication:** Required

**Request Body:**

```json
{
  "username": "string (optional, must be unique if provided)",
  "email": "string (optional, must be valid email and unique if provided)",
  "profile_image": "string or null (optional, base64 encoded image)",
  "currentPassword": "string (required if changing password)",
  "newPassword": "string (optional, min 6 characters)",
  "confirmPassword": "string (optional, must match newPassword)"
}
```

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Username and email are required / Password must be at least 6 characters / Passwords do not match / Username or email already exists
- `401`: Current password is incorrect / Unauthorized
- `405`: Method not allowed

---

## User Management Endpoints

### 1. List All Users (Admin Only)

**GET** `/admin/users`

Retrieves a list of all users with their database and transaction statistics.

**Authentication:** Required (Admin role)

**Response (200):**

```json
[
  {
    "id": "string",
    "username": "string",
    "email": "string",
    "profile_image": "string or null",
    "role": "admin or member",
    "is_active": "boolean",
    "created_at": "ISO 8601 datetime",
    "updated_at": "ISO 8601 datetime or null",
    "database_count": "integer",
    "active_database_count": "integer",
    "transaction_count": "integer"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `403`: Forbidden (not admin)
- `405`: Method not allowed

---

### 2. Get User Detail (Admin Only)

**GET** `/admin/users/<user_id>`

Retrieves detailed information about a specific user.

**Authentication:** Required (Admin role)

**Response (200):**

```json
{
}
```

**Error Responses:**

- `401`: Unauthorized
- `403`: Forbidden (not admin)
- `404`: User not found
- `405`: Method not allowed

---

### 3. Update User (Admin Only)

**PUT** `/admin/users/<user_id>`

Updates a user's profile, role, or active status.

**Authentication:** Required (Admin role)

**Request Body:**

```json
{
  "username": "string (optional)",
  "email": "string (optional)",
  "role": "admin or member (optional)",
  "is_active": "boolean (optional)"
}
```

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Username and email are required / Invalid role / You cannot deactivate your own account / At least one active admin account is required / Username or email already exists
- `401`: Unauthorized
- `403`: Forbidden (not admin)
- `404`: User not found
- `405`: Method not allowed

---

### 4. Delete User (Admin Only)

**DELETE** `/admin/users/<user_id>`

Deletes a user account and all associated data (databases, transactions, recurring transactions, audit logs).

**Authentication:** Required (Admin role)

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `400`: You cannot delete your own account / At least one active admin account is required
- `401`: Unauthorized
- `403`: Forbidden (not admin)
- `404`: User not found
- `405`: Method not allowed

---

### 5. Reset User Password (Admin Only)

**POST** `/admin/users/<user_id>/reset-password`

Resets a user's password and logs out all their sessions.

**Authentication:** Required (Admin role)

**Request Body:**

```json
{
  "newPassword": "string (required, min 6 characters)"
}
```

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `400`: Password must be at least 6 characters
- `401`: Unauthorized
- `403`: Forbidden (not admin)
- `404`: User not found
- `405`: Method not allowed

---

## Database Endpoints

### 1. List Databases

**GET** `/databases`

Retrieves all active (non-deleted) databases for the authenticated user.

**Authentication:** Required

**Response (200):**

```json
[
  {
    "id": "string",
    "user_id": "string",
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

### 2. Create Database

**POST** `/databases`

Creates a new database for the authenticated user.

**Authentication:** Required

**Request Body:**

```json
{
  "name": "string (required)",
  "description": "string (optional)",
  "lowBalanceThreshold": "float (optional, default 0)",
  "approvalThreshold": "float (optional, default 0)"
}
```

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Name required
- `401`: Unauthorized
- `405`: Method not allowed

---

### 3. Get Database Detail

**GET** `/databases/<database_id>`

Retrieves a specific database with all its transactions.

**Authentication:** Required

**Response (200):**

```json
{
  "id": "string",
  "user_id": "string",
  "name": "string",
  "description": "string",
  "balance": "float",
  "low_balance_threshold": "float",
  "approval_threshold": "float",
  "is_archived": "boolean",
  "is_deleted": "boolean",
  "created_at": "ISO 8601 datetime",
  "transactions": [
  ]
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 4. Update Database

**PUT** `/databases/<database_id>`

Updates database settings.

**Authentication:** Required

**Request Body:**

```json
{
  "name": "string (required)",
  "description": "string (optional)",
  "lowBalanceThreshold": "float (optional)",
  "approvalThreshold": "float (optional)"
}
```

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Name required
- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 5. Delete Database

**DELETE** `/databases/<database_id>`

Soft-deletes a database (moves to trash, data preserved). To permanently delete, use the trash endpoint.

**Authentication:** Required

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 6. Archive/Unarchive Database

**POST** `/databases/<database_id>/archive`

Toggles the archived status of a database.

**Authentication:** Required

**Response (200):**

```json
{
  "success": true,
  "is_archived": "boolean"
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 7. Merge Databases

**POST** `/databases/merge`

Merges two databases into a new database, archiving the source and target.

**Authentication:** Required

**Request Body:**

```json
{
  "sourceId": "string (required)",
  "targetId": "string (required)",
  "name": "string (required, name for merged database)"
}
```

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Source, target, and name are required / Cannot merge a database with itself
- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

## Transaction Endpoints

### 1. List Database Transactions

**GET** `/databases/<database_id>/transactions`

Retrieves all transactions for a specific database.

**Authentication:** Required

**Response (200):**

```json
[
  {
    "id": "string",
    "database_id": "string",
    "type": "credit or debit",
    "amount": "float",
    "date": "ISO 8601 datetime",
    "sender": "string or null",
    "receiver": "string or null",
    "mode": "electronic, cheque, or cash",
    "mode_data": {
      "elecId": "string (if electronic mode)",
      "chequeNo": "string (if cheque mode)",
      "chequeDate": "string (if cheque mode)",
      "chequeBank": "string (if cheque mode)"
    },
    "location": "string or null",
    "notes": "string or null",
    "running_balance": "float",
    "receipt_image": "string (base64) or null",
    "requires_approval": "boolean",
    "approved": "boolean",
    "approved_by": "string or null",
    "approved_at": "ISO 8601 datetime or null",
    "is_voided": "boolean",
    "void_reason": "string or null",
    "voided_by": "string or null",
    "voided_at": "ISO 8601 datetime or null",
    "created_at": "ISO 8601 datetime"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 2. Create Transaction

**POST** `/databases/<database_id>/transactions`

Creates a new transaction in a database.

**Authentication:** Required

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
  "notes": "string (optional)",
  "receiptImage": "string (base64 encoded image, optional)"
}
```

**Response (200):**

```json
{
  "transaction": {
  },
  "requiresApproval": "boolean",
  "newBalance": "float"
}
```

**Behavior:**

- If `amount >= approvalThreshold` and `approvalThreshold > 0`, transaction requires approval and `approved` is false
- For approved transactions, the database balance is updated immediately
- For pending approval, balance remains unchanged until approved
- For debit transactions, balance cannot go negative (insufficient balance error)

**Error Responses:**

- `400`: Invalid transaction type / Invalid transaction mode / Amount must be greater than 0 / Transaction date is required / Insufficient balance
- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 3. Update Transaction

**PUT** `/transactions/<transaction_id>`

Updates an existing transaction (cannot be voided).

**Authentication:** Required

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

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Enter a valid amount / Cannot edit a voided transaction / Transaction date is required
- `401`: Unauthorized
- `404`: Transaction not found
- `405`: Method not allowed

---

### 4. Void Transaction

**POST** `/transactions/<transaction_id>/void`

Marks a transaction as voided with a reason.

**Authentication:** Required

**Request Body:**

```json
{
  "reason": "string (required)"
}
```

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `400`: Void reason required / Transaction is already voided
- `401`: Unauthorized
- `404`: Transaction not found
- `405`: Method not allowed

---

### 5. Approve Transaction

**POST** `/transactions/<transaction_id>/approve`

Approves a pending transaction (only for transactions with `requires_approval: true` and `approved: false`).

**Authentication:** Required

**Response (200):**

```json
{
  "success": true,
  "newBalance": "float"
}
```

**Behavior:**

- Updates database balance
- Sets `approved` to true, `approved_by` to username, `approved_at` to current timestamp

**Error Responses:**

- `400`: Cannot approve a voided transaction / Transaction is already approved / Transaction does not require approval / Insufficient balance to approve this debit transaction
- `401`: Unauthorized
- `404`: Transaction not found
- `405`: Method not allowed

---

## Receipt Extraction Endpoints

### 1. Extract Receipt Details from Image

**POST** `/transactions/extract-receipt/`

Extracts transaction details from a receipt or transaction screenshot image using AI-powered recognition. Uses NVIDIA Nemotron 3 Nano Omni as primary provider with automatic fallback to Google Gemini 2.0 Flash.

**Authentication:** Required

**Request Body:**

```text
Content-Type: multipart/form-data

file: <image file (PNG, JPEG, WEBP)>
```

**Response (200):**

```json
{
  "amount": "float (transaction amount, null if not found)",
  "date": "ISO 8601 datetime string (null if not found)",
  "sender": "string (payer/sender name, null if not found)",
  "receiver": "string (payee/receiver name, null if not found)",
  "reference_id": "string (UPI ref, UTR, transaction ID, null if not found)",
  "mode": "string (electronic, cheque, or cash, null if not found)",
  "confidence": "float (0.0-1.0, extraction confidence score)",
  "_provider": "string (nvidia or gemini - which model was used)",
  "_mock": "boolean (true if mock mode is enabled)"
}
```

**Request Example:**

```bash
curl -X POST http://127.0.0.1:8000/api/transactions/extract-receipt/ \
  -H "Authorization: Bearer <token>" \
  -F "file=@receipt.jpg"
```

**Supported Image Formats:**

- PNG (.png)
- JPEG (.jpg, .jpeg)
- WebP (.webp)

**Image Processing:**

- Automatic image compression (max 1024x1024 pixels)
- JPEG quality: 75%
- Payload reduction: ~95%

**Extraction Fields:**

- **amount**: Transaction amount (numeric value)
- **date**: Transaction date/time in ISO 8601 format
- **sender**: Name or account identifier of the payer
- **receiver**: Name or account identifier of the payee
- **reference_id**: Transaction reference (UPI ID, UTR, etc.)
- **mode**: Payment method used
- **confidence**: AI confidence score (0.0 = no confidence, 1.0 = certain)

**Provider Information:**

- **Primary**: NVIDIA Nemotron 3 Nano Omni (`nvidia`)
- **Fallback**: Google Gemini 2.0 Flash (`gemini`)
- If primary provider fails, automatically retries with fallback
- Response includes `_provider` field indicating which model was used

**Mock Mode:**

For development and testing without API calls, enable mock mode in `.env`:

```env
NVIDIA_RECEIPT_MOCK=true
GEMINI_RECEIPT_MOCK=true
```

This returns a sample response without consuming API quota.

**Error Responses:**

- `400`: No image file provided / Invalid image format / Image processing failed
- `401`: Unauthorized (missing/invalid token)
- `405`: Method not allowed
- `500`: Both AI providers failed (check error details)

**Error Response Example:**

```json
{
  "error": "Image processing failed: Invalid file format"
}
```

Or if both providers fail:

```json
{
  "error": "Both providers failed: NVIDIA: API quota exceeded | Gemini: Network error"
}
```

**Typical Use Cases:**

1. Automatic transaction entry from receipt photos
2. Bulk import of historical transactions via receipt images
3. Quick transaction logging from payment screenshots
4. Audit trail with image-based verification

---

## Recurring Transaction Endpoints

### 1. List Recurring Transactions

**GET** `/databases/<database_id>/recurring`

Retrieves all active recurring transactions for a database.

**Authentication:** Required

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
    "created_at": "ISO 8601 datetime"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 2. Create Recurring Transaction

**POST** `/databases/<database_id>/recurring`

Creates a new recurring transaction.

**Authentication:** Required

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

**Response (200):**

```json
{
}
```

**Error Responses:**

- `400`: Invalid transaction type / Amount must be greater than 0 / Invalid frequency / Description required / Invalid next run date
- `401`: Unauthorized
- `404`: Database not found
- `405`: Method not allowed

---

### 3. Delete Recurring Transaction

**DELETE** `/recurring/<recurring_id>`

Deactivates a recurring transaction (sets `is_active` to false).

**Authentication:** Required

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Recurring transaction not found
- `405`: Method not allowed

---

### 4. Process Recurring Transactions

**POST** `/recurring/process`

Processes all due recurring transactions for the authenticated user, creating transactions automatically.

**Authentication:** Required

**Response (200):**

```json
{
  "success": true,
  "processed": "integer (number of transactions created)"
}
```

**Behavior:**

- Checks all active recurring transactions with `next_run <= today`
- Creates transactions automatically
- Updates `next_run` to the next occurrence date based on frequency

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

## Audit Log Endpoints

### 1. List Audit Logs

**GET** `/audit`

Retrieves audit logs for the authenticated user (limited to 500 most recent entries).

**Authentication:** Required

**Response (200):**

```json
[
  {
    "id": "string",
    "user_id": "string",
    "action": "string (create, update, delete, login, logout, signup, etc.)",
    "entity_type": "string (user, database, transaction, recurring, etc.)",
    "entity_id": "string or null",
    "details": "string (human-readable description)",
    "timestamp": "ISO 8601 datetime"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

## Trash Management Endpoints

### 1. List Trash Items

**GET** `/trash`

Retrieves all deleted items in the user's trash.

**Authentication:** Required

**Response (200):**

```json
[
  {
    "id": "string",
    "entity_type": "string (database, etc.)",
    "entity_data": "JSON string (original data of deleted item)",
    "deleted_at": "ISO 8601 datetime",
    "deleted_by": "string (user_id who deleted it)"
  }
]
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

### 2. Restore Item from Trash

**POST** `/trash/<item_id>/restore`

Restores a deleted item from trash (only for databases).

**Authentication:** Required

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Item not found
- `405`: Method not allowed

---

### 3. Delete Item Permanently

**DELETE** `/trash/<item_id>`

Permanently deletes an item from trash (cannot be recovered).

**Authentication:** Required

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `401`: Unauthorized
- `404`: Item not found
- `405`: Method not allowed

---

### 4. Empty Trash

**DELETE** `/trash`

Permanently deletes all items from user's trash.

**Authentication:** Required

**Response (200):**

```json
{
  "success": true
}
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

## Analytics Endpoints

### 1. Get Overview Analytics

**GET** `/analytics/overview`

Retrieves summary analytics for all user's databases.

**Authentication:** Required

**Response (200):**

```json
{
  "totalDatabases": "integer",
  "totalBalance": "float (sum of all database balances)",
  "totalCredits": "float (sum of all non-voided credit transactions)",
  "totalDebits": "float (sum of all non-voided debit transactions)",
  "monthlyData": "array (reserved for future use)",
  "modeData": "array (reserved for future use)"
}
```

**Error Responses:**

- `401`: Unauthorized
- `405`: Method not allowed

---

## Data Types & Enums

### User Roles

- `admin` - Full system access, can manage all users
- `member` - Limited access, can only manage their own data

### Transaction Types

- `credit` - Money in / deposit
- `debit` - Money out / withdrawal

### Transaction Modes

- `electronic` - Electronic transfer (requires `elecId`)
- `cheque` - Cheque payment (requires `chequeNo`, `chequeDate`, `chequeBank`)
- `cash` - Cash transaction

### AI Receipt Extraction Providers

- `nvidia` - NVIDIA Nemotron 3 Nano Omni (primary provider, fast & efficient)
- `gemini` - Google Gemini 2.0 Flash (fallback provider, high accuracy)
- `mock` - Mock provider (development/testing, no API calls)

### Extraction Confidence Scores

Confidence scores range from 0.0 to 1.0:

- `0.9-1.0` - High confidence (reliable extraction)
- `0.7-0.9` - Medium confidence (usually accurate)
- `0.5-0.7` - Low confidence (review recommended)
- `0.0-0.5` - Very low confidence (manual verification needed)

### Recurring Frequencies

- `daily` - Every day
- `weekly` - Every 7 days
- `monthly` - Every month (same date)
- `yearly` - Every year (same date)

### Common Response Codes

- `200` - Success
- `400` - Bad request (validation error)
- `401` - Unauthorized (missing/invalid token)
- `403` - Forbidden (insufficient permissions)
- `404` - Not found (resource doesn't exist)
- `405` - Method not allowed (wrong HTTP method)

### Timestamp Format

All timestamps are in ISO 8601 format with timezone information:

- Example: `2024-05-23T10:16:48.756+05:30`

---

## Authentication

### Header Format

```text
Authorization: Bearer <token>
```

### Session Management

- Tokens are generated upon signup/login
- Tokens are invalidated upon logout
- Admin password reset invalidates all user sessions except the reset request
- User profile password change invalidates all other sessions (but keeps current session)

### Error Example

```json
{
  "error": "Unauthorized"
}
```

---

## Request/Response Format

All requests and responses use JSON format with `Content-Type: application/json`.

### Error Response Format

```json
{
  "error": "string (error message)"
}
```

---

## Rate Limiting

Currently, there is no rate limiting implemented. Production deployment should consider adding rate limiting.

---

## Version History

- **v2.0** - Added AI Receipt Extraction endpoint with dual provider support (NVIDIA + Gemini)
- **v1.0** - Initial API documentation

---

## Notes

- All timestamps are stored in UTC with timezone offset information
- Database balances are stored as floats
- All entity IDs are unique strings (64 characters max)
- Audit logs are maintained for all user actions for compliance
- Deleted databases and transactions are soft-deleted by default (moved to trash)
- Receipt extraction uses dual AI providers (NVIDIA Nemotron primary, Gemini fallback)
- Receipt extraction automatically compresses images (~95% payload reduction)
- All receipt extraction confidence scores are 0.0-1.0 floats
- Mock mode for receipt extraction is configurable via environment variables for testing
