from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Producto

# Orden fijo de departamento/categoría para el código de demo:
# 2 dígitos departamento + 2 dígitos categoría + 3 dígitos correlativo de
# producto (reinicia en 100 dentro de cada categoría). Ej: Lácteos, primer
# producto -> "0101100".
#
# Departamento queda fijo en 1 (una sola tienda/línea de perecederos).
# El número de categoría sigue el orden de Producto.Categoria.
DEPARTAMENTO_ID = 1

CATEGORIA_ID = {
    Producto.Categoria.LACTEOS: 1,
    Producto.Categoria.CARNES: 2,
    Producto.Categoria.FRUTAS: 3,
    Producto.Categoria.VERDURAS: 4,
    Producto.Categoria.PANADERIA: 5,
}

NUMERO_INICIAL = 100


class Command(BaseCommand):
    help = (
        "Asigna un código UPC de demostración a los productos (formato "
        "departamento-categoría-correlativo, ej. 0101100), para que la "
        "demo no muestre UPC en blanco. Por defecto solo llena los "
        "productos que todavía no tienen codigo_upc; con --forzar "
        "reasigna a todos. Esto es solo para la demo: cuando se tengan "
        "los UPC reales de la base de datos del cliente, esos reemplazan "
        "a estos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--forzar",
            action="store_true",
            help="Reasigna el código también a productos que ya tienen codigo_upc.",
        )

    def handle(self, *args, **options):
        forzar = options["forzar"]

        contador_por_categoria = {categoria: NUMERO_INICIAL for categoria in CATEGORIA_ID}
        asignados = 0
        omitidos = 0

        with transaction.atomic():
            productos = Producto.objects.order_by("categoria", "id_producto")
            for producto in productos:
                cat_id = CATEGORIA_ID.get(producto.categoria)
                if cat_id is None:
                    self.stdout.write(self.style.WARNING(
                        f"Categoría desconocida '{producto.categoria}' en "
                        f"'{producto.nombre}', se omite."
                    ))
                    continue

                numero = contador_por_categoria[producto.categoria]
                contador_por_categoria[producto.categoria] += 1

                if producto.codigo_upc and not forzar:
                    omitidos += 1
                    continue

                codigo = f"{DEPARTAMENTO_ID:02d}{cat_id:02d}{numero:03d}"
                producto.codigo_upc = codigo
                producto.save(update_fields=["codigo_upc"])
                asignados += 1
                self.stdout.write(f"{codigo}  {producto.nombre}")

        self.stdout.write(self.style.SUCCESS(
            f"Listo: {asignados} producto(s) con UPC de demo asignado, "
            f"{omitidos} ya tenían y se dejaron igual (usá --forzar para "
            f"reasignarlos)."
        ))
