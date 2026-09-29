# Generado manualmente para Fresh Analytics (2026-09-29).
#
# auditar_modelo guardaba su resultado como JSON en Configuracion (clave
# "validacion_cruzada_resultado") para una gráfica de Predicciones que se
# retiró el 2026-09-16. Ese registro quedaba visible como un "parámetro"
# más en la pantalla de Configuración del Administrador, sin serlo. Ahora
# auditar_modelo solo imprime su reporte en consola, y esta migración
# retira el registro si existe (si no existe, no hace nada).

from django.db import migrations

CLAVE = "validacion_cruzada_resultado"


def retirar_registro(apps, schema_editor):
    Configuracion = apps.get_model("core", "Configuracion")
    Configuracion.objects.filter(clave=CLAVE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_alerta_referencia_fecha_actualizacion"),
    ]

    operations = [
        # Sin reversa real: el dato era un resultado calculado (se puede
        # regenerar corriendo auditar_modelo), no configuración del usuario.
        migrations.RunPython(retirar_registro, migrations.RunPython.noop),
    ]
