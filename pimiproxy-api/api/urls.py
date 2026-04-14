from django.urls import path
from . import views

urlpatterns = [
    path("login",           views.login_view),
    path("logout",          views.logout_view),
    path("status",          views.status_view),
    path("config",          views.config_view),
    path("groups",          views.groups_view),
    path("groups/<str:name>", views.group_detail_view),
    path("rules",           views.rules_view),
    path("rules/reorder",   views.rules_reorder_view),
    path("rules/<int:index>", views.rule_detail_view),
    path("provider",         views.provider_view),
    path("refresh",          views.refresh_view),
    path("refresh-settings", views.refresh_settings_view),
]
