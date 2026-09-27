# FundVault documentation

Start with the [project README](../README.md) for what FundVault is and a quick start.

| Document | Read it when you want to… |
|---|---|
| [architecture.md](./architecture.md) | understand how the pieces fit: control plane vs per-org databases, request lifecycle, security model |
| [backend.md](./backend.md) | work on the Django API: module-by-module reference and the invariants to respect |
| [frontend.md](./frontend.md) | work on the Next.js app: app shell, data loading, permissions mirror, dates, shortcuts |
| [api.md](./api.md) | call the HTTP API: every endpoint, its role requirement, request and response shapes |
| [development.md](./development.md) | run it locally, run the tests, follow the project's conventions |
| [deployment.md](./deployment.md) | deploy the API to Render and the frontend to Vercel |
| [codebase-audit-2026-09-27.md](./codebase-audit-2026-09-27.md) | see what the 2026-09-27 audit fixed, what it deliberately left, and which files are unnecessary |

## History

[`history/`](./history/) holds the design spec and implementation plans the multi-tenant
rewrite was built from. They record intent at the time and are not kept up to date; where they
disagree with the code or the documents above, the code wins.

- [history/specs/2026-09-09-fundvault-multitenant-design.md](./history/specs/2026-09-09-fundvault-multitenant-design.md) — the multi-tenant design
- [history/plans/2026-09-09-fundvault-multitenant.md](./history/plans/2026-09-09-fundvault-multitenant.md) — its implementation plan
- [history/plans/2026-09-25-fundvault-launch-hardening.md](./history/plans/2026-09-25-fundvault-launch-hardening.md) — the pre-launch hardening plan
