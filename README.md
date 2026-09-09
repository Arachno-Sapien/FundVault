# FundVault - Fund Management System

> **This repository is the multi-tenant, deployable build of FundVault.**
> Organisations connect their own Postgres, invite members with roles, and supply
> their own AI provider credentials. The design is in
> [`docs/superpowers/specs/2026-09-09-fundvault-multitenant-design.md`](docs/superpowers/specs/2026-09-09-fundvault-multitenant-design.md).
>
> The code below is carried over from the single-user local application and still
> describes it. It is rewritten as the platform lands; the single-user version
> remains available at
> [Fund-Management-System](https://github.com/Arachno-Sapien/Fund-Management-System).

A full-stack fund management application built with Django REST Framework and Next.js for managing financial records, ledger entries, and fund accounts with an intuitive web interface. Features AI-powered receipt extraction using NVIDIA Nemotron and Google Gemini APIs.

## 📋 Features

- **User Authentication**: Secure user registration and login with JWT token-based authentication
- **Role-based Access Control**: Admin and member roles with granular permissions
- **Fund Management**: Create and manage multiple fund accounts
- **Ledger Management**: Track financial transactions with detailed ledger entries
- **AI-Powered Receipt Extraction**: Automatically extract transaction details from receipt images using NVIDIA Nemotron 3 Nano Omni with Gemini 2.0 Flash fallback
- **User Accounts**: Manage user profiles and account information with profile images
- **Audit Logging**: Complete audit trail of all user actions and transactions
- **Trash Management**: Soft delete with recovery capability
- **REST API**: Comprehensive REST API for all operations (see [API_DOCUMENTATION.md](API_DOCUMENTATION.md))
- **Responsive UI**: Modern, responsive web interface built with Next.js and React
- **Data Export**: Export financial data to PDF reports
- **Charts & Visualization**: Visual representation of fund data using Chart.js
- **Analytics**: Dashboard analytics and insights
- **Recurring Transactions**: Support for recurring transaction management
- **CORS Support**: Secure cross-origin requests
- **Environment Configuration**: Flexible .env-based configuration for APIs and database

## 🏗️ Project Structure

```text
Fund Management System/
├── backend/                         # Django REST API
│   ├── apps/
│   │   ├── accounts/               # User authentication, profiles, and RBAC
│   │   │   ├── models.py           # User model with roles
│   │   │   ├── views.py            # Auth endpoints (signup, login, logout)
│   │   │   ├── serializers.py      # User serializers
│   │   │   └── urls.py             # Auth routes
│   │   ├── common/                 # Shared utilities and audit system
│   │   │   ├── auth.py             # JWT authentication utilities
│   │   │   ├── audit.py            # Audit logging system
│   │   │   └── utils.py            # Helper functions
│   │   └── ledger/                 # Transactions, funds, and AI extraction
│   │       ├── models.py           # Transaction, Fund, AuditLog models
│   │       ├── views.py            # Transaction/Fund endpoints
│   │       ├── serializers.py      # Transaction serializers
│   │       ├── receipt_extractor.py # AI receipt extraction (NVIDIA/Gemini)
│   │       ├── services.py         # Business logic layer
│   │       └── urls.py             # Transaction/Fund routes
│   ├── fundvault_backend/          # Django project settings
│   │   ├── settings.py             # Configuration (DB, APIs, CORS)
│   │   ├── urls.py                 # URL routing
│   │   ├── wsgi.py                 # WSGI application
│   │   └── asgi.py                 # ASGI application
│   ├── manage.py                   # Django management script
│   └── requirements.txt            # Python dependencies
├── frontend/                       # Next.js web application
│   ├── src/
│   │   ├── app/                   # Next.js pages and layout
│   │   │   ├── layout.js          # Root layout with global styles
│   │   │   ├── page.js            # Home page
│   │   │   └── globals.css        # Global styles
│   │   ├── components/            # Reusable React components
│   │   │   ├── FundVaultApp.jsx   # Main app component
│   │   │   ├── auth/              # Authentication components
│   │   │   ├── layout/            # Layout components
│   │   │   ├── modals/            # Modal dialogs
│   │   │   └── views/             # Page views
│   │   │       ├── HomeView.jsx           # Home/welcome page
│   │   │       ├── DashboardView.jsx      # Main dashboard
│   │   │       ├── DatabaseView.jsx       # Database/Transactions view
│   │   │       ├── AuditView.jsx          # Audit log view
│   │   │       └── TrashView.jsx          # Soft-deleted items view
│   │   └── lib/                   # Utility functions
│   │       ├── api.js             # API client
│   │       └── format.js          # Formatting utilities
│   ├── package.json
│   └── next.config.js
├── data/                          # Database storage
│   └── fundvault.db              # SQLite database
├── .env.example                   # Environment variables template
├── .env                           # Local environment variables (git-ignored)
├── .gitignore                     # Git ignore rules
├── API_DOCUMENTATION.md           # Complete API reference
├── install.bat                    # Windows installation script
├── run.bat                        # Windows application launcher
├── package.json                   # Root package.json with npm scripts
└── README.md                      # This file
```

### Tech Stack

#### Backend

- **Framework**: Django 5.2.1
- **API**: Django REST Framework 3.15.2
- **Authentication**: JWT (PyJWT 2.9.0)
- **Security**: bcrypt 4.2.0
- **CORS**: django-cors-headers 4.6.0
- **AI/ML**:
  - Google Gemini 2.0 Flash API (google-genai 2.9.0)
  - NVIDIA Nemotron 3 Nano Omni (openai 1.82.0 client)
- **Image Processing**: Pillow 12.2.0
- **Configuration**: python-dotenv 1.0.1
- **Database**: SQLite (with PostgreSQL support for production)

#### Frontend

- **Framework**: Next.js 16.2.6
- **UI Library**: React 19.2.6
- **Charts**: Chart.js 4.5.1
- **PDF Export**: jsPDF 4.2.1 + jspdf-autotable 5.0.7
- **Styling**: CSS Modules

## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
- Node.js 16 or higher
- npm or yarn
- Git

### Quick Start (Windows Users)

The easiest way to get started on Windows:

1. **Open Command Prompt** and navigate to the project folder
2. **Run the installer:**

   ```cmd
   install.bat
   ```

3. **Run the application:**

   ```cmd
   run.bat
   ```

This will automatically install all dependencies and start both backend and frontend servers.

### Manual Installation

1. **Clone the repository**

   ```bash
   git clone <repository-url>
   cd "Fund Management System"
   ```

2. **Install all dependencies**

   ```bash
   npm run install:all
   ```

   This will:

   - Install root-level Node.js dependencies
   - Install frontend dependencies
   - Install Python backend dependencies

3. **Alternative: Manual Installation**

   **Backend:**

   ```bash
   python -m pip install -r backend/requirements.txt
   ```

   **Frontend:**

   ```bash
   npm --prefix frontend install
   ```

### Environment Configuration

Create a `.env` file in the project root with the following variables:

```env
# Django Configuration
DJANGO_SECRET_KEY=your-secret-key-here
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

# JWT Configuration
JWT_SECRET=your-jwt-secret-key

# Session Configuration
SESSION_HOURS=24

# AI APIs - NVIDIA Nemotron (Primary)
NVIDIA_API_KEY=your-nvidia-api-key
NVIDIA_RECEIPT_MOCK=false

# AI APIs - Google Gemini (Fallback)
GEMINI_API_KEY=your-gemini-api-key
GEMINI_RECEIPT_MOCK=false
```

**For Development Testing (Mock Mode):**

```env
NVIDIA_RECEIPT_MOCK=true
GEMINI_RECEIPT_MOCK=true
```

This enables receipt extraction without actual API calls.

**Get API Keys:**

- **NVIDIA API Key**: Register at [NVIDIA NIM](https://build.nvidia.com/)
- **Gemini API Key**: Register at [Google AI Studio](https://aistudio.google.com/)

### Running the Application

#### Development Mode (Recommended)

Run both backend and frontend concurrently:

```bash
npm run dev
```

Or on Windows, simply use the batch file:

```cmd
run.bat
```

This will start:

- Backend API: `http://127.0.0.1:8000`
- Frontend: `http://localhost:3001`

#### Individual Development Servers

**Backend only:**

```bash
npm run dev:backend
```

**Frontend only:**

```bash
npm run dev:frontend
```

#### Production Build

```bash
npm run build
```

This will build the frontend and verify the backend configuration.

## 📚 API Endpoints

The backend provides REST API endpoints for:

- **Authentication** (`/api/auth/`): Registration, login, logout, profile management
- **Users** (`/api/users/`): User management and profiles
- **Funds** (`/api/funds/`): Create, read, update, delete fund accounts
- **Transactions** (`/api/transactions/`): Record transactions and view ledger entries
- **Receipt Extraction** (`/api/transactions/extract-receipt/`): AI-powered receipt image extraction
- **Audit Logs** (`/api/audit/`): View action history and audit trails
- **Trash** (`/api/trash/`): Recover soft-deleted items
- **Analytics** (`/api/analytics/`): Dashboard metrics and insights
- **Recurring Transactions** (`/api/recurring/`): Manage recurring transactions

For complete API documentation, see [API_DOCUMENTATION.md](API_DOCUMENTATION.md)

**Base URL:** `http://127.0.0.1:8000/api/`

## 🧠 AI Receipt Extraction

FundVault features intelligent receipt and transaction screenshot extraction using dual AI models with automatic fallback:

### Primary Provider: NVIDIA Nemotron 3 Nano Omni

- **Model**: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`
- **Endpoint**: NVIDIA NIM API
- **Benefits**: Fast, optimized for mobile inference, reasoning capabilities

### Fallback Provider: Google Gemini 2.0 Flash

- **Model**: `gemini-2.0-flash`
- **Endpoint**: Google AI API
- **Benefits**: Highly accurate, multi-modal understanding

### Extraction Features

The system automatically extracts:

- Transaction amount
- Transaction date (ISO 8601 format)
- Sender/Payer name
- Receiver/Payee name
- Reference ID (UPI, UTR, Transaction ID)
- Payment mode (electronic, cheque, cash)
- Extraction confidence score
- Provider information (which model was used)

### Image Processing

- Automatic image compression (max 1024px, 75% JPEG quality)
- Supports PNG, JPEG, WEBP formats
- ~95% payload reduction while maintaining quality

### Mock Mode (Development)

For testing without API calls:

```env
NVIDIA_RECEIPT_MOCK=true
GEMINI_RECEIPT_MOCK=true
```

**API Endpoint:**

```text
POST /api/transactions/extract-receipt/
Content-Type: multipart/form-data

file: <image_file>
```

## 🗄️ Database

The application uses SQLite for data persistence. The database file is stored at:

```text
data/fundvault.db
```

Database is automatically created when the Django backend runs for the first time.

## 📝 Available Scripts

| Script | Description |
| --- | --- |
| `npm run dev` | Start both backend and frontend in development mode |
| `npm run dev:backend` | Start Django development server |
| `npm run dev:frontend` | Start Next.js development server |
| `npm run check:backend` | Verify Django configuration |
| `npm run build:frontend` | Build the Next.js application |
| `npm run build` | Build entire project |
| `npm run install:all` | Install all dependencies |

## 🔐 Security Features

- **JWT Authentication**: Token-based authentication for secure API access with configurable session hours
- **Password Hashing**: bcrypt for secure password storage
- **Role-Based Access Control (RBAC)**: Admin and member roles with granular permissions
- **CORS Headers**: Controlled cross-origin requests
- **User Authorization**: Endpoint-level authorization checks
- **Soft Delete**: Non-destructive deletion with recovery capability through trash system
- **Audit Logging**: Complete audit trail of all user actions
- **Environment-based Configuration**: Sensitive data via .env files (never hardcoded)

## 📄 Frontend Views

- **Home View**: Welcome and quick navigation
- **Dashboard View**: Main dashboard with analytics and overview
- **Database View**: Transaction and ledger management
- **Audit View**: Complete audit log and action history
- **Trash View**: Recover soft-deleted transactions and items
- **Account Management**: User profile and settings

## 🧪 Testing

To test the login functionality:

```powershell
.\test_login.ps1
```

## 🐛 Troubleshooting

### Port Already in Use

If port 8000 or 3001 is already in use, the application will fail to start.

- Change the port in the respective npm script or manage.py command
- Or kill the process using the port

### Database Issues

If you encounter database errors:

1. Delete `data/fundvault.db`
2. Run migrations: `python backend/manage.py migrate`
3. Restart the development server

### Dependencies Issues

If you have dependency conflicts:

1. Clear npm cache: `npm cache clean --force`
2. Delete `node_modules` and, if you are doing a full reinstall, both `package-lock.json` files (`package-lock.json` at the root and `frontend/package-lock.json`)
3. Reinstall: `npm run install:all`

Note: this repo intentionally keeps one lockfile at the root for workspace tooling and one inside `frontend/` for the Next.js app. The frontend is pinned to its own folder in `frontend/next.config.js`, so Next.js will not treat the root lockfile as the app root.

### AI Receipt Extraction Issues

If receipt extraction is failing:

1. Ensure `.env` file is properly configured with API keys
2. Check if you're using mock mode: `NVIDIA_RECEIPT_MOCK=true` and `GEMINI_RECEIPT_MOCK=true`
3. Verify NVIDIA and/or Gemini API keys are valid
4. Check the API error response for quota or authentication issues
5. For development, enable mock mode to test without API calls

### Missing Python Dependencies

If you get import errors for `google`, `openai`, or `PIL`:

```bash
pip install -r backend/requirements.txt --upgrade --force-reinstall
```

### Next.js Build Issues

If frontend build fails:

```bash
cd frontend
npm cache clean --force
rm -r node_modules .next
npm install
npm run build
```

## 📦 Deployment

For production deployment:

1. Set `DEBUG = False` in `backend/fundvault_backend/settings.py`
2. Configure environment variables for production:
   - Update `DJANGO_SECRET_KEY`
   - Update `JWT_SECRET`
   - Add valid NVIDIA and Gemini API keys
3. Configure a production database (PostgreSQL recommended)
4. Use a production WSGI server (Gunicorn, uWSGI)
5. Build the frontend: `npm run build:frontend`
6. Set up static file serving (CDN or web server)
7. Use HTTPS for all connections

**Environment Setup Example (Production):**

```env
DJANGO_SECRET_KEY=your-production-secret-key
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

JWT_SECRET=your-production-jwt-secret
SESSION_HOURS=8

NVIDIA_API_KEY=your-production-nvidia-key
GEMINI_API_KEY=your-production-gemini-key
NVIDIA_RECEIPT_MOCK=false
GEMINI_RECEIPT_MOCK=false
```

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👥 Authors

- **Arachno-Sapien - Syed Junaid K**

## 📧 Contact & Support

For issues and questions, please create an issue in the repository.

---

## Final Notes

Happy coding! 🚀
