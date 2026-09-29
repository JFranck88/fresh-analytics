import pandas as pd
from django.core.management.base import BaseCommand
from prophet.diagnostics import cross_validation

from core.models import Producto
from core.management.commands.entrenar_modelo import obtener_serie_diaria, construir_modelo


class Command(BaseCommand):
    help = (
        "Herramienta interna de auditoría: valida el modelo con validación "
        "cruzada temporal de Prophet (varios cortes históricos, horizonte de "
        "7 días) e imprime el MAPE por producto y por día de anticipación. "
        "Se corre a mano cuando se quiere evidencia de precisión; no forma "
        "parte del pipeline diario ni se muestra en pantalla."
    )

    # Historia: antes guardaba el resultado como JSON en Configuracion
    # (clave "validacion_cruzada_resultado") para una gráfica de
    # Predicciones que se retiró el 2026-09-16 por no aportarle al usuario.
    # Guardarlo ahí dejaba un "parámetro" basura visible en la pantalla de
    # Configuración, así que ahora solo imprime el reporte en consola (la
    # migración 0010 limpia ese registro si quedó en la base).

    def add_arguments(self, parser):
        parser.add_argument("--producto", type=str, default=None)

    def handle(self, *args, **options):
        productos = Producto.objects.filter(activo=True)
        if options["producto"]:
            productos = productos.filter(nombre=options["producto"])

        todos_los_resultados = []
        resumen_por_producto = []

        for producto in productos:
            df = obtener_serie_diaria(producto)
            if len(df) < 400:
                self.stdout.write(f"  {producto.nombre}: historial insuficiente, se omite.")
                continue

            self.stdout.write(f"  {producto.nombre}: validando (varios cortes históricos)...")
            modelo = construir_modelo()
            modelo.fit(df)

            df_cv = cross_validation(modelo, initial="365 days", period="30 days", horizon="7 days")
            df_cv["dia_anticipacion"] = (df_cv["ds"] - df_cv["cutoff"]).dt.days
            df_cv["error_pct"] = (
                (df_cv["y"] - df_cv["yhat"]).abs() / df_cv["y"].replace(0, pd.NA)
            ) * 100
            todos_los_resultados.append(df_cv[["dia_anticipacion", "error_pct"]])

            mape_promedio = round(df_cv["error_pct"].mean(), 2)
            resumen_por_producto.append((producto.nombre, mape_promedio))
            self.stdout.write(self.style.SUCCESS(f"  {producto.nombre}: MAPE promedio = {mape_promedio}%"))

        if not todos_los_resultados:
            self.stdout.write("No hay suficientes datos para generar el reporte.")
            return

        combinado = pd.concat(todos_los_resultados).dropna(subset=["error_pct"])
        promedio_por_dia = combinado.groupby("dia_anticipacion")["error_pct"].mean().sort_index()

        self.stdout.write(self.style.SUCCESS(
            f"\nMAPE promedio por día de anticipación ({len(resumen_por_producto)} producto(s)):"
        ))
        for dia, mape in promedio_por_dia.items():
            self.stdout.write(f"  Día {int(dia)}: {float(mape):.1f}%")
        self.stdout.write("\nMAPE promedio por producto:")
        for nombre, mape in resumen_por_producto:
            self.stdout.write(f"  {nombre}: {mape}%")
