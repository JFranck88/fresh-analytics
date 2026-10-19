"""
Órdenes de compra con aprobación (UC-03 / UC-06 / RF-07), 2026-10-13.

Ciclo:
  Recomendaciones --"Enviar a aprobación"--> PENDIENTE
  PENDIENTE --Gerente aprueba--> APROBADA --"Registrar recepción"--> RECIBIDA
  PENDIENTE --Gerente rechaza (observación obligatoria)--> RECHAZADA
  RECHAZADA --Comprador corrige y reenvía--> PENDIENTE (otra vuelta)

El PDF para el proveedor solo se emite cuando la orden está aprobada
(UC-06, paso 4). Al registrar la recepción se crean los lotes en el
inventario, así que el stock sube con mercadería real además de la
reposición simulada (ver core/inventario.py: la simulación usa punto de
reorden, así que si ya entró una orden real no vuelve a pedir de más).
"""

import io
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .decorators import rol_requerido
from .models import (
    DecisionHistorial, Inventario, OrdenCompra, OrdenCompraDetalle, OrdenCompraEvento, Producto,
)

ESTADOS_ABIERTOS = (OrdenCompra.Estado.PENDIENTE, OrdenCompra.Estado.RECHAZADA)


def _es_gerente(usuario):
    return usuario.rol == "GERENTE" or usuario.is_superuser_admin


def _es_comprador(usuario):
    return usuario.rol == "COMPRADOR" or usuario.is_superuser_admin


def producto_del_filtro(request):
    """?producto=<id> que pone el buscador de la topbar (ver
    DESTINO_BUSCADOR en context_processors.py). Un valor que no es número o
    un producto que no existe se ignoran en vez de dar error."""
    valor = request.GET.get("producto", "").strip()
    if not valor.isdigit():
        return None
    return Producto.objects.filter(id_producto=int(valor)).first()


def url_pasillo(producto):
    """Ficha del producto en el Modo pasillo: por su código si lo tiene; si
    no (producto sin código de barras), por su id."""
    if producto.codigo_upc:
        return reverse("modo_pasillo") + "?" + urlencode({"codigo": producto.codigo_upc})
    return reverse("modo_pasillo") + "?" + urlencode({"producto": producto.id_producto})


def texto_cantidad(producto, valor):
    if valor is None:
        return "—"
    if producto.unidad_medida == Producto.UnidadMedida.PESO:
        return f"{valor:.2f} lb".replace(".", ",")
    return f"{valor:.0f} u."


def valor_casilla(producto, valor):
    """Texto para una casilla type="number": punto decimal y, para productos
    por unidad, sin ".0" (118, no 118.0)."""
    if producto.unidad_medida == Producto.UnidadMedida.PESO:
        return f"{valor:.2f}"
    return f"{valor:.0f}"


def leer_cantidad(producto, valor_raw):
    """Convierte lo que escribió la persona en una cantidad válida para el
    producto. Devuelve (cantidad, error). Vacío cuenta como 0 (el producto
    queda fuera del pedido)."""
    valor_raw = (valor_raw or "").strip().replace(",", ".")
    if valor_raw == "":
        return 0, None
    try:
        cantidad = float(valor_raw)
    except ValueError:
        return None, f"{producto.nombre}: \"{valor_raw}\" no es un número."
    if cantidad < 0:
        return None, f"{producto.nombre}: la cantidad no puede ser negativa."
    if producto.unidad_medida != Producto.UnidadMedida.PESO and cantidad != int(cantidad):
        return None, f"{producto.nombre} se maneja por unidad - la cantidad debe ser un número entero."
    return producto.redondear_cantidad(cantidad), None


def registrar_decision(producto, usuario, fecha_prediccion, sugerido, ajustada, stock=None):
    """RF-09: deja en el historial solo los ajustes distintos al sugerido.
    Si la persona vuelve a poner el sugerido, se quita el registro viejo.
    `stock` es el stock vigente al decidir (para evaluar después quién
    acertó más); si no se conoce, se conserva el que ya estaba guardado."""
    if ajustada == sugerido:
        DecisionHistorial.objects.filter(
            producto=producto, usuario=usuario, fecha_prediccion=fecha_prediccion,
        ).delete()
        return
    DecisionHistorial.objects.update_or_create(
        producto=producto, usuario=usuario, fecha_prediccion=fecha_prediccion,
        defaults={"cantidad_sugerida": sugerido, "cantidad_ajustada": ajustada,
                  **({"stock_al_decidir": stock} if stock is not None else {})},
    )


def orden_abierta_de(usuario):
    return OrdenCompra.objects.filter(comprador=usuario, estado__in=ESTADOS_ABIERTOS).first()


def crear_orden(usuario, fecha_prediccion, lineas):
    """lineas: [(producto, sugerido, cantidad)]. Solo entran las de cantidad
    mayor que 0 (UC-03, 2a: lo que quedó en 0 se quita del pedido)."""
    lineas = [(p, s, c) for p, s, c in lineas if c > 0]
    if not lineas:
        return None
    with transaction.atomic():
        orden = OrdenCompra.objects.create(comprador=usuario, fecha_prediccion=fecha_prediccion)
        OrdenCompraDetalle.objects.bulk_create([
            OrdenCompraDetalle(
                orden=orden, producto=p, cantidad_sugerida=s, cantidad=c, precio_compra=p.precio_compra,
            )
            for p, s, c in lineas
        ])
        OrdenCompraEvento.objects.create(orden=orden, usuario=usuario, accion=OrdenCompraEvento.Accion.ENVIADA)
    return orden


def _orden_visible(request, id_orden):
    orden = get_object_or_404(OrdenCompra.objects.select_related("comprador", "revisada_por"), id_orden=id_orden)
    if not _es_gerente(request.user) and orden.comprador_id != request.user.id_usuario:
        raise PermissionDenied
    return orden


@rol_requerido("GERENTE", "COMPRADOR")
def listar_ordenes(request):
    ordenes = OrdenCompra.objects.select_related("comprador", "revisada_por").prefetch_related("detalles")
    if not _es_gerente(request.user):
        ordenes = ordenes.filter(comprador=request.user)
    estado = request.GET.get("estado", "")
    if estado in OrdenCompra.Estado.values:
        ordenes = ordenes.filter(estado=estado)
    # Buscador de la topbar: las órdenes que llevan ese producto.
    producto_filtro = producto_del_filtro(request)
    if producto_filtro:
        ordenes = ordenes.filter(detalles__producto=producto_filtro).distinct()
    ordenes = list(ordenes[:100])
    # Lo que requiere acción de quien mira va primero.
    if _es_gerente(request.user):
        ordenes.sort(key=lambda o: o.estado != OrdenCompra.Estado.PENDIENTE)
    else:
        ordenes.sort(key=lambda o: o.estado not in (OrdenCompra.Estado.RECHAZADA, OrdenCompra.Estado.APROBADA))
    return render(request, "ordenes/listar.html", {
        "ordenes": ordenes,
        "estado_filtro": estado,
        "estados": OrdenCompra.Estado.choices,
        "producto_filtro": producto_filtro,
        "es_gerente": _es_gerente(request.user),
    })


@rol_requerido("GERENTE", "COMPRADOR")
def detalle_orden(request, id_orden):
    orden = _orden_visible(request, id_orden)
    detalles = list(orden.detalles.select_related("producto", "lote").order_by("producto__proveedor", "producto__nombre"))
    hoy = timezone.localdate()
    for d in detalles:
        d.texto_cantidad = texto_cantidad(d.producto, d.cantidad)
        d.texto_sugerida = texto_cantidad(d.producto, d.cantidad_sugerida)
        d.texto_recibida = texto_cantidad(d.producto, d.cantidad_recibida)
        d.vence_sugerido = hoy + timedelta(days=d.producto.vida_util_dias)
        d.ajustado = d.cantidad != d.cantidad_sugerida
        d.valor_casilla = valor_casilla(d.producto, d.cantidad)
    usuario = request.user
    es_dueno = orden.comprador_id == usuario.id_usuario or usuario.is_superuser_admin
    return render(request, "ordenes/detalle.html", {
        "orden": orden,
        "detalles": detalles,
        "total": sum(d.subtotal for d in detalles),
        "eventos": orden.eventos.select_related("usuario"),
        "puede_revisar": _es_gerente(usuario) and orden.estado == OrdenCompra.Estado.PENDIENTE,
        "puede_corregir": _es_comprador(usuario) and es_dueno and orden.estado == OrdenCompra.Estado.RECHAZADA,
        "puede_recibir": _es_comprador(usuario) and es_dueno and orden.estado == OrdenCompra.Estado.APROBADA,
        "puede_pdf": orden.estado in (OrdenCompra.Estado.APROBADA, OrdenCompra.Estado.RECIBIDA),
        "hoy": hoy,
    })


def _bloquear(id_orden, estado_esperado):
    """Trae la orden bloqueada para escritura y confirma que siga en el
    estado esperado (evita que dos personas actúen sobre la misma orden)."""
    orden = OrdenCompra.objects.select_for_update().get(id_orden=id_orden)
    return orden if orden.estado == estado_esperado else None


@require_POST
@rol_requerido("GERENTE")
def aprobar_orden(request, id_orden):
    _orden_visible(request, id_orden)
    with transaction.atomic():
        orden = _bloquear(id_orden, OrdenCompra.Estado.PENDIENTE)
        if orden is None:
            messages.warning(request, "Esa orden ya no está pendiente de aprobación.")
            return redirect("detalle_orden", id_orden=id_orden)
        orden.estado = OrdenCompra.Estado.APROBADA
        orden.revisada_por = request.user
        orden.fecha_revision = timezone.now()
        orden.save()
        OrdenCompraEvento.objects.create(orden=orden, usuario=request.user, accion=OrdenCompraEvento.Accion.APROBADA)
    messages.success(request, f"Orden {orden.numero} aprobada. Ya se puede emitir el PDF para el proveedor.")
    return redirect("detalle_orden", id_orden=id_orden)


@require_POST
@rol_requerido("GERENTE")
def rechazar_orden(request, id_orden):
    _orden_visible(request, id_orden)
    observacion = request.POST.get("observacion", "").strip()
    if not observacion:
        # UC-06, 3a: la observación es obligatoria.
        messages.error(request, "Para rechazar la orden escribe una observación: el Comprador la necesita para corregir.")
        return redirect("detalle_orden", id_orden=id_orden)
    with transaction.atomic():
        orden = _bloquear(id_orden, OrdenCompra.Estado.PENDIENTE)
        if orden is None:
            messages.warning(request, "Esa orden ya no está pendiente de aprobación.")
            return redirect("detalle_orden", id_orden=id_orden)
        orden.estado = OrdenCompra.Estado.RECHAZADA
        orden.revisada_por = request.user
        orden.fecha_revision = timezone.now()
        orden.observacion = observacion
        orden.save()
        OrdenCompraEvento.objects.create(
            orden=orden, usuario=request.user, accion=OrdenCompraEvento.Accion.RECHAZADA, observacion=observacion,
        )
    messages.success(request, f"Orden {orden.numero} devuelta al Comprador para corrección.")
    return redirect("detalle_orden", id_orden=id_orden)


@require_POST
@rol_requerido("COMPRADOR")
def reenviar_orden(request, id_orden):
    """El Comprador corrige las cantidades de una orden rechazada y la
    vuelve a enviar. Una línea en 0 sale del pedido; debe quedar al menos una."""
    orden = _orden_visible(request, id_orden)
    if orden.comprador_id != request.user.id_usuario and not request.user.is_superuser_admin:
        raise PermissionDenied
    detalles = list(orden.detalles.select_related("producto"))
    nuevas, errores = {}, []
    for d in detalles:
        cantidad, error = leer_cantidad(d.producto, request.POST.get(f"cantidad_{d.id_detalle}"))
        if error:
            errores.append(error)
        else:
            nuevas[d.id_detalle] = cantidad
    if errores:
        for e in errores:
            messages.error(request, e)
        return redirect("detalle_orden", id_orden=id_orden)
    if not any(c > 0 for c in nuevas.values()):
        messages.error(request, "La orden tiene que llevar al menos un producto con cantidad mayor que 0.")
        return redirect("detalle_orden", id_orden=id_orden)

    with transaction.atomic():
        orden = _bloquear(id_orden, OrdenCompra.Estado.RECHAZADA)
        if orden is None:
            messages.warning(request, "Esa orden ya no está en corrección.")
            return redirect("detalle_orden", id_orden=id_orden)
        cambios = []
        for d in detalles:
            cantidad = nuevas[d.id_detalle]
            if cantidad != d.cantidad:
                cambios.append(f"{d.producto.nombre}: {texto_cantidad(d.producto, d.cantidad)} → {texto_cantidad(d.producto, cantidad)}")
            if cantidad <= 0:
                d.delete()
            elif cantidad != d.cantidad:
                d.cantidad = cantidad
                d.save(update_fields=["cantidad"])
            registrar_decision(d.producto, orden.comprador, orden.fecha_prediccion, d.cantidad_sugerida, cantidad)
        orden.estado = OrdenCompra.Estado.PENDIENTE
        orden.save(update_fields=["estado"])
        OrdenCompraEvento.objects.create(
            orden=orden, usuario=request.user, accion=OrdenCompraEvento.Accion.REENVIADA,
            observacion="; ".join(cambios) if cambios else "Sin cambios en las cantidades.",
        )
    messages.success(request, f"Orden {orden.numero} corregida y reenviada a aprobación.")
    return redirect("detalle_orden", id_orden=id_orden)


@require_POST
@rol_requerido("COMPRADOR")
def recibir_orden(request, id_orden):
    """Llegó la mercadería: por cada línea se registra lo que realmente
    llegó y su fecha de vencimiento, y se crea el lote en el inventario."""
    orden = _orden_visible(request, id_orden)
    if orden.comprador_id != request.user.id_usuario and not request.user.is_superuser_admin:
        raise PermissionDenied
    hoy = timezone.localdate()
    detalles = list(orden.detalles.select_related("producto"))
    recibido, errores = {}, []
    for d in detalles:
        cantidad, error = leer_cantidad(d.producto, request.POST.get(f"recibida_{d.id_detalle}"))
        vence_raw = request.POST.get(f"vence_{d.id_detalle}", "")
        try:
            vence = date.fromisoformat(vence_raw) if vence_raw else hoy + timedelta(days=d.producto.vida_util_dias)
        except ValueError:
            vence, error = None, error or f"{d.producto.nombre}: la fecha de vencimiento no es válida."
        if not error and cantidad > 0 and vence < hoy:
            error = f"{d.producto.nombre}: no se puede recibir mercadería ya vencida."
        if error:
            errores.append(error)
        else:
            recibido[d.id_detalle] = (cantidad, vence)
    if errores:
        for e in errores:
            messages.error(request, e)
        return redirect("detalle_orden", id_orden=id_orden)

    with transaction.atomic():
        orden = _bloquear(id_orden, OrdenCompra.Estado.APROBADA)
        if orden is None:
            messages.warning(request, "Esa orden no está aprobada o ya se recibió.")
            return redirect("detalle_orden", id_orden=id_orden)
        diferencias = []
        for d in detalles:
            cantidad, vence = recibido[d.id_detalle]
            d.cantidad_recibida = cantidad
            if cantidad > 0:
                d.lote = Inventario.objects.create(
                    producto=d.producto, fecha_ingreso=hoy, fecha_vencimiento=vence,
                    cantidad=cantidad, cantidad_inicial=cantidad, lote=f"{orden.numero}-{d.producto_id}",
                )
            d.save(update_fields=["cantidad_recibida", "lote"])
            if cantidad != d.cantidad:
                diferencias.append(
                    f"{d.producto.nombre}: pedido {texto_cantidad(d.producto, d.cantidad)}, "
                    f"llegó {texto_cantidad(d.producto, cantidad)}"
                )
        orden.estado = OrdenCompra.Estado.RECIBIDA
        orden.recibida_por = request.user
        orden.fecha_recepcion = timezone.now()
        orden.save()
        OrdenCompraEvento.objects.create(
            orden=orden, usuario=request.user, accion=OrdenCompraEvento.Accion.RECIBIDA,
            observacion="; ".join(diferencias) if diferencias else "Llegó completa.",
        )
    messages.success(request, f"Recepción de {orden.numero} registrada: los lotes ya están en el inventario.")
    return redirect("detalle_orden", id_orden=id_orden)


@rol_requerido("GERENTE", "COMPRADOR")
def pdf_orden(request, id_orden):
    orden = _orden_visible(request, id_orden)
    if orden.estado not in (OrdenCompra.Estado.APROBADA, OrdenCompra.Estado.RECIBIDA):
        # UC-06, paso 4: solo una orden aprobada autoriza emitir el documento.
        messages.error(request, "El PDF solo se emite cuando el Gerente aprueba la orden.")
        return redirect("detalle_orden", id_orden=id_orden)

    detalles = orden.detalles.select_related("producto").order_by("producto__proveedor", "producto__nombre")
    por_proveedor = {}
    for d in detalles:
        por_proveedor.setdefault(d.producto.proveedor or "Sin proveedor", []).append(d)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title=f"Orden de compra {orden.numero}")
    estilos = getSampleStyleSheet()
    revision = timezone.localtime(orden.fecha_revision) if orden.fecha_revision else None
    elementos = [
        Paragraph(f"Fresh Analytics - Orden de compra {orden.numero}", estilos["Title"]),
        Paragraph(f"Elaborada por: {orden.comprador.nombre} - {timezone.localtime(orden.fecha_creacion):%d/%m/%Y}", estilos["Normal"]),
        Paragraph(
            f"Aprobada por: {orden.revisada_por.nombre if orden.revisada_por else '—'}"
            f"{' - ' + revision.strftime('%d/%m/%Y %H:%M') if revision else ''}",
            estilos["Normal"],
        ),
        Spacer(1, 16),
    ]
    estilo_tabla = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B4332")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
    ])
    total = 0
    for proveedor, items in por_proveedor.items():
        elementos.append(Paragraph(proveedor, estilos["Heading3"]))
        filas = [["UPC", "Producto", "Cantidad", "Precio compra", "Subtotal"]]
        subtotal_proveedor = 0
        for d in items:
            unidad = "lb" if d.producto.unidad_medida == Producto.UnidadMedida.PESO else "u."
            filas.append([
                d.producto.codigo_upc or "—", d.producto.nombre, texto_cantidad(d.producto, d.cantidad),
                f"Q {d.precio_compra:.2f}/{unidad}", f"Q {d.subtotal:,.2f}",
            ])
            subtotal_proveedor += d.subtotal
        filas.append(["", "", "", "Total proveedor", f"Q {subtotal_proveedor:,.2f}"])
        total += subtotal_proveedor
        tabla = Table(filas, colWidths=[70, 170, 80, 95, 85])
        tabla.setStyle(estilo_tabla)
        elementos += [tabla, Spacer(1, 14)]
    elementos.append(Paragraph(f"<b>Total de la orden: Q {total:,.2f}</b>", estilos["Normal"]))
    doc.build(elementos)
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f"{orden.numero}.pdf")
