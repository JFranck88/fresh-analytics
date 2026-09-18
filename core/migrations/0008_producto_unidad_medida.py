# Generado manualmente para Fresh Analytics - agrega el concepto de
# "unidad de medida" a Producto (RF reportado por Francisco: una merma de
# "0.9 quesos" no tiene lógica para un producto que se cuenta por pieza;
# sí la tiene para un producto de peso variable, que en la industria del
# retail se conoce justo así - "peso variable" o "catch weight" - y que
# GS1 codifica distinto en el código de barras, prefijo 2, con el peso
# incluido en el propio código). Ver decisiones-and-learnings.md
# (2026-09-17/18) para el detalle completo de la discusión con Francisco.
#
# Esta migración hace tres cosas en un solo paso, para que el despliegue
# en Render (que corre `migrate` automáticamente en el Start Command) deje
# la base de datos de producción lista sin que Francisco tenga que entrar
# a mano al admin de Django a reclasificar productos uno por uno:
#   1. Agrega el campo `unidad_medida` (default "UNIDAD" para todos).
#   2. Reclasifica a "PESO" los 6 productos que Francisco identificó como
#      de peso variable en el catálogo actual.
#   3. Si ya existe un producto "Pan de molde" (como en producción, donde
#      se sembró hace varias sesiones), lo renombra a "Pan dulce" - pan de
#      molde no es un producto típico en Guatemala - y ajusta su vida útil
#      y precios a los de un pan dulce fresco. En una base nueva (sin ese
#      producto todavía) este paso simplemente no hace nada.

from django.db import migrations, models

PRODUCTOS_PESO_VARIABLE = [
    "Pechuga de pollo",
    "Carne molida de res",
    "Tomate de riñón",
    "Cebolla blanca",
    "Manzana roja",
    "Banano",
]


def clasificar_peso_variable(apps, schema_editor):
    Producto = apps.get_model("core", "Producto")
    Producto.objects.filter(nombre__in=PRODUCTOS_PESO_VARIABLE).update(unidad_medida="PESO")


def revertir_clasificacion(apps, schema_editor):
    Producto = apps.get_model("core", "Producto")
    Producto.objects.filter(nombre__in=PRODUCTOS_PESO_VARIABLE).update(unidad_medida="UNIDAD")


def renombrar_pan_de_molde(apps, schema_editor):
    Producto = apps.get_model("core", "Producto")
    Producto.objects.filter(nombre="Pan de molde").update(
        nombre="Pan dulce", vida_util_dias=3, precio_compra=1.00, precio_venta=1.75,
    )


def revertir_renombre_pan_de_molde(apps, schema_editor):
    Producto = apps.get_model("core", "Producto")
    Producto.objects.filter(nombre="Pan dulce").update(
        nombre="Pan de molde", vida_util_dias=7, precio_compra=12.00, precio_venta=18.00,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_remove_usuario_ultimo_acceso"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="unidad_medida",
            field=models.CharField(
                choices=[("UNIDAD", "Unidad"), ("PESO", "Peso (kg)")],
                default="UNIDAD", max_length=10,
                help_text=(
                    "UNIDAD: se cuenta por pieza completa - las cantidades "
                    "siempre son números enteros. PESO: producto de peso "
                    "variable, se pesa suelto en caja - las cantidades se "
                    "manejan en kilogramos y sí tienen sentido los decimales."
                ),
            ),
        ),
        migrations.RunPython(clasificar_peso_variable, revertir_clasificacion),
        migrations.RunPython(renombrar_pan_de_molde, revertir_renombre_pan_de_molde),
    ]
