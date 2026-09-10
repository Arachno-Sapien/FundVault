"""The current request's tenant, as a contextvar.

A contextvar rather than thread-local storage: it is correct under async views
and thread pools alike, and the reset token makes nesting unambiguous.
"""

import contextlib
from contextvars import ContextVar

_current_org_alias: ContextVar = ContextVar("fundvault_org_alias", default=None)


def current_org_alias():
    return _current_org_alias.get()


def set_current_org(alias):
    return _current_org_alias.set(alias)


def reset_current_org(token):
    _current_org_alias.reset(token)


@contextlib.contextmanager
def org_context(alias):
    token = set_current_org(alias)
    try:
        yield alias
    finally:
        reset_current_org(token)
