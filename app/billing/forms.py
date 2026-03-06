from django import forms
from django.core.exceptions import ValidationError

from billing.models import (
    MeasurementLine,
    MeasurementLineKind,
    MeasurementPeriod,
    MeasurementSettlement,
)
from catalog.models import BudgetItem, Unit
from core.models import ProjectLocation


class MeasurementPeriodForm(forms.ModelForm):
    class Meta:
        model = MeasurementPeriod
        fields = ["ref_month", "start_date", "end_date", "notes"]
        widgets = {
            "ref_month": forms.DateInput(attrs={"type": "date"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_ref_month(self):
        ref_month = self.cleaned_data["ref_month"]
        if ref_month.day != 1:
            raise ValidationError("ref_month deve estar no formato YYYY-MM-01.")
        return ref_month


class MeasurementLineForm(forms.ModelForm):
    class Meta:
        model = MeasurementLine
        fields = ["item", "location", "qty_period", "justification", "note"]
        widgets = {
            "justification": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["item"].queryset = BudgetItem.objects.filter(
            project=period.project, is_active=True
        ).order_by("eap_code")
        self.fields["location"].queryset = ProjectLocation.objects.filter(
            project=period.project, is_active=True
        ).order_by("order_index", "code")
        self.fields["location"].required = False
        self.fields["justification"].required = False
        self.fields["note"].required = False

    def clean_qty_period(self):
        qty = self.cleaned_data["qty_period"]
        if qty < 0:
            raise ValidationError("qty_period deve ser >= 0.")
        return qty

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.period = self.period
        instance.line_kind = MeasurementLineKind.CONTRACTED
        if commit:
            instance.save()
        return instance


class ExtraLineForm(forms.ModelForm):
    class Meta:
        model = MeasurementLine
        fields = [
            "extra_description",
            "extra_unit",
            "extra_pu_material",
            "extra_pu_labor",
            "qty_period",
            "location",
            "justification",
            "note",
        ]
        widgets = {
            "justification": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["extra_unit"].queryset = Unit.objects.order_by("code")
        self.fields["location"].queryset = ProjectLocation.objects.filter(
            project=period.project, is_active=True
        ).order_by("order_index", "code")
        self.fields["location"].required = False
        self.fields["justification"].required = False
        self.fields["note"].required = False

    def clean_qty_period(self):
        qty = self.cleaned_data["qty_period"]
        if qty < 0:
            raise ValidationError("qty_period deve ser >= 0.")
        return qty

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("extra_description"):
            self.add_error("extra_description", "Descricao extra obrigatoria.")
        if not cleaned_data.get("extra_unit"):
            self.add_error("extra_unit", "Unidade obrigatoria.")
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.period = self.period
        instance.line_kind = MeasurementLineKind.EXTRA
        instance.item = None
        if commit:
            instance.save()
        return instance


class SettlementForm(forms.ModelForm):
    class Meta:
        model = MeasurementSettlement
        fields = ["event_date", "amount", "method", "reference", "notes"]
        widgets = {
            "event_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount < 0:
            raise ValidationError("amount deve ser >= 0.")
        return amount
