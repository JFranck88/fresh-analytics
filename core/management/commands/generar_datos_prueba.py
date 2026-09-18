import random
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Producto, Venta, Inventario, Merma
from core.clima import lluvia_sintetica


# El 7mo campo de cada tupla es Producto.UnidadMedida: "UNIDAD" (se cuenta
# por pieza - siempre cantidades enteras) o "PESO" (peso variable, se pesa
# suelto en caja - cantidades en kg, con decimales). Clasificación
# confirmada con Francisco (2026-09-18): los 6 marcados como PESO son los
# que en un súper real se pesan en la caja; el resto vienen empacados o se
# cuentan por pieza. "Pan de molde" se reemplazó por "Pan dulce" - no es un
# producto típico en Guatemala.
CATALOGO = [
    ("Leche entera 1L", "LACTEOS", 10, "Lacteos San Miguel", 6.00, 9.00, "UNIDAD"),
    ("Queso fresco", "LACTEOS", 10, "Lacteos San Miguel", 18.00, 25.00, "UNIDAD"),
    ("Yogurt natural", "LACTEOS", 14, "Lacteos San Miguel", 5.00, 8.00, "UNIDAD"),
    ("Pechuga de pollo", "CARNES", 4, "Avicola Petapa", 20.00, 32.00, "PESO"),
    ("Carne molida de res", "CARNES", 3, "Carnicos del Valle", 28.00, 42.00, "PESO"),
    ("Chorizo", "CARNES", 7, "Carnicos del Valle", 15.00, 24.00, "UNIDAD"),
    ("Tomate de riñón", "VERDURAS", 5, "Agroexport GT", 5.00, 8.50, "PESO"),
    ("Cebolla blanca", "VERDURAS", 12, "Agroexport GT", 3.50, 6.00, "PESO"),
    ("Lechuga", "VERDURAS", 6, "Agroexport GT", 3.00, 5.50, "UNIDAD"),
    ("Manzana roja", "FRUTAS", 15, "Frutas del Altiplano", 4.00, 6.00, "PESO"),
    ("Banano", "FRUTAS", 6, "Frutas del Altiplano", 2.50, 4.00, "PESO"),
    ("Aguacate hass", "FRUTAS", 5, "Frutas del Altiplano", 6.00, 9.50, "UNIDAD"),
    ("Pan francés", "PANADERIA", 2, "Panificadora Ideal", 1.50, 2.50, "UNIDAD"),
    ("Pan dulce", "PANADERIA", 3, "Panificadora Ideal", 1.00, 1.75, "UNIDAD"),
]

FACTOR_SEMANA = {0: 0.9, 1: 0.8, 2: 0.8, 3: 0.95, 4: 1.3, 5: 1.4, 6: 1.1}

BASE_DEMANDA = {
    "LACTEOS": 25,
    "CARNES": 18,
    "FRUTAS": 30,
    "VERDURAS": 35,
    "PANADERIA": 40,
}

# La precisión de cada cantidad (venta, merma, lote) según sea peso
# variable o por unidad vive en Producto.redondear_cantidad() - un único
# lugar, usado tanto aquí como en actualizar_historial_ventas.py, para que
# ambos generadores queden siempre consistentes entre sí.


class Command(BaseCommand):
    help = "Genera historial sintético (ventas, inventario, mermas, efecto de lluvia)."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=730)
        parser.add_argument("--limpiar", action="store_true")

    def handle(self, *args, **options):
        dias = options["dias"]

        if options["limpiar"]:
            Venta.objects.all().delete()
            Merma.objects.all().delete()
            Inventario.objects.all().delete()
            self.stdout.write("Ventas, mermas e inventario anteriores eliminados.")

        productos = []
        for nombre, categoria, vida_util, proveedor, p_compra, p_venta, unidad_medida in CATALOGO:
            producto, _ = Producto.objects.get_or_create(
                nombre=nombre,
                defaults=dict(
                    categoria=categoria, vida_util_dias=vida_util, proveedor=proveedor,
                    precio_compra=p_compra, precio_venta=p_venta, unidad_medida=unidad_medida,
                ),
            )
            productos.append(producto)

        hoy = timezone.localdate()
        inicio = hoy - timedelta(days=dias)

        ventas_bulk, mermas_bulk, inventarios_bulk = [], [], []
        fecha = inicio
        dia_num = 0

        while fecha <= hoy:
            factor_semana = FACTOR_SEMANA[fecha.weekday()]
            factor_quincena = 1.25 if fecha.day in (14, 15, 16, 29, 30, 31, 1) else 1.0
            factor_estacional = 1.15 if fecha.month in (9, 10, 11, 12) else 1.0
            llueve_hoy = lluvia_sintetica(fecha)
            factor_total = factor_semana * factor_quincena * factor_estacional

            for producto in productos:
                base = BASE_DEMANDA.get(producto.categoria, 20)
                cantidad_dia = max(0, producto.redondear_cantidad(
                    random.gauss(base * factor_total, base * 0.15)
                ))

                # Efecto del clima (RF-08): días de lluvia reducen la venta
                # de productos frescos, coherente con menos flujo de
                # clientes comprando perecederos delicados ese día.
                if llueve_hoy and producto.categoria in ("FRUTAS", "VERDURAS"):
                    cantidad_dia = max(0, producto.redondear_cantidad(cantidad_dia * 0.88))

                if cantidad_dia <= 0:
                    continue

                hora = random.randint(8, 20)
                ventas_bulk.append(Venta(
                    producto=producto,
                    fecha=timezone.make_aware(datetime.combine(fecha, time(hour=hora))),
                    cantidad=cantidad_dia,
                    precio_unitario=producto.precio_venta,
                    promocion_aplicada=random.random() < 0.05,
                ))

                if random.random() < 0.35:
                    # Cantidad de la merma: usa la misma regla de
                    # redondeo que Venta.cantidad e Inventario.cantidad
                    # (redondear_cantidad, ver arriba) - entero completo
                    # para productos por unidad ("0,5 yogures" no tiene
                    # sentido), decimal en kg para productos de peso
                    # variable (reportado por Francisco al ver decimales
                    # en Historial de Mermas para un producto por pieza;
                    # confirmado que sí tienen sentido para peso variable,
                    # 2026-09-17/18). Como el dado de arriba ya decide SI
                    # hay merma ese día, una vez que ocurre se garantiza
                    # una cantidad mínima perceptible (en vez de que el
                    # redondeo la deje en 0 y se pierda el registro): 1
                    # unidad completa, o 0.05 kg para peso variable.
                    minimo = 0.05 if producto.unidad_medida == "PESO" else 1
                    cantidad_merma = max(minimo, producto.redondear_cantidad(
                        cantidad_dia * random.uniform(0.005, 0.03)
                    ))
                    if producto.categoria in ("FRUTAS", "VERDURAS"):
                        if producto.nombre == "Tomate de riñón":
                            pesos = [0.25, 0.65, 0.10]
                        else:
                            pesos = [0.55, 0.35, 0.10]
                        motivo = random.choices(
                            ["DANO", "VENCIMIENTO", "OTRO"], weights=pesos
                        )[0]
                    else:
                        motivo = random.choices(
                            ["VENCIMIENTO", "OTRO", "ROBO"], weights=[0.75, 0.15, 0.10]
                        )[0]

                    mermas_bulk.append(Merma(
                        producto=producto, fecha=fecha, cantidad=cantidad_merma,
                        motivo=motivo,
                        costo_perdida=round(
                            float(cantidad_merma) * float(producto.precio_compra), 2
                        ),
                    ))

            if dia_num % 3 == 0:
                for producto in productos:
                    cantidad_lote = producto.redondear_cantidad(
                        BASE_DEMANDA.get(producto.categoria, 20) * random.uniform(2.5, 4)
                    )
                    inventarios_bulk.append(Inventario(
                        producto=producto, fecha_ingreso=fecha,
                        fecha_vencimiento=fecha + timedelta(days=producto.vida_util_dias),
                        cantidad=cantidad_lote,
                        lote=f"L-{fecha.strftime('%Y%m%d')}-{producto.id_producto}",
                    ))

            if len(ventas_bulk) > 5000:
                Venta.objects.bulk_create(ventas_bulk)
                ventas_bulk = []
            if len(mermas_bulk) > 2000:
                Merma.objects.bulk_create(mermas_bulk)
                mermas_bulk = []
            if len(inventarios_bulk) > 2000:
                Inventario.objects.bulk_create(inventarios_bulk)
                inventarios_bulk = []

            fecha += timedelta(days=1)
            dia_num += 1

        if ventas_bulk:
            Venta.objects.bulk_create(ventas_bulk)
        if mermas_bulk:
            Merma.objects.bulk_create(mermas_bulk)
        if inventarios_bulk:
            Inventario.objects.bulk_create(inventarios_bulk)

        self.stdout.write(self.style.SUCCESS(
            f"Listo: {dias} días generados para {len(productos)} productos (con efecto de lluvia)."
        ))