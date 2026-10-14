from datetime import timedelta

import pandas as pd
from django.core.management.base import BaseCommand
from django.db.models import Case, IntegerField, Max, Sum, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone
from prophet import Prophet

from core.models import Producto, Venta, Prediccion
from core.clima import lluvia_sintetica, pronostico_lluvia_real

DIAS_QUINCENA = [14, 15, 16, 29, 30, 31, 1]


def obtener_serie_diaria(producto, con_lluvia=True):
    filas = (
        Venta.objects.filter(producto=producto)
        .annotate(dia=TruncDate("fecha"))
        .values("dia")
        # Postgres no acepta MAX() sobre un booleano (SQLite sí; falló en
        # GitHub Actions el 2026-10-14), así que se convierte a 0/1 antes.
        .annotate(
            total=Sum("cantidad"),
            promo=Max(
                Case(
                    When(promocion_aplicada=True, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
        )
        .order_by("dia")
    )
    df = pd.DataFrame(list(filas))
    if df.empty:
        return df
    df = df.rename(columns={"dia": "ds", "total": "y"})
    df["ds"] = pd.to_datetime(df["ds"])
    df["promocion"] = df["promo"].astype(int)
    df = df.drop(columns=["promo"])
    df["es_quincena"] = df["ds"].dt.day.isin(DIAS_QUINCENA).astype(int)
    if con_lluvia:
        df["lluvia"] = df["ds"].apply(lambda d: lluvia_sintetica(d.date()))
    return df


def construir_modelo(con_lluvia=True, anual=True, con_promocion=False, flexibilidad=0.05):
    """Productos REALES (2026-10-14): sin el regresor de lluvia, porque la
    lluvia histórica que tiene FAs es simulada y no corresponde a su
    historia; y estacionalidad anual solo con al menos dos años de datos."""
    modelo = Prophet(interval_width=0.80, weekly_seasonality=True, yearly_seasonality=anual,
                     changepoint_prior_scale=flexibilidad)
    modelo.add_country_holidays(country_name="GT")
    modelo.add_regressor("es_quincena")
    if con_lluvia:
        modelo.add_regressor("lluvia")
    # Productos REALES con días con y sin promoción en su historia: el modelo
    # separa el efecto de la promoción de la tendencia (el yogurt del
    # supermercado vendía 19/día en promoción y 12,5 sin ella). Para los
    # días que vienen se asume que no hay promoción.
    if con_promocion:
        modelo.add_regressor("promocion")
    return modelo


def agregar_regresores_a_futuro(df_futuro, usar_pronostico_real=False, con_lluvia=True):
    df_futuro["es_quincena"] = df_futuro["ds"].dt.day.isin(DIAS_QUINCENA).astype(int)
    if "promocion" not in df_futuro:
        df_futuro["promocion"] = 0
    if not con_lluvia:
        return df_futuro

    pronostico = pronostico_lluvia_real() if usar_pronostico_real else {}

    def calcular_lluvia(ds):
        fecha = ds.date()
        if fecha in pronostico:
            return 1 if pronostico[fecha] >= 0.4 else 0
        return lluvia_sintetica(fecha)

    df_futuro["lluvia"] = df_futuro["ds"].apply(calcular_lluvia)
    return df_futuro


def calcular_error_unidades(reales, predichos):
    """Error promedio por día en unidades o libras (2026-10-14): en productos
    de poca venta el MAPE se dispara (vender 1 y pronosticar 5 ya es 400%),
    así que se muestra también cuánto se equivoca en producto."""
    if not reales:
        return None
    return round(sum(abs(r - p) for r, p in zip(reales, predichos)) / len(reales), 2)


# Productos REALES: con poca historia no hay una configuración que sirva
# para todos (el yogurt del supermercado bajó fuerte al terminar su
# promoción; el aguacate no). Se prueban estas combinaciones con la última
# semana real y se queda la que menos se equivocó en unidades.
FLEXIBILIDADES_REALES = (0.05, 0.2, 0.5)


def calcular_mape(reales, predichos):
    errores = []
    for real, pred in zip(reales, predichos):
        if real:
            errores.append(abs(real - pred) / real)
    if not errores:
        return None
    return round(sum(errores) / len(errores) * 100, 2)


class Command(BaseCommand):
    help = "Entrena Prophet con festivos GT, quincena y clima; guarda predicciones (RF-03/RF-08)."

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        productos = Producto.objects.filter(activo=True)

        for producto in productos:
            es_real = producto.origen == Producto.Origen.REAL
            con_lluvia = not es_real
            df = obtener_serie_diaria(producto, con_lluvia=con_lluvia)

            if len(df) < 30:
                self.stdout.write(
                    f"  {producto.nombre}: historial insuficiente ({len(df)} días), se omite."
                )
                continue

            con_promocion = es_real and df["promocion"].nunique() > 1
            # La historia de un producto real puede terminar antes de ayer
            # (el último reporte que mandó el supermercado): todo se mide
            # desde su último día con datos, no desde hoy.
            ultimo = df["ds"].max().date()
            # Prophet recomienda 2 años para separar bien la temporada del año.
            anual = (ultimo - df["ds"].min().date()).days >= 730
            corte = ultimo - timedelta(days=6)
            df_entreno = df[df["ds"] < pd.Timestamp(corte)]
            df_real_reciente = df[df["ds"] >= pd.Timestamp(corte)]

            if es_real:
                configuraciones = [
                    (flex, promo) for flex in FLEXIBILIDADES_REALES
                    for promo in ((False, True) if con_promocion else (False,))
                ]
            else:
                configuraciones = [(0.05, False)]

            mape = error_unidades = None
            mejor = configuraciones[0]
            if len(df_entreno) >= 30 and not df_real_reciente.empty:
                reales = df_real_reciente["y"].tolist()
                resultados = []
                for flex, promo in configuraciones:
                    modelo_val = construir_modelo(
                        con_lluvia, anual, promo and df_entreno["promocion"].nunique() > 1, flex,
                    )
                    modelo_val.fit(df_entreno)
                    # En el backtest sí se conoce si hubo promoción esos días.
                    futuro_val = agregar_regresores_a_futuro(
                        df_real_reciente[["ds", "promocion"]].copy(), usar_pronostico_real=False, con_lluvia=con_lluvia,
                    )
                    predichos = [max(0, v) for v in modelo_val.predict(futuro_val)["yhat"].tolist()]
                    resultados.append((calcular_error_unidades(reales, predichos), calcular_mape(reales, predichos), (flex, promo)))
                error_unidades, mape, mejor = min(resultados, key=lambda r: r[0])

            flexibilidad, usar_promocion = mejor
            modelo = construir_modelo(con_lluvia, anual, usar_promocion, flexibilidad)
            modelo.fit(df)
            # Siempre los 7 días desde HOY, aunque la historia termine antes.
            futuro = pd.DataFrame({"ds": pd.date_range(pd.Timestamp(hoy), periods=7, freq="D")})
            # Aquí sí usamos el pronóstico REAL de OpenWeatherMap para los
            # próximos días - es la única parte de todo el pipeline que
            # consulta clima real, no sintético.
            futuro = agregar_regresores_a_futuro(futuro, usar_pronostico_real=True, con_lluvia=con_lluvia)
            pronostico = modelo.predict(futuro)

            Prediccion.objects.filter(producto=producto, fecha_prediccion=hoy).delete()

            nuevas = []
            for _, fila in pronostico.iterrows():
                nuevas.append(Prediccion(
                    producto=producto,
                    fecha_prediccion=hoy,
                    fecha_pronosticada=fila["ds"].date(),
                    valor_predicho=max(0, round(fila["yhat"], 2)),
                    intervalo_inferior=max(0, round(fila["yhat_lower"], 2)),
                    intervalo_superior=max(0, round(fila["yhat_upper"], 2)),
                    precision_modelo=mape,
                    error_unidades=error_unidades,
                ))
            Prediccion.objects.bulk_create(nuevas)

            mape_txt = f"MAPE={mape}%, ±{error_unidades}/día" if mape is not None else "MAPE=N/D"
            if es_real:
                mape_txt += f", flexibilidad {flexibilidad}{', con promoción' if usar_promocion else ''}"
            self.stdout.write(self.style.SUCCESS(f"  {producto.nombre}: OK ({mape_txt})"))

        self.stdout.write(self.style.SUCCESS(
            "Entrenamiento completo (festivos GT, quincena y clima)."
        ))