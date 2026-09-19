from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.views.decorators.cache import never_cache
from functools import wraps


def rol_requerido(*roles_permitidos):
    """
    Restringe una vista a los roles indicados, según los casos de uso
    documentados

    Excepción: is_superuser_admin=True se salta esta restricción -
    es un modo técnico de soporte/desarrollo, separado del rol de
    negocio "Administrador" del diagrama. Antes de la defensa final,
    hay que decidir si este bypass se mantiene en producción o se
    retira para que el sistema refleje exactamente el diagrama.

    @never_cache: sin esto, el navegador puede guardar en caché la
    página ya renderizada (Django no manda Cache-Control por defecto).
    Eso provoca que, después de cerrar sesión, volver a abrir el
    navegador o darle "atrás" a veces muestre otra vez la página con
    datos de la sesión anterior aunque el servidor ya la haya cerrado -
    intermitente porque depende de si el navegador decide usar su
    copia en caché o pedirla de nuevo al servidor. never_cache fuerza
    a que esta respuesta nunca se sirva desde caché.
    """
    def decorador(vista):
        @never_cache
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