"""
Riesgo climático de descomposición (cambio estructural aditivo, decidido
con Francisco - ver bitácora del proyecto).

Frutas y Verduras, a diferencia de Lácteos/Carnes/Panadería, normalmente
no traen impresa una fecha de caducidad exacta: se echan a perder por
manipulación, exhibición prolongada y clima, no por una fecha fija.
Este módulo NO reemplaza ni toca fecha_vencimiento, Alerta, ni ninguna
lógica de vencimiento ya existente y probada - es un estimado adicional,
mostrado en una pantalla aparte, que solo aplica a lotes cuyo producto
sea de categoría Frutas o Verduras.

La justificación (documentada en los requerimientos del proyecto): el
calor acelera la maduración/descomposición de frutas y verduras, y la
exhibición prolongada agrava ese efecto - casi la mitad de las pérdidas
de verduras a nivel mundial ocurren porque se pasan de su punto antes de
venderse (WWF, 2024). No hay una fórmula científica exacta que se pueda
implementar aquí, así que el cálculo es intencionalmente simple: cada
variable suma "puntos de riesgo" en una tabla de umbrales, y el total se
traduce a un semáforo (bajo/medio/alto) fácil de leer para el Comprador
o el Gerente.
"""

# Días desde que el lote ingresó a inventario (Inventario.fecha_ingreso).
# Mientras más tiempo lleva en exhibición, más puntos de riesgo suma.
# Tuplas (umbral_minimo, puntos), evaluadas de mayor a menor umbral.
PUNTOS_POR_DIAS_EXHIBICION = (
    (6, 45),  # 6 días o más en exhibición
    (4, 30),  # 4-5 días
    (2, 15),  # 2-3 días
    (0, 0),   # 0-1 día (recién ingresado)
)

# Temperatura máxima pronosticada para hoy, en °C.
PUNTOS_POR_TEMPERATURA = (
    (30, 25),
    (25, 15),
    (20, 5),
    (0, 0),
)

# Humedad relativa promedio pronosticada para hoy, en %.
PUNTOS_POR_HUMEDAD = (
    (80, 20),
    (60, 10),
    (0, 0),
)

PUNTAJE_MAXIMO = 100

# A partir de qué puntaje se considera riesgo MEDIO o ALTO (lo que no
# llega a UMBRAL_MEDIO se considera BAJO).
UMBRAL_ALTO = 61
UMBRAL_MEDIO = 31


def _puntos_por_umbral(valor, tabla_umbrales):
    """Recorre la tabla (ordenada de mayor a menor umbral) y devuelve los
    puntos del primer umbral que el valor alcanza o supera."""
    for umbral, puntos in tabla_umbrales:
        if valor >= umbral:
            return puntos
    return 0


def calcular_riesgo_lote(dias_en_exhibicion, temp_max=None, humedad_promedio=None):
    """
    Calcula el riesgo de descomposición de un lote de fruta/verdura.

    dias_en_exhibicion: días desde que el lote ingresó a inventario
        (obligatorio, siempre se puede calcular sin depender del clima).
    temp_max: temperatura máxima pronosticada para hoy, en °C. Opcional -
        si no se pudo consultar el clima (sin API key, o la API no
        respondió), se pasa None y ese factor simplemente no suma puntos.
    humedad_promedio: humedad relativa promedio pronosticada para hoy, en
        %. Mismo criterio que temp_max: opcional, None si no hay dato.

    Devuelve un dict:
        puntaje: 0-100.
        nivel: "ALTO", "MEDIO" o "BAJO".
        badge: clase de color de Bootstrap para el semáforo (danger/
            warning/success), lista para usar en la plantilla.
        factores: cuántos puntos aportó cada variable, para mostrar el
            detalle si se necesita explicar el número.
    """
    puntos_dias = _puntos_por_umbral(dias_en_exhibicion, PUNTOS_POR_DIAS_EXHIBICION)
    puntos_temp = (
        _puntos_por_umbral(temp_max, PUNTOS_POR_TEMPERATURA)
        if temp_max is not None else 0
    )
    puntos_humedad = (
        _puntos_por_umbral(humedad_promedio, PUNTOS_POR_HUMEDAD)
        if humedad_promedio is not None else 0
    )

    puntaje = min(PUNTAJE_MAXIMO, puntos_dias + puntos_temp + puntos_humedad)

    if puntaje >= UMBRAL_ALTO:
        nivel, badge = "ALTO", "danger"
    elif puntaje >= UMBRAL_MEDIO:
        nivel, badge = "MEDIO", "warning"
    else:
        nivel, badge = "BAJO", "success"

    return {
        "puntaje": puntaje,
        "nivel": nivel,
        "badge": badge,
        "factores": {
            "dias_en_exhibicion": puntos_dias,
            "temperatura": puntos_temp,
            "humedad": puntos_humedad,
        },
    }
