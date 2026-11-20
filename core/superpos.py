"""
Conector con la base del sistema de punto de venta del supermercado
(superpos, MySQL 5.1) - 2026-11-16.

FAs NO trabaja sobre esa base: solo LEE (SELECT) las ventas diarias y la
ficha de los productos de Alimentos Frescos, y las copia a su propia base
como productos REALES. Todo lo que FAs agrega (lotes, mermas, órdenes,
predicciones) vive solo en FAs.

Tablas que se leen (ver claude/integracion-superpos.md en el proyecto):
- ventasdiarias: una fila por producto, sucursal y día (Cantidad,
  MontoTotal, Oferta). Es la fuente del historial.
- productos (+ proveedores): la ficha.
- departamentos / categorias: para elegir qué es Alimentos Frescos.
- detalleinventarios + inventarios (opcional): fechas de vencimiento de
  la mercadería recibida, para estimar la vida útil real.

La carga reutiliza la misma lógica de core/importar.py que la pantalla
Importar datos (precios por día, reimportar sin duplicar, FEFO, precio
vigente), para que el resultado sea idéntico venga de un Excel o de la
base. Aquí solo se arman las filas en el formato que esa lógica espera.

La conexión se configura con variables de entorno (.env), nunca en el
código: SUPERPOS_HOST, SUPERPOS_PUERTO, SUPERPOS_USUARIO, SUPERPOS_CLAVE,
SUPERPOS_BD y SUPERPOS_CHARSET. Lo recomendado es un usuario de MySQL con
permiso SOLO de lectura (GRANT SELECT).
"""

from datetime import timedelta

from decouple import config
from django.db import transaction
from django.utils import timezone

from . import importar as base
from .models import LIBRAS_POR_KILO

CATEGORIAS_FAS = ("FRUTAS", "VERDURAS", "LACTEOS", "CARNES", "PANADERIA")
# Nombre que entiende core/importar.py para cada categoría de FAs.
_NOMBRE_CATEGORIA = {
    "FRUTAS": "frutas", "VERDURAS": "verduras", "LACTEOS": "lacteos",
    "CARNES": "carnes", "PANADERIA": "panaderia",
}
LOTE_CONSULTA = 200  # códigos por consulta (IN (...)), para no armar SQL enormes
VIDA_UTIL_MIN, VIDA_UTIL_MAX = 1, 120
DIAS_HISTORIAL = 2 * 365 + 14  # días; fuera de ese rango se descarta la estimación


class ErrorConexion(Exception):
    pass


class FuenteSuperpos:
    """Lee la base superpos. Solo ejecuta SELECT."""

    def __init__(self, host=None, puerto=None, usuario=None, clave=None, bd=None, charset=None):
        self.parametros = dict(
            host=host or config("SUPERPOS_HOST", default="127.0.0.1"),
            port=int(puerto or config("SUPERPOS_PUERTO", default=3306)),
            user=usuario or config("SUPERPOS_USUARIO", default=""),
            password=clave if clave is not None else config("SUPERPOS_CLAVE", default=""),
            database=bd or config("SUPERPOS_BD", default="superpos"),
            # MySQL 5.1 no conoce utf8mb4: se usa utf8.
            charset=charset or config("SUPERPOS_CHARSET", default="utf8"),
        )
        self._conexion = None

    def _conectar(self):
        if self._conexion is None:
            try:
                import pymysql
                import pymysql.cursors
            except ImportError as error:
                raise ErrorConexion("Falta la librería PyMySQL: pip install -r requirements.txt") from error
            try:
                self._conexion = pymysql.connect(
                    cursorclass=pymysql.cursors.DictCursor, connect_timeout=15, read_timeout=300,
                    **self.parametros,
                )
            except pymysql.MySQLError as error:
                raise ErrorConexion(
                    f"No se pudo conectar a {self.parametros['host']}:{self.parametros['port']} "
                    f"(base {self.parametros['database']}, usuario {self.parametros['user'] or '(vacío)'}): {error}"
                ) from error
        return self._conexion

    def consultar(self, sql, parametros=()):
        with self._conectar().cursor() as cursor:
            cursor.execute(sql, parametros)
            return list(cursor.fetchall())

    def cerrar(self):
        if self._conexion is not None:
            self._conexion.close()
            self._conexion = None

    # -- Exploración (para armar el mapeo con el encargado de sistemas) --

    def version(self):
        return self.consultar("SELECT VERSION() AS version")[0]["version"]

    def sucursales_con_ventas(self):
        return self.consultar(
            "SELECT IdSucursales AS sucursal, COUNT(*) AS filas, MIN(Fecha) AS desde, MAX(Fecha) AS hasta "
            "FROM ventasdiarias GROUP BY IdSucursales ORDER BY IdSucursales"
        )

    def departamentos(self):
        return self.consultar(
            "SELECT d.Id AS id, d.Nombre AS nombre, COUNT(DISTINCT p.Upc) AS productos_activos "
            "FROM departamentos d LEFT JOIN productos p ON p.IdDepartamentos = d.Id AND p.Activo = 1 "
            "GROUP BY d.Id, d.Nombre ORDER BY d.Id"
        )

    def categorias(self, departamentos):
        if not departamentos:
            return []
        marcas = ", ".join(["%s"] * len(departamentos))
        return self.consultar(
            "SELECT c.Id AS id, c.Nombre AS nombre, c.IdDepartamentos AS departamento, COUNT(DISTINCT p.Upc) AS productos_activos "
            "FROM categorias c LEFT JOIN productos p ON p.IdCategorias = c.Id AND p.Activo = 1 "
            f"WHERE c.IdDepartamentos IN ({marcas}) GROUP BY c.Id, c.Nombre, c.IdDepartamentos ORDER BY c.IdDepartamentos, c.Id",
            list(departamentos),
        )

    # -- Lo que se importa --

    def productos(self, departamentos, categorias, codigos=None):
        """Productos activos de los departamentos/categorías del mapeo."""
        condiciones, parametros = [], []
        if departamentos:
            condiciones.append(f"p.IdDepartamentos IN ({', '.join(['%s'] * len(departamentos))})")
            parametros += list(departamentos)
        if categorias:
            condiciones.append(f"p.IdCategorias IN ({', '.join(['%s'] * len(categorias))})")
            parametros += list(categorias)
        if not condiciones:
            return []
        sql = (
            "SELECT p.Upc AS upc, p.Plu AS plu, p.IdSucursales AS sucursal, p.DescLarga AS desc_larga, "
            "p.DescCorta AS desc_corta, p.Precio AS precio, p.Costo AS costo, p.CostoPromedio AS costo_promedio, "
            "p.PesoVariable AS peso_variable, p.IdDepartamentos AS departamento, p.IdCategorias AS categoria, "
            "pr.Nombre AS proveedor "
            "FROM productos p LEFT JOIN proveedores pr ON pr.Id = p.IdProveedores "
            f"WHERE p.Activo = 1 AND ({' OR '.join(condiciones)})"
        )
        filas = self.consultar(sql, parametros)
        if codigos:
            buscados = {_limpiar(c) for c in codigos}
            filas = [f for f in filas if _limpiar(f["upc"]) in buscados or _limpiar(f["plu"]) in buscados]
        return filas

    def ventas(self, sucursal, codigos, desde, hasta):
        """Venta por código y día, ya sumada (puede haber varias filas por día)."""
        filas = []
        codigos = sorted({c for c in codigos if c})
        for i in range(0, len(codigos), LOTE_CONSULTA):
            lote = codigos[i:i + LOTE_CONSULTA]
            filas += self.consultar(
                "SELECT Upc AS upc, Fecha AS fecha, SUM(Cantidad) AS cantidad, SUM(MontoTotal) AS monto, "
                "MAX(Oferta = '1') AS oferta "  # Oferta es ENUM('0','1')
                "FROM ventasdiarias WHERE IdSucursales = %s AND Fecha BETWEEN %s AND %s "
                f"AND Upc IN ({', '.join(['%s'] * len(lote))}) GROUP BY Upc, Fecha",
                [sucursal, desde, hasta, *lote],
            )
        return filas

    def vida_util_estimada(self, codigos, desde):
        """Días promedio entre la recepción y el vencimiento, por código, según
        las recepciones con fecha de vencimiento. {} si no hay datos o si la
        tabla no se usa."""
        codigos = sorted({c for c in codigos if c})
        resultado = {}
        for i in range(0, len(codigos), LOTE_CONSULTA):
            lote = codigos[i:i + LOTE_CONSULTA]
            filas = self.consultar(
                "SELECT d.Upc AS upc, AVG(DATEDIFF(d.FechaVencimiento, i.Fecha)) AS dias, COUNT(*) AS recepciones "
                "FROM detalleinventarios d JOIN inventarios i ON i.idInventarios = d.IdInventarios "
                "WHERE d.FechaVencimiento IS NOT NULL AND d.FechaVencimiento > i.Fecha AND i.Fecha >= %s "
                f"AND d.Upc IN ({', '.join(['%s'] * len(lote))}) GROUP BY d.Upc",
                [desde, *lote],
            )
            for f in filas:
                if f["dias"] is not None:
                    resultado[_limpiar(f["upc"])] = (float(f["dias"]), int(f["recepciones"]))
        return resultado


def _limpiar(codigo):
    return str(codigo or "").strip()


def _texto_si_no(valor):
    """PesoVariable / Oferta vienen como ENUM('0','1')."""
    return str(valor).strip() in ("1", "True", "true")


def leer_mapeo(texto):
    """'5=FRUTAS,6=VERDURAS' -> {5: 'FRUTAS', 6: 'VERDURAS'}."""
    mapeo = {}
    for parte in (texto or "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "=" not in parte:
            raise ValueError(f"\"{parte}\" no tiene el formato id=CATEGORIA (ej. 5=FRUTAS).")
        clave, categoria = (x.strip() for x in parte.split("=", 1))
        categoria = base._normalizar(categoria).upper()
        if categoria not in CATEGORIAS_FAS:
            raise ValueError(f"Categoría \"{categoria}\" no válida; usa {', '.join(CATEGORIAS_FAS)}.")
        if not clave.isdigit():
            raise ValueError(f"\"{clave}\" no es un id numérico.")
        mapeo[int(clave)] = categoria
    return mapeo


def importar_desde_superpos(fuente, sucursal, departamentos, categorias=None, codigos=None,
                            desde=None, hasta=None, peso_en="lb", estimar_vida_util=True, simular=False):
    """Lee productos y ventas de superpos y los carga en FAs (productos
    REALES). departamentos / categorias: {id: 'FRUTAS', ...}; si un producto
    calza en ambos, manda la categoría. simular=True: hace todo y deshace
    al final (para revisar qué pasaría sin guardar nada)."""
    categorias = categorias or {}
    res = base.Resultado()
    hoy = timezone.localdate()
    hasta = hasta or hoy - timedelta(days=1)
    # 2 años + 2 semanas: Prophet necesita 2 años completos para la temporada
    # del año, sin contar la semana que se aparta para medir el error.
    desde = desde or hasta - timedelta(days=DIAS_HISTORIAL)
    factor = LIBRAS_POR_KILO if peso_en == "kg" else 1.0

    filas_productos = fuente.productos(list(departamentos), list(categorias), codigos)
    if not filas_productos:
        res.error("No se encontró ningún producto activo en los departamentos/categorías indicados.")
        return res

    # Un mismo código puede repetirse por sucursal: se prefiere la fila de la
    # sucursal que se importa.
    por_codigo = {}
    for f in filas_productos:
        codigo = _limpiar(f["upc"]) or _limpiar(f["plu"])
        if not codigo:
            continue
        actual = por_codigo.get(codigo)
        if actual is None or (f.get("sucursal") == sucursal and actual.get("sucursal") != sucursal):
            por_codigo[codigo] = f

    vida = {}
    if estimar_vida_util:
        try:
            vida = fuente.vida_util_estimada(list(por_codigo), hasta - timedelta(days=365))
        except Exception as error:  # la tabla puede no existir o no usarse
            res.avisos.append(f"No se pudo estimar la vida útil desde las recepciones ({type(error).__name__}); se usan los días por categoría.")

    # Ficha en el formato de columnas que entiende core/importar.py.
    cols_prod = {"codigo": 0, "nombre": 1, "categoria": 2, "unidad": 3, "precio_venta": 4,
                 "precio_compra": 5, "proveedor": 6, "vida_util": 7}
    filas_ficha = []
    for n, (codigo, f) in enumerate(sorted(por_codigo.items()), start=1):
        categoria = categorias.get(f.get("categoria")) or departamentos.get(f.get("departamento"))
        es_peso = _texto_si_no(f.get("peso_variable"))
        precio = float(f["precio"]) if f.get("precio") is not None else None
        costo = f.get("costo_promedio") or f.get("costo")
        costo = float(costo) if costo else None
        if es_peso and factor != 1.0:  # precio por kilo -> por libra
            precio = precio / factor if precio else precio
            costo = costo / factor if costo else costo
        dias_vida = None
        if codigo in vida and VIDA_UTIL_MIN <= vida[codigo][0] <= VIDA_UTIL_MAX:
            dias_vida = round(vida[codigo][0])
        filas_ficha.append((n, [
            codigo, _limpiar(f.get("desc_larga")) or _limpiar(f.get("desc_corta")),
            _NOMBRE_CATEGORIA.get(categoria, ""), "libra" if es_peso else "unidad",
            precio, costo, _limpiar(f.get("proveedor")), dias_vida,
        ]))

    filas_ventas = []
    cols_ventas = {"codigo": 0, "fecha": 1, "cantidad": 2, "precio_venta": 3, "promocion": 4}
    for n, v in enumerate(fuente.ventas(sucursal, list(por_codigo), desde, hasta), start=1):
        codigo = _limpiar(v["upc"])
        if codigo not in por_codigo:
            continue
        cantidad = float(v["cantidad"] or 0)
        monto = float(v["monto"] or 0)
        precio = monto / cantidad if cantidad > 0 and monto > 0 else None
        if _texto_si_no(por_codigo[codigo].get("peso_variable")) and factor != 1.0:
            cantidad *= factor
            precio = precio / factor if precio else precio
        if cantidad < 0:  # devoluciones netas de un día: no es venta
            res.avisos.append(f"{codigo}: el {v['fecha']:%d/%m/%Y} la venta neta fue negativa ({cantidad:.2f}); se tomó como 0.")
            cantidad = 0.0
        filas_ventas.append((n, [codigo, v["fecha"], cantidad, precio, "1" if v["oferta"] else "0"]))

    with transaction.atomic():
        base._importar_productos("superpos.productos", filas_ficha, cols_prod, res)
        if filas_ventas:
            base._importar_ventas("superpos.ventasdiarias", filas_ventas, cols_ventas, res)
        else:
            res.avisos.append(f"No hay ventas en la sucursal {sucursal} entre el {desde:%d/%m/%Y} y el {hasta:%d/%m/%Y}.")
        sin_ventas = [f[1][1] for f in filas_ficha if f[1][1] not in res.ventas]
        if filas_ventas and sin_ventas:
            res.avisos.append(
                f"{len(sin_ventas)} producto(s) sin ventas en el periodo: " + ", ".join(sin_ventas[:8])
                + ("…" if len(sin_ventas) > 8 else "")
            )
        if simular:
            transaction.set_rollback(True)
    return res
