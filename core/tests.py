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

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Producto, Inventario, Merma, Prediccion, Usuario,
    Alerta, Configuracion, DecisionHistorial,
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

    def test_administrador_no_ve_alertas_ni_lotes(self):
        """Misma regla de visibilidad que la página completa /buscar/:
        Administrador no ve lo operativo de Gerente/Comprador."""
        self.client.force_login(self.administrador)
        respuesta = self.client.get(reverse("buscar_global_json"), {"q": "yogur"})
        datos = respuesta.json()
        self.assertEqual(len(datos["productos"]), 1)
        self.assertEqual(datos["alertas"], [])
        self.assertEqual(datos["lotes"], [])


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


class DashboardPrecisionModeloTests(TestCase):
    """La tarjeta "Precisión del modelo" del dashboard (pedida por el
    diseño de interfaz documentado, 5.3.2.1) es el MAPE promedio de las
    predicciones de la corrida MÁS RECIENTE - no la validación cruzada
    histórica de auditar_modelo, que es un análisis técnico aparte sobre
    todo el catálogo junto y ya no se muestra en ninguna pantalla."""

    def setUp(self):
        self.comprador = crear_usuario("precision.comprador@test.com", Usuario.Rol.COMPRADOR)
        self.producto = crear_producto()

    def test_muestra_promedio_de_precision_de_la_corrida_mas_reciente(self):
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
            precision_modelo=10.0,
        )
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy + timedelta(days=1),
            valor_predicho=12, intervalo_inferior=6, intervalo_superior=18,
            precision_modelo=20.0,
        )
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["precision_modelo_promedio"], 15.0)
        # Con LANGUAGE_CODE="es", floatformat usa coma como separador
        # decimal (igual que el resto de la interfaz, ej. "Q 0,00") -
        # "15,0%", no "15.0%".
        self.assertContains(respuesta, "15,0%")

    def test_ignora_corridas_anteriores(self):
        hoy = timezone.localdate()
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy - timedelta(days=1), fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
            precision_modelo=99.0,
        )
        Prediccion.objects.create(
            producto=self.producto, fecha_prediccion=hoy, fecha_pronosticada=hoy,
            valor_predicho=10, intervalo_inferior=5, intervalo_superior=15,
            precision_modelo=10.0,
        )
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertEqual(respuesta.context["precision_modelo_promedio"], 10.0)

    def test_nd_cuando_no_hay_predicciones(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get(reverse("dashboard"))
        self.assertIsNone(respuesta.context["precision_modelo_promedio"])
        self.assertContains(respuesta, "N/D")


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


class CalcularRiesgoLoteTests(TestCase):
    """Pruebas puras del cálculo de riesgo climático de descomposición
    (core/riesgo_descomposicion.py) - no dependen de la base de datos ni
    de la API de clima, solo de la función y sus umbrales."""

    def test_pocos_dias_y_sin_clima_es_riesgo_bajo(self):
        riesgo = calcular_riesgo_lote(dias_en_exhibicion=1)
        self.assertEqual(riesgo["nivel"], "BAJO")
        self.assertEqual(riesgo["puntaje"], 0)

    def test_mas_dias_en_exhibicion_sube_el_riesgo(self):
        riesgo_1_dia = calcular_riesgo_lote(dias_en_exhibicion=1)
        riesgo_3_dias = calcular_riesgo_lote(dias_en_exhibicion=3)
        riesgo_6_dias = calcular_riesgo_lote(dias_en_exhibicion=6)
        self.assertLess(riesgo_1_dia["puntaje"], riesgo_3_dias["puntaje"])
        self.assertLess(riesgo_3_dias["puntaje"], riesgo_6_dias["puntaje"])

    def test_temperatura_alta_suma_puntos(self):
        sin_clima = calcular_riesgo_lote(dias_en_exhibicion=1)
        con_calor = calcular_riesgo_lote(dias_en_exhibicion=1, temp_max=32)
        self.assertGreater(con_calor["puntaje"], sin_clima["puntaje"])
        self.assertEqual(con_calor["factores"]["temperatura"], 25)

    def test_humedad_alta_suma_puntos(self):
        sin_clima = calcular_riesgo_lote(dias_en_exhibicion=1)
        con_humedad = calcular_riesgo_lote(dias_en_exhibicion=1, humedad_promedio=85)
        self.assertGreater(con_humedad["puntaje"], sin_clima["puntaje"])
        self.assertEqual(con_humedad["factores"]["humedad"], 20)

    def test_combinacion_de_variables_da_riesgo_alto(self):
        riesgo = calcular_riesgo_lote(dias_en_exhibicion=6, temp_max=32, humedad_promedio=85)
        self.assertEqual(riesgo["nivel"], "ALTO")
        self.assertEqual(riesgo["badge"], "danger")

    def test_puntaje_nunca_pasa_de_cien(self):
        # El máximo posible sumando las tres tablas de puntos (45+25+20=90)
        # ya no llega a 100 - el tope solo es un colchón de seguridad para
        # si en el futuro se ajustan los umbrales hacia arriba. Se prueba
        # directamente contra ese máximo real de hoy.
        riesgo = calcular_riesgo_lote(dias_en_exhibicion=30, temp_max=40, humedad_promedio=100)
        self.assertEqual(riesgo["puntaje"], 90)
        self.assertLessEqual(riesgo["puntaje"], 100)

    def test_clima_ausente_no_rompe_el_calculo(self):
        # Cuando no hay API key o la consulta falla, clima.py devuelve
        # (None, None) - el cálculo debe seguir funcionando solo con
        # días en exhibición, sin lanzar ningún error.
        riesgo = calcular_riesgo_lote(dias_en_exhibicion=5, temp_max=None, humedad_promedio=None)
        self.assertEqual(riesgo["factores"]["temperatura"], 0)
        self.assertEqual(riesgo["factores"]["humedad"], 0)


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
        productos_mostrados = [l["producto"] for l in respuesta.context["lotes"]]
        self.assertIn("Manzana roja", productos_mostrados)
        self.assertNotIn("Leche entera 1L", productos_mostrados)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_no_incluye_lotes_ya_vencidos(self, _mock_clima):
        # Bug real reportado por Francisco: sin este filtro, lotes viejísimos
        # del historial sintético (fecha_ingreso de hace años, nunca
        # "consumidos") aparecían con cientos de días en exhibición - algo
        # imposible en la vida real. Un lote cuya fecha_vencimiento ya pasó
        # no debe aparecer aquí (ya lo cubre la alerta de Vencimiento).
        hoy = timezone.localdate()
        Inventario.objects.create(
            producto=self.manzana, fecha_ingreso=hoy - timedelta(days=744),
            fecha_vencimiento=hoy - timedelta(days=729), cantidad=80, lote="L-VIEJO",
        )
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        lotes_mostrados = [l["lote"] for l in respuesta.context["lotes"]]
        self.assertIn("L1", lotes_mostrados)
        self.assertNotIn("L-VIEJO", lotes_mostrados)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_calcula_dias_en_exhibicion_desde_fecha_ingreso(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        fila = respuesta.context["lotes"][0]
        self.assertEqual(fila["dias_en_exhibicion"], 6)

    @patch("core.views.pronostico_temperatura_humedad_por_dia", return_value={})
    def test_sin_clima_disponible_sigue_funcionando(self, _mock_clima):
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context["clima_disponible"])
        # Con 6 días en exhibición y sin clima, el puntaje viene solo de
        # PUNTOS_POR_DIAS_EXHIBICION (45) -> nivel MEDIO.
        self.assertEqual(respuesta.context["lotes"][0]["nivel"], "MEDIO")

    @patch("core.views.pronostico_temperatura_humedad_por_dia")
    def test_con_clima_disponible_sube_el_riesgo(self, mock_clima):
        hoy = timezone.localdate()
        mock_clima.return_value = {hoy: {"temp_max": 32.0, "humedad_promedio": 85.0}}
        respuesta = self.client.get(reverse("riesgo_descomposicion"))
        self.assertTrue(respuesta.context["clima_disponible"])
        self.assertEqual(respuesta.context["lotes"][0]["nivel"], "ALTO")

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
        fila = respuesta.context["lotes"][0]
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
        fila = respuesta.context["lotes"][0]
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
        fila = respuesta.context["lotes"][0]
        self.assertIsNone(fila["aviso_proyeccion"])
