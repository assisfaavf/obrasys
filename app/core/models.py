from django.db import models


class ProjectStatus(models.TextChoices):
    PLANNED = "PLANNED", "Planned"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    PAUSED = "PAUSED", "Paused"
    DONE = "DONE", "Done"


class Client(models.Model):
    name = models.CharField(max_length=255)
    document = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    def __str__(self) -> str:
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=255)
    client = models.ForeignKey("core.Client", on_delete=models.CASCADE, related_name="projects")
    location_template = models.ForeignKey(
        "core.LocationTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    address = models.CharField(max_length=255, blank=True)
    start_date = models.DateField(null=True, blank=True)
    planned_end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ProjectStatus.choices,
        default=ProjectStatus.PLANNED,
    )

    def __str__(self) -> str:
        return self.name


class ProjectLocation(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="locations")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    order_index = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_project_location_code"),
        ]
        ordering = ["project_id", "order_index", "code"]

    def __str__(self) -> str:
        return self.code


class ProjectStage(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="stages")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=255)
    order_index = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "code"], name="uniq_project_stage_code"),
        ]
        ordering = ["project_id", "order_index", "code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class LocationTemplate(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def __str__(self) -> str:
        return self.name


class LocationTemplateItem(models.Model):
    template = models.ForeignKey(
        "core.LocationTemplate", on_delete=models.CASCADE, related_name="items"
    )
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    order_index = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "code"], name="uniq_location_template_item_code"
            ),
        ]
        ordering = ["template_id", "order_index", "code"]

    def __str__(self) -> str:
        return f"{self.template.name} - {self.code}"
