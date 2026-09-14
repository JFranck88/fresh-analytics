# Generated manually for Fresh Analytics — hace único codigo_upc.
#
# Antes de esto, nada impedía que dos productos distintos terminaran con
# el mismo UPC, lo cual le quitaba sentido a toda la búsqueda por código
# de barras. Se hace en tres pasos porque el campo era CharField con
# default="" (nunca NULL): si se agregara unique=True directamente, los
# productos que todavía no tienen UPC (codigo_upc="") chocarían entre sí
# apenas hubiera dos, porque "" es igual a "" para una restricción única.
# La solución estándar es usar NULL para "no tiene UPC todavía" -en SQL,
# NULL nunca es igual a otro NULL, así que muchos productos sin UPC
# conviven sin problema, y la unicidad solo se exige entre códigos reales.
#
# Nota: esta migración puede fallar si ya existen dos productos con el
# MISMO código UPC no vacío cargado a mano - en ese caso Postgres avisa
# exactamente cuál es el valor duplicado, y hay que corregir el dato en
# uno de los dos productos antes de volver a migrar.

from django.db import migrations, models


def vaciar_upc_en_blanco_a_null(apps, schema_editor):
    Producto = apps.get_model('core', 'Producto')
    Producto.objects.filter(codigo_upc="").update(codigo_upc=None)


def revertir_null_a_vacio(apps, schema_editor):
    Producto = apps.get_model('core', 'Producto')
    Producto.objects.filter(codigo_upc__isnull=True).update(codigo_upc="")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_decisionhistorial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='producto',
            name='codigo_upc',
            field=models.CharField(
                blank=True, max_length=64, null=True,
                help_text='Código de barras (UPC/EAN) del producto, si se conoce.',
            ),
        ),
        migrations.RunPython(vaciar_upc_en_blanco_a_null, revertir_null_a_vacio),
        migrations.AlterField(
            model_name='producto',
            name='codigo_upc',
            field=models.CharField(
                blank=True, max_length=64, null=True, unique=True,
                help_text=(
                    "Código de barras (UPC/EAN) del producto, si se conoce. "
                    "NULL cuando no se conoce (no cadena vacía), para que dos "
                    "productos sin UPC no choquen contra la restricción de "
                    "unicidad - solo choca si de verdad se repite un código."
                ),
            ),
        ),
    ]
