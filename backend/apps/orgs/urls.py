from django.urls import path

from apps.orgs import views


urlpatterns = [
    path("orgs/validate-connection", views.validate_connection),
    path("orgs/create", views.create_org),
]
