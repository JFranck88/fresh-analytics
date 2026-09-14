# Generated manually for Fresh Analytics — agrega fecha_lectura a Alerta.
# La vista marcar_alerta_leida ya intentaba guardar este dato (alerta.fecha_lectura
# = timezone.now()), pero el modelo no tenía el campo, así que Django lo
# descartaba en silencio en cada guardado: nunca fallaba, simplemente no
# quedaba nada persistido. Con este campo, el dato empieza a guardarse de
# verdad a partir de ahora (los registros ya marcados como leídos antes de
# esta migración quedan con fecha_lectura en NULL, porque ese dato nunca
# se guardó).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_producto_codigo_upc'),
    ]

    operations = [
        migrations.AddField(
            model_name='alerta',
            name='fecha_lectura',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
