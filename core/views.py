from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, F, DecimalField
from django.shortcuts import render, redirect
from django.utils import timezone

from .models import Venta, Merma, Inventario
from .forms import MermaForm


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


@login_required
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

@login_required
def listar_mermas(request):
    mermas = Merma.objects.select_related("producto").order_by("-fecha")[:100]
    return render(request, "listar_mermas.html", {"mermas": mermas})

@login_required
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