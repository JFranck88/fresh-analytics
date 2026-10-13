"""
Inventario vivo de Fresh Analytics (2026-10-13).

Antes, el "stock" era la suma de todos los lotes que habían entrado y no
habían vencido: ni las ventas ni las mermas lo descontaban nunca. Además,
el caso de uso UC-02 de la tesis dice que al registrar una merma "el
inventario queda ajustado", y eso no pasaba. Con esos números, el
sugerido de compra salía en 0 casi siempre y sobraban alertas de
EXCEDENTE.

Ahora Inventario.cantidad es el SALDO del lote (lo que queda en bodega o
en exhibición) e Inventario.cantidad_inicial es lo que se recibió. Las
salidas (ventas y mermas) se descuentan con la regla FEFO ("first
expired, first out"): sale primero el lote que vence antes, como se
rota el producto fresco en un supermercado real.

La reposición (los lotes que entran) es simulada, porque FAs no tiene
todavía un módulo de recepción de mercadería. La política es de revisión
diaria con punto de reorden:
  - demanda de referencia = promedio de venta REAL de los últimos 7 días
    (no se mira el futuro);
  - objetivo = demanda x días de cobertura (4, o los días que dura el
    lote si son menos) x colchón de seguridad (1.25);
  - si el stock vigente baja del 80% del objetivo, entra un lote que
    completa el objetivo (con ±10% de variación, como un proveedor real).

Este módulo no importa los modelos directamente: recibe las clases. Así
lo pueden usar tanto el código normal como la migración 0012 (que
trabaja con modelos históricos).
"""

import random
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

COBERTURA_DIAS = 4
COLCHON_SEGURIDAD = 1.25
PUNTO_REORDEN = 0.8
DIAS_DEMANDA_REFERENCIA = 7
VARIACION_PROVEEDOR = (0.9, 1.1)
# Lotes vencidos con saldo que todavía se pueden dar de baja como merma
# por VENCIMIENTO (el producto se retira unos días después de vencer).
DIAS_RETIRO_VENCIDOS = 30
LOTE_BD = 1000


def redondear(unidad_medida, valor):
    """Misma regla que Producto.redondear_cantidad (aquí sin depender del
    modelo, para que funcione también dentro de una migración)."""
    if unidad_medida == "PESO":
        return round(valor, 2)
    return round(valor)


def _vigente(lote, fecha):
    return lote.cantidad > 0 and lote.fecha_ingreso <= fecha <= lote.fecha_vencimiento


class SimuladorInventario:
    """Lleva los lotes en memoria, descuenta salidas FEFO, repone y al final
    guarda todo en la base con pocas consultas."""

    def __init__(self, Inventario, rng=None, Merma=None):
        self.Inventario = Inventario
        self.Merma = Merma
        self.rng = rng or random
        self.lotes = defaultdict(list)
        self.nuevos = []
        self.modificados = {}
        self.mermas_nuevas = []

    def cargar(self, producto_ids, desde):
        """Trae de la base los lotes con saldo que siguen vigentes en
        `desde`, más los vencidos recientes (para mermas por VENCIMIENTO)."""
        limite = desde - timedelta(days=DIAS_RETIRO_VENCIDOS)
        qs = self.Inventario.objects.filter(
            producto_id__in=list(producto_ids), cantidad__gt=0, fecha_vencimiento__gte=limite,
        ).order_by("fecha_vencimiento", "fecha_ingreso")
        for lote in qs:
            self.lotes[lote.producto_id].append(lote)

    def stock(self, producto_id, fecha):
        return sum(l.cantidad for l in self.lotes[producto_id] if _vigente(l, fecha))

    def _marcar(self, lote):
        if lote.pk is not None:
            self.modificados[lote.pk] = lote

    def consumir(self, producto, cantidad, fecha, vencidos_primero=False):
        """Descuenta `cantidad` de los lotes del producto. Devuelve cuánto se
        pudo descontar (nunca deja saldos negativos)."""
        pendiente = float(cantidad)
        lotes = self.lotes[producto.id_producto]
        orden = []
        if vencidos_primero:
            orden += sorted(
                (l for l in lotes if l.cantidad > 0 and l.fecha_vencimiento < fecha),
                key=lambda l: (l.fecha_vencimiento, l.fecha_ingreso),
            )
        orden += sorted(
            (l for l in lotes if _vigente(l, fecha)),
            key=lambda l: (l.fecha_vencimiento, l.fecha_ingreso),
        )
        consumido = 0.0
        for lote in orden:
            if pendiente <= 1e-9:
                break
            toma = min(lote.cantidad, pendiente)
            lote.cantidad = round(lote.cantidad - toma, 2)
            pendiente = round(pendiente - toma, 6)
            consumido += toma
            self._marcar(lote)
        return round(consumido, 2)

    def reponer(self, producto, fecha, demanda_diaria):
        """Política de revisión diaria con punto de reorden (ver arriba).
        Devuelve el lote creado, o None si no hacía falta pedir."""
        if demanda_diaria <= 0:
            return None
        # Nunca más días de los que dura el producto: pedir de más en algo que
        # vence pronto solo termina en merma.
        cobertura = min(COBERTURA_DIAS, max(1, producto.vida_util_dias))
        objetivo = demanda_diaria * cobertura * COLCHON_SEGURIDAD
        stock = self.stock(producto.id_producto, fecha)
        if stock >= objetivo * PUNTO_REORDEN:
            return None
        cantidad = redondear(
            producto.unidad_medida, (objetivo - stock) * self.rng.uniform(*VARIACION_PROVEEDOR)
        )
        if cantidad <= 0:
            return None
        lote = self.Inventario(
            producto_id=producto.id_producto, fecha_ingreso=fecha,
            fecha_vencimiento=fecha + timedelta(days=producto.vida_util_dias),
            cantidad=cantidad, cantidad_inicial=cantidad,
            lote=f"L-{fecha.strftime('%Y%m%d')}-{producto.id_producto}",
        )
        self.lotes[producto.id_producto].append(lote)
        self.nuevos.append(lote)
        return lote

    def podar(self, fecha):
        """Saca de memoria los lotes que ya no pueden tener movimiento (sin
        saldo, o vencidos hace más de DIAS_RETIRO_VENCIDOS días) para que
        simular dos años de historia no se vuelva lento. Siguen en
        `nuevos`/`modificados`, así que se guardan igual."""
        limite = fecha - timedelta(days=DIAS_RETIRO_VENCIDOS)
        for pid, lotes in self.lotes.items():
            self.lotes[pid] = [l for l in lotes if l.cantidad > 0 and l.fecha_vencimiento >= limite]

    def dar_de_baja_vencidos(self, producto, fecha, vencidos_desde=None):
        """Lo que venció sin venderse sale del inventario como merma por
        VENCIMIENTO, igual que en el súper cuando se retira el producto
        vencido del anaquel. Se registra al día siguiente de vencer, con
        el costo de lo que se perdió, y marcada como automática.
        `vencidos_desde` limita a los lotes que vencieron desde esa fecha:
        el job nocturno solo da de baja lo del periodo que simula, para no
        llenar de golpe el historial con sobrantes viejos de antes de que
        existiera esta regla."""
        for lote in self.lotes[producto.id_producto]:
            if vencidos_desde and lote.fecha_vencimiento < vencidos_desde:
                continue
            if lote.cantidad > 0 and lote.fecha_vencimiento < fecha:
                cantidad = lote.cantidad
                self.mermas_nuevas.append(self.Merma(
                    producto_id=producto.id_producto, fecha=lote.fecha_vencimiento + timedelta(days=1),
                    cantidad=cantidad, motivo="VENCIMIENTO", automatica=True,
                    costo_perdida=round(cantidad * float(producto.precio_compra), 2),
                ))
                lote.cantidad = 0
                self._marcar(lote)

    def guardar(self):
        if self.mermas_nuevas:
            self.Merma.objects.bulk_create(self.mermas_nuevas, batch_size=LOTE_BD)
            self.mermas_nuevas = []
        if self.nuevos:
            self.Inventario.objects.bulk_create(self.nuevos, batch_size=LOTE_BD)
        if self.modificados:
            self.Inventario.objects.bulk_update(
                list(self.modificados.values()), ["cantidad"], batch_size=LOTE_BD
            )
        self.nuevos, self.modificados = [], {}


def _ventas_por_dia(Venta, producto_ids, desde, hasta):
    """{(producto_id, fecha_local): cantidad vendida ese día}."""
    totales = defaultdict(float)
    qs = Venta.objects.filter(
        producto_id__in=list(producto_ids),
        fecha__date__gte=desde - timedelta(days=1), fecha__date__lte=hasta + timedelta(days=1),
    ).values_list("producto_id", "fecha", "cantidad")
    for pid, fecha, cantidad in qs.iterator(chunk_size=LOTE_BD):
        dia = timezone.localtime(fecha).date()
        if desde <= dia <= hasta:
            totales[(pid, dia)] += cantidad
    return totales


def _mermas_por_dia(Merma, producto_ids, desde, hasta, excluir_automaticas=False):
    """{(producto_id, fecha): [(cantidad, motivo), ...]}. Las mermas
    automáticas por vencimiento no se vuelven a descontar: cuando se
    crearon, el lote ya quedó en 0."""
    mermas = defaultdict(list)
    qs = Merma.objects.filter(
        producto_id__in=list(producto_ids), fecha__gte=desde, fecha__lte=hasta,
    )
    if excluir_automaticas:
        qs = qs.filter(automatica=False)
    qs = qs.order_by("id_merma").values_list("producto_id", "fecha", "cantidad", "motivo")
    for pid, fecha, cantidad, motivo in qs.iterator(chunk_size=LOTE_BD):
        mermas[(pid, fecha)].append((cantidad, motivo))
    return mermas


def demanda_de_referencia(ventas, producto_id, fecha, inicio=None):
    """Promedio de venta de los últimos DIAS_DEMANDA_REFERENCIA días
    anteriores a `fecha` (los días sin venta cuentan como 0). Si se pasa
    `inicio`, solo cuenta días desde ahí (al arrancar el historial no hay
    7 días previos). Devuelve 0 si no hay ningún día que contar."""
    dias = [fecha - timedelta(days=i) for i in range(1, DIAS_DEMANDA_REFERENCIA + 1)]
    if inicio is not None:
        dias = [d for d in dias if d >= inicio]
    if not dias:
        return 0
    return sum(ventas.get((producto_id, d), 0) for d in dias) / len(dias)


def simular_periodo(productos, desde, hasta, Venta, Merma, Inventario, reponer_hasta=None, rng=None,
                    inicio_historial=None, dar_de_baja_vencidos=False):
    """Recorre los días `desde`..`hasta`: cada mañana da de baja lo vencido
    (si `dar_de_baja_vencidos`) y repone; durante el día descuenta las
    ventas y las mermas registradas. Si se pasa `reponer_hasta`
    (normalmente hoy), hace también esas mañanas sin descontar ventas,
    que todavía no ocurren. Guarda al final."""
    productos = list(productos)
    ids = [p.id_producto for p in productos]
    sim = SimuladorInventario(Inventario, rng=rng, Merma=Merma)
    sim.cargar(ids, desde)
    ventas = _ventas_por_dia(Venta, ids, desde - timedelta(days=DIAS_DEMANDA_REFERENCIA), hasta)
    mermas = _mermas_por_dia(Merma, ids, desde, hasta, excluir_automaticas=dar_de_baja_vencidos)
    vencidos_desde = None if inicio_historial else desde - timedelta(days=1)

    fecha = desde
    while fecha <= hasta:
        for p in productos:
            if dar_de_baja_vencidos:
                sim.dar_de_baja_vencidos(p, fecha, vencidos_desde)
            demanda = demanda_de_referencia(ventas, p.id_producto, fecha, inicio_historial)
            if demanda <= 0:
                # Arranque del historial: todavía no hay 7 días previos.
                demanda = ventas.get((p.id_producto, fecha), 0)
            sim.reponer(p, fecha, demanda)
            vendido = ventas.get((p.id_producto, fecha), 0)
            if vendido:
                sim.consumir(p, vendido, fecha)
            for cantidad, motivo in mermas.get((p.id_producto, fecha), []):
                sim.consumir(p, cantidad, fecha, vencidos_primero=(motivo == "VENCIMIENTO"))
        if (fecha - desde).days % 30 == 29:
            sim.podar(fecha)
        fecha += timedelta(days=1)

    if reponer_hasta:
        while fecha <= reponer_hasta:
            for p in productos:
                if dar_de_baja_vencidos:
                    sim.dar_de_baja_vencidos(p, fecha, vencidos_desde)
                sim.reponer(p, fecha, demanda_de_referencia(ventas, p.id_producto, fecha))
            fecha += timedelta(days=1)
    sim.guardar()
    return sim


def reconstruir_inventario(Producto, Venta, Merma, Inventario, hoy=None, semilla=2026,
                           dar_de_baja_vencidos=False):
    """Borra los lotes y los vuelve a construir desde la primera venta,
    simulando la reposición y descontando cada venta y merma registrada.
    Se usa una semilla fija para que el resultado sea reproducible.
    Con `dar_de_baja_vencidos`, primero borra las mermas automáticas (se
    vuelven a generar en la simulación). La migración 0012 lo llama sin
    esa opción, porque su modelo histórico de Merma no tiene ese campo."""
    hoy = hoy or timezone.localdate()
    Inventario.objects.all().delete()
    if dar_de_baja_vencidos:
        Merma.objects.filter(automatica=True).delete()
    primera = Venta.objects.order_by("fecha").values_list("fecha", flat=True).first()
    if primera is None:
        return None
    desde = timezone.localtime(primera).date()
    ultima = timezone.localtime(Venta.objects.order_by("-fecha").values_list("fecha", flat=True).first()).date()
    hasta = min(ultima, hoy)
    productos = Producto.objects.filter(id_producto__in=Venta.objects.values("producto_id"))
    return simular_periodo(
        productos, desde, hasta, Venta, Merma, Inventario,
        reponer_hasta=hoy, rng=random.Random(semilla), inicio_historial=desde,
        dar_de_baja_vencidos=dar_de_baja_vencidos,
    )


def descontar_merma(merma, Inventario=None):
    """UC-02: al registrar una merma, el inventario queda ajustado. Devuelve
    la cantidad que efectivamente se descontó; puede ser menor si el
    sistema tenía registrado menos stock del que se dio de baja."""
    if Inventario is None:
        from .models import Inventario
    sim = SimuladorInventario(Inventario)
    sim.cargar([merma.producto_id], merma.fecha)
    descontado = sim.consumir(
        merma.producto, merma.cantidad, merma.fecha,
        vencidos_primero=(merma.motivo == "VENCIMIENTO"),
    )
    sim.guardar()
    return descontado
