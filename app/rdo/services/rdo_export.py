from pathlib import Path
from copy import copy

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.text import slugify
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.worksheet import Worksheet

from exports.models import ExportStatus, ExportType, MeasurementExport
from rdo.models import DailyWorkLog
from utils.paths import get_exports_dir, get_template_path

TEMPLATE_NAME = "modelo-de-diario-de-obras-2-0.xlsx"
WORK_ORDER_SHEET = "Livro de Ordem"
DAILY_LOG_SHEET = "Diário de Obras"


def _text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _date_text(value) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y")


def _sheet(workbook, *names: str) -> Worksheet:
    normalized = {name.casefold(): name for name in workbook.sheetnames}
    for name in names:
        existing = normalized.get(name.casefold())
        if existing:
            return workbook[existing]
    return workbook.create_sheet(names[0])


def _write(ws: Worksheet, row: int, col: int, value) -> None:
    cell = ws.cell(row=row, column=col)
    if isinstance(cell, MergedCell):
        for merged_range in ws.merged_cells.ranges:
            if cell.coordinate in merged_range:
                cell = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
                break
    cell.value = value


def _write_pair(ws: Worksheet, row: int, label: str, value) -> None:
    _write(ws, row, 1, label)
    _write(ws, row, 2, value)


def _clear_row(ws: Worksheet, row: int, max_col: int = 6) -> None:
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        if not isinstance(cell, MergedCell):
            cell.value = None


def _copy_row_format(ws: Worksheet, source_row: int, target_row: int, max_col: int = 6) -> None:
    ws.row_dimensions[target_row].height = ws.row_dimensions[source_row].height
    for col in range(1, max_col + 1):
        source = ws.cell(row=source_row, column=col)
        target = ws.cell(row=target_row, column=col)
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        if source.alignment:
            target.alignment = copy(source.alignment)
        if source.border:
            target.border = copy(source.border)
        if source.fill:
            target.fill = copy(source.fill)
        if source.font:
            target.font = copy(source.font)


def _copy_single_row_merges(ws: Worksheet, source_row: int, target_row: int) -> None:
    ranges = list(ws.merged_cells.ranges)
    for merged_range in ranges:
        if merged_range.min_row == source_row and merged_range.max_row == source_row:
            target_range = CellRange(
                min_col=merged_range.min_col,
                max_col=merged_range.max_col,
                min_row=target_row,
                max_row=target_row,
            )
            if str(target_range) not in {str(existing) for existing in ws.merged_cells.ranges}:
                ws.merge_cells(str(target_range))


def _unmerge_single_row(ws: Worksheet, row: int, min_col: int = 1, max_col: int = 6) -> None:
    for merged_range in list(ws.merged_cells.ranges):
        if (
            merged_range.min_row == row
            and merged_range.max_row == row
            and merged_range.min_col <= max_col
            and merged_range.max_col >= min_col
        ):
            ws.unmerge_cells(str(merged_range))


def _merge_row_range(ws: Worksheet, row: int, start_col: int, end_col: int) -> None:
    merge_range = CellRange(min_row=row, max_row=row, min_col=start_col, max_col=end_col)
    if str(merge_range) not in {str(existing) for existing in ws.merged_cells.ranges}:
        ws.merge_cells(str(merge_range))


def _ensure_block_rows(ws: Worksheet, start_row: int, available_rows: int, needed_rows: int, max_col: int = 6) -> int:
    extra_rows = max(0, needed_rows - available_rows)
    if not extra_rows:
        return 0

    insert_at = start_row + available_rows
    ws.insert_rows(insert_at, extra_rows)
    source_row = insert_at - 1
    for row in range(insert_at, insert_at + extra_rows):
        _copy_row_format(ws, source_row, row, max_col=max_col)
        _copy_single_row_merges(ws, source_row, row)
        _clear_row(ws, row, max_col=max_col)
    return extra_rows


def _join_parts(*parts) -> str:
    return " - ".join(part for part in (_text(part) for part in parts) if part)


def fill_work_order_sheet(workbook, project) -> None:
    ws = _sheet(workbook, WORK_ORDER_SHEET)
    try:
        work_order = project.work_order_info
    except ObjectDoesNotExist:
        work_order = None

    _write(ws, 4, 3, getattr(work_order, "art_number", ""))
    _write(ws, 10, 2, getattr(work_order, "technical_manager_name", ""))
    _write(ws, 11, 4, getattr(work_order, "technical_manager_crea", ""))
    _write(ws, 19, 2, getattr(project.client, "name", ""))
    _write(ws, 20, 4, getattr(project.client, "document", ""))
    _write(ws, 21, 2, getattr(work_order, "contractor_name", ""))
    _write(ws, 24, 4, getattr(work_order, "contractor_document", ""))
    _write(ws, 27, 2, _text(getattr(work_order, "address_snapshot", "")) or project.address)
    _write(ws, 32, 2, getattr(work_order, "additional_notes", ""))
    _write(ws, 33, 2, getattr(work_order, "contract_number", ""))
    _write(ws, 34, 2, getattr(work_order, "work_start_date", None) or project.start_date)
    _write(ws, 34, 4, getattr(work_order, "expected_end_date", None) or project.planned_end_date)
    _write(ws, 37, 2, getattr(work_order, "technical_manager_name", ""))
    _write(ws, 38, 2, getattr(project.client, "name", ""))


def _weather_display(daily_log: DailyWorkLog, field_name: str) -> str:
    value = getattr(daily_log, field_name)
    if not value:
        return ""
    return dict(daily_log._meta.get_field(field_name).choices).get(value, value)


def _weather_column(value: str) -> int | None:
    return {
        "CLEAR": 2,
        "CLOUDY": 3,
        "WINDY": 3,
        "RAINY": 4,
        "STOPPED": 5,
    }.get(value)


def _weekday_name(value) -> str:
    if not value:
        return ""
    weekdays = (
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    )
    return weekdays[value.weekday()]


def _entry_material_description(entry) -> str:
    if entry.item_id:
        return entry.description_snapshot or entry.item.description
    return entry.description_snapshot


def _project_work_order(project):
    try:
        return project.work_order_info
    except ObjectDoesNotExist:
        return None


def fill_daily_log_sheet(workbook, daily_log: DailyWorkLog) -> None:
    ws = _sheet(workbook, DAILY_LOG_SHEET, "Diario de Obras", "DiÃ¡rio de Obras")

    activity_entries = list(daily_log.activity_entries.select_related("location", "discipline"))
    occurrence_entries = list(daily_log.occurrences.all())
    team_entries = list(daily_log.team_entries.select_related("location"))
    material_entries = list(daily_log.material_entries.select_related("item", "location"))

    offset = 0
    activity_start = 23
    offset += _ensure_block_rows(ws, activity_start + offset, 6, len(activity_entries))
    occurrence_start = 32 + offset
    offset += _ensure_block_rows(ws, occurrence_start, 4, len(occurrence_entries))
    team_start = 39 + offset
    offset += _ensure_block_rows(ws, team_start, 4, len(team_entries))
    material_heading_row = 44 + offset
    material_start = 46 + offset
    _ensure_block_rows(ws, material_start, 5, len(material_entries))

    project = daily_log.project
    work_order = _project_work_order(project)
    _write(ws, 2, 4, project.name)
    _write(ws, 5, 4, project.address)
    _write(ws, 7, 4, project.start_date)
    _write(ws, 7, 6, project.planned_end_date)
    _write(ws, 9, 2, getattr(work_order, "technical_manager_name", ""))
    _write(ws, 10, 2, daily_log.log_date)
    _write(ws, 10, 4, _weekday_name(daily_log.log_date))
    _write(ws, 11, 2, daily_log.responsible_name)
    _write(ws, 12, 3, _join_parts(daily_log.notes, daily_log.general_observation, daily_log.interruption_reason))

    for weather_row, field_name in ((17, "weather_morning"), (18, "weather_afternoon"), (19, "weather_night")):
        for col in range(2, 6):
            _write(ws, weather_row, col, "")
        weather_col = _weather_column(getattr(daily_log, field_name))
        if weather_col:
            _write(ws, weather_row, weather_col, "x")

    for row in range(activity_start, activity_start + max(6, len(activity_entries))):
        _clear_row(ws, row)
    for row, entry in enumerate(activity_entries, start=activity_start):
        observation = _join_parts(entry.location, entry.discipline, entry.notes)
        _write(ws, row, 1, entry.description)
        _write(ws, row, 4, observation)

    for row in range(occurrence_start, occurrence_start + max(4, len(occurrence_entries))):
        _clear_row(ws, row)
    for row, entry in enumerate(occurrence_entries, start=occurrence_start):
        _write(ws, row, 1, _join_parts(entry.description, entry.notes))
        _write(ws, row, 4, entry.get_occurrence_type_display())

    team_header_row = team_start - 1
    for row in range(team_header_row, team_start + max(4, len(team_entries))):
        _unmerge_single_row(ws, row)
        _merge_row_range(ws, row, 4, 6)
    _write(ws, team_header_row, 1, "Equipe")
    _write(ws, team_header_row, 2, "Quantidade")
    _write(ws, team_header_row, 3, "Local")
    _write(ws, team_header_row, 4, "Atividade/Serviço executado")

    for row in range(team_start, team_start + max(4, len(team_entries))):
        _clear_row(ws, row)
    for row, entry in enumerate(team_entries, start=team_start):
        _write(ws, row, 1, entry.team_name)
        _write(ws, row, 2, entry.worker_count)
        _write(ws, row, 3, _text(entry.location))
        _write(ws, row, 4, entry.activity_description)

    _write(ws, material_heading_row, 1, "Materiais aplicados")
    _write(ws, material_heading_row + 1, 1, "Código")
    _write(ws, material_heading_row + 1, 2, "Descrição")
    _write(ws, material_heading_row + 1, 5, "Quantidade / Unidade")
    for row in range(material_start, material_start + max(5, len(material_entries))):
        _clear_row(ws, row)
    for row, entry in enumerate(material_entries, start=material_start):
        _write(ws, row, 1, getattr(entry.item, "eap_code", ""))
        _write(ws, row, 2, _join_parts(_entry_material_description(entry), entry.location, entry.notes))
        _write(ws, row, 5, _join_parts(entry.quantity, entry.unit_snapshot))


@transaction.atomic
def generate_rdo_xlsx(daily_log_id: int):
    daily_log = (
        DailyWorkLog.objects.select_related("project", "project__client", "project__work_order_info")
        .prefetch_related(
            "team_entries",
            "activity_entries__location",
            "activity_entries__discipline",
            "occurrences",
            "team_entries__location",
            "material_entries__item",
            "material_entries__location",
        )
        .get(pk=daily_log_id)
    )

    try:
        template_path = get_template_path(TEMPLATE_NAME)
        workbook = load_workbook(template_path)

        fill_work_order_sheet(workbook, daily_log.project)
        fill_daily_log_sheet(workbook, daily_log)

        slug_project = slugify(daily_log.project.name) or f"project-{daily_log.project_id}"
        export_dir = get_exports_dir() / str(daily_log.project_id) / "rdo"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"RDO_{daily_log.log_date:%Y-%m-%d}_{slug_project}.xlsx"
        workbook.save(output_path)
    except Exception as exc:
        export_record = MeasurementExport.objects.create(
            project=daily_log.project,
            period=None,
            export_type=ExportType.RDO_XLSX,
            file_path="",
            status=ExportStatus.ERROR,
            error_message=f"[rdo_xlsx] {exc}",
            summary_json={
                "daily_log_id": daily_log.id,
                "log_date": daily_log.log_date.isoformat(),
                "template": TEMPLATE_NAME,
            },
        )
        return export_record, None

    export_record = MeasurementExport.objects.create(
        project=daily_log.project,
        period=None,
        export_type=ExportType.RDO_XLSX,
        file_path=str(Path(output_path)),
        status=ExportStatus.OK,
        summary_json={
            "daily_log_id": daily_log.id,
            "log_date": daily_log.log_date.isoformat(),
            "team_entries_count": daily_log.team_entries.count(),
            "activity_entries_count": daily_log.activity_entries.count(),
            "occurrences_count": daily_log.occurrences.count(),
            "material_entries_count": daily_log.material_entries.count(),
            "template": TEMPLATE_NAME,
        },
    )
    return export_record, str(output_path)
