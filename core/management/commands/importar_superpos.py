"""
Carga productos y ventas de Alimentos Frescos desde la base del punto de
venta del supermercado (superpos). Ver core/superpos.py.

Primero, explorar (no guarda nada) para armar el mapeo con su encargado
de sistemas:

    python manage.py importar_superpos --explorar
    python manage.py importar_superpos --explorar --departamentos "5=FRUTAS,7=LACTEOS"

Luego, una prueba que hace todo y deshace al final:

    python manage.py importar_superpos --sucursal 1 --departamentos "5=FRUTAS,7=LACTEOS" --simular

Y la carga real (por defecto, los últimos 2 años y 2 semanas hasta ayer):

    python manage.py importar_superpos --sucursal 1 --departamentos "5=FRUTAS,7=LACTEOS"

Para la corrida de cada noche basta con los últimos días (reimportar no
duplica: los días ya cargados se reemplazan):

    python manage.py importar_superpos --sucursal 1 --departamentos "..." --ultimos-dias 7
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.superpos import ErrorConexion, FuenteSuperpos, importar_desde_superpos, leer_mapeo


def _fecha(texto):
    try:
        return date.fromisoformat(texto)
    except ValueError as error:
        raise CommandError(f"Fecha \"{texto}\" no válida; usa AAAA-MM-DD.") from error


class Command(BaseCommand):
    help = "Lee ventas diarias y productos de la base superpos del supermercado (solo lectura) y los carga en FAs."

    def add_arguments(self, parser):
        parser.add_argument("--explorar", action="store_true",
                            help="Muestra sucursales, departamentos y categorías de superpos. No guarda nada.")
        parser.add_argument("--sucursal", type=int, help="IdSucursales de la tienda en ventasdiarias.")
        parser.add_argument("--departamentos", default="",
                            help="Departamentos a importar y su categoría en FAs: \"5=FRUTAS,7=LACTEOS\".")
        parser.add_argument("--categorias", default="",
                            help="Categorías de superpos a importar (mandan sobre el departamento): \"31=VERDURAS\".")
        parser.add_argument("--productos", default="",
                            help="Solo estos códigos (UPC o PLU), separados por coma: la lista del piloto.")
        parser.add_argument("--desde", help="AAAA-MM-DD (por defecto, 2 años y 2 semanas antes de --hasta).")
        parser.add_argument("--hasta", help="AAAA-MM-DD (por defecto, ayer).")
        parser.add_argument("--ultimos-dias", type=int, help="Solo los últimos N días hasta ayer (corrida nocturna).")
        parser.add_argument("--peso-en", choices=["lb", "kg"], default="lb",
                            help="Unidad de Cantidad en los productos de peso variable (FAs trabaja en libras).")
        parser.add_argument("--sin-vida-util", action="store_true",
                            help="No estimar la vida útil desde las recepciones con fecha de vencimiento.")
        parser.add_argument("--simular", action="store_true", help="Hace todo y deshace al final: no guarda nada.")

    def handle(self, *args, **op):
        try:
            departamentos = leer_mapeo(op["departamentos"])
            categorias = leer_mapeo(op["categorias"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        fuente = FuenteSuperpos()
        try:
            if op["explorar"]:
                self._explorar(fuente, departamentos)
                return
            self._importar(fuente, departamentos, categorias, op)
        except ErrorConexion as error:
            raise CommandError(str(error)) from error
        finally:
            fuente.cerrar()

    def _explorar(self, fuente, departamentos):
        out = self.stdout.write
        out(f"Servidor MySQL {fuente.version()}")
        out("\nSucursales con ventas (ventasdiarias):")
        for s in fuente.sucursales_con_ventas():
            out(f"  {s['sucursal']}: {s['filas']} filas, del {s['desde']} al {s['hasta']}")
        out("\nDepartamentos (id, nombre, productos activos):")
        for d in fuente.departamentos():
            out(f"  {d['id']:>4}  {d['nombre']}  ({d['productos_activos']})")
        if departamentos:
            out("\nCategorías de esos departamentos:")
            for c in fuente.categorias(list(departamentos)):
                out(f"  {c['id']:>4}  {c['nombre']}  (depto {c['departamento']}, {c['productos_activos']} activos)")
        out("\nCon eso arma el mapeo, por ejemplo: --departamentos \"5=FRUTAS,6=VERDURAS,7=LACTEOS\"")

    def _importar(self, fuente, departamentos, categorias, op):
        if op["sucursal"] is None:
            raise CommandError("Falta --sucursal (usa --explorar para ver cuáles hay).")
        if not departamentos and not categorias:
            raise CommandError("Falta --departamentos o --categorias (usa --explorar para ver los ids).")
        hasta = _fecha(op["hasta"]) if op["hasta"] else timezone.localdate() - timedelta(days=1)
        if op["ultimos_dias"]:
            desde = hasta - timedelta(days=op["ultimos_dias"] - 1)
        else:
            desde = _fecha(op["desde"]) if op["desde"] else None
        codigos = [c.strip() for c in op["productos"].split(",") if c.strip()] or None

        res = importar_desde_superpos(
            fuente, op["sucursal"], departamentos, categorias, codigos=codigos, desde=desde, hasta=hasta,
            peso_en=op["peso_en"], estimar_vida_util=not op["sin_vida_util"], simular=op["simular"],
        )
        out = self.stdout.write
        if op["simular"]:
            out(self.style.WARNING("SIMULACIÓN: no se guardó nada."))
        if res.productos_creados:
            out(f"Productos nuevos ({len(res.productos_creados)}): " + ", ".join(res.productos_creados))
        if res.productos_actualizados:
            out(f"Productos actualizados ({len(res.productos_actualizados)}): " + ", ".join(res.productos_actualizados))
        for nombre, v in res.ventas.items():
            out(f"  {nombre}: {v['dias']} días, {v['desde']:%d/%m/%Y} a {v['hasta']:%d/%m/%Y}, {v['total']:.2f} {v['unidad']}")
        if res.descontado_de_lotes:
            out(f"Las ventas nuevas descontaron {res.descontado_de_lotes:.2f} de los lotes contados.")
        for aviso in res.avisos:
            out(self.style.WARNING(f"Aviso: {aviso}"))
        for error in res.errores:
            out(self.style.ERROR(f"Error: {error}"))
        if res.errores and not res.ventas and not res.productos_creados and not res.productos_actualizados:
            raise CommandError("No se cargó nada.")
        if not op["simular"] and res.ventas:
            out(self.style.SUCCESS(
                "Listo. Siguiente paso: python manage.py entrenar_modelo (y generar_alertas)."
            ))
