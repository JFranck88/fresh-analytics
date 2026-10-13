# Generado manualmente para Fresh Analytics (2026-10-13).
#
# Inventario vivo: Inventario.cantidad pasa a ser el SALDO del lote y se
# agrega Inventario.cantidad_inicial (lo que se recibió). Antes las ventas
# y las mermas nunca descontaban el stock, así que el "stock" era la suma
# de todo lo que había entrado y no había vencido.
#
# Los lotes que había eran sintéticos (FAs no tiene todavía recepción de
# mercadería), así que esta migración los reconstruye desde la primera
# venta: simula la reposición diaria con punto de reorden y descuenta
# cada venta y cada merma registrada con la regla FEFO. No toca Venta ni
# Merma. Usa una semilla fija, así que el resultado es reproducible.
# Detalle de la política en core/inventario.py.
#
# La reversa solo quita el campo nuevo: los lotes reconstruidos se
# quedan (los anteriores eran datos simulados sin valor propio).

from django.db import migrations, models


def reconstruir(apps, schema_editor):
    from core.inventario import reconstruir_inventario

    reconstruir_inventario(
        apps.get_model("core", "Producto"),
        apps.get_model("core", "Venta"),
        apps.get_model("core", "Merma"),
        apps.get_model("core", "Inventario"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_peso_en_libras"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventario",
            name="cantidad_inicial",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.RunPython(reconstruir, migrations.RunPython.noop),
    ]
