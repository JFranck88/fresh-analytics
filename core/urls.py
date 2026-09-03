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
    path("usuarios/", views.listar_usuarios, name="listar_usuarios"),
    path("usuarios/crear/", views.crear_usuario, name="crear_usuario"),
    path("configuracion/", views.listar_configuracion, name="listar_configuracion"),
]