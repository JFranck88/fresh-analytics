from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F, DecimalField
from django.shortcuts import render
from django.utils import timezone

from .models import Venta, Merma, Inventario


@login_required
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