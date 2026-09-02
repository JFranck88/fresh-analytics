from datetime import timedelta

from .decorators import rol_requerido
from django.contrib import messages
from django.db.models import Sum, F, DecimalField
from django.shortcuts import render, redirect
from django.utils import timezone
import io
from django.http import FileResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from .models import Venta, Merma, Inventario, Prediccion, Producto
from .forms import MermaForm


@rol_requerido("ADMINISTRADOR", "GERENTE", "COMPRADOR")
def dashboard(request):
    hoy = timezone.localdate()

    ventas_hoy = Venta.objects.filter(fecha__date=hoy).aggregate(
        total=Sum(
            F("cantidad") * F("precio_unitario"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"] or 0

    merma_hoy = Merma.objects.filter(fecha=hoy).aggregate(
        total=Sum("costo_perdida")
    )["total"] or 0

    limite = hoy + timedelta(days=7)
    lotes_por_vencer = (
        Inventario.objects.filter(
            fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=limite
        )
        .select_related("producto")
        .order_by("fecha_vencimiento")
    )

    alertas = []
    for lote in lotes_por_vencer:
        dias_restantes = (lote.fecha_vencimiento - hoy).days
        alertas.append(
            {
                "producto": lote.producto.nombre,
                "dias_restantes": dias_restantes,
                "cantidad": lote.cantidad,
                "nivel": "danger" if dias_restantes <= 3 else "warning",
            }
        )

    contexto = {
        "usuario": request.user,
        "ventas_hoy": ventas_hoy,
        "merma_hoy": merma_hoy,
        "por_vencer_semana": len(alertas),
        "alertas": alertas,
    }
    return render(request, "dashboard.html", contexto)


@rol_requerido("GERENTE", "COMPRADOR")
def registrar_merma(request):
    if request.method == "POST":
        form = MermaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Merma registrada correctamente.")
            return redirect("dashboard")
    else:
        form = MermaForm(initial={"fecha": timezone.localdate()})

    return render(request, "registrar_merma.html", {"form": form})

@rol_requerido("COMPRADOR")
def listar_mermas(request):
    mermas = Merma.objects.select_related("producto").order_by("-fecha")[:100]
    return render(request, "listar_mermas.html", {"mermas": mermas})

@rol_requerido("ADMINISTRADOR", "GERENTE", "COMPRADOR")
def listar_alertas(request):
    hoy = timezone.localdate()
    limite = hoy + timedelta(days=7)
    lotes = (
        Inventario.objects.filter(
            fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=limite
        )
        .select_related("producto")
        .order_by("fecha_vencimiento")
    )

    alertas = []
    for lote in lotes:
        dias_restantes = (lote.fecha_vencimiento - hoy).days
        alertas.append(
            {
                "producto": lote.producto.nombre,
                "dias_restantes": dias_restantes,
                "cantidad": lote.cantidad,
                "nivel": "danger" if dias_restantes <= 3 else "warning",
            }
        )

    return render(request, "listar_alertas.html", {"alertas": alertas})

from .models import Prediccion


@rol_requerido("ADMINISTRADOR", "GERENTE", "COMPRADOR")
def listar_predicciones(request):
    fecha_max = Prediccion.objects.order_by("-fecha_prediccion").values_list(
        "fecha_prediccion", flat=True
    ).first()

    predicciones = (
        Prediccion.objects.filter(fecha_prediccion=fecha_max)
        .select_related("producto")
        .order_by("producto__nombre", "fecha_pronosticada")
    )

    return render(
        request, "listar_predicciones.html",
        {"predicciones": predicciones, "fecha_corrida": fecha_max},
    )
@rol_requerido("GERENTE", "COMPRADOR")
def listar_recomendaciones(request):
    hoy = timezone.localdate()
    fecha_max = Prediccion.objects.order_by("-fecha_prediccion").values_list(
        "fecha_prediccion", flat=True
    ).first()

    recomendaciones = []
    for producto in Producto.objects.filter(activo=True):
        prediccion_semana = Prediccion.objects.filter(
            producto=producto, fecha_prediccion=fecha_max
        ).aggregate(total=Sum("valor_predicho"))["total"] or 0

        stock_actual = Inventario.objects.filter(
            producto=producto, fecha_vencimiento__gte=hoy
        ).aggregate(total=Sum("cantidad"))["total"] or 0

        sugerido = max(0, round(prediccion_semana - stock_actual))

        recomendaciones.append({
            "producto": producto.nombre,
            "prediccion_semana": round(prediccion_semana, 1),
            "stock_actual": stock_actual,
            "sugerido": sugerido,
        })

    return render(request, "listar_recomendaciones.html", {"recomendaciones": recomendaciones})
@rol_requerido("COMPRADOR")
def generar_orden_compra(request):
    hoy = timezone.localdate()
    fecha_max = Prediccion.objects.order_by("-fecha_prediccion").values_list(
        "fecha_prediccion", flat=True
    ).first()

    filas_por_proveedor = {}
    for producto in Producto.objects.filter(activo=True).order_by("proveedor", "nombre"):
        prediccion_semana = Prediccion.objects.filter(
            producto=producto, fecha_prediccion=fecha_max
        ).aggregate(total=Sum("valor_predicho"))["total"] or 0
        stock_actual = Inventario.objects.filter(
            producto=producto, fecha_vencimiento__gte=hoy
        ).aggregate(total=Sum("cantidad"))["total"] or 0
        sugerido = max(0, round(prediccion_semana - stock_actual))
        if sugerido > 0:
            proveedor = producto.proveedor or "Sin proveedor"
            filas_por_proveedor.setdefault(proveedor, []).append([producto.nombre, sugerido])

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    estilos = getSampleStyleSheet()
    elementos = [
        Paragraph("Fresh Analytics - Orden de Compra Sugerida", estilos["Title"]),
        Paragraph(f"Fecha: {hoy.strftime('%d/%m/%Y')}", estilos["Normal"]),
        Spacer(1, 16),
    ]

    for proveedor, items in filas_por_proveedor.items():
        elementos.append(Paragraph(proveedor, estilos["Heading3"]))
        data = [["Producto", "Cantidad sugerida"]] + items
        tabla = Table(data, colWidths=[300, 150])
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#212529")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
        ]))
        elementos.append(tabla)
        elementos.append(Spacer(1, 16))

    doc.build(elementos)
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f"orden_compra_{hoy}.pdf")