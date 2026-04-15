import csv
import io
import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import LocationTemplate, Project, ProjectLocation, ProjectStage

STAGE_CSV_REQUIRED_FIELDS = ("code", "name")
STAGE_CSV_OPTIONAL_FIELDS = ("order_index", "is_active")


def get_stage_prefix_from_eap(eap_code) -> str:
    text = str(eap_code or "").strip()
    if "." not in text:
        return text
    return text.rsplit(".", 1)[0]


def resolve_project_stage(project: Project, eap_code) -> ProjectStage | None:
    prefix = get_stage_prefix_from_eap(eap_code)
    if not prefix or project is None:
        return None
    return ProjectStage.objects.filter(project=project, code=prefix, is_active=True).first()


def _decode_csv_content(csv_content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return csv_content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("nao foi possivel decodificar o CSV")


def _detect_csv_dialect(csv_text: str) -> csv.Dialect:
    sample = csv_text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        return csv.get_dialect("excel")


def _normalize_stage_csv_header(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.strip().lower()


def _read_csv_file(csv_file) -> bytes:
    if isinstance(csv_file, bytes):
        return csv_file
    if isinstance(csv_file, str):
        return csv_file.encode("utf-8")
    return csv_file.read()


def _parse_stage_bool(raw_value) -> bool:
    value = str(raw_value or "").strip().casefold()
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    if value == "":
        return True
    if value in {"1", "true", "yes", "y", "sim", "s", "ativo", "active"}:
        return True
    if value in {"0", "false", "no", "n", "nao", "inativo", "inactive"}:
        return False
    raise ValueError(f"is_active invalido: {raw_value!r}")


def _parse_stage_order_index(raw_value) -> int:
    value = str(raw_value or "").strip()
    if value == "":
        return 0
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"order_index invalido: {raw_value!r}") from exc


def import_project_stages_from_csv(csv_file, project: Project) -> dict:
    result = {
        "total": 0,
        "created": 0,
        "updated": 0,
        "errors": [],
    }

    try:
        csv_text = _decode_csv_content(_read_csv_file(csv_file))
        dialect = _detect_csv_dialect(csv_text)
        reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
    except Exception as exc:
        result["errors"].append({"line": 1, "error": str(exc)})
        return result

    if not reader.fieldnames:
        result["errors"].append({"line": 1, "error": "CSV sem cabecalho"})
        return result

    field_mapping = {_normalize_stage_csv_header(header): header for header in reader.fieldnames}
    missing_fields = [field for field in STAGE_CSV_REQUIRED_FIELDS if field not in field_mapping]
    if missing_fields:
        result["errors"].append(
            {
                "line": 1,
                "error": f"colunas obrigatorias ausentes: {', '.join(missing_fields)}",
            }
        )
        return result

    for line_number, row in enumerate(reader, start=2):
        result["total"] += 1
        code = (row.get(field_mapping["code"]) or "").strip()
        name = (row.get(field_mapping["name"]) or "").strip()

        row_errors = []
        if not code:
            row_errors.append("code obrigatorio")
        if not name:
            row_errors.append("name obrigatorio")

        try:
            order_index = _parse_stage_order_index(row.get(field_mapping.get("order_index", "")))
        except ValueError as exc:
            row_errors.append(str(exc))
            order_index = 0

        try:
            is_active = _parse_stage_bool(row.get(field_mapping.get("is_active", "")))
        except ValueError as exc:
            row_errors.append(str(exc))
            is_active = True

        if row_errors:
            result["errors"].append({"line": line_number, "error": "; ".join(row_errors)})
            continue

        try:
            _, created = ProjectStage.objects.update_or_create(
                project=project,
                code=code,
                defaults={
                    "name": name,
                    "order_index": order_index,
                    "is_active": is_active,
                },
            )
        except Exception as exc:
            result["errors"].append({"line": line_number, "error": str(exc)})
            continue

        if created:
            result["created"] += 1
        else:
            result["updated"] += 1

    return result


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
