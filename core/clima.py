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