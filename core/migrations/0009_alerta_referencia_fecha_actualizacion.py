# Generado manualmente para Fresh Analytics (2026-09-29).
#
# Antes, generar_alertas borraba TODAS las alertas cada día y las volvía a
# crear, así que se perdía quién había leído cada alerta y cuándo
# (usuario_lector / fecha_lectura). Estos dos campos permiten reconocer
# una alerta que sigue vigente y conservarla tal cual:
#   - referencia: identifica el evento concreto (para VENCIMIENTO, la fecha
#     del lote más próximo a vencer).
#   - fecha_actualizacion: última corrida que confirmó que sigue vigente
#     (Mantenimiento la usa para "Última generación de alertas").
# Ambos campos son opcionales, así que la migración es segura sobre la
# base de producción existente (Render la aplica solo en el Start Command).
#
# De paso se sincroniza el help_text de Producto.unidad_medida, que se había
# editado en models.py después de la 0008 sin generar migración (solo
# cambia el texto de ayuda, no toca la base de datos).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_producto_unidad_medida"),
    ]

    operations = [
        migrations.AddField(
            model_name="alerta",
            name="referencia",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="alerta",
            name="fecha_actualizacion",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="producto",
            name="unidad_medida",
            field=models.CharField(
                choices=[("UNIDAD", "Unidad"), ("PESO", "Peso (kg)")],
                default="UNIDAD",
                help_text='UNIDAD: se cuenta por pieza completa (leche, pan, queso empacado) - las cantidades siempre son números enteros. PESO: producto de peso variable, se pesa suelto en caja (carnes, algunas frutas/verduras) - las cantidades se manejan en kilogramos y sí tienen sentido los decimales (ej. 0.9 kg). Reportado por Francisco al ver mermas con decimales sin sentido en productos que se cuentan por pieza (ver decisiones-and-learnings.md, 2026-09-17/18); es el mismo concepto que GS1 llama "peso variable" en sus códigos de barras (prefijo 2, peso codificado en el propio código).',
                max_length=10,
            ),
        ),
    ]
