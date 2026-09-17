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

Corrección de diseño (reportada por Francisco al ver la pantalla en
producción): la primera versión de este cálculo usaba los DÍAS
ABSOLUTOS en exhibición para puntuar. El problema es que
Producto.vida_util_dias varía mucho entre frutas/verduras (5 a 15 días
en el catálogo) y la vista ya descarta los lotes cuya fecha_vencimiento
ya pasó - así que casi cualquier lote que sigue vivo en pantalla tiene
6 o más días en exhibición, sin importar si ese producto dura 5 días o
15. Resultado: casi todos los lotes quedaban con el mismo puntaje base
(el tope de la tabla), y como el clima es el mismo para toda la ciudad
en un día dado, casi todos "subían de nivel" el mismo día - el aviso de
proyección salía repetido en casi toda la tabla, sin lógica real.

La corrección: en vez de días absolutos, se usa el PORCENTAJE de la
vida útil propia del producto ya consumido
(dias_en_exhibicion / producto.vida_util_dias). Así, un tomate a 4 de
sus 5 días de vida (80%) y una manzana a 4 de sus 15 días (27%) ya no
quedan con el mismo puntaje solo por llevar los mismos días en el
mueble - el tomate, que de verdad está por vencerse, pesa más que la
manzana, que apenas empieza su vida útil. Esto también hace que la
proyección a los próximos días vuelva a tener sentido: cada lote sube
de nivel el día que le corresponde según SU propio porcentaje, no todos
el mismo día.
"""

# Porcentaje de la vida útil propia del producto (vida_util_dias) que ya
# lleva consumido el lote, en exhibición. Mientras más cerca está de
# agotar su propia vida útil, más puntos de riesgo suma - sin importar
# si esa vida útil son 5 días (ej. un tomate) o 15 (ej. una manzana).
# Tuplas (umbral_minimo, puntos), evaluadas de mayor a menor umbral.
PUNTOS_POR_PORCENTAJE_VIDA_UTIL = (
    (90, 45),  # 90% o más de su propia vida útil ya consumida
    (60, 30),  # 60-89%
    (30, 15),  # 30-59%
    (0, 0),    # menos de 30% (recién ingresado, relativo a su producto)
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
# llega a UMBRAL_MEDIO se considera BAJO). Elegidos para que calcen con
# los mismos cortes de 30/60/90% de la tabla de arriba: un lote que solo
# lleva puntos por porcentaje de vida útil (sin clima) queda exactamente
# en el nivel que corresponde a su propio porcentaje.
UMBRAL_ALTO = 60
UMBRAL_MEDIO = 30

# Orden de menor a mayor riesgo, para poder comparar "¿este nivel es peor
# que aquel?" - usado por la proyección de los próximos días (ver
# primer_dia_que_sube_de_nivel más abajo).
ORDEN_NIVELES = ("BAJO", "MEDIO", "ALTO")


def _puntos_por_umbral(valor, tabla_umbrales):
    """Recorre la tabla (ordenada de mayor a menor umbral) y devuelve los
    puntos del primer umbral que el valor alcanza o supera."""
    for umbral, puntos in tabla_umbrales:
        if valor >= umbral:
            return puntos
    return 0


def calcular_riesgo_lote(dias_en_exhibicion, vida_util_dias, temp_max=None, humedad_promedio=None):
    """
    Calcula el riesgo de descomposición de un lote de fruta/verdura.

    dias_en_exhibicion: días desde que el lote ingresó a inventario
        (obligatorio, siempre se puede calcular sin depender del clima).
    vida_util_dias: Producto.vida_util_dias del lote - cuántos días dura
        ESE producto en particular. Se usa para calcular qué porcentaje
        de su propia vida útil ya lleva consumido el lote, en vez de
        comparar días absolutos entre productos que duran distinto
        (ver la nota de corrección de diseño arriba del módulo).
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
        porcentaje_vida_util: qué porcentaje de su propia vida útil lleva
            consumido el lote (redondeado a entero), para poder explicar
            el número en pantalla.
        factores: cuántos puntos aportó cada variable, para mostrar el
            detalle si se necesita explicar el número.
    """
    if vida_util_dias:
        porcentaje_vida_util = (dias_en_exhibicion / vida_util_dias) * 100
    else:
        # No debería pasar (vida_util_dias es un campo obligatorio del
        # producto), pero si llegara en 0 se trata como "ya al límite" en
        # vez de dividir entre cero.
        porcentaje_vida_util = 100

    puntos_porcentaje = _puntos_por_umbral(porcentaje_vida_util, PUNTOS_POR_PORCENTAJE_VIDA_UTIL)
    puntos_temp = (
        _puntos_por_umbral(temp_max, PUNTOS_POR_TEMPERATURA)
        if temp_max is not None else 0
    )
    puntos_humedad = (
        _puntos_por_umbral(humedad_promedio, PUNTOS_POR_HUMEDAD)
        if humedad_promedio is not None else 0
    )

    puntaje = min(PUNTAJE_MAXIMO, puntos_porcentaje + puntos_temp + puntos_humedad)

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
        "porcentaje_vida_util": round(porcentaje_vida_util),
        "factores": {
            "porcentaje_vida_util": puntos_porcentaje,
            "temperatura": puntos_temp,
            "humedad": puntos_humedad,
        },
    }


def primer_dia_que_sube_de_nivel(nivel_hoy, dias_proyectados):
    """
    Recorre los próximos días (ya ordenados por fecha) buscando el primer
    día en el que el riesgo sube de nivel respecto a HOY - por ejemplo, un
    lote que hoy está en BAJO pero mañana pasa a MEDIO o ALTO por el calor
    pronosticado. Pensado para el aviso corto "Sube a ALTO el jueves" en
    vez de mostrar una tabla completa día por día (decisión de Francisco:
    algo simple de leer, no una tabla más ancha).

    nivel_hoy: "BAJO"/"MEDIO"/"ALTO", el nivel ya calculado para hoy.
    dias_proyectados: lista de dicts, cada uno con al menos "fecha" y
        "nivel" (el resultado de calcular_riesgo_lote para ese día),
        ya en orden cronológico.

    Devuelve el primer dict de dias_proyectados cuyo nivel es peor que
    nivel_hoy, o None si ninguno sube (se mantiene igual o mejora).
    Un lote que YA está en ALTO nunca puede "subir más", así que siempre
    devuelve None en ese caso.
    """
    rango_hoy = ORDEN_NIVELES.index(nivel_hoy)
    for dia in dias_proyectados:
        if ORDEN_NIVELES.index(dia["nivel"]) > rango_hoy:
            return dia
    return None
