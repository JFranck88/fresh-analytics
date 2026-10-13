# Generado con makemigrations para Fresh Analytics (2026-10-13).
#
# Merma.automatica: marca las mermas por VENCIMIENTO que registra el
# sistema solo cuando un lote vence con saldo (core/inventario.py,
# dar_de_baja_vencidos). Las que registra una persona quedan en False.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_orden_compra_aprobacion"),
    ]

    operations = [
        migrations.AddField(
            model_name="merma",
            name="automatica",
            field=models.BooleanField(default=False),
        ),
    ]
