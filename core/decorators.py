from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from functools import wraps


def rol_requerido(*roles_permitidos):
    """
    Restringe una vista a los roles indicados, según los casos de uso
    documentados (Figuras 18, 19 y 20 del Capítulo IV).

    Excepción: is_superuser_admin=True se salta esta restricción -
    es un modo técnico de soporte/desarrollo, separado del rol de
    negocio "Administrador" del diagrama. Antes de la defensa final,
    hay que decidir si este bypass se mantiene en producción o se
    retira para que el sistema refleje exactamente el diagrama.
    """
    def decorador(vista):
        @login_required
        @wraps(vista)
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser_admin:
                return vista(request, *args, **kwargs)
            if request.user.rol not in roles_permitidos:
                raise PermissionDenied
            return vista(request, *args, **kwargs)
        return wrapper
    return decorador