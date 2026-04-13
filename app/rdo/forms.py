from django import forms
from django.core.exceptions import ValidationError

from catalog.models import Discipline
from core.models import ProjectLocation
from rdo.models import (
    DailyWorkActivityEntry,
    DailyWorkLog,
    DailyWorkOccurrence,
    DailyWorkTeamEntry,
    ProjectWorkOrderInfo,
)


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
                raise ValidationError("Ja existe diario cadastrado para esta obra nesta data.")
        return log_date


class DailyWorkTeamEntryForm(forms.ModelForm):
    class Meta:
        model = DailyWorkTeamEntry
        fields = ("team_name", "contractor_name", "role_or_service", "worker_count", "notes")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class DailyWorkActivityEntryForm(forms.ModelForm):
    class Meta:
        model = DailyWorkActivityEntry
        fields = ("description", "location", "discipline", "notes")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
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
