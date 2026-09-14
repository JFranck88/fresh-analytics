# Generated manually for Fresh Analytics — agrega DecisionHistorial (RF-09 /
# UC-05: Ver Historial de Decisiones y Precisión). Guarda cada ajuste manual
# que un Comprador hace sobre la cantidad sugerida por el modelo, para poder
# comparar después sugerido vs. ajustado junto con el MAPE del modelo.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0004_alerta_fecha_lectura'),
    ]

    operations = [
        migrations.CreateModel(
            name='DecisionHistorial',
            fields=[
                ('id_historial', models.AutoField(primary_key=True, serialize=False)),
                ('fecha_decision', models.DateTimeField(auto_now_add=True)),
                ('fecha_prediccion', models.DateField(help_text='Fecha de corrida del modelo (Prediccion.fecha_prediccion) usada para esta recomendación.')),
                ('cantidad_sugerida', models.FloatField()),
                ('cantidad_ajustada', models.FloatField()),
                ('producto', models.ForeignKey(db_column='id_producto', on_delete=django.db.models.deletion.CASCADE, related_name='historial_decisiones', to='core.producto')),
                ('usuario', models.ForeignKey(blank=True, db_column='id_usuario', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='decisiones_tomadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'decision_historial',
                'verbose_name': 'Historial de decisión',
                'verbose_name_plural': 'Historial de decisiones',
            },
        ),
        migrations.AddIndex(
            model_name='decisionhistorial',
            index=models.Index(fields=['producto'], name='idx_decision_producto'),
        ),
        migrations.AddIndex(
            model_name='decisionhistorial',
            index=models.Index(fields=['usuario'], name='idx_decision_usuario'),
        ),
        migrations.AddIndex(
            model_name='decisionhistorial',
            index=models.Index(fields=['fecha_decision'], name='idx_decision_fecha'),
        ),
        migrations.AlterUniqueTogether(
            name='decisionhistorial',
            unique_together={('producto', 'usuario', 'fecha_prediccion')},
        ),
    ]
