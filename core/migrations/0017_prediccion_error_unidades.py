# Generado con makemigrations para Fresh Analytics (2026-10-14).
#
# Prediccion.error_unidades: error promedio por día de la última semana de
# prueba, en unidades o libras. Se muestra junto al MAPE (Predicciones),
# porque en productos de poca venta el porcentaje se dispara aunque el
# error real sea de pocas unidades (yogurt del supermercado).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_producto_origen"),
    ]

    operations = [
        migrations.AddField(
            model_name="prediccion",
            name="error_unidades",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
