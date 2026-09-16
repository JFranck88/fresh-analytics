# Generated manually for Fresh Analytics — elimina el campo muerto
# Usuario.ultimo_acceso.
#
# Nadie escribia nunca este campo (ni una señal, ni una vista lo
# actualizaba), asi que en la pantalla de Usuarios siempre mostraba
# "Nunca", incluso para cuentas que ya habian iniciado sesion varias
# veces - el mismo tipo de bug silencioso que Alerta.fecha_lectura en
# la migracion 0004. La solucion no es agregar logica para llenarlo:
# Usuario ya hereda `last_login` de AbstractBaseUser, y Django lo
# actualiza automaticamente en cada login exitoso (señal
# user_logged_in, conectada por django.contrib.auth). El campo propio
# era simplemente redundante y nunca tuvo datos, asi que se elimina
# sin riesgo de perder informacion real.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_producto_codigo_upc_unico'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='usuario',
            name='ultimo_acceso',
        ),
    ]
