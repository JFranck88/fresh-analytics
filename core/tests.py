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

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Producto, Inventario, Merma, Prediccion, Usuario,
    Alerta, Configuracion, DecisionHistorial,
)


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


class ControlDeAccesoPorRolTests(TestCase):
    """Cada rol debe ver solo lo que le corresponde: Comprador sus módulos
    operativos, Gerente un subconjunto de solo lectura/decisión, y
    Administrador los módulos de gestión - nunca los operativos. El
    superusuario técnico (is_superuser_admin) debe poder entrar a todo,
    sin excepción."""

    # Vistas que NO reciben argumentos de URL, agrupadas por a quién
    # deben permitirle entrar (200) y a quién deben rechazarle (403).
    SOLO_COMPRADOR = ["registrar_merma", "listar_recomendaciones", "generar_orden_compra"]
    COMPRADOR_Y_GERENTE = ["listar_alertas", "listar_mermas", "historial_decisiones"]
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
