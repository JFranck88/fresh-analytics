from django.core.management.base import BaseCommand
from decouple import config

from core.models import Usuario


class Command(BaseCommand):
    help = "Crea el usuario administrador inicial en producción (no interactivo)."

    # OJO: este comando corre en CADA despliegue, dentro del Start Command de
    # Render (`migrate && crear_admin_produccion && gunicorn ...`). Por eso
    # nunca debe terminar con error: si fallara, gunicorn no arrancaría y el
    # sitio quedaría caído. Ante cualquier situación "rara" solo avisa y sale.

    def handle(self, *args, **options):
        correo = "admin@freshanalytics.com"
        if Usuario.objects.filter(correo=correo).exists():
            self.stdout.write("El usuario admin ya existe, no se crea de nuevo.")
            return

        # Antes, si faltaba la variable, se usaba "cambiar-esto-ya" como
        # contraseña por defecto: un superusuario con una contraseña
        # adivinable (y publicada en el propio repositorio). Ahora, sin la
        # variable simplemente no se crea - mismo criterio que
        # establecer_password_admin.
        password_inicial = config("ADMIN_PASSWORD_INICIAL", default="")
        if not password_inicial:
            self.stdout.write(self.style.WARNING(
                "ADMIN_PASSWORD_INICIAL no está definida: no se crea el "
                "usuario admin. Defínela en las variables de entorno y vuelve "
                "a desplegar."
            ))
            return

        usuario = Usuario.objects.create_user(
            correo=correo, nombre="Administrador", rol="ADMINISTRADOR",
            password=password_inicial,
        )
        usuario.is_staff = True
        usuario.is_superuser_admin = True
        usuario.save()
        self.stdout.write(self.style.SUCCESS(f"Usuario admin creado: {correo}"))
