from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.core.exceptions import ValidationError
from django.utils import timezone

from billing.models import (
    MeasurementLine,
    MeasurementLineKind,
    MeasurementLineHistory,
    MeasurementPeriod,
    MeasurementSettlement,
)
from billing.services.measurement_calc import split_contracted_and_excess
from catalog.models import BudgetItem, Unit
from core.models import ProjectLocation


def _apply_measurement_item_select_attrs(field) -> None:
    existing_class = field.widget.attrs.get("class", "").strip()
    classes = [name for name in existing_class.split() if name]
    if "measurement-item-select" not in classes:
        classes.append("measurement-item-select")
    field.widget.attrs["class"] = " ".join(classes)


class MeasurementPeriodForm(forms.ModelForm):
    ref_month = forms.DateField(
        input_formats=["%m/%Y", "%Y-%m", "%Y-%m-%d"],
        error_messages={"invalid": "Informe o mes de referencia no formato MM/AAAA."},
        widget=forms.DateInput(
            format="%Y-%m",
            attrs={
                "type": "month",
                "placeholder": "MM/AAAA",
                "title": "Mes de referencia (MM/AAAA)",
            },
        ),
        help_text="Formato: MM/AAAA",
    )

    class Meta:
        model = MeasurementPeriod
        fields = ["ref_month", "start_date", "end_date", "notes"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ref_month"].label = "Mes de referencia"
        value = self.initial.get("ref_month")
        if value is None and getattr(self.instance, "pk", None):
            value = self.instance.ref_month
        if value:
            self.initial["ref_month"] = value.strftime("%Y-%m")

    def clean_ref_month(self):
        ref_month = self.cleaned_data["ref_month"]
        return ref_month.replace(day=1)


class MeasurementLineForm(forms.ModelForm):
    class Meta:
        model = MeasurementLine
        fields = ["item", "location", "qty_period", "excess_justification", "note"]
        widgets = {
            "excess_justification": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["item"].queryset = BudgetItem.objects.filter(
            project=period.project, is_active=True
        ).order_by("eap_code")
        _apply_measurement_item_select_attrs(self.fields["item"])
        self.fields["location"].queryset = ProjectLocation.objects.filter(
            project=period.project, is_active=True
        ).order_by("order_index", "code")
        self.fields["location"].required = False
        self.fields["excess_justification"].required = False
        self.fields["excess_justification"].label = "Justificativa do excedente"
        self.fields["note"].required = False

    def clean_qty_period(self):
        qty = self.cleaned_data["qty_period"]
        if qty < 0:
            raise ValidationError("qty_period deve ser >= 0.")
        return qty

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get("item")
        qty_period = cleaned_data.get("qty_period")
        if not item or qty_period is None:
            return cleaned_data

        probe_line = MeasurementLine(
            period=self.period,
            line_kind=MeasurementLineKind.CONTRACTED,
            item=item,
            qty_period=qty_period,
        )
        contracted_effective_qty, excess_qty, saldo_antes = split_contracted_and_excess(probe_line)

        cleaned_data["contracted_effective_qty"] = contracted_effective_qty
        cleaned_data["excess_qty"] = excess_qty
        cleaned_data["saldo_antes"] = saldo_antes

        if excess_qty > 0 and not (cleaned_data.get("excess_justification") or "").strip():
            self.add_error("excess_justification", "Justificativa do excedente e obrigatoria.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.period = self.period
        instance.line_kind = MeasurementLineKind.CONTRACTED
        instance.justification = ""
        instance.excess_qty = self.cleaned_data.get("excess_qty", Decimal("0"))
        instance.excess_justification = (self.cleaned_data.get("excess_justification") or "").strip()
        if instance.excess_qty == 0:
            instance.excess_justification = ""
        if commit:
            instance.save()
        return instance


class ContractedLineAddForm(forms.ModelForm):
    application_date = forms.DateField(
        required=False,
        label="Data de aplicacao",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    use_today = forms.BooleanField(required=False, label="Hoje")
    use_additional_materials = forms.BooleanField(required=False, label="Usar materiais adicionais")

    class Meta:
        model = MeasurementLine
        fields = ["item", "location", "qty_period", "excess_justification", "note"]
        widgets = {
            "excess_justification": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["item"].queryset = BudgetItem.objects.filter(
            project=period.project, is_active=True
        ).order_by("eap_code")
        _apply_measurement_item_select_attrs(self.fields["item"])
        self.fields["location"].queryset = ProjectLocation.objects.filter(
            project=period.project, is_active=True
        ).order_by("order_index", "code")
        self.fields["location"].required = False
        self.fields["excess_justification"].required = False
        self.fields["excess_justification"].label = "Justificativa do excedente"
        self.fields["note"].required = False

    def clean_qty_period(self):
        qty = self.cleaned_data["qty_period"]
        if qty <= 0:
            raise ValidationError("quantity_added deve ser > 0.")
        return qty

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get("item")
        qty_period = cleaned_data.get("qty_period")
        application_date = cleaned_data.get("application_date")

        if cleaned_data.get("use_today") or not application_date:
            application_date = timezone.localdate()
            cleaned_data["application_date"] = application_date

        if not item or qty_period is None:
            return cleaned_data

        probe_line = MeasurementLine(
            period=self.period,
            line_kind=MeasurementLineKind.CONTRACTED,
            item=item,
            qty_period=qty_period,
        )
        contracted_effective_qty, excess_qty, saldo_antes = split_contracted_and_excess(probe_line)

        cleaned_data["contracted_effective_qty"] = contracted_effective_qty
        cleaned_data["excess_qty"] = excess_qty
        cleaned_data["saldo_antes"] = saldo_antes

        if excess_qty > 0 and not (cleaned_data.get("excess_justification") or "").strip():
            self.add_error("excess_justification", "Justificativa do excedente e obrigatoria.")

        return cleaned_data


class BulkContractedLineCommonForm(forms.Form):
    location = forms.ModelChoiceField(
        queryset=ProjectLocation.objects.none(),
        required=False,
        label="Localizacao",
    )
    application_date = forms.DateField(
        required=False,
        label="Data de aplicacao",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    use_today = forms.BooleanField(required=False, label="Hoje")
    note = forms.CharField(
        required=False,
        label="Notas",
        max_length=255,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    excess_justification = forms.CharField(
        required=False,
        label="Justificativa do excedente",
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["location"].queryset = ProjectLocation.objects.filter(
            project=period.project,
            is_active=True,
        ).order_by("order_index", "code")

    def clean(self):
        cleaned_data = super().clean()
        application_date = cleaned_data.get("application_date")
        if cleaned_data.get("use_today") or not application_date:
            cleaned_data["application_date"] = timezone.localdate()
        return cleaned_data


class BulkContractedLineItemForm(forms.Form):
    item = forms.ModelChoiceField(
        queryset=BudgetItem.objects.none(),
        required=False,
        label="Item",
    )
    qty_period = forms.DecimalField(
        required=False,
        label="Quantidade no periodo",
        max_digits=14,
        decimal_places=3,
    )

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, **kwargs)
        self.period = period
        self.fields["item"].queryset = BudgetItem.objects.filter(
            project=period.project,
            is_active=True,
        ).order_by("eap_code")
        _apply_measurement_item_select_attrs(self.fields["item"])

    def clean(self):
        cleaned_data = super().clean()
        item = cleaned_data.get("item")
        qty_period = cleaned_data.get("qty_period")
        is_marked_for_delete = self.cleaned_data.get("DELETE", False)

        if is_marked_for_delete:
            return cleaned_data
        if not item and qty_period is None:
            cleaned_data["is_empty"] = True
            return cleaned_data
        if not item:
            self.add_error("item", "Item obrigatorio.")
        if qty_period is None:
            self.add_error("qty_period", "Quantidade obrigatoria.")
        elif qty_period <= 0:
            self.add_error("qty_period", "quantity_added deve ser > 0.")
        cleaned_data["is_empty"] = False
        return cleaned_data


class BaseBulkContractedLineItemFormSet(BaseFormSet):
    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        self.period = period
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["period"] = self.period
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen_item_ids: set[int] = set()
        valid_rows = 0
        for form in self.forms:
            if not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE") or form.cleaned_data.get("is_empty"):
                continue
            item = form.cleaned_data.get("item")
            if not item:
                continue
            valid_rows += 1
            if item.id in seen_item_ids:
                form.add_error("item", f'O item "{item}" foi informado mais de uma vez.')
                raise ValidationError("Ha itens duplicados no cadastro em lote.")
            seen_item_ids.add(item.id)

        if valid_rows == 0:
            raise ValidationError("Informe pelo menos um item para cadastrar.")

    def valid_item_forms(self):
        for form in self.forms:
            if not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE") or form.cleaned_data.get("is_empty"):
                continue
            yield form


BulkContractedLineItemFormSet = formset_factory(
    BulkContractedLineItemForm,
    formset=BaseBulkContractedLineItemFormSet,
    extra=1,
    can_delete=True,
)


class ContractedLineEditForm(MeasurementLineForm):
    use_additional_materials = forms.BooleanField(required=False, label="Usar materiais adicionais")

    def __init__(self, *args, period: MeasurementPeriod, **kwargs):
        super().__init__(*args, period=period, **kwargs)
        if not self.is_bound:
            self.initial["use_additional_materials"] = (
                (self.instance.additional_materials_base_qty or Decimal("0")) > Decimal("0")
            )

    def clean(self):
        cleaned_data = super().clean()
        qty_period = cleaned_data.get("qty_period")
        use_additional_materials = cleaned_data.get("use_additional_materials", False)

        if qty_period is not None:
            self.instance.additional_materials_base_qty = (
                qty_period if use_additional_materials else Decimal("0")
            )
        self.instance.use_additional_materials = bool(use_additional_materials)
        return cleaned_data


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


class MeasurementLineHistoryEditForm(forms.ModelForm):
    uses_additional_materials = forms.BooleanField(required=False, label="Usar materiais adicionais neste lancamento")

    class Meta:
        model = MeasurementLineHistory
        fields = ["quantity_added", "application_date", "note"]
        widgets = {
            "application_date": forms.DateInput(attrs={"type": "date"}),
            "note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["note"].required = False
        if not self.is_bound:
            self.initial["uses_additional_materials"] = (
                (self.instance.additional_materials_quantity or Decimal("0")) > Decimal("0")
            )

    def clean_quantity_added(self):
        quantity_added = self.cleaned_data["quantity_added"]
        if quantity_added <= 0:
            raise ValidationError("quantity_added deve ser > 0.")
        return quantity_added

    def clean(self):
        cleaned_data = super().clean()
        quantity_added = cleaned_data.get("quantity_added")
        uses_additional_materials = cleaned_data.get("uses_additional_materials", False)

        if quantity_added is not None:
            self.instance.additional_materials_quantity = (
                quantity_added if uses_additional_materials else Decimal("0")
            )
        return cleaned_data


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
