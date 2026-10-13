# Generado manualmente para Fresh Analytics (2026-10-13).
#
# Requerimiento de Francisco (2026-10-13): en Guatemala el peso se maneja
# en LIBRAS, no en kilogramos. Decisión: guardar todo en libras (no solo
# mostrarlo), para que lo que ve el comprador, lo que registra en caja y lo
# que aprende el modelo usen la misma medida.
#
# Esta migración convierte los datos existentes de los productos de peso
# variable (unidad_medida = "PESO"); los productos por unidad no cambian:
#   - Cantidades (kg -> lb, x 2.20462): Venta.cantidad, Inventario.cantidad,
#     Merma.cantidad, DecisionHistorial.cantidad_sugerida/_ajustada y
#     Prediccion.valor_predicho / intervalo_inferior / intervalo_superior.
#   - Precios (Q/kg -> Q/lb, / 2.20462): Producto.precio_compra /
#     precio_venta y Venta.precio_unitario.
#   - Merma.costo_perdida NO cambia: son quetzales perdidos, no peso.
#   - Mensajes de alertas ya generadas ("12.50 kg ..." -> "27.56 lb ...").
#
# Como cantidad sube y precio baja en el mismo factor, todo lo que se
# expresa en quetzales (venta esperada del departamento, merma en Q,
# aviso de quincena) queda igual, salvo centavos de redondeo.
#
# Las predicciones convertidas siguen siendo válidas hasta la siguiente
# corrida nocturna; desde ahí Prophet ya entrena directamente con el
# historial en libras.
#
# Es reversible (python manage.py migrate core 0010) por si hubiera que
# regresar; al ir y volver los precios pueden variar un centavo por el
# redondeo a 2 decimales.

import re
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations, models

LIBRAS_POR_KILO = 2.20462
LOTE = 1000

# (modelo, campos de cantidad, campos de precio, redondear cantidades a 2
# decimales como Producto.redondear_cantidad)
TABLAS = [
    ("Venta", ["cantidad"], ["precio_unitario"], True),
    ("Inventario", ["cantidad"], [], True),
    ("Merma", ["cantidad"], [], True),
    ("DecisionHistorial", ["cantidad_sugerida", "cantidad_ajustada"], [], True),
    # Las predicciones se dejan sin redondear: son valores del modelo.
    ("Prediccion", ["valor_predicho", "intervalo_inferior", "intervalo_superior"], [], False),
]

PATRON_KG = re.compile(r"(\d+(?:\.\d+)?) kg\b")
PATRON_LB = re.compile(r"(\d+(?:\.\d+)?) lb\b")


def _precio(valor, factor):
    return (Decimal(str(float(valor) * factor))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _convertir(apps, factor_cantidad, patron, unidad_nueva):
    """factor_cantidad: 2.20462 para kg -> lb, 1/2.20462 para lb -> kg.
    Los precios se multiplican por el inverso."""
    factor_precio = 1 / factor_cantidad
    Producto = apps.get_model("core", "Producto")
    ids_peso = list(
        Producto.objects.filter(unidad_medida="PESO").values_list("id_producto", flat=True)
    )
    if not ids_peso:
        return

    productos = list(Producto.objects.filter(id_producto__in=ids_peso))
    for p in productos:
        p.precio_compra = _precio(p.precio_compra, factor_precio)
        p.precio_venta = _precio(p.precio_venta, factor_precio)
    Producto.objects.bulk_update(productos, ["precio_compra", "precio_venta"])

    for nombre, campos_cantidad, campos_precio, redondear in TABLAS:
        Modelo = apps.get_model("core", nombre)
        pendientes = []
        for fila in Modelo.objects.filter(producto_id__in=ids_peso).iterator(chunk_size=LOTE):
            for campo in campos_cantidad:
                valor = getattr(fila, campo) * factor_cantidad
                setattr(fila, campo, round(valor, 2) if redondear else valor)
            for campo in campos_precio:
                setattr(fila, campo, _precio(getattr(fila, campo), factor_precio))
            pendientes.append(fila)
            if len(pendientes) >= LOTE:
                Modelo.objects.bulk_update(pendientes, campos_cantidad + campos_precio)
                pendientes = []
        if pendientes:
            Modelo.objects.bulk_update(pendientes, campos_cantidad + campos_precio)

    Alerta = apps.get_model("core", "Alerta")
    alertas = []
    for alerta in Alerta.objects.filter(producto_id__in=ids_peso):
        nuevo = patron.sub(
            lambda m: f"{float(m.group(1)) * factor_cantidad:.2f} {unidad_nueva}",
            alerta.mensaje,
        )
        if nuevo != alerta.mensaje:
            alerta.mensaje = nuevo
            alertas.append(alerta)
    if alertas:
        Alerta.objects.bulk_update(alertas, ["mensaje"])


def kilos_a_libras(apps, schema_editor):
    _convertir(apps, LIBRAS_POR_KILO, PATRON_KG, "lb")


def libras_a_kilos(apps, schema_editor):
    _convertir(apps, 1 / LIBRAS_POR_KILO, PATRON_LB, "kg")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_limpiar_validacion_cruzada"),
    ]

    operations = [
        migrations.AlterField(
            model_name="producto",
            name="unidad_medida",
            field=models.CharField(
                choices=[("UNIDAD", "Unidad"), ("PESO", "Peso (lb)")],
                default="UNIDAD",
                help_text='UNIDAD: se cuenta por pieza completa (leche, pan, queso empacado) - las cantidades siempre son números enteros. PESO: producto de peso variable, se pesa suelto en caja (carnes, algunas frutas/verduras) - las cantidades se manejan en libras - la medida que se usa en Guatemala - y sí tienen sentido los decimales (ej. 2.5 lb). Reportado por Francisco al ver mermas con decimales sin sentido en productos que se cuentan por pieza (ver decisiones-and-learnings.md, 2026-09-17/18); es el mismo concepto que GS1 llama "peso variable" en sus códigos de barras (prefijo 2, peso codificado en el propio código).',
                max_length=10,
            ),
        ),
        migrations.RunPython(kilos_a_libras, libras_a_kilos),
    ]
