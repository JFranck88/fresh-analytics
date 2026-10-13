# Generado con makemigrations para Fresh Analytics (2026-10-13).
#
# UC-03 / UC-06 / RF-07: órdenes de compra con aprobación del Gerente.
# OrdenCompra (estado PENDIENTE -> APROBADA o RECHAZADA -> RECIBIDA),
# OrdenCompraDetalle (una línea por producto, con el precio congelado y lo
# que llegó en la recepción, enlazado al lote creado) y OrdenCompraEvento
# (bitácora de cada vuelta: enviada, rechazada con su observación,
# reenviada, aprobada, recibida). Solo crea tablas nuevas: no toca datos.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_inventario_vivo"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrdenCompra",
            fields=[
                ("id_orden", models.AutoField(primary_key=True, serialize=False)),
                ("fecha_creacion", models.DateTimeField(auto_now_add=True)),
                (
                    "fecha_prediccion",
                    models.DateField(
                        help_text="Corrida del modelo (Prediccion.fecha_prediccion) de la que salieron las sugerencias."
                    ),
                ),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("PENDIENTE", "Pendiente de aprobación"),
                            ("RECHAZADA", "Rechazada - por corregir"),
                            ("APROBADA", "Aprobada"),
                            ("RECIBIDA", "Recibida"),
                        ],
                        default="PENDIENTE",
                        max_length=20,
                    ),
                ),
                ("fecha_revision", models.DateTimeField(blank=True, null=True)),
                (
                    "observacion",
                    models.TextField(
                        blank=True, default="", help_text="Motivo del último rechazo."
                    ),
                ),
                ("fecha_recepcion", models.DateTimeField(blank=True, null=True)),
                (
                    "comprador",
                    models.ForeignKey(
                        db_column="id_comprador",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ordenes_creadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "recibida_por",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_receptor",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ordenes_recibidas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "revisada_por",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_gerente",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ordenes_revisadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Orden de compra",
                "verbose_name_plural": "Órdenes de compra",
                "db_table": "orden_compra",
                "ordering": ["-id_orden"],
            },
        ),
        migrations.CreateModel(
            name="OrdenCompraDetalle",
            fields=[
                ("id_detalle", models.AutoField(primary_key=True, serialize=False)),
                ("cantidad_sugerida", models.FloatField()),
                (
                    "cantidad",
                    models.FloatField(
                        help_text="Cantidad final pedida (ajustada por el Comprador)."
                    ),
                ),
                ("precio_compra", models.DecimalField(decimal_places=2, max_digits=10)),
                ("cantidad_recibida", models.FloatField(blank=True, null=True)),
                (
                    "lote",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_inventario",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="detalle_orden",
                        to="core.inventario",
                    ),
                ),
                (
                    "orden",
                    models.ForeignKey(
                        db_column="id_orden",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="detalles",
                        to="core.ordencompra",
                    ),
                ),
                (
                    "producto",
                    models.ForeignKey(
                        db_column="id_producto",
                        on_delete=django.db.models.deletion.PROTECT,
                        to="core.producto",
                    ),
                ),
            ],
            options={
                "db_table": "orden_compra_detalle",
            },
        ),
        migrations.CreateModel(
            name="OrdenCompraEvento",
            fields=[
                ("id_evento", models.AutoField(primary_key=True, serialize=False)),
                ("fecha", models.DateTimeField(auto_now_add=True)),
                (
                    "accion",
                    models.CharField(
                        choices=[
                            ("ENVIADA", "Enviada a aprobación"),
                            ("RECHAZADA", "Rechazada"),
                            ("REENVIADA", "Corregida y reenviada"),
                            ("APROBADA", "Aprobada"),
                            ("RECIBIDA", "Mercadería recibida"),
                        ],
                        max_length=20,
                    ),
                ),
                ("observacion", models.TextField(blank=True, default="")),
                (
                    "orden",
                    models.ForeignKey(
                        db_column="id_orden",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="eventos",
                        to="core.ordencompra",
                    ),
                ),
                (
                    "usuario",
                    models.ForeignKey(
                        blank=True,
                        db_column="id_usuario",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "orden_compra_evento",
                "ordering": ["fecha", "id_evento"],
            },
        ),
        migrations.AddIndex(
            model_name="ordencompra",
            index=models.Index(fields=["estado"], name="idx_orden_estado"),
        ),
        migrations.AlterUniqueTogether(
            name="ordencompradetalle",
            unique_together={("orden", "producto")},
        ),
    ]
