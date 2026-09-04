from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from core.models import Producto, Inventario, Prediccion, Alerta, Configuracion


def obtener_parametro(clave, default):
    try:
        return Configuracion.objects.get(clave=clave).valor
    except Configuracion.DoesNotExist:
        return default


class Command(BaseCommand):
    help = "Genera y persiste alertas de vencimiento, stock bajo y excedente (DFD P-07)."

    def handle(self, *args, **options):
        hoy = timezone.localdate()

        dias_vencimiento = int(obtener_parametro("dias_alerta_vencimiento", 3))
        dias_cobertura = int(obtener_parametro("dias_cobertura_stock_bajo", 2))
        porcentaje_excedente = float(obtener_parametro("porcentaje_excedente", 30)) / 100

        fecha_prediccion_max = Prediccion.objects.order_by(
            "-fecha_prediccion"
        ).values_list("fecha_prediccion", flat=True).first()

        # Regenerar las alertas de hoy - evita duplicados si el comando
        # corre más de una vez en el mismo día.
        Alerta.objects.filter(fecha_generacion__date=hoy).delete()

        nuevas = []
        for producto in Producto.objects.filter(activo=True):
            stock_actual = Inventario.objects.filter(
                producto=producto, fecha_vencimiento__gte=hoy
            ).aggregate(total=Sum("cantidad"))["total"] or 0

            # P-07A: Verificar vencimiento
            limite_venc = hoy + timedelta(days=dias_vencimiento)
            lotes_por_vencer = Inventario.objects.filter(
                producto=producto, fecha_vencimiento__gte=hoy,
                fecha_vencimiento__lte=limite_venc,
            )
            if lotes_por_vencer.exists():
                cantidad_riesgo = lotes_por_vencer.aggregate(
                    total=Sum("cantidad")
                )["total"] or 0
                proxima = lotes_por_vencer.order_by("fecha_vencimiento").first()
                dias_restantes = (proxima.fecha_vencimiento - hoy).days
                nuevas.append(Alerta(
                    producto=producto, tipo=Alerta.Tipo.VENCIMIENTO,
                    mensaje=(
                        f"{cantidad_riesgo:.0f} unidades vencen en "
                        f"{dias_restantes} día(s)."
                    ),
                ))

            if fecha_prediccion_max:
                prediccion_cobertura = Prediccion.objects.filter(
                    producto=producto, fecha_prediccion=fecha_prediccion_max,
                    fecha_pronosticada__lte=hoy + timedelta(days=dias_cobertura),
                ).aggregate(total=Sum("valor_predicho"))["total"] or 0

                prediccion_semana = Prediccion.objects.filter(
                    producto=producto, fecha_prediccion=fecha_prediccion_max,
                ).aggregate(total=Sum("valor_predicho"))["total"] or 0

                # P-07B: Verificar stock bajo
                if stock_actual < prediccion_cobertura:
                    nuevas.append(Alerta(
                        producto=producto, tipo=Alerta.Tipo.STOCK_BAJO,
                        mensaje=(
                            f"Stock actual ({stock_actual:.0f}) no cubre la "
                            f"venta esperada de los próximos {dias_cobertura} "
                            f"días ({prediccion_cobertura:.0f})."
                        ),
                    ))

                # P-07C: Verificar excedente
                limite_excedente = prediccion_semana * (1 + porcentaje_excedente)
                if prediccion_semana > 0 and stock_actual > limite_excedente:
                    nuevas.append(Alerta(
                        producto=producto, tipo=Alerta.Tipo.EXCEDENTE,
                        mensaje=(
                            f"Stock actual ({stock_actual:.0f}) supera en más "
                            f"de {porcentaje_excedente*100:.0f}% la predicción "
                            f"semanal ({prediccion_semana:.0f})."
                        ),
                    ))

        # P-07D + P-07E: Consolidar y almacenar
        Alerta.objects.bulk_create(nuevas)
        self.stdout.write(self.style.SUCCESS(f"{len(nuevas)} alertas generadas (P-07)."))