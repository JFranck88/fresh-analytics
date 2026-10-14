# Generado con makemigrations para Fresh Analytics (2026-10-14).
#
# Producto.origen: SIMULADO (datos de prueba, como hasta ahora) o REAL
# (cargado desde el reporte de ventas del supermercado en "Importar
# datos"). El proceso nocturno solo inventa ventas, inventario y mermas
# para los SIMULADOS. Todos los productos existentes quedan como SIMULADO.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_decision_stock_al_decidir"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="origen",
            field=models.CharField(
                choices=[
                    ("SIMULADO", "Datos de prueba"),
                    ("REAL", "Datos reales del supermercado"),
                ],
                default="SIMULADO",
                max_length=10,
            ),
        ),
    ]
