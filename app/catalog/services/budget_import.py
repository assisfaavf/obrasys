import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db import transaction

from catalog.models import (
    BudgetImportJob,
    BudgetImportJobStatus,
    BudgetImportMode,
    BudgetItem,
    Unit,
)
from catalog.utils import strip_manufacturer_hint

CANONICAL_FIELDS = (
    "eap_code",
    "description",
    "unit",
    "qty_contracted",
    "pu_material",
    "pu_labor",
)

HEADER_SYNONYMS = {
    "eap_code": {"ITEM", "EAP", "CODIGO"},
    "description": {"MATERIAL SERVICO", "DESCRICAO", "SERVICO"},
    "unit": {"UD", "UN", "UNIDADE"},
    "qty_contracted": {"QUANT", "QTD", "QUANTIDADE"},
    "pu_material": {"P UNIT MAT", "PU MAT", "UNIT MAT", "PUNITMAT"},
    "pu_labor": {"P UNIT MDO", "PU MDO", "UNIT MDO", "PUNITMDO"},
}

PRICE_FIELDS = {"pu_material", "pu_labor"}
IMPORT_TEMPLATE_NAME = "modelo_importacao_itens_contrato.csv"


def normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", (value or ""))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.upper().strip()
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def parse_decimal(raw_value: str, *, empty_is_zero: bool) -> Decimal:
    value = (raw_value or "").strip()
    if value == "":
        if empty_is_zero:
            return Decimal("0")
        raise ValueError("valor vazio")

    value = value.replace(" ", "")
    has_comma = "," in value
    has_dot = "." in value

    if has_comma and has_dot:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif has_comma:
        if value.count(",") > 1:
            value = value.replace(",", "")
        else:
            comma_index = value.rfind(",")
            digits_after = len(value) - comma_index - 1
            if digits_after == 3 and len(value[:comma_index].replace("-", "")) >= 1:
                value = value.replace(",", "")
            else:
                value = value.replace(",", ".")
    elif has_dot:
        if value.count(".") > 1:
            value = value.replace(".", "")
        else:
            dot_index = value.rfind(".")
            digits_after = len(value) - dot_index - 1
            if digits_after == 3 and len(value[:dot_index].replace("-", "")) >= 1:
                value = value.replace(".", "")

    if not re.fullmatch(r"-?\d+(\.\d+)?", value):
        raise ValueError(f"valor numerico invalido: {raw_value!r}")

    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"valor numerico invalido: {raw_value!r}") from exc


def _decode_csv_content(csv_content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return csv_content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("nao foi possivel decodificar o CSV")


def _detect_dialect(csv_text: str) -> csv.Dialect:
    sample = csv_text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        return csv.get_dialect("excel")


def _build_field_mapping(fieldnames: list[str]) -> tuple[dict[str, str], list[str]]:
    normalized_headers = {header: normalize_header(header) for header in fieldnames}
    mapped_fields: dict[str, str] = {}

    for canonical in CANONICAL_FIELDS:
        synonyms = HEADER_SYNONYMS[canonical]
        for original, normalized in normalized_headers.items():
            if normalized in synonyms:
                mapped_fields[canonical] = original
                break

    missing_fields = [field for field in CANONICAL_FIELDS if field not in mapped_fields]
    return mapped_fields, missing_fields


def build_preview_data(csv_content: bytes) -> dict:
    csv_text = _decode_csv_content(csv_content)
    dialect = _detect_dialect(csv_text)
    reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)

    if not reader.fieldnames:
        raise ValueError("CSV sem cabecalho")

    mapped_fields, missing_fields = _build_field_mapping(reader.fieldnames)
    payload_errors = []
    if missing_fields:
        payload_errors.append(
            {
                "line": 1,
                "field": "header",
                "message": f"colunas obrigatorias ausentes: {', '.join(sorted(missing_fields))}",
            }
        )

    preview_rows = []
    normalized_rows = []

    for line_number, row in enumerate(reader, start=2):
        normalized_row = {
            "line_number": line_number,
            "eap_code": "",
            "description": "",
            "unit": "",
            "qty_contracted": "0",
            "pu_material": "0",
            "pu_labor": "0",
            "errors": [],
            "valid": False,
        }

        if missing_fields:
            normalized_row["errors"].append("faltam colunas obrigatorias no cabecalho")
            normalized_rows.append(normalized_row)
            if len(preview_rows) < 20:
                preview_rows.append(normalized_row)
            continue

        eap_code = (row.get(mapped_fields["eap_code"]) or "").strip()
        description = strip_manufacturer_hint((row.get(mapped_fields["description"]) or "").strip())
        unit_code = (row.get(mapped_fields["unit"]) or "").strip().upper()

        normalized_row["eap_code"] = eap_code
        normalized_row["description"] = description
        normalized_row["unit"] = unit_code

        if not eap_code:
            normalized_row["errors"].append("eap_code obrigatorio")
        if not description:
            normalized_row["errors"].append("description obrigatorio")
        if not unit_code:
            normalized_row["errors"].append("unit obrigatorio")

        for field in ("qty_contracted", "pu_material", "pu_labor"):
            raw_value = row.get(mapped_fields[field], "")
            try:
                parsed_value = parse_decimal(raw_value, empty_is_zero=(field in PRICE_FIELDS))
            except ValueError as exc:
                normalized_row["errors"].append(f"{field}: {exc}")
                continue

            if parsed_value < 0:
                normalized_row["errors"].append(f"{field} deve ser >= 0")
            normalized_row[field] = str(parsed_value)

        normalized_row["valid"] = not normalized_row["errors"]
        normalized_rows.append(normalized_row)

        if normalized_row["errors"]:
            payload_errors.append(
                {
                    "line": line_number,
                    "field": "row",
                    "message": "; ".join(normalized_row["errors"]),
                }
            )

        if len(preview_rows) < 20:
            preview_rows.append(normalized_row)

    return {
        "mapped_headers": mapped_fields,
        "missing_fields": missing_fields,
        "rows_total": len(normalized_rows),
        "preview_rows": preview_rows,
        "normalized_rows": normalized_rows,
        "errors": payload_errors,
        "has_fatal_errors": bool(missing_fields),
    }


def create_preview_job(*, project, mode: str, original_filename: str, csv_content: bytes, created_by=None):
    preview_json = {}
    summary_json = {}
    status = BudgetImportJobStatus.PREVIEW

    try:
        preview_json = build_preview_data(csv_content)
    except Exception as exc:
        status = BudgetImportJobStatus.ERROR
        summary_json = {"error_message": str(exc)}

    return BudgetImportJob.objects.create(
        project=project,
        mode=mode,
        original_filename=original_filename,
        created_by=created_by,
        status=status,
        summary_json=summary_json,
        preview_json=preview_json,
    )


def apply_import_job(job: BudgetImportJob) -> dict:
    try:
        if job.status != BudgetImportJobStatus.PREVIEW:
            raise ValueError("somente imports em PREVIEW podem ser aplicados")

        preview_json = job.preview_json or {}
        if preview_json.get("has_fatal_errors"):
            raise ValueError("preview contem erros fatais de cabecalho")

        normalized_rows = preview_json.get("normalized_rows") or []
        if not isinstance(normalized_rows, list):
            raise ValueError("preview invalido: normalized_rows ausente")

        summary = {
            "created": 0,
            "updated": 0,
            "deactivated": 0,
            "skipped": 0,
            "errors": [],
        }

        imported_codes = set()

        with transaction.atomic():
            for row in normalized_rows:
                line_number = row.get("line_number", 0)
                row_errors = row.get("errors", [])
                if row_errors:
                    summary["skipped"] += 1
                    summary["errors"].append({"line": line_number, "message": "; ".join(row_errors)})
                    continue

                eap_code = (row.get("eap_code") or "").strip()
                description = strip_manufacturer_hint((row.get("description") or "").strip())
                unit_code = (row.get("unit") or "").strip().upper()
                qty_contracted = Decimal(str(row.get("qty_contracted", "0")))
                pu_material = Decimal(str(row.get("pu_material", "0")))
                pu_labor = Decimal(str(row.get("pu_labor", "0")))

                unit, _ = Unit.objects.get_or_create(code=unit_code, defaults={"name": unit_code})
                imported_codes.add(eap_code)

                defaults = {
                    "description": description,
                    "unit": unit,
                    "qty_contracted": qty_contracted,
                    "pu_material": pu_material,
                    "pu_labor": pu_labor,
                    "is_active": True,
                }

                if job.mode == BudgetImportMode.CREATE_ONLY:
                    if BudgetItem.objects.filter(project=job.project, eap_code=eap_code).exists():
                        summary["skipped"] += 1
                        continue
                    BudgetItem.objects.create(project=job.project, eap_code=eap_code, **defaults)
                    summary["created"] += 1
                    continue

                if job.mode in {BudgetImportMode.UPSERT_BY_EAP, BudgetImportMode.REPLACE_ALL}:
                    _, created = BudgetItem.objects.update_or_create(
                        project=job.project,
                        eap_code=eap_code,
                        defaults=defaults,
                    )
                    if created:
                        summary["created"] += 1
                    else:
                        summary["updated"] += 1
                    continue

                raise ValueError(f"modo de importacao invalido: {job.mode}")

            if job.mode == BudgetImportMode.REPLACE_ALL:
                deactivated_count = (
                    BudgetItem.objects.filter(project=job.project, is_active=True)
                    .exclude(eap_code__in=imported_codes)
                    .update(is_active=False)
                )
                summary["deactivated"] = deactivated_count

            job.summary_json = summary
            job.status = BudgetImportJobStatus.APPLIED
            job.save(update_fields=["summary_json", "status"])

        return summary
    except Exception as exc:
        job.status = BudgetImportJobStatus.ERROR
        job.summary_json = {
            **(job.summary_json or {}),
            "error_message": str(exc),
        }
        job.save(update_fields=["summary_json", "status"])
        raise
