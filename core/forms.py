from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django import forms

from .models import Merma, Usuario, Configuracion


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Correo",
        widget=forms.EmailInput(attrs={"class": "form-control", "autofocus": True}),
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )


class MermaForm(forms.ModelForm):
    class Meta:
        model = Merma
        fields = ["producto", "fecha", "cantidad", "motivo"]
        widgets = {
            # El producto ya no se elige de un <select>: se busca por UPC
            # o por descripción, en campos separados, con el buscador de
            # registrar_merma.html, que llena este campo oculto con el
            # id del producto elegido.
            "producto": forms.HiddenInput(),
            "fecha": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "cantidad": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "motivo": forms.Select(attrs={"class": "form-select"}),
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["motivo"].choices = [("", "Selecciona un motivo")] + list(
            Merma.Motivo.choices
        )

from .models import Usuario


class CrearUsuarioForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Contraseña", widget=forms.PasswordInput(attrs={"class": "form-control"})
    )
    password2 = forms.CharField(
        label="Confirmar contraseña", widget=forms.PasswordInput(attrs={"class": "form-control"})
    )

    class Meta:
        model = Usuario
        fields = ["nombre", "correo", "rol"]
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "form-control"}),
            "correo": forms.EmailInput(attrs={"class": "form-control"}),
            "rol": forms.Select(attrs={"class": "form-select"}),
        }

    def clean(self):
        datos = super().clean()
        password1, password2 = datos.get("password1"), datos.get("password2")
        if password1 != password2:
            raise forms.ValidationError("Las contraseñas no coinciden.")
        if password1:
            # Las mismas reglas de AUTH_PASSWORD_VALIDATORS (settings.py) que
            # ya se le exigen a cualquier password de Django, para que un
            # usuario creado desde este formulario no quede con una
            # contraseña débil solo porque este ModelForm es manual.
            validate_password(password1)
        return datos

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.set_password(self.cleaned_data["password1"])
        if commit:
            usuario.save()
        return usuario

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rol"].choices = [("", "Selecciona un rol")] + list(Usuario.Rol.choices)

        from .models import Configuracion


class EditarUsuarioForm(forms.ModelForm):
    """RF de gestión de usuarios: permite al Administrador corregir nombre,
    correo o rol de un usuario existente, y activar/desactivar su acceso
    (is_active ya lo exige Django para el login - aquí solo se expone en la
    interfaz). No toca la contraseña; eso vive en RestablecerPasswordForm."""

    class Meta:
        model = Usuario
        fields = ["nombre", "correo", "rol", "is_active"]
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "form-control"}),
            "correo": forms.EmailInput(attrs={"class": "form-control"}),
            "rol": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {"is_active": "Usuario activo (puede iniciar sesión)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rol"].choices = list(Usuario.Rol.choices)


class RestablecerPasswordForm(forms.Form):
    """Formulario separado (no ModelForm) para que restablecer la contraseña
    de otro usuario sea una acción explícita y distinta de editar su perfil."""

    password1 = forms.CharField(
        label="Nueva contraseña",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="Confirmar nueva contraseña",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)

    def clean(self):
        datos = super().clean()
        password1, password2 = datos.get("password1"), datos.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Las contraseñas no coinciden.")
        if password1:
            validate_password(password1, user=self.usuario)
        return datos


class ConfiguracionForm(forms.ModelForm):
    class Meta:
        model = Configuracion
        fields = ["clave", "valor", "descripcion"]
        widgets = {
            "clave": forms.TextInput(attrs={"class": "form-control"}),
            "valor": forms.TextInput(attrs={"class": "form-control"}),
            "descripcion": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }