"""
¿Quién acertó más? (UC-05, paso 4: "el sistema presenta los datos de
precisión (MAPE) y la efectividad de las decisiones registradas").
Agregado el 2026-10-14.

Cada DecisionHistorial guarda lo que sugirió el modelo y lo que pidió el
Comprador para una corrida del modelo. Cuando ya pasaron los 7 días que
cubría esa corrida, se puede saber qué hacía falta de verdad:

    necesidad real = venta real de esos 7 días - stock que había al decidir

(el mismo cálculo que hace el sugerido, pero con la venta que de verdad
ocurrió en lugar de la pronosticada). Gana quien quedó más cerca de esa
necesidad real. Si los dos quedan igual de cerca, es empate.

El stock al decidir se guarda desde el 2026-10-14
(DecisionHistorial.stock_al_decidir). Para decisiones anteriores se
deduce del propio sugerido: sugerido = predicción 7 días - stock, así que
stock = predicción 7 días - sugerido. Si el sugerido era 0, ese despeje
no sirve (el stock podía ser cualquier cantidad mayor) y la decisión
queda "sin datos suficientes".
"""

from datetime import timedelta

from django.db.models import Max, Min, Sum
from django.utils import timezone

from .models import Prediccion, Venta

TOLERANCIA_EMPATE = 0.005  # unidades o libras; evita "ganar" por redondeo


def _ventana_y_prediccion(decision):
    datos = Prediccion.objects.filter(
        producto_id=decision.producto_id, fecha_prediccion=decision.fecha_prediccion,
    ).aggregate(desde=Min("fecha_pronosticada"), hasta=Max("fecha_pronosticada"), total=Sum("valor_predicho"))
    return datos["desde"], datos["hasta"], datos["total"]


def _venta_real(producto_id, desde, hasta):
    total = 0.0
    for fecha, cantidad in Venta.objects.filter(
        producto_id=producto_id, fecha__date__gte=desde, fecha__date__lte=hasta,
    ).values_list("fecha", "cantidad"):
        if desde <= timezone.localtime(fecha).date() <= hasta:
            total += cantidad
    return total


def evaluar_decision(decision, hoy=None):
    """Devuelve un dict con el resultado. `estado` puede ser:
    "pendiente" (todavía no pasan los 7 días), "sin_datos" o "evaluada"."""
    hoy = hoy or timezone.localdate()
    desde, hasta, prediccion = _ventana_y_prediccion(decision)
    if desde is None:
        return {"estado": "sin_datos", "motivo": "No se encontró la corrida del modelo."}
    if hasta >= hoy:
        return {"estado": "pendiente", "evalua_el": hasta + timedelta(days=1)}

    stock = decision.stock_al_decidir
    if stock is None:
        if decision.cantidad_sugerida <= 0:
            return {"estado": "sin_datos", "motivo": "No se guardó el stock de ese día."}
        stock = max(0.0, prediccion - decision.cantidad_sugerida)

    venta_real = _venta_real(decision.producto_id, desde, hasta)
    necesidad = max(0.0, venta_real - stock)
    error_modelo = abs(decision.cantidad_sugerida - necesidad)
    error_comprador = abs(decision.cantidad_ajustada - necesidad)
    if abs(error_modelo - error_comprador) <= TOLERANCIA_EMPATE:
        ganador = "EMPATE"
    elif error_modelo < error_comprador:
        ganador = "MODELO"
    else:
        ganador = "COMPRADOR"
    return {
        "estado": "evaluada",
        "venta_real": venta_real,
        "necesidad": necesidad,
        "error_modelo": error_modelo,
        "error_comprador": error_comprador,
        "ganador": ganador,
    }


def resumen(evaluaciones):
    """Totales para la tarjeta de resumen. El error se expresa en % de la
    necesidad real, para poder promediar libras con unidades."""
    evaluadas = [e for e in evaluaciones if e["estado"] == "evaluada"]
    conteo = {"MODELO": 0, "COMPRADOR": 0, "EMPATE": 0}
    for e in evaluadas:
        conteo[e["ganador"]] += 1
    con_base = [e for e in evaluadas if e["necesidad"] > 0]

    def promedio(campo):
        if not con_base:
            return None
        return round(100 * sum(e[campo] / e["necesidad"] for e in con_base) / len(con_base), 1)

    return {
        "evaluadas": len(evaluadas),
        "pendientes": sum(1 for e in evaluaciones if e["estado"] == "pendiente"),
        "gana_modelo": conteo["MODELO"],
        "gana_comprador": conteo["COMPRADOR"],
        "empates": conteo["EMPATE"],
        "error_modelo_pct": promedio("error_modelo"),
        "error_comprador_pct": promedio("error_comprador"),
    }
