"""
Modelos de Fresh Analytics.

Traducción directa de la sección 5.1 (Diseño de Datos) del Capítulo V
del Trabajo de Graduación. Los nombres de índices coinciden con los
documentados en 5.1.6 para que el código y la tesis sean el mismo
artefacto, no dos versiones distintas.
"""

from django.db import models


class Producto(models.Model):
    """5.1.6.1 - Catálogo maestro de productos perecederos."""

    class Categoria(models.TextChoices):
        LACTEOS = "LACTEOS", "Lácteos"
        CARNES = "CARNES", "Carnes"
        FRUTAS = "FRUTAS", "Frutas"
        VERDURAS = "VERDURAS", "Verduras"
        PANADERIA = "PANADERIA", "Panadería"

    id_producto = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=200)
    categoria = models.CharField(max_length=50, choices=Categoria.choices)
    vida_util_dias = models.PositiveIntegerField()
    proveedor = models.CharField(max_length=100, blank=True, default="")
    precio_compra = models.DecimalField(max_digits=10, decimal_places=2)
    precio_venta = models.DecimalField(max_digits=10, decimal_places=2)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "producto"
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        indexes = [
            models.Index(fields=["categoria"], name="idx_producto_categoria"),
            models.Index(fields=["activo"], name="idx_producto_activo"),
        ]

    def __str__(self):
        return self.nombre


class Venta(models.Model):
    """5.1.6.2 - Historial de transacciones de venta."""

    id_venta = models.BigAutoField(primary_key=True)
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, db_column="id_producto",
        related_name="ventas",
    )
    fecha = models.DateTimeField()
    cantidad = models.FloatField()
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    promocion_aplicada = models.BooleanField(default=False)

    class Meta:
        db_table = "venta"
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        indexes = [
            models.Index(fields=["fecha"], name="idx_venta_fecha"),
            models.Index(fields=["producto"], name="idx_venta_producto"),
            models.Index(fields=["producto", "fecha"], name="idx_venta_prod_fecha"),
        ]

    def __str__(self):
        return f"Venta #{self.id_venta} - {self.producto}"


class Inventario(models.Model):
    """5.1.6.3 - Stock por lote, con fechas de ingreso y vencimiento."""

    id_inventario = models.BigAutoField(primary_key=True)
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, db_column="id_producto",
        related_name="lotes_inventario",
    )
    fecha_ingreso = models.DateField()
    fecha_vencimiento = models.DateField()
    cantidad = models.FloatField()
    lote = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        db_table = "inventario"
        verbose_name = "Inventario"
        verbose_name_plural = "Inventario"
        indexes = [
            models.Index(fields=["producto"], name="idx_inventario_producto"),
            models.Index(fields=["fecha_vencimiento"], name="idx_inventario_vencim"),
            models.Index(
                fields=["producto", "fecha_vencimiento"],
                name="idx_inventario_prod_venc",
            ),
        ]

    def __str__(self):
        return f"Lote {self.lote} - {self.producto}"


class Merma(models.Model):
    """5.1.6.4 - Productos desechados y su causa."""

    class Motivo(models.TextChoices):
        VENCIMIENTO = "VENCIMIENTO", "Vencimiento"
        DANO = "DANO", "Daño"
        ROBO = "ROBO", "Robo"
        OTRO = "OTRO", "Otro"

    id_merma = models.BigAutoField(primary_key=True)
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, db_column="id_producto",
        related_name="mermas",
    )
    fecha = models.DateField()
    cantidad = models.FloatField()
    motivo = models.CharField(max_length=50, choices=Motivo.choices)
    costo_perdida = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = "merma"
        verbose_name = "Merma"
        verbose_name_plural = "Mermas"

    def __str__(self):
        return f"Merma #{self.id_merma} - {self.producto} ({self.motivo})"


class Prediccion(models.Model):
    """5.1.6.5 - Salida del modelo Prophet por producto y fecha futura."""

    id_prediccion = models.BigAutoField(primary_key=True)
    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, db_column="id_producto",
        related_name="predicciones",
    )
    fecha_prediccion = models.DateField()
    fecha_pronosticada = models.DateField()
    valor_predicho = models.FloatField()
    intervalo_inferior = models.FloatField()
    intervalo_superior = models.FloatField()
    precision_modelo = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "prediccion"
        verbose_name = "Predicción"
        verbose_name_plural = "Predicciones"
        indexes = [
            models.Index(
                fields=["producto", "fecha_pronosticada"],
                name="idx_prediccion_prod_fecha",
            ),
        ]

    def __str__(self):
        return f"Predicción {self.producto} -> {self.fecha_pronosticada}"


class Usuario(models.Model):
    """5.1.6.7 - Usuarios del sistema Fresh Analytics."""

    class Rol(models.TextChoices):
        ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
        GERENTE = "GERENTE", "Gerente"
        COMPRADOR = "COMPRADOR", "Comprador"

    id_usuario = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    correo = models.EmailField(max_length=100, unique=True)
    rol = models.CharField(max_length=20, choices=Rol.choices)
    contrasena_hash = models.CharField(max_length=255)
    ultimo_acceso = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "usuario"
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        indexes = [
            models.Index(fields=["correo"], name="idx_usuario_correo"),
            models.Index(fields=["rol"], name="idx_usuario_rol"),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.rol})"


class Alerta(models.Model):
    """
    5.1.6.6 - Notificaciones automáticas sobre productos en riesgo.

    Relación "lee" con Usuario: 1 (Usuario, obligatorio) a 0..N (Alerta),
    tal como está documentado en 5.1.6.6 y 5.1.6.7 ("relación opcional").
    Por eso el FK vive aquí, del lado "muchos", y es nulo.
    """

    class Tipo(models.TextChoices):
        VENCIMIENTO = "VENCIMIENTO", "Vencimiento"
        STOCK_BAJO = "STOCK_BAJO", "Stock bajo"
        EXCEDENTE = "EXCEDENTE", "Excedente"

    id_alerta = models.BigAutoField(primary_key=True)
    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, db_column="id_producto",
        related_name="alertas",
    )
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    accion_tomada = models.TextField(blank=True, default="")
    usuario_lector = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="id_usuario_lector", related_name="alertas_leidas",
    )

    class Meta:
        db_table = "alerta"
        verbose_name = "Alerta"
        verbose_name_plural = "Alertas"
        indexes = [
            models.Index(fields=["producto"], name="idx_alerta_producto"),
            models.Index(fields=["leida"], name="idx_alerta_leida"),
        ]

    def __str__(self):
        return f"Alerta {self.tipo} - {self.producto}"


class Configuracion(models.Model):
    """5.1.6.8 - Parámetros ajustables del sistema."""

    id_config = models.AutoField(primary_key=True)
    clave = models.CharField(max_length=50, unique=True)
    valor = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, default="")
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "configuracion"
        verbose_name = "Configuración"
        verbose_name_plural = "Configuraciones"

    def __str__(self):
        return f"{self.clave} = {self.valor}"

# Create your models here.
