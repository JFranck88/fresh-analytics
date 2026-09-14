# Generated manually for Fresh Analytics — agrega código de barras (UPC/EAN)
# opcional al producto, usado por el buscador global.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_alter_alerta_options_alter_configuracion_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='codigo_upc',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=64,
                help_text='Código de barras (UPC/EAN) del producto, si se conoce.',
            ),
        ),
    ]
