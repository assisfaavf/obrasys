from django.db import models


class ExportType(models.TextChoices):
    SIENGE_MASTER = "SIENGE_MASTER", "Sienge master"
    SIENGE_SNAPSHOT = "SIENGE_SNAPSHOT", "Sienge snapshot"
    DOCX_TIMBRADO = "DOCX_TIMBRADO", "Docx timbrado"
    PDF_TIMBRADO = "PDF_TIMBRADO", "Pdf timbrado"


class ExportStatus(models.TextChoices):
    OK = "OK", "Ok"
    ERROR = "ERROR", "Error"


class MeasurementExport(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="exports")
    period = models.ForeignKey(
        "billing.MeasurementPeriod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exports",
    )
    export_type = models.CharField(max_length=20, choices=ExportType.choices)
    file_path = models.CharField(max_length=500)
    status = models.CharField(max_length=10, choices=ExportStatus.choices)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "id"]

    def __str__(self) -> str:
        return f"{self.project.name} - {self.export_type}"
