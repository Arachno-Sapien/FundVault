def serialize_user(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "profile_image": user.profile_image,
        "role": user.role,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }
