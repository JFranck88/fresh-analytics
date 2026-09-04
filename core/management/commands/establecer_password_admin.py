from django.core.management.base import BaseCommand
from decouple import config

from core.models import Usuario


class Command(BaseCommand):
    help = (
        "Cambia la contraseña del usuario admin leyendo el nuevo valor desde "
        "la variable de entorno ADMIN_PASSWORD_TEMPORAL (nunca desde el "
        "código)."
    )

    def handle(self, *args, **options):
        nueva_password = config("ADMIN_PASSWORD_TEMPORAL", default=None)
        if not nueva_password:
            self.stdout.write("ADMIN_PASSWORD_TEMPORAL no definida, no se cambia nada.")
            return

        try:
            usuario = Usuario.objects.get(correo="admin@freshanalytics.com")
        except Usuario.DoesNotExist:
            self.stdout.write("El usuario admin no existe todavía.")
            return

        usuario.set_password(nueva_password)
        usuario.save()
        self.stdout.write(self.style.SUCCESS("Contraseña del admin actualizada."))