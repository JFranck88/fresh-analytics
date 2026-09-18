"""
Pruebas automatizadas de Fresh Analytics.

Cubren lo que un comité de tesis suele revisar primero: que el control de
acceso por rol funcione exactamente como está documentado (Comprador solo
sus módulos, Gerente solo algunos, Administrador los suyos, superusuario
todo), que el login funcione, y que la lógica de negocio más delicada -el
cálculo de recomendaciones, el guardado del historial de decisiones, la
persistencia de fecha_lectura en alertas, y la edición real de
Configuración- se comporte como se espera. No sustituye una revisión
manual completa, pero cubre las rutas críticas.
"""

import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Producto, Inventario, Merma, Prediccion, Usuario,
    Alerta, Configuracion, DecisionHistorial, Venta,
)
from .riesgo_descomposicion import calcular_riesgo_lote, primer_dia_que_sube_de_nivel


def crear_usuario(correo, rol, password="Clave-Segura-123"):
    return Usuario.objects.create_user(correo=correo, nombre=f"Usuario {rol}", rol=rol, password=password)


def crear_producto(nombre="Leche entera 1L", upc="7501234567890", **extra):
    datos = {
        "nombre": nombre,
        "codigo_upc": upc,
        "categoria": Producto.Categoria.LACTEOS,
        "vida_util_dias": 10,
        "precio_compra": 5.0,
        "precio_venta": 8.0,
        "activo": True,
    }
    datos.update(extra)
    return Producto.objects.create(**datos)


class AutenticacionTests(TestCase):
    """RF-01: solo se entra con credenciales válidas, y la contraseña
    nunca queda en texto plano."""

    def setUp(self):
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)

    def test_login_correcto_redirige_al_dashboard(self):
        respuesta = self.client.post(reverse("login"), {
            "username": "comprador@test.com", "password": "Clave-Segura-123",
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(respuesta.wsgi_request.user.is_authenticated)

    def test_login_con_password_incorrecto_no_autentica(self):
        respuesta = self.client.post(reverse("login"), {
            "username": "comprador@test.com", "password": "clave-equivocada",
        })
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_password_queda_hasheado_no_en_texto_plano(self):
        self.assertNotEqual(self.comprador.password, "Clave-Segura-123")
        self.assertTrue(self.comprador.check_password("Clave-Segura-123"))

    def test_pagina_protegida_sin_login_redirige_a_login(self):
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta.url)

    def test_login_actualiza_last_login_y_se_ve_en_listar_usuarios(self):
        """Usuario no guarda un 'ultimo_acceso' propio (ver migración 0007):
        usa el last_login que Django ya trae y actualiza solo en cada login.
        Antes había un campo separado que nadie escribía nunca y la pantalla
        de Usuarios siempre mostraba 'Nunca', aunque la cuenta sí hubiera
        iniciado sesión."""
        self.assertIsNone(self.comprador.last_login)
        self.client.post(reverse("login"), {
            "username": "comprador@test.com", "password": "Clave-Segura-123",
        })
        self.comprador.refresh_from_db()
        self.assertIsNotNone(self.comprador.last_login)

        # force_login (usado en el resto de la suite) no dispara la señal
        # user_logged_in, así que aquí se inicia sesión real también para
        # el administrador, y así confirmar que la pantalla no muestra
        # "Nunca" para ninguna de las dos cuentas que sí accedieron.
        administrador = crear_usuario("admin.listar@test.com", Usuario.Rol.ADMINISTRADOR)
        self.client.post(reverse("login"), {
            "username": "admin.listar@test.com", "password": "Clave-Segura-123",
        })
        respuesta = self.client.get(reverse("listar_usuarios"))
        self.assertNotContains(respuesta, "Nunca")


class ControlDeAccesoPorRolTests(TestCase):
    """Cada rol debe ver solo lo que le corresponde: Comprador sus módulos
    operativos, Gerente un subconjunto de solo lectura/decisión, y
    Administrador los módulos de gestión - nunca los operativos. El
    superusuario técnico (is_superuser_admin) debe poder entrar a todo,
    sin excepción."""

    # Vistas que NO reciben argumentos de URL, agrupadas por a quién
    # deben permitirle entrar (200) y a quién deben rechazarle (403).
    SOLO_COMPRADOR = ["registrar_merma", "listar_recomendaciones", "generar_orden_compra"]
    COMPRADOR_Y_GERENTE = ["listar_alertas", "listar_mermas", "historial_decisiones", "riesgo_descomposicion"]
    SOLO_ADMINISTRADOR = ["listar_usuarios", "crear_usuario", "listar_configuracion", "mantenimiento"]
    TODOS_LOS_ROLES = ["dashboard", "listar_predicciones", "buscar_global"]

    def setUp(self):
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)
        self.gerente = crear_usuario("gerente@test.com", Usuario.Rol.GERENTE)
        self.administrador = crear_usuario("admin@test.com", Usuario.Rol.ADMINISTRADOR)
        self.superadmin = Usuario.objects.create_superuser(
            correo="super@test.com", nombre="Super Admin", password="Clave-Segura-123"
        )

    def _codigo(self, usuario, url_name):
        self.client.force_login(usuario)
        return self.client.get(reverse(url_name)).status_code

    def test_comprador_entra_a_sus_modulos(self):
        for url_name in self.SOLO_COMPRADOR + self.COMPRADOR_Y_GERENTE + self.TODOS_LOS_ROLES:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.comprador, url_name), 200)

    def test_comprador_no_entra_a_modulos_de_administrador(self):
        for url_name in self.SOLO_ADMINISTRADOR:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.comprador, url_name), 403)

    def test_gerente_entra_solo_a_su_subconjunto(self):
        for url_name in self.COMPRADOR_Y_GERENTE + self.TODOS_LOS_ROLES:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.gerente, url_name), 200)

    def test_gerente_no_entra_a_modulos_de_comprador_ni_administrador(self):
        for url_name in self.SOLO_COMPRADOR + self.SOLO_ADMINISTRADOR:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.gerente, url_name), 403)

    def test_administrador_entra_solo_a_sus_modulos_de_gestion(self):
        for url_name in self.SOLO_ADMINISTRADOR + self.TODOS_LOS_ROLES:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.administrador, url_name), 200)

    def test_administrador_no_entra_a_modulos_operativos(self):
        for url_name in self.SOLO_COMPRADOR + self.COMPRADOR_Y_GERENTE:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.administrador, url_name), 403)

    def test_superadmin_entra_a_absolutamente_todo(self):
        todas = (
            self.SOLO_COMPRADOR + self.COMPRADOR_Y_GERENTE
            + self.SOLO_ADMINISTRADOR + self.TODOS_LOS_ROLES
        )
        for url_name in todas:
            with self.subTest(url_name=url_name):
                self.assertEqual(self._codigo(self.superadmin, url_name), 200)


class RecomendacionesTests(TestCase):
    """RF-06/RF-07: la cantidad sugerida es la predicción de la semana
    menos el stock actual, nunca negativa."""

    def setUp(self):
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)
        self.producto = crear_producto()
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy,
            fecha_pronosticada=hoy, valor_predicho=20, intervalo_inferior=15,
            intervalo_superior=25, precision_modelo=10.0,
        )
        Inventario.objects.create(
            producto=self.producto, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timezone.timedelta(days=5), cantidad=5,
        )
        self.client.force_login(self.comprador)

    def test_sugerido_es_prediccion_menos_stock(self):
        respuesta = self.client.get(reverse("listar_recomendaciones"))
        fila = respuesta.context["recomendaciones"][0]
        self.assertEqual(fila["sugerido"], 15)  # 20 predicho - 5 en stock

    def test_sugerido_nunca_es_negativo(self):
        Inventario.objects.create(
            producto=self.producto, fecha_ingreso=timezone.localdate(),
            fecha_vencimiento=timezone.localdate() + timezone.timedelta(days=5),
            cantidad=100,  # mucho más stock que lo predicho
        )
        respuesta = self.client.get(reverse("listar_recomendaciones"))
        fila = respuesta.context["recomendaciones"][0]
        self.assertEqual(fila["sugerido"], 0)


class HistorialDeDecisionesTests(TestCase):
    """RF-09/UC-05: un ajuste solo se guarda en el historial cuando es
    distinto al sugerido, y cada comprador solo ve sus propias decisiones
    mientras que el gerente ve todas."""

    def setUp(self):
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)
        self.otro_comprador = crear_usuario("otro@test.com", Usuario.Rol.COMPRADOR)
        self.gerente = crear_usuario("gerente@test.com", Usuario.Rol.GERENTE)
        self.producto = crear_producto()
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy,
            fecha_pronosticada=hoy, valor_predicho=20, intervalo_inferior=15,
            intervalo_superior=25, precision_modelo=8.5,
        )

    def test_ajuste_igual_al_sugerido_no_se_guarda(self):
        self.client.force_login(self.comprador)
        self.client.post(reverse("listar_recomendaciones"), {
            f"ajuste_{self.producto.id_producto}": "20",  # igual al sugerido (20 - 0 stock)
        })
        self.assertEqual(DecisionHistorial.objects.count(), 0)

    def test_ajuste_distinto_al_sugerido_si_se_guarda(self):
        self.client.force_login(self.comprador)
        self.client.post(reverse("listar_recomendaciones"), {
            f"ajuste_{self.producto.id_producto}": "30",
        })
        historial = DecisionHistorial.objects.get()
        self.assertEqual(historial.cantidad_sugerida, 20)
        self.assertEqual(historial.cantidad_ajustada, 30)
        self.assertEqual(historial.usuario, self.comprador)

    def test_comprador_solo_ve_su_propio_historial(self):
        DecisionHistorial.objects.create(
            producto=self.producto, usuario=self.comprador,
            fecha_prediccion=timezone.localdate(),
            cantidad_sugerida=20, cantidad_ajustada=30,
        )
        DecisionHistorial.objects.create(
            producto=self.producto, usuario=self.otro_comprador,
            fecha_prediccion=timezone.localdate(),
            cantidad_sugerida=20, cantidad_ajustada=25,
        )
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("historial_decisiones"))
        self.assertEqual(len(respuesta.context["filas"]), 1)

    def test_gerente_ve_el_historial_completo(self):
        DecisionHistorial.objects.create(
            producto=self.producto, usuario=self.comprador,
            fecha_prediccion=timezone.localdate(),
            cantidad_sugerida=20, cantidad_ajustada=30,
        )
        DecisionHistorial.objects.create(
            producto=self.producto, usuario=self.otro_comprador,
            fecha_prediccion=timezone.localdate(),
            cantidad_sugerida=20, cantidad_ajustada=25,
        )
        self.client.force_login(self.gerente)
        respuesta = self.client.get(reverse("historial_decisiones"))
        self.assertEqual(len(respuesta.context["filas"]), 2)


class AlertaFechaLecturaTests(TestCase):
    """Regresión del bug real encontrado esta sesión: fecha_lectura debe
    quedar guardada de verdad al marcar una alerta como leída."""

    def setUp(self):
        self.gerente = crear_usuario("gerente@test.com", Usuario.Rol.GERENTE)
        self.producto = crear_producto()
        self.alerta = Alerta.objects.create(
            producto=self.producto, tipo=Alerta.Tipo.STOCK_BAJO, mensaje="Stock bajo",
        )
        self.client.force_login(self.gerente)

    def test_marcar_leida_persiste_fecha_lectura(self):
        self.assertIsNone(self.alerta.fecha_lectura)
        self.client.post(reverse("marcar_alerta_leida", args=[self.alerta.id_alerta]))
        self.alerta.refresh_from_db()
        self.assertTrue(self.alerta.leida)
        self.assertIsNotNone(self.alerta.fecha_lectura)
        self.assertEqual(self.alerta.usuario_lector, self.gerente)

    def test_marcar_leida_por_get_no_esta_permitido(self):
        """Antes era un <a href>: un simple GET (sin CSRF, sin intención
        real del usuario) bastaba para marcar la alerta como leída. Ahora
        solo se acepta POST, y un GET no debe cambiar nada."""
        respuesta = self.client.get(reverse("marcar_alerta_leida", args=[self.alerta.id_alerta]))
        self.assertEqual(respuesta.status_code, 405)
        self.alerta.refresh_from_db()
        self.assertFalse(self.alerta.leida)
        self.assertIsNone(self.alerta.fecha_lectura)


class ConfiguracionEdicionTests(TestCase):
    """Regresión del fix de esta sesión: editar un parámetro existente
    debe actualizarlo, no fallar por la validación de clave única."""

    def setUp(self):
        self.administrador = crear_usuario("admin@test.com", Usuario.Rol.ADMINISTRADOR)
        self.config = Configuracion.objects.create(
            clave="dias_alerta_vencimiento", valor="3", descripcion="Días antes de vencer",
        )
        self.client.force_login(self.administrador)

    def test_editar_parametro_existente_actualiza_sin_error(self):
        respuesta = self.client.post(reverse("listar_configuracion"), {
            "id_config": self.config.id_config,
            "clave": "dias_alerta_vencimiento",
            "valor": "5",
            "descripcion": "Días antes de vencer",
        })
        self.assertEqual(respuesta.status_code, 302)
        self.config.refresh_from_db()
        self.assertEqual(self.config.valor, "5")
        self.assertEqual(Configuracion.objects.count(), 1)  # no se duplicó

    def test_crear_parametro_con_clave_repetida_falla_validacion(self):
        respuesta = self.client.post(reverse("listar_configuracion"), {
            "clave": "dias_alerta_vencimiento",  # sin id_config: es una creación nueva
            "valor": "9",
            "descripcion": "Duplicado",
        })
        self.assertEqual(respuesta.status_code, 200)  # se re-muestra el formulario con error
        self.assertEqual(Configuracion.objects.count(), 1)  # no se creó un duplicado


class RegistrarMermaTests(TestCase):
    """La merma registrada debe guardar el producto elegido (vía el
    buscador de UPC/descripción, que llena el campo oculto) y calcular
    correctamente el costo de la pérdida (precio_compra * cantidad)."""

    def setUp(self):
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)
        self.producto = crear_producto(precio_compra=5.0)
        self.client.force_login(self.comprador)

    def test_registrar_merma_calcula_costo_perdida(self):
        respuesta = self.client.post(reverse("registrar_merma"), {
            "producto": self.producto.id_producto,
            "fecha": timezone.localdate().isoformat(),
            "cantidad": "3",
            "motivo": Merma.Motivo.VENCIMIENTO,
        })
        self.assertEqual(respuesta.status_code, 302)
        merma = Merma.objects.get()
        self.assertEqual(merma.producto, self.producto)
        self.assertEqual(float(merma.costo_perdida), 15.0)  # 5.0 * 3

    def test_registrar_merma_sin_producto_falla_validacion(self):
        respuesta = self.client.post(reverse("registrar_merma"), {
            "producto": "",
            "fecha": timezone.localdate().isoformat(),
            "cantidad": "3",
            "motivo": Merma.Motivo.VENCIMIENTO,
        })
        self.assertEqual(respuesta.status_code, 200)  # re-muestra el formulario con error
        self.assertEqual(Merma.objects.count(), 0)


class BuscarProductosJsonTests(TestCase):
    """El endpoint que respalda los buscadores de UPC/descripción debe
    filtrar cada campo por separado."""

    def setUp(self):
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)
        self.leche = crear_producto(nombre="Leche entera 1L", upc="7501234567890")
        self.queso = crear_producto(nombre="Queso fresco", upc="7509876543210")
        self.client.force_login(self.comprador)

    def test_filtra_por_upc(self):
        respuesta = self.client.get(reverse("buscar_productos_json"), {"upc": "750123"})
        datos = respuesta.json()
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]["nombre"], "Leche entera 1L")

    def test_filtra_por_descripcion(self):
        respuesta = self.client.get(reverse("buscar_productos_json"), {"q": "queso"})
        datos = respuesta.json()
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]["nombre"], "Queso fresco")

    def test_buscar_por_id_recupera_un_producto_puntual(self):
        respuesta = self.client.get(reverse("buscar_productos_json"), {"id": self.leche.id_producto})
        datos = respuesta.json()
        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]["id"], self.leche.id_producto)


class GestionUsuariosTests(TestCase):
    """Cierra el ciclo de vida de usuarios que antes solo tenía "crear": un
    Administrador debe poder corregir datos, desactivar el acceso de otro
    usuario, y restablecerle la contraseña cuando la olvida (no hay flujo
    de recuperación por correo). Nadie que no sea Administrador (ni
    siquiera el propio usuario) puede llegar a estas vistas."""

    def setUp(self):
        self.administrador = crear_usuario("admin@test.com", Usuario.Rol.ADMINISTRADOR)
        self.comprador = crear_usuario("comprador@test.com", Usuario.Rol.COMPRADOR)
        self.client.force_login(self.administrador)

    def test_administrador_puede_editar_otro_usuario(self):
        respuesta = self.client.post(
            reverse("editar_usuario", args=[self.comprador.id_usuario]),
            {"nombre": "Comprador Renombrado", "correo": self.comprador.correo,
             "rol": Usuario.Rol.GERENTE, "is_active": "on"},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.comprador.refresh_from_db()
        self.assertEqual(self.comprador.nombre, "Comprador Renombrado")
        self.assertEqual(self.comprador.rol, Usuario.Rol.GERENTE)

    def test_desactivar_a_otro_usuario_le_impide_iniciar_sesion(self):
        self.client.post(
            reverse("editar_usuario", args=[self.comprador.id_usuario]),
            {"nombre": self.comprador.nombre, "correo": self.comprador.correo,
             "rol": self.comprador.rol},  # is_active ausente = desmarcado
        )
        self.comprador.refresh_from_db()
        self.assertFalse(self.comprador.is_active)

        self.client.logout()
        respuesta = self.client.post(reverse("login"), {
            "username": "comprador@test.com", "password": "Clave-Segura-123",
        })
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_administrador_no_puede_desactivarse_a_si_mismo(self):
        respuesta = self.client.post(
            reverse("editar_usuario", args=[self.administrador.id_usuario]),
            {"nombre": self.administrador.nombre, "correo": self.administrador.correo,
             "rol": self.administrador.rol},  # is_active ausente = desmarcado
        )
        self.assertEqual(respuesta.status_code, 200)  # re-muestra el formulario con error
        self.administrador.refresh_from_db()
        self.assertTrue(self.administrador.is_active)

    def test_restablecer_password_permite_iniciar_sesion_con_la_nueva(self):
        respuesta = self.client.post(
            reverse("restablecer_password_usuario", args=[self.comprador.id_usuario]),
            {"password1": "Nueva-Clave-456", "password2": "Nueva-Clave-456"},
        )
        self.assertEqual(respuesta.status_code, 302)

        self.client.logout()
        respuesta = self.client.post(reverse("login"), {
            "username": "comprador@test.com", "password": "Nueva-Clave-456",
        })
        self.assertTrue(respuesta.wsgi_request.user.is_authenticated)

    def test_restablecer_password_rechaza_una_contrasena_debil(self):
        respuesta = self.client.post(
            reverse("restablecer_password_usuario", args=[self.comprador.id_usuario]),
            {"password1": "1234", "password2": "1234"},
        )
        self.assertEqual(respuesta.status_code, 200)  # re-muestra el formulario con error
        self.assertTrue(self.comprador.check_password("Clave-Segura-123"))  # sin cambios

    def test_comprador_no_puede_editar_usuarios(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("editar_usuario", args=[self.administrador.id_usuario]))
        self.assertEqual(respuesta.status_code, 403)

    def test_comprador_no_puede_restablecer_passwords(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(
            reverse("restablecer_password_usuario", args=[self.administrador.id_usuario])
        )
        self.assertEqual(respuesta.status_code, 403)


class PaginasDeErrorTests(TestCase):
    """Verifica que los errores 404 y 403 usen las plantillas personalizadas
    de Fresh Analytics en vez de la página genérica de Django - importante
    porque el control de acceso por rol (ver ControlDeAccesoPorRolTests)
    genera 403 reales durante el uso normal del sistema, no solo en casos
    raros. El 500 no se prueba aquí porque forzar un error real de servidor
    en la suite de pruebas no aporta valor frente a la revisión manual."""

    def test_url_inexistente_devuelve_404_personalizado(self):
        respuesta = self.client.get("/esta-url-no-existe-en-el-sistema/")
        self.assertEqual(respuesta.status_code, 404)
        self.assertTemplateUsed(respuesta, "404.html")
        self.assertContains(respuesta, "Página no encontrada", status_code=404)

    def test_acceso_denegado_devuelve_403_personalizado(self):
        comprador = crear_usuario("comprador.403@test.com", Usuario.Rol.COMPRADOR)
        self.client.force_login(comprador)
        respuesta = self.client.get(reverse("listar_usuarios"))
        self.assertEqual(respuesta.status_code, 403)
        self.assertTemplateUsed(respuesta, "403.html")
        self.assertContains(respuesta, "Acceso no autorizado", status_code=403)


class PaginacionTests(TestCase):
    """Mermas e Historial de Decisiones antes se cortaban en silencio con
    [:100]/[:200] sin avisarle al usuario; ahora usan paginación real de
    Django. Verifica que exista una segunda página con el resto de los
    registros, y que los filtros de Historial de Decisiones sobrevivan
    al cambiar de página."""

    def test_listar_mermas_pagina_correctamente(self):
        comprador = crear_usuario("paginacion.comprador@test.com", Usuario.Rol.COMPRADOR)
        producto = crear_producto()
        for i in range(30):
            Merma.objects.create(
                producto=producto, fecha=timezone.localdate() - timedelta(days=i),
                cantidad=1, motivo=Merma.Motivo.VENCIMIENTO, costo_perdida=1,
            )
        self.client.force_login(comprador)

        respuesta = self.client.get(reverse("listar_mermas"))
        self.assertEqual(len(respuesta.context["mermas"]), 25)
        self.assertEqual(respuesta.context["mermas"].paginator.num_pages, 2)

        respuesta_pagina_2 = self.client.get(reverse("listar_mermas"), {"pagina": 2})
        self.assertEqual(len(respuesta_pagina_2.context["mermas"]), 5)

    def test_historial_decisiones_pagina_y_conserva_filtros(self):
        gerente = crear_usuario("paginacion.gerente@test.com", Usuario.Rol.GERENTE)
        producto = crear_producto()
        hoy = timezone.localdate()
        for i in range(30):
            DecisionHistorial.objects.create(
                producto=producto, usuario=gerente,
                fecha_prediccion=hoy - timedelta(days=i),
                cantidad_sugerida=10, cantidad_ajustada=12,
            )
        self.client.force_login(gerente)

        respuesta = self.client.get(reverse("historial_decisiones"), {"producto": producto.id_producto})
        self.assertEqual(len(respuesta.context["filas"]), 25)
        self.assertEqual(respuesta.context["pagina"].paginator.num_pages, 2)

        respuesta_pagina_2 = self.client.get(
            reverse("historial_decisiones"), {"producto": producto.id_producto, "pagina": 2}
        )
        self.assertEqual(len(respuesta_pagina_2.context["filas"]), 5)


class DashboardEtiquetaVencimientoTests(TestCase):
    """El KPI "Por vencer" del dashboard decía "(7 días)" fijo en la
    plantilla, pero el conteo real depende del parámetro configurable
    dias_alerta_vencimiento (el mismo que usa generar_alertas.py, default
    3). La etiqueta ahora debe mostrar el valor configurado de verdad."""

    def test_dashboard_muestra_dias_configurados_en_vez_de_7_fijo(self):
        comprador = crear_usuario("vencimiento.comprador@test.com", Usuario.Rol.COMPRADOR)
        Configuracion.objects.create(
            clave="dias_alerta_vencimiento", valor="5", descripcion="Días antes de vencer",
        )
        self.client.force_login(comprador)

        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["dias_alerta_vencimiento"], 5)
        self.assertContains(respuesta, "Por vencer (5 días)")
        self.assertNotContains(respuesta, "Por vencer (7 días)")

    def test_dashboard_usa_default_3_si_no_hay_parametro_configurado(self):
        comprador = crear_usuario("vencimiento.default@test.com", Usuario.Rol.COMPRADOR)
        self.client.force_login(comprador)

        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["dias_alerta_vencimiento"], 3)


class BuscarGlobalTests(TestCase):
    """El buscador del sidebar cortaba los resultados en [:15] sin avisar
    - si había 20 productos que coincidían, el usuario solo veía 15 y
    nunca sabía que faltaban 5. Ahora se cuenta el total real y se avisa
    en la página cuando el resultado se recortó."""

    def setUp(self):
        self.comprador = crear_usuario("buscador.comprador@test.com", Usuario.Rol.COMPRADOR)
        for i in range(20):
            crear_producto(nombre=f"Producto buscable {i:02d}", upc=f"999000000{i:04d}")
        self.client.force_login(self.comprador)

    def test_avisa_cuando_hay_mas_productos_de_los_mostrados(self):
        respuesta = self.client.get(reverse("buscar_global"), {"q": "buscable"})
        self.assertEqual(len(respuesta.context["productos"]), 15)
        self.assertEqual(respuesta.context["productos_total"], 20)
        self.assertContains(respuesta, "Mostrando 15 de 20 productos")

    def test_no_avisa_cuando_todos_los_resultados_caben(self):
        respuesta = self.client.get(reverse("buscar_global"), {"q": "buscable 0"})
        self.assertEqual(respuesta.context["productos_total"], 10)  # 00..09
        self.assertNotContains(respuesta, "refina tu búsqueda para ver menos resultados")


class BuscarGlobalJsonTests(TestCase):
    """El buscador de la topbar (base.html) antes solo funcionaba como un
    <form> normal: había que presionar Enter para ver cualquier resultado.
    Ahora consulta este endpoint mientras se escribe, para mostrar
    sugerencias en un dropdown sin recargar la página."""

    def setUp(self):
        self.producto = crear_producto(nombre="Yogur natural", upc="7501111111111")
        self.comprador = crear_usuario("json.comprador@test.com", Usuario.Rol.COMPRADOR)
        self.administrador = crear_usuario("json.admin@test.com", Usuario.Rol.ADMINISTRADOR)
        Alerta.objects.create(
            producto=self.producto, tipo=Alerta.Tipo.STOCK_BAJO, mensaje="Stock bajo de yogur",
        )

    def test_sin_consulta_devuelve_todo_vacio(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("buscar_global_json"))
        datos = respuesta.json()
        self.assertEqual(datos["total"], 0)
        self.assertEqual(datos["productos"], [])

    def test_comprador_ve_productos_y_alertas(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("buscar_global_json"), {"q": "yogur"})
        datos = respuesta.json()
        self.assertEqual(len(datos["productos"]), 1)
        self.assertEqual(datos["productos"][0]["nombre"], "Yogur natural")
        self.assertEqual(len(datos["alertas"]), 1)

    def test_alerta_incluye_el_id_del_producto_para_poder_filtrar(self):
        # El resultado de tipo alerta debe traer el id del producto, para
        # que el clic en la topbar lleve al MISMO destino que un
        # resultado de producto (Predicciones filtrado a ese producto) en
        # vez de sacar a quien busca del módulo en el que estaba y
        # llevarlo al módulo de Alertas (bug real reportado por Francisco
        # - ver BuscarGlobalRedireccionDeAlertasTests).
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("buscar_global_json"), {"q": "yogur"})
        datos = respuesta.json()
        self.assertEqual(datos["alertas"][0]["producto_id"], self.producto.id_producto)

    def test_administrador_no_ve_alertas_ni_lotes(self):
        """Misma regla de visibilidad que la página completa /buscar/:
        Administrador no ve lo operativo de Gerente/Comprador."""
        self.client.force_login(self.administrador)
        respuesta = self.client.get(reverse("buscar_global_json"), {"q": "yogur"})
        datos = respuesta.json()
        self.assertEqual(len(datos["productos"]), 1)
        self.assertEqual(datos["alertas"], [])
        self.assertEqual(datos["lotes"], [])


class BuscarGlobalRedireccionDeAlertasTests(TestCase):
    """Bug real reportado por Francisco (con capturas): buscando desde
    Predicciones (el buscador de la topbar es el mismo en todas las
    pantallas), un resultado de tipo alerta lo sacaba del módulo en el
    que estaba y lo mandaba al módulo de Alertas - "creo que lógicamente
    no está bien", en sus palabras, porque para él un resultado de
    alerta y uno de producto sobre lo mismo (ej. "leche") son igual de
    válidos, y no esperaba que uno lo cambiara de módulo. Un primer
    intento filtró Alertas por producto, pero seguía siendo un cambio de
    módulo - la corrección real es que el resultado de alerta lleve al
    MISMO destino que el de producto (Predicciones filtrado a ese
    producto), nunca a Alertas."""

    def setUp(self):
        self.gerente = crear_usuario("redireccion.gerente@test.com", Usuario.Rol.GERENTE)
        self.leche = crear_producto(nombre="Leche entera 1L", upc="7503333333333")
        Alerta.objects.create(
            producto=self.leche, tipo=Alerta.Tipo.STOCK_BAJO, mensaje="Stock bajo de leche",
        )
        self.client.force_login(self.gerente)

    def test_resultado_de_alerta_en_la_pagina_completa_lleva_a_predicciones(self):
        respuesta = self.client.get(reverse("buscar_global"), {"q": "leche"})
        # El link del resultado de alerta (sección "Alertas activas" de
        # /buscar/) debe ir a Predicciones filtrado a este producto - no
        # a Alertas. La barra lateral sí tiene su propio link fijo a
        # Alertas (/alertas/, sin filtro), así que no se puede afirmar
        # "el href /alertas/ no aparece en ningún lado de la página" -
        # se verifica específicamente que exista el link correcto.
        url_esperada = f"{reverse('listar_predicciones')}?producto={self.leche.id_producto}"
        self.assertContains(respuesta, f'href="{url_esperada}"', count=2)


class AsignarUpcDemoTests(TestCase):
    """Comando de gestión que rellena codigo_upc con un código de demo
    (departamento-categoría-correlativo) para que la demo no muestre UPC
    en blanco, sin tocar los productos que ya tienen uno real."""

    def setUp(self):
        self.sin_upc = crear_producto(nombre="Producto sin UPC", upc=None)
        self.con_upc = crear_producto(
            nombre="Producto con UPC real", upc="7501234500000",
            categoria=Producto.Categoria.CARNES,
        )

    def _correr(self, *args):
        salida = StringIO()
        call_command("asignar_upc_demo", *args, stdout=salida)
        return salida.getvalue()

    def test_asigna_codigo_con_formato_departamento_categoria_correlativo(self):
        self._correr()
        self.sin_upc.refresh_from_db()
        self.assertEqual(self.sin_upc.codigo_upc, "0101100")

    def test_no_toca_productos_que_ya_tienen_upc(self):
        self._correr()
        self.con_upc.refresh_from_db()
        self.assertEqual(self.con_upc.codigo_upc, "7501234500000")

    def test_forzar_reasigna_incluso_si_ya_tenia_upc(self):
        self._correr("--forzar")
        self.con_upc.refresh_from_db()
        self.assertEqual(self.con_upc.codigo_upc, "0102100")

    def test_correr_dos_veces_sin_forzar_no_cambia_nada(self):
        self._correr()
        self.sin_upc.refresh_from_db()
        primer_codigo = self.sin_upc.codigo_upc
        self._correr()
        self.sin_upc.refresh_from_db()
        self.assertEqual(self.sin_upc.codigo_upc, primer_codigo)


class DashboardKpisTests(TestCase):
    """Dos tarjetas del dashboard reemplazadas a petición de Francisco
    (2026-09-18): "Ventas de hoy" (dato real) siempre mostraba Q0,00
    porque el job nocturno de datos sintéticos nunca genera ventas del
    día en curso, solo hasta ayer - y "Precisión del modelo" (MAPE) es
    una métrica que no le sirve al usuario final para decidir nada. Se
    reemplazaron por "Venta esperada hoy" (pronóstico del propio modelo
    para hoy, en Q) y "Productos a reabastecer hoy" (cuántos productos
    tienen sugerido > 0 en Recomendaciones, misma fórmula factorizada en
    calcular_cantidad_sugerida)."""

    def setUp(self):
        self.comprador = crear_usuario("kpis.comprador@test.com", Usuario.Rol.COMPRADOR)
        self.producto = crear_producto(precio_venta=8.0)
        self.producto2 = crear_producto(
            nombre="Yogurt natural", upc="7501234500099", precio_venta=5.0,
        )

    def test_venta_esperada_hoy_suma_el_pronostico_de_hoy_de_todo_el_catalogo(self):
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
        )
        Prediccion.objects.create(
            producto=self.producto2, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=4, intervalo_inferior=2, intervalo_superior=6,
        )
        # Otro día del mismo pronóstico - no debe contarse en "hoy".
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy + timedelta(days=1),
            valor_predicho=999, intervalo_inferior=1, intervalo_superior=1,
        )
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        # 10*8.0 + 4*5.0 = 100.00
        self.assertEqual(respuesta.context["venta_esperada_hoy"], 100.0)
        self.assertContains(respuesta, "100,00")

    def test_venta_esperada_hoy_ignora_corridas_anteriores(self):
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy - timedelta(days=1), fecha_pronosticada=hoy,
            valor_predicho=999, intervalo_inferior=1, intervalo_superior=1,
        )
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
        )
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["venta_esperada_hoy"], 80.0)

    def test_venta_esperada_hoy_es_cero_sin_predicciones(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["venta_esperada_hoy"], 0)

    def test_productos_a_reabastecer_cuenta_solo_los_que_necesitan_reabasto(self):
        hoy = timezone.localdate()
        # producto: pronóstico 20, stock 5 -> sugerido 15 (cuenta)
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=20, intervalo_inferior=10, intervalo_superior=30,
        )
        Inventario.objects.create(
            producto=self.producto, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=5), cantidad=5,
        )
        # producto2: pronóstico 5, stock 10 -> sugerido 0 (no cuenta)
        Prediccion.objects.create(
            producto=self.producto2, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=5, intervalo_inferior=2, intervalo_superior=8,
        )
        Inventario.objects.create(
            producto=self.producto2, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=5), cantidad=10,
        )
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["productos_a_reabastecer_hoy"], 1)
        self.assertContains(respuesta, "Productos a reabastecer hoy")

    def test_productos_a_reabastecer_es_cero_sin_predicciones(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["productos_a_reabastecer_hoy"], 0)


class DashboardKpisClickablesTests(TestCase):
    """Las tarjetas "Por vencer" y "Productos a reabastecer hoy" del
    dashboard deben llevar a la pantalla con el detalle (Alertas y
    Recomendaciones) SOLO para los roles que de verdad tienen acceso a
    esa pantalla - petición de Francisco (2026-09-18) de hacerlas
    clicables, sin mandar a un rol sin permiso a un 403."""

    def setUp(self):
        self.comprador = crear_usuario("clic.comprador@test.com", Usuario.Rol.COMPRADOR)
        self.gerente = crear_usuario("clic.gerente@test.com", Usuario.Rol.GERENTE)
        self.administrador = crear_usuario("clic.admin@test.com", Usuario.Rol.ADMINISTRADOR)

    def test_comprador_ve_los_dos_enlaces(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertContains(respuesta, reverse("listar_alertas") + "?tipo=VENCIMIENTO")
        self.assertContains(respuesta, reverse("listar_recomendaciones") + "?pendientes=1")

    def test_gerente_ve_solo_el_enlace_de_alertas(self):
        # Gerente entra a Alertas pero no a Recomendaciones (no ajusta
        # cantidades - ver matriz de control de acceso).
        self.client.force_login(self.gerente)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertContains(respuesta, reverse("listar_alertas") + "?tipo=VENCIMIENTO")
        self.assertNotContains(respuesta, reverse("listar_recomendaciones") + "?pendientes=1")

    def test_administrador_no_ve_ningun_enlace(self):
        # Administrador no entra ni a Alertas ni a Recomendaciones -
        # ambas tarjetas deben quedar como texto plano, no como link.
        self.client.force_login(self.administrador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertNotContains(respuesta, reverse("listar_alertas") + "?tipo=VENCIMIENTO")
        self.assertNotContains(respuesta, reverse("listar_recomendaciones") + "?pendientes=1")


class ListarAlertasFiltroPorTipoTests(TestCase):
    """?tipo=VENCIMIENTO en Alertas - agregado para que la tarjeta "Por
    vencer" del dashboard lleve directo a esas alertas."""

    def setUp(self):
        self.gerente = crear_usuario("filtro.gerente@test.com", Usuario.Rol.GERENTE)
        self.producto = crear_producto()
        Alerta.objects.create(
            producto=self.producto, tipo=Alerta.Tipo.VENCIMIENTO, mensaje="Vence pronto",
        )
        Alerta.objects.create(
            producto=self.producto, tipo=Alerta.Tipo.STOCK_BAJO, mensaje="Stock bajo",
        )
        self.client.force_login(self.gerente)

    def test_filtra_solo_el_tipo_pedido(self):
        respuesta = self.client.get(reverse("listar_alertas"), {"tipo": "VENCIMIENTO"})
        self.assertEqual(len(respuesta.context["alertas"]), 1)
        self.assertContains(respuesta, "Vence pronto")
        self.assertNotContains(respuesta, "Stock bajo")

    def test_sin_filtro_muestra_todas(self):
        respuesta = self.client.get(reverse("listar_alertas"))
        self.assertEqual(len(respuesta.context["alertas"]), 2)

    def test_tipo_invalido_se_ignora(self):
        respuesta = self.client.get(reverse("listar_alertas"), {"tipo": "NO_EXISTE"})
        self.assertEqual(len(respuesta.context["alertas"]), 2)
        self.assertIsNone(respuesta.context["tipo_filtro"])


class ListarRecomendacionesPendientesTests(TestCase):
    """?pendientes=1 en Recomendaciones - agregado para que la tarjeta
    "Productos a reabastecer hoy" del dashboard lleve directo a la lista
    filtrada a esos productos."""

    def setUp(self):
        self.comprador = crear_usuario("pendientes.comprador@test.com", Usuario.Rol.COMPRADOR)
        hoy = timezone.localdate()
        self.necesita_reabasto = crear_producto(nombre="Lechuga", upc="7501234500098")
        self.no_necesita = crear_producto(nombre="Aguacate hass", upc="7501234500097")
        Prediccion.objects.create(
            producto=self.necesita_reabasto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=20, intervalo_inferior=10, intervalo_superior=30,
        )
        Inventario.objects.create(
            producto=self.necesita_reabasto, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=5), cantidad=5,
        )
        Prediccion.objects.create(
            producto=self.no_necesita, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=5, intervalo_inferior=2, intervalo_superior=8,
        )
        Inventario.objects.create(
            producto=self.no_necesita, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=5), cantidad=10,
        )
        self.client.force_login(self.comprador)

    def test_filtra_solo_los_que_necesitan_reabasto(self):
        respuesta = self.client.get(reverse("listar_recomendaciones"), {"pendientes": "1"})
        self.assertEqual(len(respuesta.context["recomendaciones"]), 1)
        self.assertContains(respuesta, "Lechuga")
        self.assertNotContains(respuesta, "Aguacate hass")

    def test_sin_filtro_muestra_todos(self):
        respuesta = self.client.get(reverse("listar_recomendaciones"))
        self.assertEqual(len(respuesta.context["recomendaciones"]), 2)


class ListarPrediccionesTests(TestCase):
    """La gráfica "Qué tan confiable es el modelo" (validación cruzada
    de auditar_modelo, agregada sobre TODO el catálogo) se sacó de esta
    pantalla: no es parte del diseño de interfaz documentado para
    Predicciones, y mostraba un promedio de todos los productos aunque
    la pantalla ya está mostrando un producto puntual - la precisión de
    ESE producto sigue en la columna "Margen de error" de la tabla."""

    def test_ya_no_muestra_la_grafica_de_validacion_cruzada(self):
        comprador = crear_usuario("prediccion.comprador@test.com", Usuario.Rol.COMPRADOR)
        Configuracion.objects.create(
            clave="validacion_cruzada_resultado",
            valor='{"dias": [1], "mape": [12.0], "productos": 1, "fecha": "2026-01-01"}',
            descripcion="Resultado de auditar_modelo (uso interno, ya no se muestra en pantalla).",
        )
        self.client.force_login(comprador)
        respuesta = self.client.get(reverse("listar_predicciones"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, "Qué tan confiable es el modelo")
        self.assertNotIn("validacion_json", respuesta.context)


class PrediccionesDetalleDiaTests(TestCase):
    """Petición de Francisco: al hacer clic en un punto de la gráfica de
    Predicciones, ver el pronóstico del tiempo de ESE día, más un aviso
    si además cae en quincena o en fin de mes. `info_dias_json` (por
    fecha ISO) es lo que la plantilla usa para armar ese detalle - estas
    pruebas cubren el cálculo del lado del servidor, no el clic en sí
    (eso es JS puro, sin lógica de negocio que probar aquí)."""

    def setUp(self):
        self.comprador = crear_usuario("detalle.comprador@test.com", Usuario.Rol.COMPRADOR)
        self.producto = crear_producto()
        self.client.force_login(self.comprador)

    def _info_dias(self, respuesta):
        return json.loads(respuesta.context["info_dias_json"])

    def _crear_prediccion(self, fecha_pronosticada):
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=timezone.localdate(),
            fecha_pronosticada=fecha_pronosticada,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
        )

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    @patch("core.views.pronostico_lluvia_real", return_value={})
    def test_dia_15_se_marca_como_quincena(self, _mock_lluvia, _mock_clima):
        fecha = date(2026, 9, 15)
        self._crear_prediccion(fecha)
        respuesta = self.client.get(reverse("listar_predicciones"))
        info = self._info_dias(respuesta)[fecha.isoformat()]
        self.assertTrue(info["es_quincena"])
        self.assertFalse(info["es_fin_de_mes"])

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    @patch("core.views.pronostico_lluvia_real", return_value={})
    def test_dia_30_se_marca_como_fin_de_mes(self, _mock_lluvia, _mock_clima):
        fecha = date(2026, 9, 30)
        self._crear_prediccion(fecha)
        respuesta = self.client.get(reverse("listar_predicciones"))
        info = self._info_dias(respuesta)[fecha.isoformat()]
        self.assertTrue(info["es_fin_de_mes"])
        self.assertFalse(info["es_quincena"])

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    @patch("core.views.pronostico_lluvia_real", return_value={})
    def test_dia_normal_no_es_ni_quincena_ni_fin_de_mes(self, _mock_lluvia, _mock_clima):
        fecha = date(2026, 9, 10)
        self._crear_prediccion(fecha)
        respuesta = self.client.get(reverse("listar_predicciones"))
        info = self._info_dias(respuesta)[fecha.isoformat()]
        self.assertFalse(info["es_quincena"])
        self.assertFalse(info["es_fin_de_mes"])

    def test_dia_con_clima_disponible_incluye_temperatura_humedad_y_lluvia(self):
        fecha = date(2026, 9, 10)
        self._crear_prediccion(fecha)
        with patch(
            "core.views.pronostico_temperatura_humedad_por_dia",
            return_value={fecha: {"temp_max": 29.0, "humedad_promedio": 70.0}},
        ), patch("core.views.pronostico_lluvia_real", return_value={fecha: 0.6}):
            respuesta = self.client.get(reverse("listar_predicciones"))
        info = self._info_dias(respuesta)[fecha.isoformat()]
        self.assertTrue(info["clima_disponible"])
        self.assertEqual(info["temp_max"], 29.0)
        self.assertEqual(info["humedad_promedio"], 70.0)
        self.assertEqual(info["prob_lluvia"], 60)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    @patch("core.views.pronostico_lluvia_real", return_value={})
    def test_dia_fuera_del_rango_del_pronostico_avisa_que_no_hay_clima(self, _mock_lluvia, _mock_clima):
        # El plan gratuito de OpenWeatherMap solo cubre ~5 días - un día
        # fuera de ese rango (los últimos de los 7 que muestra la
        # gráfica) simplemente no aparece en ninguno de los dos
        # diccionarios de clima.
        fecha = date(2026, 9, 10)
        self._crear_prediccion(fecha)
        respuesta = self.client.get(reverse("listar_predicciones"))
        info = self._info_dias(respuesta)[fecha.isoformat()]
        self.assertFalse(info["clima_disponible"])
        self.assertIsNone(info["temp_max"])
        self.assertIsNone(info["humedad_promedio"])
        self.assertIsNone(info["prob_lluvia"])

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    @patch("core.views.pronostico_lluvia_real", return_value={})
    def test_fechas_de_la_grafica_alinean_con_las_etiquetas(self, _mock_lluvia, _mock_clima):
        fecha = date(2026, 9, 10)
        self._crear_prediccion(fecha)
        respuesta = self.client.get(reverse("listar_predicciones"))
        datos_grafica = json.loads(respuesta.context["datos_grafica_json"])
        pid = str(self.producto.id_producto)
        self.assertEqual(datos_grafica[pid]["fechas"], [fecha.isoformat()])
        self.assertEqual(len(datos_grafica[pid]["fechas"]), len(datos_grafica[pid]["labels"]))


class PrediccionesDepartamentoTests(TestCase):
    """Requerimiento de Francisco (2026-09-18): al entrar a Predicciones
    sin haber buscado nada todavía, la primera gráfica es la del
    DEPARTAMENTO completo, en quetzales (no se pueden sumar kg con
    unidades entre productos de categorías distintas) - mismo criterio
    que "Venta esperada hoy" del dashboard."""

    def setUp(self):
        self.comprador = crear_usuario("depto.comprador@test.com", Usuario.Rol.COMPRADOR)
        self.producto1 = crear_producto(precio_venta=8.0)
        self.producto2 = crear_producto(
            nombre="Yogurt natural", upc="7501234500199", precio_venta=5.0,
        )
        self.client.force_login(self.comprador)

    def _datos_departamento(self, respuesta):
        return json.loads(respuesta.context["datos_departamento_json"])

    def test_suma_el_valor_de_todo_el_catalogo_en_quetzales(self):
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto1, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
        )
        Prediccion.objects.create(
            producto=self.producto2, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=4, intervalo_inferior=2, intervalo_superior=6,
        )
        respuesta = self.client.get(reverse("listar_predicciones"))
        datos = self._datos_departamento(respuesta)
        indice = datos["fechas"].index(hoy.isoformat())
        # 10*8.0 + 4*5.0 = 100.00
        self.assertEqual(datos["predicho"][indice], 100.0)
        # (5*8.0)+(2*5.0)=50.0 ; (15*8.0)+(6*5.0)=150.0
        self.assertEqual(datos["inferior"][indice], 50.0)
        self.assertEqual(datos["superior"][indice], 150.0)

    def test_ignora_corridas_anteriores_a_la_mas_reciente(self):
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto1, fecha_prediccion=hoy - timedelta(days=1), fecha_pronosticada=hoy,
            valor_predicho=999, intervalo_inferior=1, intervalo_superior=1,
        )
        Prediccion.objects.create(
            producto=self.producto1, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
        )
        respuesta = self.client.get(reverse("listar_predicciones"))
        datos = self._datos_departamento(respuesta)
        self.assertEqual(datos["predicho"], [80.0])

    def test_vacio_sin_predicciones(self):
        respuesta = self.client.get(reverse("listar_predicciones"))
        datos = self._datos_departamento(respuesta)
        self.assertEqual(datos["labels"], [])
        self.assertEqual(datos["predicho"], [])

    def test_pantalla_muestra_el_titulo_de_departamento_por_defecto(self):
        respuesta = self.client.get(reverse("listar_predicciones"))
        self.assertContains(respuesta, "Tendencia de predicción del Departamento completo")


class TendenciaAprendizajeModeloTests(TestCase):
    """Petición del asesor de tesis (2026-09-18): en Predicciones, no solo
    mostrar lo que el modelo pronostica, también cómo va aprendiendo con
    más historial - MAPE por corrida en el tiempo, y lo pronosticado un
    día antes contra la venta real, día a día. Pruebas directas de
    `calcular_tendencia_aprendizaje` (core/views.py), sin pasar por la
    vista completa, para aislar el cálculo de la plantilla."""

    def setUp(self):
        self.producto = crear_producto(precio_venta=8.0)

    def test_precision_por_producto_en_el_tiempo(self):
        from .views import calcular_tendencia_aprendizaje

        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        anteayer = hoy - timedelta(days=2)
        for fecha, mape in [(anteayer, 20.0), (ayer, 15.0)]:
            Prediccion.objects.create(
                producto=self.producto, fecha_prediccion=fecha, fecha_pronosticada=fecha,
                valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
                precision_modelo=mape,
            )
        resultado = calcular_tendencia_aprendizaje(hoy)
        pid = str(self.producto.id_producto)
        self.assertEqual(resultado[pid]["precision"]["valores"], [20.0, 15.0])

    def test_ignora_corridas_sin_mape(self):
        from .views import calcular_tendencia_aprendizaje

        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
            precision_modelo=None,
        )
        resultado = calcular_tendencia_aprendizaje(hoy)
        pid = str(self.producto.id_producto)
        self.assertEqual(resultado[pid]["precision"]["valores"], [])

    def test_predicho_vs_real_usa_solo_la_corrida_de_un_dia_antes(self):
        from .views import calcular_tendencia_aprendizaje

        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        anteayer = hoy - timedelta(days=2)
        # Corrida de "un día antes" para el día de ayer: debe ser la que se usa.
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=anteayer, fecha_pronosticada=ayer,
            valor_predicho=12, intervalo_inferior=6, intervalo_superior=18,
        )
        # Corrida con más anticipación para el MISMO día - no debe usarse.
        Prediccion.objects.create(
            producto=self.producto,
            fecha_prediccion=hoy - timedelta(days=5), fecha_pronosticada=ayer,
            valor_predicho=999, intervalo_inferior=1, intervalo_superior=1,
        )
        Venta.objects.create(
            producto=self.producto, fecha=timezone.now() - timedelta(days=1),
            cantidad=10, precio_unitario=8.0,
        )
        resultado = calcular_tendencia_aprendizaje(hoy)
        pid = str(self.producto.id_producto)
        indice = resultado[pid]["predicho_real"]["labels"].index(ayer.strftime("%d/%m"))
        self.assertEqual(resultado[pid]["predicho_real"]["predicho"][indice], 12)
        self.assertEqual(resultado[pid]["predicho_real"]["real"][indice], 10.0)

    def test_departamento_agrega_en_quetzales(self):
        from .views import calcular_tendencia_aprendizaje

        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        anteayer = hoy - timedelta(days=2)
        producto2 = crear_producto(
            nombre="Yogurt natural", upc="7501234500299", precio_venta=5.0,
        )
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=anteayer, fecha_pronosticada=ayer,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
        )
        Prediccion.objects.create(
            producto=producto2, fecha_prediccion=anteayer, fecha_pronosticada=ayer,
            valor_predicho=4, intervalo_inferior=2, intervalo_superior=6,
        )
        resultado = calcular_tendencia_aprendizaje(hoy)
        indice = resultado["departamento"]["predicho_real"]["labels"].index(ayer.strftime("%d/%m"))
        # 10*8.0 + 4*5.0 = 100.0
        self.assertEqual(resultado["departamento"]["predicho_real"]["predicho"][indice], 100.0)

    def test_sin_historial_devuelve_series_vacias_sin_error(self):
        from .views import calcular_tendencia_aprendizaje

        resultado = calcular_tendencia_aprendizaje(timezone.localdate())
        pid = str(self.producto.id_producto)
        self.assertEqual(resultado[pid]["precision"], {"labels": [], "valores": []})
        self.assertEqual(
            resultado[pid]["predicho_real"], {"labels": [], "predicho": [], "real": []}
        )
        self.assertIn("departamento", resultado)


class CalcularRiesgoLoteTests(TestCase):
    """Pruebas puras del cálculo de riesgo climático de descomposición
    (core/riesgo_descomposicion.py) - no dependen de la base de datos ni
    de la API de clima, solo de la función y sus umbrales.

    Corrección de diseño (reportada por Francisco en producción): el
    cálculo ya no usa días absolutos en exhibición, sino el PORCENTAJE de
    la vida útil propia del producto (vida_util_dias) ya consumido - así
    un producto que dura poco y uno que dura mucho no quedan con el mismo
    puntaje solo por llevar los mismos días en el mueble."""

    def test_pocos_dias_relativos_a_su_vida_util_es_riesgo_bajo(self):
        # 1 de 10 días de vida útil = 10% -> no llega ni al primer umbral.
        riesgo = calcular_riesgo_lote(dias_en_exhibicion=1, vida_util_dias=10)
        self.assertEqual(riesgo["nivel"], "BAJO")
        self.assertEqual(riesgo["puntaje"], 0)
        self.assertEqual(riesgo["porcentaje_vida_util"], 10)

    def test_mayor_porcentaje_de_vida_util_consumida_sube_el_riesgo(self):
        riesgo_10pct = calcular_riesgo_lote(dias_en_exhibicion=1, vida_util_dias=10)
        riesgo_50pct = calcular_riesgo_lote(dias_en_exhibicion=5, vida_util_dias=10)
        riesgo_80pct = calcular_riesgo_lote(dias_en_exhibicion=8, vida_util_dias=10)
        self.assertLess(riesgo_10pct["puntaje"], riesgo_50pct["puntaje"])
        self.assertLess(riesgo_50pct["puntaje"], riesgo_80pct["puntaje"])

    def test_mismos_dias_pero_distinta_vida_util_dan_distinto_riesgo(self):
        # Este es justo el problema que se corrigió: un producto que dura
        # poco (ej. un tomate, 5 días) y uno que dura mucho (ej. una
        # manzana, 15 días) NO deben quedar con el mismo puntaje solo por
        # llevar los mismos 6 días en exhibición - el tomate ya está casi
        # al final de su vida útil, la manzana apenas va empezando.
        tomate_6_dias = calcular_riesgo_lote(dias_en_exhibicion=6, vida_util_dias=5)
        manzana_6_dias = calcular_riesgo_lote(dias_en_exhibicion=6, vida_util_dias=15)
        self.assertGreater(tomate_6_dias["puntaje"], manzana_6_dias["puntaje"])
        # Tomate: 6/5 = 120% de su vida útil -> tope de la tabla (45 pts,
        # MEDIO). Manzana: 6/15 = 40% -> apenas 15 pts, BAJO.
        self.assertEqual(tomate_6_dias["nivel"], "MEDIO")
        self.assertEqual(manzana_6_dias["nivel"], "BAJO")

    def test_temperatura_alta_suma_puntos(self):
        sin_clima = calcular_riesgo_lote(dias_en_exhibicion=1, vida_util_dias=10)
        con_calor = calcular_riesgo_lote(dias_en_exhibicion=1, vida_util_dias=10, temp_max=32)
        self.assertGreater(con_calor["puntaje"], sin_clima["puntaje"])
        self.assertEqual(con_calor["factores"]["temperatura"], 25)

    def test_humedad_alta_suma_puntos(self):
        sin_clima = calcular_riesgo_lote(dias_en_exhibicion=1, vida_util_dias=10)
        con_humedad = calcular_riesgo_lote(dias_en_exhibicion=1, vida_util_dias=10, humedad_promedio=85)
        self.assertGreater(con_humedad["puntaje"], sin_clima["puntaje"])
        self.assertEqual(con_humedad["factores"]["humedad"], 20)

    def test_combinacion_de_variables_da_riesgo_alto(self):
        # 9 de 10 días = 90% de vida útil consumida (45 pts) + calor (25)
        # + humedad (20) = 90 -> ALTO.
        riesgo = calcular_riesgo_lote(
            dias_en_exhibicion=9, vida_util_dias=10, temp_max=32, humedad_promedio=85,
        )
        self.assertEqual(riesgo["nivel"], "ALTO")
        self.assertEqual(riesgo["badge"], "danger")

    def test_puntaje_nunca_pasa_de_cien(self):
        # El máximo posible sumando las tres tablas de puntos (45+25+20=90)
        # ya no llega a 100 - el tope solo es un colchón de seguridad para
        # si en el futuro se ajustan los umbrales hacia arriba. Se prueba
        # directamente contra ese máximo real de hoy. Un lote muy por
        # encima del 100% de su vida útil (ya debería estar descartado por
        # fecha_vencimiento, pero se prueba el cálculo de forma aislada)
        # sigue topando en 90, no sigue subiendo.
        riesgo = calcular_riesgo_lote(
            dias_en_exhibicion=30, vida_util_dias=10, temp_max=40, humedad_promedio=100,
        )
        self.assertEqual(riesgo["puntaje"], 90)
        self.assertLessEqual(riesgo["puntaje"], 100)

    def test_clima_ausente_no_rompe_el_calculo(self):
        # Cuando no hay API key o la consulta falla, clima.py devuelve
        # (None, None) - el cálculo debe seguir funcionando solo con el
        # porcentaje de vida útil, sin lanzar ningún error.
        riesgo = calcular_riesgo_lote(
            dias_en_exhibicion=5, vida_util_dias=10, temp_max=None, humedad_promedio=None,
        )
        self.assertEqual(riesgo["factores"]["temperatura"], 0)
        self.assertEqual(riesgo["factores"]["humedad"], 0)

    def test_vida_util_en_cero_no_lanza_error(self):
        # No debería pasar en la práctica (vida_util_dias es obligatorio
        # en Producto), pero el cálculo no debe reventar con división
        # entre cero - se trata como "ya al límite de su vida útil".
        riesgo = calcular_riesgo_lote(dias_en_exhibicion=3, vida_util_dias=0)
        self.assertEqual(riesgo["porcentaje_vida_util"], 100)
        # 100% sin clima = 45 pts (tope de la tabla de porcentaje) -> MEDIO.
        self.assertEqual(riesgo["nivel"], "MEDIO")


class PrimerDiaQueSubeDeNivelTests(TestCase):
    """Pruebas puras del aviso de proyección (petición de Francisco:
    avisar solo cuando el riesgo va a subir de nivel, sin mostrar una
    tabla completa día por día)."""

    def test_encuentra_el_primer_dia_que_sube(self):
        dias = [
            {"fecha": "lunes", "nivel": "BAJO"},
            {"fecha": "martes", "nivel": "MEDIO"},
            {"fecha": "miércoles", "nivel": "ALTO"},
        ]
        resultado = primer_dia_que_sube_de_nivel("BAJO", dias)
        self.assertEqual(resultado["fecha"], "martes")

    def test_ninguno_sube_devuelve_none(self):
        dias = [{"fecha": "martes", "nivel": "BAJO"}, {"fecha": "miércoles", "nivel": "BAJO"}]
        self.assertIsNone(primer_dia_que_sube_de_nivel("MEDIO", dias))

    def test_bajar_de_nivel_no_cuenta_como_subida(self):
        dias = [{"fecha": "martes", "nivel": "BAJO"}]
        self.assertIsNone(primer_dia_que_sube_de_nivel("MEDIO", dias))

    def test_ya_en_alto_nunca_avisa(self):
        # Un lote que ya está en el nivel más alto no puede "subir más".
        dias = [{"fecha": "martes", "nivel": "ALTO"}]
        self.assertIsNone(primer_dia_que_sube_de_nivel("ALTO", dias))

    def test_lista_vacia_devuelve_none(self):
        self.assertIsNone(primer_dia_que_sube_de_nivel("BAJO", []))


class RiesgoDescomposicionVistaTests(TestCase):
    """RF/cambio estructural aditivo: la pantalla de riesgo climático solo
    debe mostrar lotes de Frutas y Verduras (las categorías sin fecha de
    caducidad impresa), calculando los días en exhibición desde
    Inventario.fecha_ingreso y sin tocar fecha_vencimiento ni Alerta."""

    def setUp(self):
        self.comprador = crear_usuario("riesgo.comprador@test.com", Usuario.Rol.COMPRADOR)
        hoy = timezone.localdate()

        self.manzana = crear_producto(
            nombre="Manzana roja", upc="1111111111", categoria=Producto.Categoria.FRUTAS,
        )
        Inventario.objects.create(
            producto=self.manzana, fecha_ingreso=hoy - timedelta(days=6),
            fecha_vencimiento=hoy + timedelta(days=2), cantidad=10, lote="L1",
        )

        self.leche = crear_producto(
            nombre="Leche entera 1L", upc="2222222222", categoria=Producto.Categoria.LACTEOS,
        )
        Inventario.objects.create(
            producto=self.leche, fecha_ingreso=hoy - timedelta(days=6),
            fecha_vencimiento=hoy + timedelta(days=2), cantidad=10, lote="L2",
        )

        self.client.force_login(self.comprador)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_solo_incluye_frutas_y_verduras(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        productos_mostrados = [l["producto"] for l in respuesta.context["filas"]]
        self.assertIn("Manzana roja", productos_mostrados)
        self.assertNotIn("Leche entera 1L", productos_mostrados)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_no_incluye_lotes_ya_vencidos(self, _mock_clima):
        # Bug real reportado por Francisco: sin este filtro, lotes viejísimos
        # del historial sintético (fecha_ingreso de hace años, nunca
        # "consumidos") aparecían con cientos de días en exhibición - algo
        # imposible en la vida real. Un lote cuya fecha_vencimiento ya pasó
        # no debe aparecer aquí (ya lo cubre la alerta de Vencimiento), ni
        # siquiera para inflar la cantidad total o los días en exhibición
        # del producto agrupado.
        hoy = timezone.localdate()
        Inventario.objects.create(
            producto=self.manzana, fecha_ingreso=hoy - timedelta(days=744),
            fecha_vencimiento=hoy - timedelta(days=729), cantidad=80, lote="L-VIEJO",
        )
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila_manzana = next(f for f in respuesta.context["filas"] if f["producto"] == "Manzana roja")
        self.assertEqual(fila_manzana["num_lotes"], 1)
        self.assertEqual(fila_manzana["cantidad_total"], 10)
        self.assertEqual(fila_manzana["dias_en_exhibicion"], 6)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_calcula_dias_en_exhibicion_desde_fecha_ingreso(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = respuesta.context["filas"][0]
        self.assertEqual(fila["dias_en_exhibicion"], 6)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_sin_clima_disponible_sigue_funcionando(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context["clima_disponible"])
        # Con 6 días en exhibición y sin clima, el puntaje viene solo de
        # PUNTOS_POR_DIAS_EXHIBICION (45) -> nivel MEDIO.
        self.assertEqual(respuesta.context["filas"][0]["nivel"], "MEDIO")

    @patch("core.views.pronostico_temperatura_humedad_por_dia")
    def test_con_clima_disponible_sube_el_riesgo(self, mock_clima):
        hoy = timezone.localdate()
        mock_clima.return_value = {hoy: {"temp_max": 32.0, "humedad_promedio": 85.0}}
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertTrue(respuesta.context["clima_disponible"])
        self.assertEqual(respuesta.context["filas"][0]["nivel"], "ALTO")

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_no_afecta_fecha_vencimiento_del_inventario(self, _mock_clima):
        self.client.get(reverse("riesgo_descomposicion"))
        lote = Inventario.objects.get(lote="L1")
        self.assertEqual(lote.fecha_vencimiento, timezone.localdate() + timedelta(days=2))

    @patch("core.views.pronostico_temperatura_humedad_por_dia")
    def test_avisa_cuando_el_riesgo_va_a_subir_de_nivel(self, mock_clima):
        # Petición explícita de Francisco: en vez de una tabla día por
        # día, un aviso corto cuando un lote va a subir de nivel pronto.
        # L1 lleva 6 días en exhibición (ya en MEDIO sin clima, ver
        # test_sin_clima_disponible_sigue_funcionando) y su fecha de
        # vencimiento es en 2 días - se simula un mañana muy caluroso y
        # húmedo para que sí alcance a subir a ALTO antes de esa fecha.
        hoy = timezone.localdate()
        mañana = hoy + timedelta(days=1)
        mock_clima.return_value = {
            hoy: {"temp_max": None, "humedad_promedio": None},
            mañana: {"temp_max": 35.0, "humedad_promedio": 90.0},
        }
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = respuesta.context["filas"][0]
        self.assertEqual(fila["nivel"], "MEDIO")
        self.assertIsNotNone(fila["aviso_proyeccion"])
        self.assertIn("ALTO", fila["aviso_proyeccion"])

    @patch("core.views.pronostico_temperatura_humedad_por_dia")
    def test_no_avisa_si_no_hay_ningun_dia_que_suba_de_nivel(self, mock_clima):
        hoy = timezone.localdate()
        mañana = hoy + timedelta(days=1)
        # Clima templado de mañana: no alcanza a mover el nivel de MEDIO.
        mock_clima.return_value = {mañana: {"temp_max": 18.0, "humedad_promedio": 40.0}}
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = respuesta.context["filas"][0]
        self.assertIsNone(fila["aviso_proyeccion"])

    @patch("core.views.pronostico_temperatura_humedad_por_dia")
    def test_proyeccion_no_pasa_de_la_fecha_de_vencimiento_del_lote(self, mock_clima):
        # L1 vence en 2 días - un pronóstico de calor extremo para dentro
        # de 5 días (ya fuera de la vida del lote) no debe generar aviso,
        # porque para entonces el lote ya no debería seguir en exhibición.
        hoy = timezone.localdate()
        dia_lejano = hoy + timedelta(days=5)
        mock_clima.return_value = {dia_lejano: {"temp_max": 40.0, "humedad_promedio": 95.0}}
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = respuesta.context["filas"][0]
        self.assertIsNone(fila["aviso_proyeccion"])


class RiesgoDescomposicionFiltroPorProductoTests(TestCase):
    """Bug real reportado por Francisco (con capturas): el buscador global
    de la topbar siempre mandaba a Predicciones, incluso buscando desde
    Riesgo climático - mismo patrón ya corregido antes para Predicciones/
    Alertas (ver BuscarGlobalRedireccionDeAlertasTests), que reapareció al
    agregar esta pantalla nueva porque el destino nunca se generalizó.
    Ahora esta vista sabe filtrarse por ?producto=, para que el buscador
    pueda quedarse aquí en vez de saltar de módulo."""

    def setUp(self):
        self.comprador = crear_usuario("riesgo.filtro.comprador@test.com", Usuario.Rol.COMPRADOR)
        hoy = timezone.localdate()

        self.manzana = crear_producto(
            nombre="Manzana roja", upc="3333333333", categoria=Producto.Categoria.FRUTAS,
        )
        Inventario.objects.create(
            producto=self.manzana, fecha_ingreso=hoy - timedelta(days=6),
            fecha_vencimiento=hoy + timedelta(days=2), cantidad=10, lote="L-MANZANA",
        )

        self.lechuga = crear_producto(
            nombre="Lechuga", upc="4444444444", categoria=Producto.Categoria.VERDURAS,
        )
        Inventario.objects.create(
            producto=self.lechuga, fecha_ingreso=hoy - timedelta(days=3),
            fecha_vencimiento=hoy + timedelta(days=3), cantidad=20, lote="L-LECHUGA",
        )

        self.leche = crear_producto(
            nombre="Leche entera 1L", upc="5555555555", categoria=Producto.Categoria.LACTEOS,
        )

        self.client.force_login(self.comprador)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_filtra_solo_el_producto_pedido(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"), {"producto": self.manzana.id_producto})
        lotes_mostrados = [l["producto"] for l in respuesta.context["filas"]]
        self.assertEqual(lotes_mostrados, ["Manzana roja"])
        self.assertEqual(respuesta.context["producto_filtro"], self.manzana)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_sin_filtro_muestra_todos_como_antes(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        lotes_mostrados = {l["producto"] for l in respuesta.context["filas"]}
        self.assertEqual(lotes_mostrados, {"Manzana roja", "Lechuga"})
        self.assertIsNone(respuesta.context["producto_filtro"])

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_avisa_cuando_el_producto_no_es_fruta_ni_verdura(self, _mock_clima):
        # Buscar "leche" desde aquí no debe verse como un error silencioso
        # (tabla vacía sin explicación) - esta pantalla es exclusiva de
        # Frutas/Verduras, y hay que decir por qué no aparece nada.
        respuesta = self.client.get(reverse("riesgo_descomposicion"), {"producto": self.leche.id_producto})
        self.assertEqual(list(respuesta.context["filas"]), [])
        self.assertTrue(respuesta.context["producto_fuera_de_alcance"])
        self.assertContains(respuesta, "solo aplica a Frutas y Verduras")


class RiesgoDescomposicionAgrupadoPorProductoTests(TestCase):
    """Cambio decidido con Francisco (2026-09-18): Frutas y Verduras no
    traen ninguna marca física de lote, así que una vez en el anaquel es
    imposible saber a qué lote pertenece cada pieza - mostrar el riesgo
    lote por lote no es accionable. Ahora se agrupa por producto: una
    sola fila, cantidad sumada entre todos los lotes vigentes, y el
    riesgo calculado con el lote MÁS ANTIGUO (peor caso)."""

    def setUp(self):
        self.comprador = crear_usuario("riesgo.grupo.comprador@test.com", Usuario.Rol.COMPRADOR)
        hoy = timezone.localdate()

        self.tomate = crear_producto(
            nombre="Tomate de riñón", upc="8888888888",
            categoria=Producto.Categoria.VERDURAS, vida_util_dias=10,
        )
        # Lote más nuevo (bajo riesgo si se mirara solo).
        Inventario.objects.create(
            producto=self.tomate, fecha_ingreso=hoy - timedelta(days=2),
            fecha_vencimiento=hoy + timedelta(days=8), cantidad=10, lote="L-NUEVO",
        )
        # Lote más antiguo (peor caso) - debe ser el que domine el cálculo.
        Inventario.objects.create(
            producto=self.tomate, fecha_ingreso=hoy - timedelta(days=6),
            fecha_vencimiento=hoy + timedelta(days=4), cantidad=15, lote="L-VIEJO",
        )
        self.client.force_login(self.comprador)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_una_sola_fila_por_producto(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        filas_tomate = [f for f in respuesta.context["filas"] if f["producto"] == "Tomate de riñón"]
        self.assertEqual(len(filas_tomate), 1)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_cantidad_total_suma_todos_los_lotes_vigentes(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = next(f for f in respuesta.context["filas"] if f["producto"] == "Tomate de riñón")
        self.assertEqual(fila["cantidad_total"], 25)
        self.assertEqual(fila["num_lotes"], 2)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_usa_el_lote_mas_antiguo_como_peor_caso(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = next(f for f in respuesta.context["filas"] if f["producto"] == "Tomate de riñón")
        # 6 días de exhibición del lote más antiguo, no 2 del más nuevo.
        self.assertEqual(fila["dias_en_exhibicion"], 6)


class ListarMermasFiltroPorProductoTests(TestCase):
    """Mismo criterio que RiesgoDescomposicionFiltroPorProductoTests: el
    buscador global también puede traer aquí un producto puntual desde
    cualquier módulo, así que Mermas necesita saber filtrarse por él."""

    def setUp(self):
        self.gerente = crear_usuario("mermas.filtro.gerente@test.com", Usuario.Rol.GERENTE)
        self.manzana = crear_producto(nombre="Manzana roja", upc="6666666666")
        self.leche = crear_producto(nombre="Leche entera 1L", upc="7777777777")
        Merma.objects.create(
            producto=self.manzana, fecha=timezone.localdate(),
            cantidad=2, motivo=Merma.Motivo.DANO, costo_perdida=5,
        )
        Merma.objects.create(
            producto=self.leche, fecha=timezone.localdate(),
            cantidad=1, motivo=Merma.Motivo.VENCIMIENTO, costo_perdida=6,
        )
        self.client.force_login(self.gerente)

    def test_filtra_solo_el_producto_pedido(self):
        respuesta = self.client.get(reverse("listar_mermas"), {"producto": self.manzana.id_producto})
        productos_mostrados = [m.producto.nombre for m in respuesta.context["mermas"]]
        self.assertEqual(productos_mostrados, ["Manzana roja"])
        self.assertEqual(respuesta.context["producto_filtro"], self.manzana)

    def test_sin_filtro_muestra_todas_como_antes(self):
        respuesta = self.client.get(reverse("listar_mermas"))
        productos_mostrados = {m.producto.nombre for m in respuesta.context["mermas"]}
        self.assertEqual(productos_mostrados, {"Manzana roja", "Leche entera 1L"})
        self.assertIsNone(respuesta.context["producto_filtro"])

    def test_paginacion_conserva_el_filtro_de_producto(self):
        for i in range(30):
            Merma.objects.create(
                producto=self.manzana, fecha=timezone.localdate() - timedelta(days=i),
                cantidad=1, motivo=Merma.Motivo.VENCIMIENTO, costo_perdida=1,
            )
        respuesta = self.client.get(reverse("listar_mermas"), {"producto": self.manzana.id_producto})
        self.assertEqual(respuesta.context["mermas"].paginator.num_pages, 2)
        self.assertContains(respuesta, f"producto={self.manzana.id_producto}")


class BuscarTopbarModuloActualTests(TestCase):
    """El buscador de la topbar (base.html) es un único componente
    compartido por todas las pantallas - por eso el bug de "me saca del
    módulo" reapareció dos veces con módulos distintos (ver
    BuscarGlobalRedireccionDeAlertasTests y
    RiesgoDescomposicionFiltroPorProductoTests). En vez de arreglar
    módulo por módulo otra vez la próxima vez, esto verifica que CADA
    pantalla le informe correctamente al JS del buscador cuál es el
    módulo actual (moduloActual), que es lo que decide a dónde manda un
    resultado de producto/alerta."""

    def setUp(self):
        self.gerente = crear_usuario("topbar.modulo.gerente@test.com", Usuario.Rol.GERENTE)
        self.client.force_login(self.gerente)

    def test_dashboard_reporta_su_propio_modulo(self):
        respuesta = self.client.get(reverse("dashboard"))
        self.assertContains(respuesta, "var moduloActual = 'dashboard';")

    def test_predicciones_reporta_su_propio_modulo(self):
        respuesta = self.client.get(reverse("listar_predicciones"))
        self.assertContains(respuesta, "var moduloActual = 'listar_predicciones';")

    def test_historial_decisiones_reporta_su_propio_modulo(self):
        respuesta = self.client.get(reverse("historial_decisiones"))
        self.assertContains(respuesta, "var moduloActual = 'historial_decisiones';")

    def test_riesgo_descomposicion_reporta_su_propio_modulo(self):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertContains(respuesta, "var moduloActual = 'riesgo_descomposicion';")

    def test_listar_mermas_reporta_su_propio_modulo(self):
        respuesta = self.client.get(reverse("listar_mermas"))
        self.assertContains(respuesta, "var moduloActual = 'listar_mermas';")


class ProductoUnidadMedidaTests(TestCase):
    """Nuevo campo Producto.unidad_medida (reportado por Francisco,
    2026-09-17/18): una merma de "0.9 quesos" no tiene lógica para un
    producto que se cuenta por pieza, pero sí la tiene para uno de peso
    variable (se pesa suelto en la caja) - mismo concepto que la
    industria retail llama "peso variable" o "catch weight", y que GS1
    codifica distinto en el código de barras (prefijo 2, con el peso
    incluido en el propio código)."""

    def test_por_defecto_es_unidad(self):
        producto = crear_producto()
        self.assertEqual(producto.unidad_medida, Producto.UnidadMedida.UNIDAD)

    def test_redondear_cantidad_da_entero_para_unidad(self):
        producto = crear_producto(unidad_medida=Producto.UnidadMedida.UNIDAD)
        resultado = producto.redondear_cantidad(4.6)
        self.assertEqual(resultado, 5)
        self.assertEqual(resultado, int(resultado))

    def test_redondear_cantidad_conserva_decimales_para_peso(self):
        producto = crear_producto(unidad_medida=Producto.UnidadMedida.PESO)
        self.assertEqual(producto.redondear_cantidad(4.567), 4.57)


class RegistrarMermaUnidadMedidaTests(TestCase):
    """La cantidad de una merma debe respetar Producto.unidad_medida: un
    producto por unidad no admite decimales; uno de peso variable sí,
    porque se pesa suelto en la caja (reportado por Francisco al ver
    "0.9" en la cantidad de un queso, 2026-09-17/18)."""

    def setUp(self):
        self.comprador = crear_usuario("comprador.peso@test.com", Usuario.Rol.COMPRADOR)
        self.queso = crear_producto(nombre="Queso fresco", upc="7501112223301", unidad_medida=Producto.UnidadMedida.UNIDAD)
        self.pechuga = crear_producto(nombre="Pechuga de pollo", upc="7501112223302", unidad_medida=Producto.UnidadMedida.PESO)
        self.client.force_login(self.comprador)

    def _post(self, producto, cantidad):
        return self.client.post(reverse("registrar_merma"), {
            "producto": producto.id_producto,
            "fecha": timezone.localdate().isoformat(),
            "cantidad": cantidad,
            "motivo": Merma.Motivo.VENCIMIENTO,
        })

    def test_decimal_en_producto_por_unidad_falla_la_validacion(self):
        respuesta = self._post(self.queso, "0.9")
        self.assertEqual(respuesta.status_code, 200)  # re-muestra el formulario con error
        self.assertEqual(Merma.objects.count(), 0)
        self.assertContains(respuesta, "número entero")

    def test_entero_en_producto_por_unidad_se_registra(self):
        respuesta = self._post(self.queso, "2")
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Merma.objects.get().cantidad, 2)

    def test_decimal_en_producto_de_peso_variable_se_registra(self):
        respuesta = self._post(self.pechuga, "0.9")
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Merma.objects.get().cantidad, 0.9)


class BuscarProductosJsonUnidadMedidaTests(TestCase):
    """registrar_merma.html usa este endpoint para saber, apenas se elige
    un producto, si debe permitir decimales en el campo Cantidad."""

    def setUp(self):
        self.comprador = crear_usuario("comprador.json.peso@test.com", Usuario.Rol.COMPRADOR)
        self.pechuga = crear_producto(nombre="Pechuga de pollo", upc="7501112223303", unidad_medida=Producto.UnidadMedida.PESO)
        self.client.force_login(self.comprador)

    def test_incluye_unidad_medida_en_la_respuesta(self):
        respuesta = self.client.get(reverse("buscar_productos_json"), {"q": "pechuga"})
        datos = respuesta.json()
        self.assertEqual(datos[0]["unidad_medida"], "PESO")


class RecomendacionesUnidadMedidaTests(TestCase):
    """El criterio de peso variable también aplica a la cantidad sugerida
    de reabastecimiento: para un producto que se pesa (kg), el sugerido y
    el ajuste pueden tener decimales; para uno por pieza, deben ser
    siempre un entero (reportado por Francisco, 2026-09-17/18)."""

    def setUp(self):
        self.comprador = crear_usuario("comprador.recom.peso@test.com", Usuario.Rol.COMPRADOR)
        self.pechuga = crear_producto(nombre="Pechuga de pollo", upc="7501112223304", unidad_medida=Producto.UnidadMedida.PESO)
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.pechuga, fecha_prediccion=hoy,
            fecha_pronosticada=hoy, valor_predicho=20.7, intervalo_inferior=15,
            intervalo_superior=25, precision_modelo=10.0,
        )
        Inventario.objects.create(
            producto=self.pechuga, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=5), cantidad=5.2,
        )
        self.client.force_login(self.comprador)

    def test_sugerido_conserva_decimales_para_peso_variable(self):
        respuesta = self.client.get(reverse("listar_recomendaciones"))
        fila = respuesta.context["recomendaciones"][0]
        self.assertEqual(fila["sugerido"], 15.5)  # 20.7 predicho - 5.2 en stock

    def test_ajuste_decimal_se_guarda_para_peso_variable(self):
        self.client.post(reverse("listar_recomendaciones"), {
            f"ajuste_{self.pechuga.id_producto}": "18.25",
        })
        historial = DecisionHistorial.objects.get()
        self.assertEqual(historial.cantidad_ajustada, 18.25)

    def test_ajuste_decimal_se_rechaza_para_producto_por_unidad(self):
        queso = crear_producto(nombre="Queso fresco", upc="7501112223305", unidad_medida=Producto.UnidadMedida.UNIDAD)
        Prediccion.objects.create(
            producto=queso, fecha_prediccion=timezone.localdate(),
            fecha_pronosticada=timezone.localdate(), valor_predicho=10, intervalo_inferior=8,
            intervalo_superior=12,
        )
        self.client.post(reverse("listar_recomendaciones"), {
            f"ajuste_{queso.id_producto}": "7.5",
        })
        self.assertEqual(DecisionHistorial.objects.count(), 0)


class HistorialDecisionesUnidadMedidaTests(TestCase):
    """La tabla de Historial de decisiones debe mostrar kg para productos
    de peso variable y unidades para el resto - mismo criterio que en
    toda la pantalla de Recomendaciones."""

    def setUp(self):
        self.comprador = crear_usuario("comprador.hist.peso@test.com", Usuario.Rol.COMPRADOR)
        self.pechuga = crear_producto(nombre="Pechuga de pollo", upc="7501112223306", unidad_medida=Producto.UnidadMedida.PESO)
        DecisionHistorial.objects.create(
            producto=self.pechuga, usuario=self.comprador,
            fecha_prediccion=timezone.localdate(),
            cantidad_sugerida=15.5, cantidad_ajustada=18.25,
        )
        self.client.force_login(self.comprador)

    def test_muestra_kg_para_producto_de_peso_variable(self):
        respuesta = self.client.get(reverse("historial_decisiones"))
        # El formato local (es-GT) usa coma decimal - igual que el resto
        # del sistema (ej. "Q 18,00" en Mermas).
        self.assertContains(respuesta, "18,25 kg")


class RiesgoDescomposicionUnidadMedidaTests(TestCase):
    """La columna Cantidad de Riesgo climático debe mostrar kg para
    productos de peso variable y unidades para el resto - mismo criterio
    en todo el sistema (reportado por Francisco, 2026-09-17/18)."""

    def setUp(self):
        self.gerente = crear_usuario("gerente.riesgo.peso@test.com", Usuario.Rol.GERENTE)
        self.manzana = crear_producto(
            nombre="Manzana roja", upc="7501112223307", categoria=Producto.Categoria.FRUTAS,
            unidad_medida=Producto.UnidadMedida.PESO, vida_util_dias=15,
        )
        self.aguacate = crear_producto(
            nombre="Aguacate hass", upc="7501112223308", categoria=Producto.Categoria.FRUTAS,
            unidad_medida=Producto.UnidadMedida.UNIDAD, vida_util_dias=5,
        )
        hoy = timezone.localdate()
        Inventario.objects.create(
            producto=self.manzana, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=10), cantidad=8.4,
        )
        Inventario.objects.create(
            producto=self.aguacate, fecha_ingreso=hoy,
            fecha_vencimiento=hoy + timedelta(days=4), cantidad=12,
        )
        self.client.force_login(self.gerente)

    def test_producto_de_peso_variable_muestra_kg(self):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertContains(respuesta, "8,40 kg")  # coma decimal (es-GT)

    def test_producto_por_unidad_muestra_u(self):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertContains(respuesta, "12 u.")


class ListarMermasUnidadMedidaTests(TestCase):
    """Mismo criterio que Riesgo climático, aplicado a la tabla de
    Historial de mermas."""

    def setUp(self):
        self.gerente = crear_usuario("gerente.mermas.peso@test.com", Usuario.Rol.GERENTE)
        self.pechuga = crear_producto(nombre="Pechuga de pollo", upc="7501112223309", unidad_medida=Producto.UnidadMedida.PESO)
        self.queso = crear_producto(nombre="Queso fresco", upc="7501112223310", unidad_medida=Producto.UnidadMedida.UNIDAD)
        Merma.objects.create(
            producto=self.pechuga, fecha=timezone.localdate(), cantidad=0.9,
            motivo=Merma.Motivo.DANO, costo_perdida=18.0,
        )
        Merma.objects.create(
            producto=self.queso, fecha=timezone.localdate(), cantidad=2,
            motivo=Merma.Motivo.VENCIMIENTO, costo_perdida=36.0,
        )
        self.client.force_login(self.gerente)

    def test_peso_variable_muestra_kg(self):
        respuesta = self.client.get(reverse("listar_mermas"))
        self.assertContains(respuesta, "0,90 kg")  # coma decimal (es-GT)

    def test_por_unidad_muestra_u(self):
        respuesta = self.client.get(reverse("listar_mermas"))
        self.assertContains(respuesta, "2 u.")


class GenerarDatosPruebaUnidadMedidaTests(TestCase):
    """El catálogo de siembra clasifica los productos por unidad de
    medida (confirmado con Francisco, 2026-09-18): Pechuga de pollo,
    Carne molida de res, Tomate de riñón, Cebolla blanca, Manzana roja y
    Banano son de peso variable; el resto es por unidad. "Pan de molde"
    ya no existe en el catálogo - se reemplazó por "Pan dulce" (Francisco:
    pan de molde no es un producto típico en Guatemala)."""

    PESO_VARIABLE = [
        "Pechuga de pollo", "Carne molida de res", "Tomate de riñón",
        "Cebolla blanca", "Manzana roja", "Banano",
    ]
    POR_UNIDAD = [
        "Leche entera 1L", "Queso fresco", "Yogurt natural", "Chorizo",
        "Lechuga", "Aguacate hass", "Pan francés", "Pan dulce",
    ]

    def setUp(self):
        salida = StringIO()
        call_command("generar_datos_prueba", "--dias", "15", stdout=salida)

    def test_clasifica_productos_de_peso_variable(self):
        for nombre in self.PESO_VARIABLE:
            producto = Producto.objects.get(nombre=nombre)
            self.assertEqual(producto.unidad_medida, Producto.UnidadMedida.PESO, nombre)

    def test_clasifica_productos_por_unidad(self):
        for nombre in self.POR_UNIDAD:
            producto = Producto.objects.get(nombre=nombre)
            self.assertEqual(producto.unidad_medida, Producto.UnidadMedida.UNIDAD, nombre)

    def test_pan_de_molde_ya_no_existe_en_el_catalogo(self):
        self.assertFalse(Producto.objects.filter(nombre="Pan de molde").exists())

    def test_producto_de_peso_variable_genera_ventas_con_decimales(self):
        pechuga = Producto.objects.get(nombre="Pechuga de pollo")
        cantidades = list(Venta.objects.filter(producto=pechuga).values_list("cantidad", flat=True))
        self.assertTrue(cantidades)
        self.assertTrue(any(c != int(c) for c in cantidades))

    def test_producto_por_unidad_siempre_genera_ventas_enteras(self):
        queso = Producto.objects.get(nombre="Queso fresco")
        cantidades = list(Venta.objects.filter(producto=queso).values_list("cantidad", flat=True))
        self.assertTrue(cantidades)
        self.assertTrue(all(c == int(c) for c in cantidades))

    def test_merma_minima_es_0_05_kg_para_peso_variable(self):
        pechuga = Producto.objects.get(nombre="Pechuga de pollo")
        mermas = Merma.objects.filter(producto=pechuga)
        if mermas.exists():
            self.assertTrue(all(m.cantidad >= 0.05 for m in mermas))

    def test_merma_minima_es_1_unidad_para_producto_por_unidad(self):
        queso = Producto.objects.get(nombre="Queso fresco")
        mermas = Merma.objects.filter(producto=queso)
        if mermas.exists():
            self.assertTrue(all(m.cantidad >= 1 and m.cantidad == int(m.cantidad) for m in mermas))


class ActualizarHistorialVentasUnidadMedidaTests(TestCase):
    """Mismo criterio de unidad de medida que generar_datos_prueba.py,
    pero en el job nocturno que mantiene vivo el historial (RNF-04) - debe
    seguir siendo consistente día a día (reportado por Francisco,
    2026-09-17/18)."""

    def setUp(self):
        self.pechuga = crear_producto(
            nombre="Pechuga de pollo", upc="7501112223311",
            categoria=Producto.Categoria.CARNES, unidad_medida=Producto.UnidadMedida.PESO,
        )
        self.queso = crear_producto(
            nombre="Queso fresco", upc="7501112223312",
            categoria=Producto.Categoria.LACTEOS, unidad_medida=Producto.UnidadMedida.UNIDAD,
        )
        # Sin ninguna Venta previa, el comando solo genera el día de ayer -
        # insuficiente para una muestra representativa. Se siembra una
        # venta vieja para forzar varios días de backfill (el comando mira
        # la fecha de la ÚLTIMA venta de TODO el catálogo, no por producto).
        self.venta_semilla = Venta.objects.create(
            producto=self.queso, fecha=timezone.now() - timedelta(days=15),
            cantidad=1, precio_unitario=self.queso.precio_venta,
        )
        salida = StringIO()
        call_command("actualizar_historial_ventas", stdout=salida)

    def test_producto_de_peso_variable_genera_ventas_con_decimales(self):
        cantidades = list(Venta.objects.filter(producto=self.pechuga).values_list("cantidad", flat=True))
        self.assertTrue(cantidades)
        self.assertTrue(any(c != int(c) for c in cantidades))

    def test_producto_por_unidad_siempre_genera_ventas_enteras(self):
        # Se excluye la venta sembrada en setUp (ya entera de por sí) para
        # verificar específicamente lo que generó el comando.
        cantidades = list(
            Venta.objects.filter(producto=self.queso)
            .exclude(pk=self.venta_semilla.pk)
            .values_list("cantidad", flat=True)
        )
        self.assertTrue(cantidades)
        self.assertTrue(all(c == int(c) for c in cantidades))
