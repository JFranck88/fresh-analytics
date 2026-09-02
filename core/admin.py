from django.contrib import admin
from .models import Producto, Venta, Inventario, Merma, Prediccion, Alerta, Configuracion


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "vida_util_dias", "precio_venta", "activo")


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    list_display = ("producto", "fecha", "cantidad", "precio_unitario")


@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = ("producto", "lote", "cantidad", "fecha_vencimiento")


@admin.register(Merma)
class MermaAdmin(admin.ModelAdmin):
    list_display = ("producto", "fecha", "cantidad", "motivo", "costo_perdida")


admin.site.register(Prediccion)
admin.site.register(Alerta)
admin.site.register(Configuracion)

# Register your models here.
