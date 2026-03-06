from django import forms

from catalog.models import BudgetImportMode
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
