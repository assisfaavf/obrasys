from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from catalog.models import BudgetItem, Discipline
from core.models import ProjectLocation
from rdo.models import (
    DailyWorkActivityEntry,
    DailyWorkLog,
    DailyWorkMaterialEntry,
    DailyWorkOccurrence,
    DailyWorkTeamEntry,
    ProjectWorkOrderInfo,
)


class BudgetItemChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.eap_code} — {obj.description}"


class FlexibleDecimalField(forms.DecimalField):
    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip()
            if "," in value:
                value = value.replace(".", "").replace(",", ".")
        return super().to_python(value)


class ProjectWorkOrderInfoForm(forms.ModelForm):
    class Meta:
        model = ProjectWorkOrderInfo
        fields = (
            "art_number",
            "contractor_name",
            "contractor_document",
            "technical_manager_name",
            "technical_manager_crea",
            "work_start_date",
            "expected_end_date",
            "address_snapshot",
            "contract_number",
            "contract_value",
            "additional_notes",
        )
        widgets = {
            "work_start_date": forms.DateInput(attrs={"type": "date"}),
            "expected_end_date": forms.DateInput(attrs={"type": "date"}),
            "additional_notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "art_number": "ART",
            "contractor_name": "Contratada",
            "contractor_document": "Documento da contratada",
            "technical_manager_name": "Responsável técnico",
            "technical_manager_crea": "CREA/CAU",
            "work_start_date": "Início da obra",
            "expected_end_date": "Previsão de término",
            "address_snapshot": "Endereço",
            "contract_number": "Contrato",
            "contract_value": "Valor do contrato",
            "additional_notes": "Observações",
        }


class DailyWorkLogForm(forms.ModelForm):
    class Meta:
        model = DailyWorkLog
        fields = (
            "log_date",
            "responsible_name",
            "weather_morning",
            "weather_afternoon",
            "weather_night",
            "notes",
            "general_observation",
            "interruption_reason",
        )
        widgets = {
            "log_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "general_observation": forms.Textarea(attrs={"rows": 3}),
            "interruption_reason": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "log_date": "Data",
            "responsible_name": "Responsável",
            "weather_morning": "Tempo pela manhã",
            "weather_afternoon": "Tempo à tarde",
            "weather_night": "Tempo à noite",
            "notes": "Observações do dia",
            "general_observation": "Observação geral",
            "interruption_reason": "Motivo de interrupção",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project or getattr(self.instance, "project", None)
        for field_name in ("weather_morning", "weather_afternoon", "weather_night"):
            self.fields[field_name].required = False

    def clean_log_date(self):
        log_date = self.cleaned_data["log_date"]
        if self.project and log_date:
            duplicate = DailyWorkLog.objects.filter(project=self.project, log_date=log_date)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise ValidationError("Já existe diário cadastrado para esta obra nesta data.")
        return log_date


class DailyWorkTeamEntryForm(forms.ModelForm):
    class Meta:
        model = DailyWorkTeamEntry
        fields = ("team_name", "worker_count", "location", "activity_description")
        widgets = {
            "activity_description": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "team_name": "Equipe",
            "worker_count": "Quantidade",
            "location": "Local",
            "activity_description": "Atividade/Serviço executado",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or getattr(getattr(self.instance, "daily_log", None), "project", None)
        if project:
            self.fields["location"].queryset = ProjectLocation.objects.filter(
                project=project,
                is_active=True,
            ).order_by("order_index", "code")
        else:
            self.fields["location"].queryset = ProjectLocation.objects.none()

    def clean_worker_count(self):
        worker_count = self.cleaned_data.get("worker_count")
        if worker_count is not None and worker_count <= 0:
            raise ValidationError("A quantidade deve ser maior que zero.")
        return worker_count


class DailyWorkActivityEntryForm(forms.ModelForm):
    class Meta:
        model = DailyWorkActivityEntry
        fields = ("description", "location", "discipline", "notes")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "description": "Descrição",
            "location": "Local",
            "discipline": "Disciplina",
            "notes": "Observações",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or getattr(getattr(self.instance, "daily_log", None), "project", None)
        if project:
            self.fields["location"].queryset = ProjectLocation.objects.filter(
                project=project,
                is_active=True,
            ).order_by("order_index", "code")
        else:
            self.fields["location"].queryset = ProjectLocation.objects.none()
        self.fields["discipline"].queryset = Discipline.objects.filter(is_active=True).order_by("name")


class DailyWorkOccurrenceForm(forms.ModelForm):
    class Meta:
        model = DailyWorkOccurrence
        fields = ("occurrence_type", "description", "notes")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "occurrence_type": "Tipo",
            "description": "Descrição",
            "notes": "Observações",
        }


class DailyWorkMaterialEntryForm(forms.ModelForm):
    item = BudgetItemChoiceField(queryset=BudgetItem.objects.none(), required=False, label="Material")
    quantity = FlexibleDecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=Decimal("0.001"),
        label="Quantidade",
    )

    class Meta:
        model = DailyWorkMaterialEntry
        fields = ("item", "location", "quantity", "unit_snapshot", "notes")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {
            "item": "Material",
            "location": "Local",
            "quantity": "Quantidade",
            "unit_snapshot": "Unidade",
            "notes": "Observações",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        project = project or getattr(getattr(self.instance, "daily_log", None), "project", None)
        if project:
            self.fields["item"].queryset = BudgetItem.objects.filter(
                project=project,
                is_active=True,
            ).select_related("unit").order_by("eap_code")
            self.fields["location"].queryset = ProjectLocation.objects.filter(
                project=project,
                is_active=True,
            ).order_by("order_index", "code")
        else:
            self.fields["item"].queryset = BudgetItem.objects.none()
            self.fields["location"].queryset = ProjectLocation.objects.none()
        self.fields["item"].widget.attrs["data-material-item"] = "true"
        self.fields["unit_snapshot"].widget.attrs["data-material-unit"] = "true"

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is not None and quantity <= 0:
            raise ValidationError("A quantidade deve ser maior que zero.")
        return quantity
