from django import forms

from core.models import Project


class ProjectAdminForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location_template"].required = False
        self.fields["location_template"].queryset = self.fields["location_template"].queryset.order_by("name")


class ProjectLocationTemplateForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["location_template"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location_template"].required = False
        self.fields["location_template"].queryset = self.fields["location_template"].queryset.order_by("name")
