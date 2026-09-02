from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("mermas/registrar/", views.registrar_merma, name="registrar_merma"),
    path("mermas/", views.listar_mermas, name="listar_mermas"),
    path("alertas/", views.listar_alertas, name="listar_alertas"),
]