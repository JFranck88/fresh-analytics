from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("mermas/registrar/", views.registrar_merma, name="registrar_merma"),
    path("mermas/", views.listar_mermas, name="listar_mermas"),
    path("alertas/", views.listar_alertas, name="listar_alertas"),
    path("predicciones/", views.listar_predicciones, name="listar_predicciones"),
    path("recomendaciones/", views.listar_recomendaciones, name="listar_recomendaciones"),
    path("recomendaciones/orden-compra/", views.generar_orden_compra, name="generar_orden_compra"),
]