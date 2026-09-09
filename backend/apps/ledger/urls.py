from django.urls import path

from apps.ledger import views


urlpatterns = [
    path("databases", views.databases_list_create),
    path("databases/merge", views.databases_merge),
    path("databases/<str:database_id>", views.database_detail),
    path("databases/<str:database_id>/archive", views.database_archive),
    path("databases/<str:database_id>/transactions", views.database_transactions),
    path("databases/<str:database_id>/recurring", views.recurring_list_create),
    path("transactions/<str:transaction_id>", views.transaction_update),
    path("transactions/<str:transaction_id>/void", views.transaction_void),
    path("transactions/<str:transaction_id>/delete", views.transaction_delete_voided),
    path("transactions/<str:transaction_id>/approve", views.transaction_approve),
    path("recurring/process", views.recurring_process),
    path("recurring/<str:recurring_id>", views.recurring_delete),
    path("audit", views.audit_list),
    path("analytics/overview", views.analytics_overview),
    path("trash", views.trash_list),
    path("trash/<str:item_id>/restore", views.trash_restore),
    path("trash/<str:item_id>", views.trash_delete),
    path("extract-receipt", views.extract_receipt),
]
