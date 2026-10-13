from .models import Alerta, OrdenCompra


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
