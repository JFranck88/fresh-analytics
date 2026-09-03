from datetime import timedelta

import pandas as pd
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from prophet import Prophet

from core.models import Producto, Venta, Prediccion

DIAS_QUINCENA = [14, 15, 16, 29, 30, 31, 1]


def obtener_serie_diaria(producto):
    """Devuelve un DataFrame con columnas ds/y/es_quincena, una fila por día vendido."""
    filas = (
        Venta.objects.filter(producto=producto)
        .annotate(dia=TruncDate("fecha"))
        .values("dia")
        .annotate(total=Sum("cantidad"))
        .order_by("dia")
    )
    df = pd.DataFrame(list(filas))
    if df.empty:
        return df
    df = df.rename(columns={"dia": "ds", "total": "y"})
    df["ds"] = pd.to_datetime(df["ds"])
    df["es_quincena"] = df["ds"].dt.day.isin(DIAS_QUINCENA).astype(int)
    return df


def construir_modelo():
    """Crea un Prophet con festivos de Guatemala y el regresor de quincena
    ya registrados (RF-08). Se usa una función para no repetir la
    configuración entre el modelo de validación y el modelo final."""
    modelo = Prophet(interval_width=0.80, weekly_seasonality=True, yearly_seasonality=True)
    modelo.add_country_holidays(country_name="GT")
    modelo.add_regressor("es_quincena")
    return modelo


def agregar_quincena_a_futuro(df_futuro):
    df_futuro["es_quincena"] = df_futuro["ds"].dt.day.isin(DIAS_QUINCENA).astype(int)
    return df_futuro


def calcular_mape(reales, predichos):
    """MAPE simple, ignorando días con venta real de 0 (división por cero)."""
    errores = []
    for real, pred in zip(reales, predichos):
        if real:
            errores.append(abs(real - pred) / real)
    if not errores:
        return None
    return round(sum(errores) / len(errores) * 100, 2)


class Command(BaseCommand):
    help = "Entrena Prophet por producto con festivos GT y quincena, guarda predicciones (RF-03/RF-08)."

    def handle(self, *args, **options):
        hoy = timezone.localdate()
        productos = Producto.objects.filter(activo=True)

        for producto in productos:
            df = obtener_serie_diaria(producto)

            if len(df) < 30:
                self.stdout.write(
                    f"  {producto.nombre}: historial insuficiente ({len(df)} días), se omite."
                )
                continue

            # --- Paso 1: validar el modelo con los últimos 7 días reales ---
            corte = hoy - timedelta(days=7)
            df_entreno = df[df["ds"] < pd.Timestamp(corte)]
            df_real_reciente = df[df["ds"] >= pd.Timestamp(corte)]

            mape = None
            if len(df_entreno) >= 30 and not df_real_reciente.empty:
                modelo_val = construir_modelo()
                modelo_val.fit(df_entreno)
                futuro_val = modelo_val.make_future_dataframe(periods=len(df_real_reciente))
                futuro_val = agregar_quincena_a_futuro(futuro_val)
                pronostico_val = modelo_val.predict(futuro_val).tail(len(df_real_reciente))
                mape = calcular_mape(
                    df_real_reciente["y"].tolist(), pronostico_val["yhat"].tolist()
                )

            # --- Paso 2: modelo final con TODO el historial, predice el futuro real ---
            modelo = construir_modelo()
            modelo.fit(df)
            futuro = modelo.make_future_dataframe(periods=7)
            futuro = agregar_quincena_a_futuro(futuro)
            pronostico = modelo.predict(futuro).tail(7)

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
                ))
            Prediccion.objects.bulk_create(nuevas)

            mape_txt = f"MAPE={mape}%" if mape is not None else "MAPE=N/D"
            self.stdout.write(self.style.SUCCESS(f"  {producto.nombre}: OK ({mape_txt})"))

        self.stdout.write(self.style.SUCCESS("Entrenamiento completo (con festivos GT y quincena)."))