from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class WeatherCondition(models.TextChoices):
    CLEAR = "CLEAR", "Limpo"
    CLOUDY = "CLOUDY", "Nublado"
    RAINY = "RAINY", "Chuvoso"
    WINDY = "WINDY", "Vento"
    STOPPED = "STOPPED", "Paralisado"


class OccurrenceType(models.TextChoices):
    ORIENTACAO = "ORIENTACAO", "Orientação"
    ACIDENTE = "ACIDENTE", "Acidente"
    INTERRUPCAO = "INTERRUPCAO", "Interrupção"
    VISITA = "VISITA", "Visita"
    INSPECAO = "INSPECAO", "Inspeção"
    OUTRO = "OUTRO", "Outro"


class ProjectWorkOrderInfo(models.Model):
    project = models.OneToOneField("core.Project", on_delete=models.CASCADE, related_name="work_order_info")
    art_number = models.CharField(max_length=80, blank=True)
    contractor_name = models.CharField(max_length=255, blank=True)
    contractor_document = models.CharField(max_length=32, blank=True)
    technical_manager_name = models.CharField(max_length=255, blank=True)
    technical_manager_crea = models.CharField(max_length=80, blank=True)
    work_start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    address_snapshot = models.CharField(max_length=255, blank=True)
    contract_number = models.CharField(max_length=80, blank=True)
    contract_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    additional_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project__name"]

    def __str__(self) -> str:
        return f"Livro de Ordem - {self.project.name}"


class DailyWorkLog(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="daily_work_logs")
    log_date = models.DateField()
    responsible_name = models.CharField(max_length=255)
    weather_morning = models.CharField(max_length=20, choices=WeatherCondition.choices, blank=True)
    weather_afternoon = models.CharField(max_length=20, choices=WeatherCondition.choices, blank=True)
    weather_night = models.CharField(max_length=20, choices=WeatherCondition.choices, blank=True)
    notes = models.TextField(blank=True)
    general_observation = models.TextField(blank=True)
    interruption_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_work_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "log_date"], name="uniq_daily_work_log_project_date"),
        ]
        ordering = ["-log_date", "-id"]

    def __str__(self) -> str:
        return f"{self.project.name} - {self.log_date:%d/%m/%Y}"


class DailyWorkTeamEntry(models.Model):
    daily_log = models.ForeignKey(DailyWorkLog, on_delete=models.CASCADE, related_name="team_entries")
    team_name = models.CharField(max_length=120)
    worker_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    import_tag = models.CharField(max_length=64, blank=True, default="", db_index=True)
    location = models.ForeignKey(
        "core.ProjectLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_work_team_entries",
    )
    activity_description = models.TextField(default="")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["team_name", "id"]

    def __str__(self) -> str:
        return f"{self.team_name} ({self.worker_count})"

    def clean(self):
        super().clean()

        if self.worker_count is not None and self.worker_count <= 0:
            raise ValidationError({"worker_count": "A quantidade deve ser maior que zero."})

        project_id = getattr(self.daily_log, "project_id", None)
        if self.location_id and project_id and self.location.project_id != project_id:
            raise ValidationError({"location": "O local deve pertencer à mesma obra do diário."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class DailyWorkActivityEntry(models.Model):
    daily_log = models.ForeignKey(DailyWorkLog, on_delete=models.CASCADE, related_name="activity_entries")
    description = models.TextField()
    import_tag = models.CharField(max_length=64, blank=True, default="", db_index=True)
    location = models.ForeignKey(
        "core.ProjectLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_work_activities",
    )
    discipline = models.ForeignKey(
        "catalog.Discipline",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_work_activities",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return self.description[:80]


class DailyWorkOccurrence(models.Model):
    daily_log = models.ForeignKey(DailyWorkLog, on_delete=models.CASCADE, related_name="occurrences")
    occurrence_type = models.CharField(max_length=20, choices=OccurrenceType.choices)
    description = models.TextField()
    import_tag = models.CharField(max_length=64, blank=True, default="", db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.get_occurrence_type_display()} - {self.description[:60]}"


class DailyWorkMaterialEntry(models.Model):
    daily_log = models.ForeignKey(DailyWorkLog, on_delete=models.CASCADE, related_name="material_entries")
    item = models.ForeignKey(
        "catalog.BudgetItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_work_material_entries",
    )
    import_tag = models.CharField(max_length=64, blank=True, default="", db_index=True)
    description_snapshot = models.CharField(max_length=255, blank=True)
    location = models.ForeignKey(
        "core.ProjectLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_work_material_entries",
    )
    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_snapshot = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        description = self.description_snapshot or getattr(self.item, "description", "") or "Material"
        return f"{description} - {self.quantity}"

    def clean(self):
        super().clean()

        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "A quantidade deve ser maior que zero."})

        project_id = getattr(self.daily_log, "project_id", None)
        if self.item_id and project_id and self.item.project_id != project_id:
            raise ValidationError({"item": "O material deve pertencer à mesma obra do diário."})

        if self.location_id and project_id and self.location.project_id != project_id:
            raise ValidationError({"location": "O local deve pertencer à mesma obra do diário."})

    def save(self, *args, **kwargs):
        if self.item_id:
            self.description_snapshot = self.item.description
            self.unit_snapshot = self.item.unit.code
        self.full_clean()
        super().save(*args, **kwargs)
