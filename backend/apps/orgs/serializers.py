"""Org serialisers.

Credentials are never serialised. `serialize_org` lists its keys explicitly
rather than iterating model fields, so adding a secret column later cannot
silently start exposing it.
"""


def serialize_org(org):
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


def serialize_org_summary(org):
    """The shape shown on the org picker before anyone has authenticated."""
    return {"id": org.id, "name": org.name, "slug": org.slug}
