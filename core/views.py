import io
import json
from datetime import timedelta

from django.contrib import messages
from django.db.models import Sum, F, DecimalField
from django.http import FileResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .decorators import rol_requerido
from .forms import MermaForm, CrearUsuarioForm, ConfiguracionForm
from .models import Venta, Merma, Inventario, Prediccion, Producto, Usuario, Configuracion, Alerta
from .clima import pronostico_lluvia_real

NIVEL_POR_TIPO = {
    "VENCIMIENTO": "danger",
    "STOCK_BAJO": "warning",
    "EXCEDENTE": "success",
}

DIAS_QUINCENA = [14, 15, 16, 29, 30, 31, 1]

DIAS_SEMANA_ES = [
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
]


def obtener_parametro(clave, default):
    try:
        return Configuracion.objects.get(clave=clave).valor
    except Configuracion.DoesNotExist:
        return default


def obtener_validacion_cruzada():
    try:
        raw = Configuracion.objects.get(clave="validacion_cruzada_resultado").valor
        return json.loads(raw)
    except (Configuracion.DoesNotExist, json.JSONDecodeError):
        return None


def construir_contexto_inteligente(hoy):
    mensajes = []

    if hoy.day in DIAS_QUINCENA:
        mensajes.append({
            "icono": "📅",
            "texto": "Estamos en periodo de quincena - el modelo ya ajustó "
                     "sus predicciones esperando mayor demanda.",
        })

    pronostico = pronostico_lluvia_real()
    for fecha, prob in sorted(pronostico.items()):
        if fecha < hoy or fecha > hoy + timedelta(days=4):
            continue
        if prob >= 0.4:
            nombre_dia = DIAS_SEMANA_ES[fecha.weekday()]
            mensajes.append({
                "icono": "🌧️",
                "texto": (
                    f"Se pronostica lluvia el {nombre_dia} ({fecha.strftime('%d/%m')}) "
                    "- posible baja en la venta de frutas y verduras frescas."
                ),
            })
            break

    return mensajes


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

    alertas_qs = (
        Alerta.objects.filter(leida=False)
        .select_related("producto")
        .order_by("tipo", "producto__nombre")
    )
    alertas = [
        {
            "id": a.id_alerta,
            "producto": a.producto.nombre,
            "mensaje": a.mensaje,
            "nivel": NIVEL_POR_TIPO.get(a.tipo, "secondary"),
            "tipo": a.get_tipo_display(),
        }
        for a in alertas_qs
    ]

    fecha_prediccion_max = Prediccion.objects.order_by(
        "-fecha_prediccion"
    ).values_list("fecha_prediccion", flat=True).first()
    dias_desde_prediccion = (hoy - fecha_prediccion_max).days if fecha_prediccion_max else None
    modelo_al_dia = dias_desde_prediccion is not None and dias_desde_prediccion == 0

    contexto = {
        "usuario": request.user,
        "ventas_hoy": ventas_hoy,
        "merma_hoy": merma_hoy,
        "por_vencer_semana": sum(1 for a in alertas if a["tipo"] == "Vencimiento"),
        "alertas": alertas,
        "mensajes_contexto": construir_contexto_inteligente(hoy),
        "modelo_al_dia": modelo_al_dia,
        "dias_desde_prediccion": dias_desde_prediccion,
        "fecha_prediccion_max": fecha_prediccion_max,
    }
    return render(request, "dashboard.html", contexto)


@rol_requerido("GERENTE", "COMPRADOR")
def listar_alertas(request):
    alertas_qs = (
        Alerta.objects.filter(leida=False)
        .select_related("producto")
        .order_by("tipo", "producto__nombre")
    )
    alertas = [
        {
            "id": a.id_alerta,
            "producto": a.producto.nombre,
            "mensaje": a.mensaje,
            "nivel": NIVEL_POR_TIPO.get(a.tipo, "secondary"),
            "tipo": a.get_tipo_display(),
        }
        for a in alertas_qs
    ]
    return render(request, "listar_alertas.html", {"alertas": alertas})


@rol_requerido("GERENTE", "COMPRADOR")
def marcar_alerta_leida(request, alerta_id):
    alerta = get_object_or_404(Alerta, id_alerta=alerta_id)
    alerta.leida = True
    alerta.usuario_lector = request.user
    alerta.fecha_lectura = timezone.now()
    alerta.save()
    messages.success(request, "Alerta marcada como leída.")
    return redirect("listar_alertas")


@rol_requerido("COMPRADOR")
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


@rol_requerido("GERENTE", "COMPRADOR")
def listar_mermas(request):
    mermas = Merma.objects.select_related("producto").order_by("-fecha")[:100]
    return render(request, "listar_mermas.html", {"mermas": mermas})


@rol_requerido("ADMINISTRADOR", "GERENTE", "COMPRADOR")
def listar_predicciones(request):
    hoy = timezone.localdate()
    mensajes_contexto = construir_contexto_inteligente(hoy)

    fecha_max = Prediccion.objects.order_by("-fecha_prediccion").values_list(
        "fecha_prediccion", flat=True
    ).first()

    predicciones = (
        Prediccion.objects.filter(fecha_prediccion=fecha_max)
        .select_related("producto")
        .order_by("producto__nombre", "fecha_pronosticada")
    )

    datos_grafica = {}
    for p in predicciones:
        datos_grafica.setdefault(
            p.producto.nombre, {"labels": [], "predicho": [], "inferior": [], "superior": []}
        )
        datos_grafica[p.producto.nombre]["labels"].append(p.fecha_pronosticada.strftime("%d/%m"))
        datos_grafica[p.producto.nombre]["predicho"].append(float(p.valor_predicho))
        datos_grafica[p.producto.nombre]["inferior"].append(float(p.intervalo_inferior))
        datos_grafica[p.producto.nombre]["superior"].append(float(p.intervalo_superior))

    dias_desde_prediccion = (hoy - fecha_max).days if fecha_max else None
    validacion_cruzada = obtener_validacion_cruzada()

    return render(request, "listar_predicciones.html", {
        "predicciones": predicciones,
        "fecha_corrida": fecha_max,
        "modelo_al_dia": dias_desde_prediccion == 0,
        "datos_grafica_json": json.dumps(datos_grafica),
        "validacion_json": json.dumps(validacion_cruzada) if validacion_cruzada else None,
        "mensajes_contexto": mensajes_contexto,
    })


@rol_requerido("COMPRADOR")
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


@rol_requerido("ADMINISTRADOR")
def listar_usuarios(request):
    usuarios = Usuario.objects.all().order_by("nombre")
    return render(request, "listar_usuarios.html", {"usuarios": usuarios})


@rol_requerido("ADMINISTRADOR")
def crear_usuario(request):
    if request.method == "POST":
        form = CrearUsuarioForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Usuario creado correctamente.")
            return redirect("listar_usuarios")
    else:
        form = CrearUsuarioForm()
    return render(request, "crear_usuario.html", {"form": form})


@rol_requerido("ADMINISTRADOR")
def listar_configuracion(request):
    if request.method == "POST":
        form = ConfiguracionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Parámetro guardado.")
            return redirect("listar_configuracion")
    else:
        form = ConfiguracionForm()

    configuraciones = Configuracion.objects.all().order_by("clave")
    return render(
        request, "listar_configuracion.html",
        {"form": form, "configuraciones": configuraciones},
    )


@rol_requerido("ADMINISTRADOR")
def mantenimiento(request):
    hoy = timezone.localdate()

    fecha_prediccion_max = Prediccion.objects.order_by(
        "-fecha_prediccion"
    ).values_list("fecha_prediccion", flat=True).first()
    dias_desde_prediccion = (hoy - fecha_prediccion_max).days if fecha_prediccion_max else None

    fecha_alerta_max = Alerta.objects.order_by(
        "-fecha_generacion"
    ).values_list("fecha_generacion", flat=True).first()

    productos_con_mape_alto = (
        Prediccion.objects.filter(fecha_prediccion=fecha_prediccion_max, precision_modelo__gt=25)
        .select_related("producto")
        .values_list("producto__nombre", "precision_modelo")
        .distinct()
    )

    contexto = {
        "hoy": hoy,
        "fecha_prediccion_max": fecha_prediccion_max,
        "dias_desde_prediccion": dias_desde_prediccion,
        "prediccion_desactualizada": dias_desde_prediccion is not None and dias_desde_prediccion >= 2,
        "fecha_alerta_max": fecha_alerta_max,
        "total_productos": Producto.objects.filter(activo=True).count(),
        "total_ventas": Venta.objects.count(),
        "total_predicciones": Prediccion.objects.count(),
        "alertas_activas": Alerta.objects.filter(leida=False).count(),
        "productos_con_mape_alto": productos_con_mape_alto,
    }
    return render(request, "mantenimiento.html", contexto)