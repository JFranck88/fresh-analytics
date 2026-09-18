from django.contrib import admin
from .models import Producto, Venta, Inventario, Merma, Prediccion, Alerta, Configuracion, DecisionHistorial


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("codigo_upc", "nombre", "categoria", "unidad_medida", "vida_util_dias", "precio_venta", "activo")
    list_filter = ("categoria", "unidad_medida", "activo")
    search_fields = ("codigo_upc", "nombre")


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


@admin.register(DecisionHistorial)
class DecisionHistorialAdmin(admin.ModelAdmin):
    list_display = ("producto", "usuario", "fecha_decision", "cantidad_sugerida", "cantidad_ajustada")
    list_filter = ("usuario",)

# Register your models here.
