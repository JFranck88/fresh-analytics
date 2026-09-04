import json

import pandas as pd
from django.core.management.base import BaseCommand
from django.utils import timezone
from prophet.diagnostics import cross_validation

from core.models import Producto, Configuracion
from core.management.commands.entrenar_modelo import obtener_serie_diaria, construir_modelo


class Command(BaseCommand):
    help = (
        "Audita los modelos con validación cruzada temporal y guarda el "
        "resultado en Configuracion (clave 'validacion_cruzada_resultado') "
        "para que el dashboard lo muestre en una gráfica interactiva."
    )

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

        resultado = {
            "dias": [int(d) for d in promedio_por_dia.index],
            "mape": [round(float(v), 1) for v in promedio_por_dia.values],
            "productos": len(resumen_por_producto),
            "fecha": timezone.localdate().isoformat(),
        }

        Configuracion.objects.update_or_create(
            clave="validacion_cruzada_resultado",
            defaults={
                "valor": json.dumps(resultado, separators=(",", ":")),
                "descripcion": "Resultado de la última validación cruzada del modelo (JSON, uso interno).",
            },
        )

        self.stdout.write(self.style.SUCCESS(
            f"\nGuardado en Configuracion. {len(resumen_por_producto)} producto(s) validados."
        ))
        for nombre, mape in resumen_por_producto:
            self.stdout.write(f"  {nombre}: {mape}%")