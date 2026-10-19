from django.urls import reverse

from .models import Alerta, OrdenCompra

# Buscador de la topbar (base.html y la página /buscar/): a qué pantalla
# lleva un resultado de producto o de alerta según el módulo desde el que
# se buscó. Clave: módulo actual; valor: pantalla que sabe filtrar por
# ?producto=. Lo que no está aquí (Dashboard, Pasillo, Configuración...)
# cae a Predicciones. 2026-10-19: Francisco vio en sus demos que desde
# Recomendaciones y Órdenes de compra el buscador lo mandaba a
# Predicciones; se agregaron esos dos módulos, Alertas y el Modo pasillo
# (ahí sirve para buscar por nombre cuando no hay código de barras a mano).
DESTINO_BUSCADOR = {
    "listar_predicciones": "listar_predicciones",
    "historial_decisiones": "historial_decisiones",
    "riesgo_descomposicion": "riesgo_descomposicion",
    "listar_mermas": "listar_mermas",
    "listar_recomendaciones": "listar_recomendaciones",
    "listar_ordenes": "listar_ordenes",
    "detalle_orden": "listar_ordenes",
    "listar_alertas": "listar_alertas",
    "modo_pasillo": "modo_pasillo",
}


def buscador(request):
    """Módulo desde el que se busca y URL a la que van los resultados.
    En la página completa /buscar/ (al presionar Enter) el módulo viaja en
    ?desde=, para que esa página tampoco saque a nadie de donde estaba."""
    coincidencia = getattr(request, "resolver_match", None)
    modulo = coincidencia.url_name if coincidencia else ""
    if modulo == "buscar_global":
        modulo = request.GET.get("desde", "")
    if modulo not in DESTINO_BUSCADOR:
        modulo = ""
    return {
        "modulo_buscador": modulo,
        "url_destino_buscador": reverse(DESTINO_BUSCADOR.get(modulo, "listar_predicciones")),
    }


def alertas_globales(request):
    """Conteo de alertas activas (no leídas), disponible en todos los
    templates para el badge del sidebar. Solo consulta si hay un usuario
    autenticado, para no pegarle a la base de datos en la pantalla de login."""
    if not request.user.is_authenticated:
        return {}
    usuario = request.user
    # Badge de "Órdenes de compra": lo que espera una acción de quien mira.
    # Gerente: órdenes por aprobar. Comprador: las suyas rechazadas (por
    # corregir) o aprobadas (por recibir).
    if usuario.rol == "GERENTE" or usuario.is_superuser_admin:
        ordenes_por_atender = OrdenCompra.objects.filter(estado=OrdenCompra.Estado.PENDIENTE).count()
    elif usuario.rol == "COMPRADOR":
        ordenes_por_atender = OrdenCompra.objects.filter(
            comprador=usuario, estado__in=[OrdenCompra.Estado.RECHAZADA, OrdenCompra.Estado.APROBADA],
        ).count()
    else:
        ordenes_por_atender = 0
    return {
        "alertas_activas_count": Alerta.objects.filter(leida=False).count(),
        "ordenes_por_atender_count": ordenes_por_atender,
    }
