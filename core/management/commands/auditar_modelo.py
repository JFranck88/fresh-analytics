import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from django.core.management.base import BaseCommand
from prophet.diagnostics import cross_validation

from core.models import Producto
from core.management.commands.entrenar_modelo import obtener_serie_diaria, construir_modelo

META_MAPE = 25


class Command(BaseCommand):
    help = "Audita los modelos con validación cruzada y genera una gráfica resumen clara en español."

    def add_arguments(self, parser):
        parser.add_argument("--producto", type=str, default=None)

    def handle(self, *args, **options):
        productos = Producto.objects.filter(activo=True)
        if options["producto"]:
            productos = productos.filter(nombre=options["producto"])

        os.makedirs("reportes_validacion", exist_ok=True)
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

            # Calculamos el error nosotros mismos, día por día, en vez de
            # depender de performance_metrics (su parámetro rolling_window
            # agrupa por proporción de datos, no por día - fácil de
            # malinterpretar, así que aquí lo hacemos explícito y simple).
            df_cv["dia_anticipacion"] = (df_cv["ds"] - df_cv["cutoff"]).dt.days
            df_cv["error_pct"] = (
                (df_cv["y"] - df_cv["yhat"]).abs() / df_cv["y"].replace(0, pd.NA)
            ) * 100
            df_cv["producto"] = producto.nombre
            todos_los_resultados.append(df_cv[["producto", "dia_anticipacion", "error_pct"]])

            mape_promedio = round(df_cv["error_pct"].mean(), 2)
            resumen_por_producto.append((producto.nombre, mape_promedio))
            self.stdout.write(self.style.SUCCESS(f"  {producto.nombre}: MAPE promedio = {mape_promedio}%"))

        if not todos_los_resultados:
            self.stdout.write("No hay suficientes datos para generar el reporte.")
            return

        combinado = pd.concat(todos_los_resultados).dropna(subset=["error_pct"])
        promedio_por_dia = combinado.groupby("dia_anticipacion")["error_pct"].mean().sort_index()

        fig, ax = plt.subplots(figsize=(9, 5.5))
        colores = ["#2e7d32" if v <= META_MAPE else "#c62828" for v in promedio_por_dia.values]
        ax.bar(promedio_por_dia.index, promedio_por_dia.values, color=colores, width=0.6)

        ax.axhline(META_MAPE, color="#c62828", linestyle="--", linewidth=1.5)
        ax.text(
            promedio_por_dia.index.max(), META_MAPE + 1,
            f"Meta del proyecto: {META_MAPE}% de error máximo",
            color="#c62828", ha="right", fontsize=10,
        )

        for x, v in zip(promedio_por_dia.index, promedio_por_dia.values):
            ax.text(x, v + 0.6, f"{v:.1f}%", ha="center", fontsize=10, fontweight="bold")

        ax.set_xlabel("Días de anticipación de la predicción")
        ax.set_ylabel("Error promedio del modelo (%)")
        ax.set_title(
            f"Precisión de Fresh Analytics\n"
            f"Validado con {len(resumen_por_producto)} producto(s) y múltiples periodos históricos",
            fontsize=12,
        )
        ax.set_xticks(promedio_por_dia.index)
        ax.set_ylim(0, max(promedio_por_dia.values.max(), META_MAPE) * 1.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout()
        ruta = "reportes_validacion/resumen_precision.png"
        fig.savefig(ruta, dpi=150)
        plt.close(fig)

        self.stdout.write(self.style.SUCCESS(f"\nGráfica resumen guardada en: {ruta}"))
        self.stdout.write("\nMAPE promedio por producto (validación cruzada):")
        for nombre, mape in resumen_por_producto:
            self.stdout.write(f"  {nombre}: {mape}%")