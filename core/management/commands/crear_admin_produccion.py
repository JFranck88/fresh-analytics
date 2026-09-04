from django.core.management.base import BaseCommand
from decouple import config

from core.models import Usuario


class Command(BaseCommand):
    help = "Crea el usuario administrador inicial en producción (no interactivo)."

    def handle(self, *args, **options):
        correo = "admin@freshanalytics.com"
        if Usuario.objects.filter(correo=correo).exists():
            self.stdout.write("El usuario admin ya existe, no se crea de nuevo.")
            return

        password_inicial = config("ADMIN_PASSWORD_INICIAL", default="cambiar-esto-ya")

        usuario = Usuario.objects.create_user(
            correo=correo, nombre="Administrador", rol="ADMINISTRADOR",
            password=password_inicial,
        )
        usuario.is_staff = True
        usuario.is_superuser_admin = True
        usuario.save()
        self.stdout.write(self.style.SUCCESS(f"Usuario admin creado: {correo}"))