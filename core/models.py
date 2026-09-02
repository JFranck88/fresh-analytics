"""
Modelos de Fresh Analytics.

Usuario ahora extiende AbstractBaseUser para que Django lo reconozca
como el modelo de autenticación oficial (ver settings.AUTH_USER_MODEL).
La columna sigue llamándose 'contrasena_hash' como en el ER (5.1.6.7);
solo el atributo interno de Django se llama 'password' por convención
del framework - la tabla física no cambia de nombre de columna.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import models


class Producto(models.Model):
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
        indexes = [
            models.Index(fields=["categoria"], name="idx_producto_categoria"),
            models.Index(fields=["activo"], name="idx_producto_activo"),
        ]

    def __str__(self):
        return self.nombre


class Venta(models.Model):
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
        indexes = [
            models.Index(fields=["fecha"], name="idx_venta_fecha"),
            models.Index(fields=["producto"], name="idx_venta_producto"),
            models.Index(fields=["producto", "fecha"], name="idx_venta_prod_fecha"),
        ]

    def __str__(self):
        return f"Venta #{self.id_venta} - {self.producto}"


class Inventario(models.Model):
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

    def __str__(self):
        return f"Merma #{self.id_merma} - {self.producto} ({self.motivo})"


class Prediccion(models.Model):
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
        indexes = [
            models.Index(
                fields=["producto", "fecha_pronosticada"],
                name="idx_prediccion_prod_fecha",
            ),
        ]

    def __str__(self):
        return f"Predicción {self.producto} -> {self.fecha_pronosticada}"


class UsuarioManager(BaseUserManager):
    """Gestor requerido por Django para crear usuarios y superusuarios."""

    def create_user(self, correo, nombre, rol="COMPRADOR", password=None):
        if not correo:
            raise ValueError("El correo es obligatorio")
        usuario = self.model(
            correo=self.normalize_email(correo), nombre=nombre, rol=rol
        )
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, correo, nombre, rol=None, password=None, **extra_fields):
        usuario = self.create_user(
            correo, nombre, rol="ADMINISTRADOR", password=password
        )
        usuario.is_staff = True
        usuario.is_superuser_admin = True
        usuario.save(using=self._db)
        return usuario


class Usuario(AbstractBaseUser):
    class Rol(models.TextChoices):
        ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
        GERENTE = "GERENTE", "Gerente"
        COMPRADOR = "COMPRADOR", "Comprador"

    id_usuario = models.AutoField(primary_key=True)
    nombre = models.CharField(max_length=100)
    correo = models.EmailField(max_length=100, unique=True)
    rol = models.CharField(max_length=20, choices=Rol.choices)
    # Mismo nombre de columna del ER (contrasena_hash); Django la maneja
    # internamente vía self.password, set_password() y check_password().
    password = models.CharField(max_length=255, db_column="contrasena_hash")
    ultimo_acceso = models.DateTimeField(null=True, blank=True)

    # Campos técnicos mínimos que Django exige para el login (no son
    # parte del negocio, son requisito del framework):
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser_admin = models.BooleanField(default=False)

    USERNAME_FIELD = "correo"
    REQUIRED_FIELDS = ["nombre", "rol"]

    objects = UsuarioManager()

    class Meta:
        db_table = "usuario"
        indexes = [
            models.Index(fields=["correo"], name="idx_usuario_correo"),
            models.Index(fields=["rol"], name="idx_usuario_rol"),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.rol})"

    def es_administrador(self):
        return self.rol == self.Rol.ADMINISTRADOR

    def es_gerente(self):
        return self.rol == self.Rol.GERENTE

    def es_comprador(self):
        return self.rol == self.Rol.COMPRADOR


class Alerta(models.Model):
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
        indexes = [
            models.Index(fields=["producto"], name="idx_alerta_producto"),
            models.Index(fields=["leida"], name="idx_alerta_leida"),
        ]

    def __str__(self):
        return f"Alerta {self.tipo} - {self.producto}"


class Configuracion(models.Model):
    id_config = models.AutoField(primary_key=True)
    clave = models.CharField(max_length=50, unique=True)
    valor = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, default="")
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "configuracion"

    def __str__(self):
        return f"{self.clave} = {self.valor}"