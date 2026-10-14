# Generado con makemigrations para Fresh Analytics (2026-10-14).
#
# DecisionHistorial.stock_al_decidir: stock vigente cuando el Comprador
# ajustó la cantidad. Sirve para calcular "¿Quién acertó más?" (UC-05,
# paso 4; ver core/efectividad.py). Opcional: las decisiones anteriores
# lo deducen del propio sugerido.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_merma_automatica"),
    ]

    operations = [
        migrations.AddField(
            model_name="decisionhistorial",
            name="stock_al_decidir",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
