"""
Datos reales del supermercado (2026-10-14): importar productos y ventas
diarias (Administrador) y registrar la existencia contada en el anaquel
desde el Modo pasillo (Comprador / Administrador).
"""

from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import rol_requerido
from .importar import importar
from .models import Inventario, Producto
from .views_ordenes import leer_cantidad, texto_cantidad

TAMANO_MAXIMO = 10 * 1024 * 1024  # 10 MB


@rol_requerido("ADMINISTRADOR")
def importar_datos(request):
    resultado = None
    if request.method == "POST":
        archivo = request.FILES.get("archivo")
        if not archivo:
            messages.error(request, "Elige el archivo de Excel o CSV con los datos del supermercado.")
        elif not archivo.name.lower().endswith((".xlsx", ".csv")):
            messages.error(request, "El archivo debe ser Excel (.xlsx) o CSV.")
        elif archivo.size > TAMANO_MAXIMO:
            messages.error(request, "El archivo pesa más de 10 MB.")
        else:
            resultado = importar(archivo, archivo.name)
    reales = Producto.objects.filter(origen=Producto.Origen.REAL).order_by("nombre")
    return render(request, "importar_datos.html", {"resultado": resultado, "reales": reales})


@require_POST
@rol_requerido("COMPRADOR", "ADMINISTRADOR")
def registrar_existencia(request):
    """Conteo en el anaquel: entra un lote con la cantidad contada y su
    fecha de vencimiento. Así arranca el inventario de un producto real
    (el supermercado no lleva inventario por lote)."""
    producto = get_object_or_404(Producto, id_producto=request.POST.get("producto"), activo=True)
    volver = f"{redirect('modo_pasillo').url}?codigo={producto.codigo_upc or ''}"
    cantidad, error = leer_cantidad(producto, request.POST.get("cantidad"))
    hoy = timezone.localdate()
    try:
        vence = date.fromisoformat(request.POST.get("vence", ""))
    except ValueError:
        vence = None
    if error or not cantidad:
        messages.error(request, error or "Escribe la cantidad contada (mayor que 0).")
    elif vence is None or vence < hoy:
        messages.error(request, "La fecha de vencimiento debe ser de hoy en adelante.")
    else:
        Inventario.objects.create(
            producto=producto, cantidad=cantidad, cantidad_inicial=cantidad,
            fecha_ingreso=hoy, fecha_vencimiento=vence, lote=f"CONTEO-{hoy:%Y%m%d}",
        )
        messages.success(request, f"Existencia registrada: {texto_cantidad(producto, cantidad)} de {producto.nombre}, vence el {vence:%d/%m/%Y}.")
    return redirect(volver)
