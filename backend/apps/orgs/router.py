"""Route tenant models to the current org's database.

The router raises rather than falling back to `default` when no org is set.
Falling back would mean a bug in context propagation silently reads or writes
the wrong organisation's ledger; raising turns that into a visible 500 instead
of a quiet data leak.
"""

from apps.orgs.context import current_org_alias

TENANT_APPS = {"accounts", "ledger"}


class NoOrgContext(RuntimeError):
    """A tenant model was queried with no organisation in context."""


class TenantRouter:
    def _route(self, model, operation):
        app_label = model._meta.app_label
        if app_label in TENANT_APPS:
            alias = current_org_alias()
            if alias is None:
                raise NoOrgContext(
                    f"{model.__name__} ({app_label}) was queried for {operation} with no "
                    "organisation in context. Tenant models require OrgContextMiddleware "
                    "or an explicit org_context() block."
                )
            return alias
        return "default"

    def db_for_read(self, model, **hints):
        return self._route(model, "read")

    def db_for_write(self, model, **hints):
        return self._route(model, "write")

    def allow_relation(self, obj1, obj2, **hints):
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in TENANT_APPS:
            return db != "default"
        return db == "default"
