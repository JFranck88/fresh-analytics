import io
import json
from datetime import timedelta

from django.contrib import messages
from django.db.models import Sum, F, Q, Avg, DecimalField
from django.db.models.functions import TruncDate
from django.http import FileResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .decorators import rol_requerido
from .forms import (
    MermaForm, CrearUsuarioForm, ConfiguracionForm,
    EditarUsuarioForm, RestablecerPasswordForm,
)
from .models import Venta, Merma, Inventario, Prediccion, Producto, Usuario, Configuracion, Alerta, DecisionHistorial
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

CATEGORIA_TEXTO = {
    "FRUTAS": "frutas",
    "VERDURAS": "verduras",
    "LACTEOS": "lácteos",
    "CARNES": "carnes",
    "PANADERIA": "productos de panadería",
}


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


def categorias_afectadas_por_lluvia(fecha_lluvia):
    """Revisa las predicciones REALES de la semana: para cada producto,
    compara su venta estimada en el dia de lluvia contra el promedio de
    sus otros dias. Si cae al menos 5%, se considera afectado, y se
    devuelve el conjunto de categorias con al menos un producto
    afectado - dinamico, no una lista escrita a mano."""
    fecha_max = Prediccion.objects.order_by(
        "-fecha_prediccion"
    ).values_list("fecha_prediccion", flat=True).first()
    if not fecha_max:
        return set()

    predicciones_semana = (
        Prediccion.objects.filter(fecha_prediccion=fecha_max)
        .select_related("producto")
    )

    por_producto = {}
    for p in predicciones_semana:
        por_producto.setdefault(p.producto_id, []).append(p)

    categorias = set()
    for filas in por_producto.values():
        fila_lluvia = next((f for f in filas if f.fecha_pronosticada == fecha_lluvia), None)
        if not fila_lluvia:
            continue
        otras = [f.valor_predicho for f in filas if f.fecha_pronosticada != fecha_lluvia]
        if not otras:
            continue
        promedio_otros = sum(otras) / len(otras)
        if promedio_otros > 0 and fila_lluvia.valor_predicho < promedio_otros * 0.95:
            categorias.add(filas[0].producto.categoria)

    return categorias


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
            categorias = categorias_afectadas_por_lluvia(fecha)

            if categorias:
                nombres = sorted(CATEGORIA_TEXTO.get(c, c.lower()) for c in categorias)
                if len(nombres) == 1:
                    texto_categorias = nombres[0]
                else:
                    texto_categorias = ", ".join(nombres[:-1]) + " y " + nombres[-1]
                texto = (
                    f"Se pronostica lluvia el {nombre_dia} ({fecha.strftime('%d/%m')}) "
                    f"- las predicciones muestran una posible baja en la venta de {texto_categorias}."
                )
            else:
                texto = f"Se pronostica lluvia el {nombre_dia} ({fecha.strftime('%d/%m')})."

            mensajes.append({"icono": "🌧️", "texto": texto})
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
            "producto_upc": a.producto.codigo_upc,
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

    inicio_semana = hoy - timedelta(days=6)
    ventas_diarias = (
        Venta.objects.filter(fecha__date__gte=inicio_semana, fecha__date__lte=hoy)
        .annotate(dia=TruncDate("fecha"))
        .values("dia")
        .annotate(total=Sum(
            F("cantidad") * F("precio_unitario"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ))
        .order_by("dia")
    )
    mapa_ventas = {v["dia"]: float(v["total"]) for v in ventas_diarias}
    ventas_labels = []
    ventas_datos = []
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        ventas_labels.append(dia.strftime("%d/%m"))
        ventas_datos.append(mapa_ventas.get(dia, 0))

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
        "ventas_labels_json": json.dumps(ventas_labels),
        "ventas_datos_json": json.dumps(ventas_datos),
    }
    return render(request, "dashboard.html", contexto)


@rol_requerido("ADMINISTRADOR", "GERENTE", "COMPRADOR")
def buscar_global(request):
    """Buscador del sidebar: productos (por nombre o código UPC), lotes de
    inventario y alertas activas. Lotes y alertas quedan restringidos a los
    mismos roles que ya los ven en el menú (Gerente/Comprador/superuser)."""
    consulta = request.GET.get("q", "").strip()
    puede_ver_operativo = (
        request.user.rol in ("GERENTE", "COMPRADOR") or request.user.is_superuser_admin
    )

    productos = []
    alertas = []
    lotes = []

    if consulta:
        productos = list(
            Producto.objects.filter(
                Q(nombre__icontains=consulta) | Q(codigo_upc__icontains=consulta)
            ).order_by("nombre")[:15]
        )

        if puede_ver_operativo:
            alertas_qs = (
                Alerta.objects.filter(leida=False)
                .filter(Q(mensaje__icontains=consulta) | Q(producto__nombre__icontains=consulta))
                .select_related("producto")
                .order_by("tipo", "producto__nombre")[:15]
            )
            alertas = [
                {
                    "producto": a.producto.nombre,
                    "producto_upc": a.producto.codigo_upc,
                    "mensaje": a.mensaje,
                    "nivel": NIVEL_POR_TIPO.get(a.tipo, "secondary"),
                }
                for a in alertas_qs
            ]

            lotes = list(
                Inventario.objects.filter(lote__icontains=consulta)
                .select_related("producto")
                .order_by("fecha_vencimiento")[:15]
            )

    contexto = {
        "consulta": consulta,
        "productos": productos,
        "alertas": alertas,
        "lotes": lotes,
        "puede_ver_operativo": puede_ver_operativo,
        "hay_resultados": bool(productos or alertas or lotes),
    }
    return render(request, "buscar.html", contexto)


@rol_requerido("ADMINISTRADOR", "GERENTE", "COMPRADOR")
def buscar_productos_json(request):
    """Endpoint JSON para los buscadores de producto embebidos en
    formularios y filtros: UPC y descripción se buscan por separado (nunca
    en el mismo campo), para que siga teniendo sentido cuando el catálogo
    crezca más allá de un puñado de productos. También acepta ?id= para
    recuperar un producto puntual (usado para re-mostrar una selección al
    recargar la página o tras un error de validación)."""
    producto_id = request.GET.get("id", "").strip()
    upc = request.GET.get("upc", "").strip()
    q = request.GET.get("q", "").strip()

    productos = Producto.objects.filter(activo=True)
    if producto_id:
        productos = productos.filter(id_producto=producto_id)
    else:
        if upc:
            productos = productos.filter(codigo_upc__icontains=upc)
        if q:
            productos = productos.filter(nombre__icontains=q)

    productos = productos.order_by("nombre")[:20]
    data = [
        {"id": p.id_producto, "upc": p.codigo_upc, "nombre": p.nombre}
        for p in productos
    ]
    return JsonResponse(data, safe=False)


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
            "producto_upc": a.producto.codigo_upc,
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
            merma = form.save(commit=False)
            merma.costo_perdida = round(float(merma.producto.precio_compra) * merma.cantidad, 2)
            merma.save()
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
    info_productos = {}
    for p in predicciones:
        pid = str(p.producto.id_producto)
        if pid not in datos_grafica:
            datos_grafica[pid] = {"labels": [], "predicho": [], "inferior": [], "superior": []}
            info_productos[pid] = {"nombre": p.producto.nombre, "upc": p.producto.codigo_upc}
        datos_grafica[pid]["labels"].append(p.fecha_pronosticada.strftime("%d/%m"))
        datos_grafica[pid]["predicho"].append(float(p.valor_predicho))
        datos_grafica[pid]["inferior"].append(float(p.intervalo_inferior))
        datos_grafica[pid]["superior"].append(float(p.intervalo_superior))

    dias_lluvia = []
    for fecha, prob in pronostico_lluvia_real().items():
        if prob >= 0.4:
            dias_lluvia.append(fecha.strftime("%d/%m"))

    dias_desde_prediccion = (hoy - fecha_max).days if fecha_max else None
    validacion_cruzada = obtener_validacion_cruzada()

    return render(request, "listar_predicciones.html", {
        "predicciones": predicciones,
        "fecha_corrida": fecha_max,
        "modelo_al_dia": dias_desde_prediccion == 0,
        "datos_grafica_json": json.dumps(datos_grafica),
        "info_productos_json": json.dumps(info_productos),
        "validacion_json": json.dumps(validacion_cruzada) if validacion_cruzada else None,
        "mensajes_contexto": mensajes_contexto,
        "dias_lluvia_json": json.dumps(dias_lluvia),
    })


@rol_requerido("COMPRADOR")
def listar_recomendaciones(request):
    hoy = timezone.localdate()
    fecha_max = Prediccion.objects.order_by("-fecha_prediccion").values_list(
        "fecha_prediccion", flat=True
    ).first()

    if request.method == "POST" and fecha_max:
        for producto in Producto.objects.filter(activo=True):
            valor_raw = request.POST.get(f"ajuste_{producto.id_producto}")
            if valor_raw is None or valor_raw == "":
                continue
            try:
                cantidad_ajustada = float(valor_raw)
            except ValueError:
                continue

            prediccion_semana = Prediccion.objects.filter(
                producto=producto, fecha_prediccion=fecha_max
            ).aggregate(total=Sum("valor_predicho"))["total"] or 0
            stock_actual = Inventario.objects.filter(
                producto=producto, fecha_vencimiento__gte=hoy
            ).aggregate(total=Sum("cantidad"))["total"] or 0
            sugerido = max(0, round(prediccion_semana - stock_actual))

            if cantidad_ajustada == sugerido:
                continue  # sin ajuste real: no vale la pena guardar historial

            DecisionHistorial.objects.update_or_create(
                producto=producto, usuario=request.user, fecha_prediccion=fecha_max,
                defaults={
                    "cantidad_sugerida": sugerido,
                    "cantidad_ajustada": cantidad_ajustada,
                },
            )
        messages.success(request, "Ajustes guardados en el historial de decisiones.")
        return redirect("listar_recomendaciones")

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
            "producto_id": producto.id_producto,
            "producto": producto.nombre,
            "producto_upc": producto.codigo_upc,
            "prediccion_semana": round(prediccion_semana, 1),
            "stock_actual": stock_actual,
            "sugerido": sugerido,
        })

    return render(request, "listar_recomendaciones.html", {"recomendaciones": recomendaciones})


@rol_requerido("GERENTE", "COMPRADOR")
def historial_decisiones(request):
    """RF-09 / UC-05: comparativa sugerido-vs-ajustado y MAPE del modelo
    para cada decisión registrada. El Gerente ve el historial completo
    (no puede ajustar cantidades); el Comprador solo ve las decisiones
    que él mismo tomó."""
    es_gerente = request.user.rol == "GERENTE" or request.user.is_superuser_admin

    historial_qs = (
        DecisionHistorial.objects.select_related("producto", "usuario")
        .order_by("-fecha_decision")
    )
    if not es_gerente:
        historial_qs = historial_qs.filter(usuario=request.user)

    producto_id = request.GET.get("producto", "").strip()
    desde = request.GET.get("desde", "").strip()
    hasta = request.GET.get("hasta", "").strip()

    if producto_id:
        historial_qs = historial_qs.filter(producto_id=producto_id)
    if desde:
        historial_qs = historial_qs.filter(fecha_decision__date__gte=desde)
    if hasta:
        historial_qs = historial_qs.filter(fecha_decision__date__lte=hasta)

    filas = []
    for h in historial_qs[:200]:
        mape = Prediccion.objects.filter(
            producto=h.producto, fecha_prediccion=h.fecha_prediccion
        ).aggregate(promedio=Avg("precision_modelo"))["promedio"]

        filas.append({
            "fecha_decision": h.fecha_decision,
            "producto_upc": h.producto.codigo_upc,
            "producto": h.producto.nombre,
            "usuario": h.usuario.nombre if h.usuario else "—",
            "sugerido": h.cantidad_sugerida,
            "ajustado": h.cantidad_ajustada,
            "diferencia": h.cantidad_ajustada - h.cantidad_sugerida,
            "mape": round(mape, 2) if mape is not None else None,
        })

    contexto = {
        "filas": filas,
        "es_gerente": es_gerente,
        "producto_id": producto_id,
        "desde": desde,
        "hasta": hasta,
        "hay_resultados": bool(filas),
    }
    return render(request, "historial_decisiones.html", contexto)


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
            filas_por_proveedor.setdefault(proveedor, []).append(
                [producto.codigo_upc or "—", producto.nombre, sugerido]
            )

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
        data = [["UPC", "Producto", "Cantidad sugerida"]] + items
        tabla = Table(data, colWidths=[100, 230, 120])
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
def editar_usuario(request, usuario_id):
    """Gestión de usuarios (parte 2 de 2, junto con crear_usuario): corregir
    nombre/correo/rol y activar o desactivar el acceso de un usuario ya
    existente. No permite que un administrador se desactive a sí mismo -
    eso dejaría al sistema sin nadie que pueda revertirlo desde la interfaz."""
    usuario_editado = get_object_or_404(Usuario, id_usuario=usuario_id)
    es_uno_mismo = usuario_editado.pk == request.user.pk

    if request.method == "POST":
        form = EditarUsuarioForm(request.POST, instance=usuario_editado)
        if form.is_valid():
            if es_uno_mismo and not form.cleaned_data["is_active"]:
                form.add_error("is_active", "No puedes desactivar tu propia cuenta.")
            else:
                form.save()
                messages.success(request, "Usuario actualizado correctamente.")
                return redirect("listar_usuarios")
    else:
        form = EditarUsuarioForm(instance=usuario_editado)

    return render(request, "editar_usuario.html", {
        "form": form,
        "usuario_editado": usuario_editado,
        "es_uno_mismo": es_uno_mismo,
    })


@rol_requerido("ADMINISTRADOR")
def restablecer_password_usuario(request, usuario_id):
    """Como no hay flujo de recuperación por correo (el backend de email es
    de consola, ver settings.py), el Administrador es quien restablece la
    contraseña de cualquier usuario que la haya olvidado."""
    usuario_editado = get_object_or_404(Usuario, id_usuario=usuario_id)

    if request.method == "POST":
        form = RestablecerPasswordForm(request.POST, usuario=usuario_editado)
        if form.is_valid():
            usuario_editado.set_password(form.cleaned_data["password1"])
            usuario_editado.save()
            messages.success(
                request,
                f"Contraseña de {usuario_editado.nombre} restablecida. "
                "Comunícasela de forma segura.",
            )
            return redirect("listar_usuarios")
    else:
        form = RestablecerPasswordForm(usuario=usuario_editado)

    return render(request, "restablecer_password_usuario.html", {
        "form": form,
        "usuario_editado": usuario_editado,
    })


@rol_requerido("ADMINISTRADOR")
def listar_configuracion(request):
    editar_id = request.GET.get("editar")
    instancia = Configuracion.objects.filter(id_config=editar_id).first() if editar_id else None

    if request.method == "POST":
        id_config = request.POST.get("id_config")
        instancia_post = (
            Configuracion.objects.filter(id_config=id_config).first() if id_config else None
        )
        form = ConfiguracionForm(request.POST, instance=instancia_post)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Parámetro actualizado." if instancia_post else "Parámetro guardado.",
            )
            return redirect("listar_configuracion")
    else:
        form = ConfiguracionForm(instance=instancia)

    configuraciones = Configuracion.objects.all().order_by("clave")
    return render(
        request, "listar_configuracion.html",
        {"form": form, "configuraciones": configuraciones, "editando": instancia},
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
        .values_list("producto__nombre", "producto__codigo_upc", "precision_modelo")
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