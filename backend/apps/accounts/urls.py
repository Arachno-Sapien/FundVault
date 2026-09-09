from django.urls import path

from apps.accounts import views


urlpatterns = [
    path("auth/signup", views.signup),
    path("auth/login", views.login),
    path("auth/logout", views.logout),
    path("auth/me", views.me),
    path("admin/users", views.admin_users),
    path("admin/users/<str:user_id>", views.admin_user_detail),
    path("admin/users/<str:user_id>/reset-password", views.admin_reset_password),
]
