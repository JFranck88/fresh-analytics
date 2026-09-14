"""
Mantiene vivo el historial sintético de ventas (RNF-04): sin este comando,
la tabla Venta se queda congelada en la fecha en que se cargó por última
vez, y entrenar_modelo termina "prediciendo" fechas que ya pasaron, aunque
el entrenamiento en sí corra todos los días sin errores.

Genera automáticamente cada día que falte entre la última venta registrada
y ayer (inclusive), usando exactamente la misma lógica de demanda que
generar_datos_prueba.py (mismos factores de día de semana, quincena,
temporada y lluvia), para que el historial sea estadísticamente
consistente con el resto de los datos. Pensado para correr cada madrugada
junto con entrenar_modelo (ver .github/workflows/entrenar_diario.yml).

Es seguro correrlo más de una vez el mismo día o varios días seguidos: si
el historial ya está al día, no hace nada.
"""

import random
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from core.clima import lluvia_sintetica
from core.management.commands.generar_datos_prueba import (
    BASE_DEMANDA,
    FACTOR_SEMANA,
)
from core.models import Inventario, Merma, Producto, Venta

# Tope de seguridad: si por algún motivo faltaran muchísimos días (por
# ejemplo, la primera vez que corre este comando tras una laguna larga),
# no generamos de golpe un historial descontrolado en una sola corrida.
MAXIMO_DIAS_POR_CORRIDA = 60


class Command(BaseCommand):
    help = "Rellena el historial sintético (ventas/inventario/mermas) hasta el día de ayer."

    def handle(self, *args, **options):
        ayer = timezone.localdate() - timedelta(days=1)

        ultima_venta = Venta.objects.aggregate(ultima=Max("fecha"))["ultima"]
        if ultima_venta:
            desde = timezone.localtime(ultima_venta).date() + timedelta(days=1)
        else:
            # No debería pasar en producción (ya hay 2 años de historial
            # sintético cargado), pero por seguridad no generamos años
            # hacia atrás si la tabla estuviera vacía.
            desde = ayer

        if desde > ayer:
            self.stdout.write(
                f"El historial ya está al día (última venta: {ultima_venta.date()})."
            )
            return

        dias_pendientes = (ayer - desde).days + 1
        if dias_pendientes > MAXIMO_DIAS_POR_CORRIDA:
            self.stdout.write(self.style.WARNING(
                f"Faltan {dias_pendientes} días, más de lo esperado - "
                f"generando solo los primeros {MAXIMO_DIAS_POR_CORRIDA} "
                "por seguridad. Vuelve a correr el comando para continuar."
            ))
            hasta = desde + timedelta(days=MAXIMO_DIAS_POR_CORRIDA - 1)
        else:
            hasta = ayer

        productos = list(Producto.objects.filter(activo=True))
        fecha = desde
        dias_generados = 0
        while fecha <= hasta:
            self._generar_dia(fecha, productos)
            dias_generados += 1
            fecha += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS(
            f"Historial actualizado: {dias_generados} día(s) generado(s), del {desde} al {hasta}."
        ))

    def _generar_dia(self, fecha, productos):
        factor_semana = FACTOR_SEMANA[fecha.weekday()]
        factor_quincena = 1.25 if fecha.day in (14, 15, 16, 29, 30, 31, 1) else 1.0
        factor_estacional = 1.15 if fecha.month in (9, 10, 11, 12) else 1.0
        llueve_ese_dia = lluvia_sintetica(fecha)
        factor_total = factor_semana * factor_quincena * factor_estacional

        ventas_bulk, mermas_bulk, inventarios_bulk = [], [], []

        for producto in productos:
            base = BASE_DEMANDA.get(producto.categoria, 20)
            cantidad_dia = max(0, round(random.gauss(base * factor_total, base * 0.15)))

            if llueve_ese_dia and producto.categoria in ("FRUTAS", "VERDURAS"):
                cantidad_dia = max(0, round(cantidad_dia * 0.88))

            if cantidad_dia > 0:
                hora = random.randint(8, 20)
                ventas_bulk.append(Venta(
                    producto=producto,
                    fecha=timezone.make_aware(datetime.combine(fecha, time(hour=hora))),
                    cantidad=cantidad_dia,
                    precio_unitario=producto.precio_venta,
                    promocion_aplicada=random.random() < 0.05,
                ))

                if random.random() < 0.35:
                    cantidad_merma = round(cantidad_dia * random.uniform(0.005, 0.03), 1)
                    if cantidad_merma > 0:
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

            # Reposición de inventario: un lote nuevo cada día, como en una
            # tienda real donde el stock se repone constantemente.
            cantidad_lote = round(
                BASE_DEMANDA.get(producto.categoria, 20) * random.uniform(2.5, 4)
            )
            inventarios_bulk.append(Inventario(
                producto=producto, fecha_ingreso=fecha,
                fecha_vencimiento=fecha + timedelta(days=producto.vida_util_dias),
                cantidad=cantidad_lote,
                lote=f"L-{fecha.strftime('%Y%m%d')}-{producto.id_producto}",
            ))

        if ventas_bulk:
            Venta.objects.bulk_create(ventas_bulk)
        if mermas_bulk:
            Merma.objects.bulk_create(mermas_bulk)
        if inventarios_bulk:
            Inventario.objects.bulk_create(inventarios_bulk)
