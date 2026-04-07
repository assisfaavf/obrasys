from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import LocationTemplate, Project, ProjectLocation


@transaction.atomic
def apply_location_template_to_project(*, project: Project, template: LocationTemplate | None = None) -> dict:
    template = template or project.location_template
    if template is None:
        raise ValidationError("Selecione um template de locais antes de aplicar.")

    template_items = list(template.items.order_by("order_index", "code"))
    existing_codes = set(project.locations.values_list("code", flat=True))

    to_create = []
    skipped_codes: list[str] = []

    for item in template_items:
        if item.code in existing_codes:
            skipped_codes.append(item.code)
            continue
        to_create.append(
            ProjectLocation(
                project=project,
                code=item.code,
                name=item.name,
                order_index=item.order_index,
            )
        )
        existing_codes.add(item.code)

    if to_create:
        ProjectLocation.objects.bulk_create(to_create)

    return {
        "template_name": template.name,
        "created_count": len(to_create),
        "skipped_count": len(skipped_codes),
        "skipped_codes": skipped_codes,
    }
