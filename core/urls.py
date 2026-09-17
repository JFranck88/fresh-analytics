from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("buscar/", views.buscar_global, name="buscar_global"),
    path("buscar/json/", views.buscar_global_json, name="buscar_global_json"),
    path("productos/buscar-json/", views.buscar_productos_json, name="buscar_productos_json"),
    path("mermas/registrar/", views.registrar_merma, name="registrar_merma"),
    path("mermas/", views.listar_mermas, name="listar_mermas"),
    path("riesgo-descomposicion/", views.riesgo_descomposicion, name="riesgo_descomposicion"),
    path("alertas/", views.listar_alertas, name="listar_alertas"),
    path("alertas/<int:alerta_id>/leida/", views.marcar_alerta_leida, name="marcar_alerta_leida"),
    path("predicciones/", views.listar_predicciones, name="listar_predicciones"),
    path("recomendaciones/", views.listar_recomendaciones, name="listar_recomendaciones"),
    path("recomendaciones/orden-compra/", views.generar_orden_compra, name="generar_orden_compra"),
    path("historial-decisiones/", views.historial_decisiones, name="historial_decisiones"),
    path("usuarios/", views.listar_usuarios, name="listar_usuarios"),
    path("usuarios/crear/", views.crear_usuario, name="crear_usuario"),
    path("usuarios/<int:usuario_id>/editar/", views.editar_usuario, name="editar_usuario"),
    path(
        "usuarios/<int:usuario_id>/restablecer-password/",
        views.restablecer_password_usuario,
        name="restablecer_password_usuario",
    ),
    path("configuracion/", views.listar_configuracion, name="listar_configuracion"),
    path("mantenimiento/", views.mantenimiento, name="mantenimiento"),
]