from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from catalog.models import BudgetImportMode, BudgetItem, BudgetItemAdditionalMaterial
from core.models import Project


class BudgetImportUploadForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.order_by("name"),
        required=True,
    )
    mode = forms.ChoiceField(
        choices=BudgetImportMode.choices,
        initial=BudgetImportMode.UPSERT_BY_EAP,
        required=True,
    )
    csv_file = forms.FileField(required=True)


class BudgetItemAdditionalMaterialForm(forms.ModelForm):
    class Meta:
        model = BudgetItemAdditionalMaterial
        fields = ("additional_item", "quantity_per_unit", "note", "is_active")

    def __init__(self, *args, parent_item=None, **kwargs):
        super().__init__(*args, **kwargs)
        parent_item = parent_item or getattr(self.instance, "parent_item", None)
        self.parent_item = parent_item
        if parent_item and not getattr(self.instance, "parent_item_id", None):
            self.instance.parent_item = parent_item
        queryset = BudgetItem.objects.none()

        if parent_item and parent_item.pk:
            queryset = (
                BudgetItem.objects.filter(project_id=parent_item.project_id, is_active=True)
                .exclude(pk=parent_item.pk)
                .select_related("project", "unit")
                .order_by("eap_code", "description")
            )

        self.fields["additional_item"].queryset = queryset

    def clean(self):
        cleaned_data = super().clean()
        parent_item = self.parent_item or getattr(self.instance, "parent_item", None)
        additional_item = cleaned_data.get("additional_item")

        if not parent_item or not additional_item:
            return cleaned_data

        if parent_item.pk == additional_item.pk:
            raise ValidationError("parent_item e additional_item nao podem ser o mesmo item.")

        if parent_item.project_id != additional_item.project_id:
            raise ValidationError("Os materiais adicionais devem pertencer ao mesmo projeto.")

        return cleaned_data


class BudgetItemAdditionalMaterialInlineFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["parent_item"] = self.instance
        return kwargs
