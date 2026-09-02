from django.contrib.auth.forms import AuthenticationForm
from django import forms

from .models import Merma


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
        fields = ["producto", "fecha", "cantidad", "motivo", "costo_perdida"]
        widgets = {
            "producto": forms.Select(attrs={"class": "form-select"}),
            "fecha": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "cantidad": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "motivo": forms.Select(attrs={"class": "form-select"}),
            "costo_perdida": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].empty_label = "Selecciona un producto"
        self.fields["motivo"].choices = [("", "Selecciona un motivo")] + list(
            Merma.Motivo.choices
        )       