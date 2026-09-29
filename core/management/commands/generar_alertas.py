from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
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

        # Antes aquí se hacía Alerta.objects.all().delete() y se volvían a
        # crear todas desde cero cada día: se perdía quién había leído cada
        # alerta y cuándo, y una alerta ya revisada reaparecía como "nueva"
        # al día siguiente aunque fuera la misma situación. Ahora primero se
        # calcula qué alertas aplican hoy (`vigentes`) y al final se
        # concilian contra las que ya existen (ver _conciliar).
        vigentes = []
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
                # Peso variable se reporta en kg (con decimal); por unidad,
                # en piezas enteras - mismo criterio que en pantalla (ver
                # Producto.unidad_medida).
                texto_cantidad = (
                    f"{cantidad_riesgo:.2f} kg" if producto.unidad_medida == Producto.UnidadMedida.PESO
                    else f"{cantidad_riesgo:.0f} unidades"
                )
                vigentes.append(Alerta(
                    producto=producto, tipo=Alerta.Tipo.VENCIMIENTO,
                    # Si entra otro lote a la ventana de vencimiento, es un
                    # evento distinto y debe volver a avisarse aunque la
                    # alerta anterior ya se hubiera leído.
                    referencia=proxima.fecha_vencimiento.isoformat(),
                    mensaje=(
                        f"{texto_cantidad} vencen en {dias_restantes} día(s)."
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

                # Formato de cantidad según Producto.unidad_medida - mismo
                # criterio que la alerta de vencimiento de arriba y que el
                # resto de pantallas del sistema.
                es_peso = producto.unidad_medida == Producto.UnidadMedida.PESO
                fmt_cantidad = (lambda v: f"{v:.2f} kg") if es_peso else (lambda v: f"{v:.0f} unidades")

                # P-07B: Verificar stock bajo
                if stock_actual < prediccion_cobertura:
                    vigentes.append(Alerta(
                        producto=producto, tipo=Alerta.Tipo.STOCK_BAJO,
                        mensaje=(
                            f"Stock actual ({fmt_cantidad(stock_actual)}) no cubre la "
                            f"venta esperada de los próximos {dias_cobertura} "
                            f"días ({fmt_cantidad(prediccion_cobertura)})."
                        ),
                    ))

                # P-07C: Verificar excedente
                limite_excedente = prediccion_semana * (1 + porcentaje_excedente)
                if prediccion_semana > 0 and stock_actual > limite_excedente:
                    vigentes.append(Alerta(
                        producto=producto, tipo=Alerta.Tipo.EXCEDENTE,
                        mensaje=(
                            f"Stock actual ({fmt_cantidad(stock_actual)}) supera en más "
                            f"de {porcentaje_excedente*100:.0f}% la predicción "
                            f"semanal ({fmt_cantidad(prediccion_semana)})."
                        ),
                    ))

        # P-07D + P-07E: Consolidar y almacenar
        creadas, mantenidas, resueltas = _conciliar(vigentes)
        self.stdout.write(self.style.SUCCESS(
            f"{len(vigentes)} alertas vigentes (P-07): {creadas} nuevas, "
            f"{mantenidas} se mantienen, {resueltas} resueltas y retiradas."
        ))


def _clave(alerta):
    return (alerta.producto_id, alerta.tipo, alerta.referencia)


def _conciliar(vigentes):
    """Concilia las alertas que aplican hoy contra las ya guardadas:
    - Si la misma alerta (producto + tipo + referencia) ya existe, se
      conserva tal cual - incluido si ya fue leída, por quién y cuándo -
      y solo se actualiza su mensaje (las cantidades cambian cada día).
    - Si es nueva, se crea sin leer.
    - Si una alerta guardada ya no aplica (se resolvió), se retira.
    Correr el comando dos veces el mismo día no duplica nada."""
    ahora = timezone.now()
    existentes = {}
    sobrantes = []
    for alerta in Alerta.objects.order_by("id_alerta"):
        if _clave(alerta) in existentes:
            sobrantes.append(alerta.pk)  # duplicado heredado: se limpia
        else:
            existentes[_clave(alerta)] = alerta

    nuevas, actualizadas, claves_vigentes = [], [], set()
    for alerta in vigentes:
        clave = _clave(alerta)
        if clave in claves_vigentes:
            continue
        claves_vigentes.add(clave)
        guardada = existentes.get(clave)
        if guardada:
            guardada.mensaje = alerta.mensaje
            guardada.fecha_actualizacion = ahora
            actualizadas.append(guardada)
        else:
            alerta.fecha_actualizacion = ahora
            nuevas.append(alerta)

    resueltas = [a.pk for clave, a in existentes.items() if clave not in claves_vigentes]

    with transaction.atomic():
        Alerta.objects.filter(pk__in=resueltas + sobrantes).delete()
        Alerta.objects.bulk_update(actualizadas, ["mensaje", "fecha_actualizacion"])
        Alerta.objects.bulk_create(nuevas)

    return len(nuevas), len(actualizadas), len(resueltas)