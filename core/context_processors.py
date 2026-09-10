from .models import Alerta


def alertas_globales(request):
    """Conteo de alertas activas (no leídas), disponible en todos los
    templates para el badge del sidebar. Solo consulta si hay un usuario
    autenticado, para no pegarle a la base de datos en la pantalla de login."""
    if not request.user.is_authenticated:
        return {}
    return {
        "alertas_activas_count": Alerta.objects.filter(leida=False).count(),
    }
