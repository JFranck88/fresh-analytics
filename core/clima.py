"""
Integración de clima para Prophet (RF-08).

El historial de ventas es sintético (2 años generados por nosotros), así
que el "clima histórico" también debe serlo, de forma reproducible: la
misma fecha siempre produce el mismo valor, tanto al generar los datos
como al entrenar el modelo, sin necesidad de guardar nada en la base de
datos. El pronóstico de los próximos días, en cambio, sí usa la API real
de OpenWeatherMap, porque de eso no hay forma de "inventar" datos - es
información real que sí necesitamos consultar.
"""

import datetime as dt
import random
from collections import defaultdict

import requests
from decouple import config


def lluvia_sintetica(fecha):
    """Devuelve 1 (llovió) o 0 (no llovió) para una fecha dada, de forma
    determinística - misma fecha, mismo resultado siempre. Usa mayor
    probabilidad de lluvia en la época lluviosa de Guatemala (mayo-oct)."""
    semilla = int(fecha.strftime("%Y%m%d"))
    generador = random.Random(semilla)
    prob_lluvia = 0.55 if fecha.month in (5, 6, 7, 8, 9, 10) else 0.15
    return 1 if generador.random() < prob_lluvia else 0


def pronostico_lluvia_real():
    """Consulta el pronóstico real de OpenWeatherMap (Ciudad de Guatemala)
    y devuelve {fecha: probabilidad_precipitacion}. El plan gratuito solo
    cubre ~5 días hacia adelante; los días fuera de ese rango simplemente
    no aparecen en el diccionario devuelto."""
    api_key = config("OPENWEATHER_API_KEY", default=None)
    if not api_key:
        return {}

    url = (
        "https://api.openweathermap.org/data/2.5/forecast"
        f"?q=Guatemala City,GT&appid={api_key}&units=metric"
    )
    try:
        respuesta = requests.get(url, timeout=10)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except requests.RequestException:
        return {}

    prob_por_dia = defaultdict(list)
    for item in datos.get("list", []):
        fecha = dt.datetime.fromtimestamp(item["dt"]).date()
        prob_por_dia[fecha].append(item.get("pop", 0))

    return {fecha: max(valores) for fecha, valores in prob_por_dia.items()}


def pronostico_temperatura_humedad_por_dia():
    """Consulta el mismo pronóstico de OpenWeatherMap (Ciudad de Guatemala)
    que pronostico_lluvia_real(), pero para alimentar el riesgo climático
    de descomposición (ver core/riesgo_descomposicion.py): agrupa los
    bloques de 3 horas por fecha y devuelve, para cada día disponible,
    la temperatura MÁXIMA (el peor caso para una fruta o verdura expuesta)
    y la humedad relativa PROMEDIO. Se hace como una consulta aparte, en
    vez de reutilizar la de pronostico_lluvia_real(), a propósito: ese
    módulo/función ya está probado y en uso (RF-08) y el cambio de riesgo
    climático se pidió como algo aditivo que no debía tocar nada
    existente.

    Devuelve {fecha: {"temp_max": float, "humedad_promedio": float}}.
    El plan gratuito de OpenWeatherMap solo cubre ~5 días hacia adelante
    (incluyendo hoy); los días fuera de ese rango simplemente no aparecen
    en el diccionario. Si no hay API key o la consulta falla, devuelve
    {} en vez de reventar - el cálculo de riesgo simplemente no suma
    puntos por clima cuando no hay dato para un día."""
    api_key = config("OPENWEATHER_API_KEY", default=None)
    if not api_key:
        return {}

    url = (
        "https://api.openweathermap.org/data/2.5/forecast"
        f"?q=Guatemala City,GT&appid={api_key}&units=metric"
    )
    try:
        respuesta = requests.get(url, timeout=10)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except requests.RequestException:
        return {}

    temperaturas_por_dia = defaultdict(list)
    humedades_por_dia = defaultdict(list)
    for item in datos.get("list", []):
        fecha = dt.datetime.fromtimestamp(item["dt"]).date()
        principal = item.get("main", {})
        if "temp" in principal:
            temperaturas_por_dia[fecha].append(principal["temp"])
        if "humidity" in principal:
            humedades_por_dia[fecha].append(principal["humidity"])

    fechas = set(temperaturas_por_dia) | set(humedades_por_dia)
    return {
        fecha: {
            "temp_max": max(temperaturas_por_dia[fecha]) if temperaturas_por_dia.get(fecha) else None,
            "humedad_promedio": (
                sum(humedades_por_dia[fecha]) / len(humedades_por_dia[fecha])
                if humedades_por_dia.get(fecha) else None
            ),
        }
        for fecha in fechas
    }


def pronostico_temperatura_humedad_hoy():
    """Atajo sobre pronostico_temperatura_humedad_por_dia() para el caso
    más simple: solo el clima de HOY, como (temp_max, humedad_promedio).
    Devuelve (None, None) si no hay dato para hoy (mismo criterio que el
    resto de funciones de este módulo)."""
    datos_hoy = pronostico_temperatura_humedad_por_dia().get(dt.date.today(), {})
    return datos_hoy.get("temp_max"), datos_hoy.get("humedad_promedio")