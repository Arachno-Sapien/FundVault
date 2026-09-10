from django.urls import path

from apps.orgs import views


urlpatterns = [
    path("orgs/validate-connection", views.validate_connection),
    path("orgs/create", views.create_org),
    path("orgs/join/preview", views.join_preview),
    path("orgs/join", views.join_org),
    path("orgs/codes", views.join_codes),
    path("orgs/codes/<str:code>", views.revoke_join_code),
]
