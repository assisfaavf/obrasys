from collections import OrderedDict
from decimal import Decimal
from shutil import copy2

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from billing.models import MeasurementLineKind, MeasurementPeriod, WorkflowStatus
from billing.services.measurement_calc import get_item_cumulative
from exports.models import ExportStatus, ExportType, MeasurementExport
from utils.paths import get_exports_dir, get_template_path

TEMPLATE_NAME = "boletim_template.xlsx"
WORK_START_COL = 2  # B
WORK_END_COL = 13  # M
TABLE_FONT_SIZE = 9

THIN_SIDE = Side(style="thin", color="000000")
TABLE_BORDER = Border(
    left=THIN_SIDE,
    right=THIN_SIDE,
    top=THIN_SIDE,
    bottom=THIN_SIDE,
)
HEADER_FILL = PatternFill(fill_type="solid", fgColor="F2F2F2")
HEADER_FONT = Font(size=TABLE_FONT_SIZE, bold=True)
BODY_FONT = Font(size=TABLE_FONT_SIZE)
TITLE_FONT = Font(size=12, bold=True)
SUBTITLE_FONT = Font(size=10, bold=True)


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(value)


def _to_text(value) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text or "-"


def _format_month(value) -> str:
    if not value:
        return "-"
    return value.strftime("%Y-%m")


def _format_period(start_date, end_date) -> str:
    if not start_date or not end_date:
        return "-"
    return f"{start_date:%d/%m/%Y} a {end_date:%d/%m/%Y}"


def _resolve_discipline(line) -> str | None:
    candidates = [
        getattr(line, "discipline", None),
        getattr(line, "discipline_name", None),
        getattr(line, "discipline_code", None),
    ]
    item = getattr(line, "item", None)
    if item is not None:
        candidates.extend(
            [
                getattr(item, "discipline", None),
                getattr(item, "discipline_name", None),
                getattr(item, "discipline_code", None),
            ]
        )

    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, str):
            text = candidate.strip()
            if text:
                return text
            continue
        for attr in ("name", "code", "title"):
            nested = getattr(candidate, attr, None)
            if nested:
                return str(nested)
        return str(candidate)
    return None


def _order_grouped_rows(rows: list[dict]) -> tuple[OrderedDict, bool]:
    has_discipline = any(row["discipline"] for row in rows)
    if not has_discipline:
        return OrderedDict({None: [row["values"] for row in rows]}), False

    grouped: OrderedDict[str, list[list]] = OrderedDict()
    for row in rows:
        key = row["discipline"] or "Sem disciplina"
        grouped.setdefault(key, []).append(row["values"])
    return grouped, True


def _build_period_data(period: MeasurementPeriod) -> dict:
    lines = list(
        period.lines.select_related("item", "item__unit", "location", "extra_unit").order_by("id")
    )

    contracted_lines = [
        line
        for line in lines
        if line.line_kind == MeasurementLineKind.CONTRACTED
        and line.item_id
        and _to_decimal(line.qty_period) > 0
    ]
    extra_lines = [
        line
        for line in lines
        if line.line_kind == MeasurementLineKind.EXTRA and _to_decimal(line.qty_period) > 0
    ]

    period_qty_by_item: dict[int, Decimal] = {}
    for line in contracted_lines:
        period_qty_by_item[line.item_id] = period_qty_by_item.get(line.item_id, Decimal("0")) + _to_decimal(
            line.qty_period
        )

    contracted_rows_raw: list[dict] = []
    overflow_by_item: dict[int, dict] = {}

    contracted_lines.sort(
        key=lambda line: (
            _to_text(getattr(line.item, "eap_code", "")),
            _to_text(getattr(line.location, "code", "")),
            line.id,
        )
    )

    for line in contracted_lines:
        item = line.item
        previous = get_item_cumulative(period.project, line.item_id, period.number - 1)
        contracted_qty = _to_decimal(item.qty_contracted)
        period_qty = _to_decimal(line.qty_period)
        accumulated = previous + period_qty_by_item.get(line.item_id, Decimal("0"))
        percent = Decimal("0")
        if contracted_qty > 0:
            percent = accumulated / contracted_qty
        total_period = period_qty * (_to_decimal(item.pu_material) + _to_decimal(item.pu_labor))

        contracted_rows_raw.append(
            {
                "discipline": _resolve_discipline(line),
                "values": [
                    _to_text(item.eap_code),
                    _to_text(item.description),
                    _to_text(getattr(line.location, "code", None)),
                    _to_text(getattr(item.unit, "code", None)),
                    contracted_qty,
                    previous,
                    period_qty,
                    accumulated,
                    percent,
                    total_period,
                ],
            }
        )

        item_overflow = overflow_by_item.setdefault(
            line.item_id,
            {
                "item": item,
                "contracted_qty": contracted_qty,
                "accumulated": accumulated,
                "justifications": [],
            },
        )
        item_overflow["accumulated"] = accumulated
        if (line.justification or "").strip():
            item_overflow["justifications"].append(line.justification.strip())

    extra_rows_raw: list[dict] = []
    extra_lines.sort(
        key=lambda line: (
            _to_text(line.extra_description),
            _to_text(getattr(line.location, "code", "")),
            line.id,
        )
    )
    for line in extra_lines:
        qty = _to_decimal(line.qty_period)
        total = qty * (_to_decimal(line.extra_pu_material) + _to_decimal(line.extra_pu_labor))
        extra_rows_raw.append(
            {
                "discipline": _resolve_discipline(line),
                "values": [
                    _to_text(line.extra_description),
                    _to_text(getattr(line.location, "code", None)),
                    _to_text(getattr(line.extra_unit, "code", None)),
                    qty,
                    total,
                    _to_text(line.justification),
                ],
            }
        )

    overflow_rows: list[list] = []
    overflow_items = sorted(
        overflow_by_item.values(),
        key=lambda row: (_to_text(row["item"].eap_code), _to_text(row["item"].description)),
    )
    for row in overflow_items:
        contracted_qty = row["contracted_qty"]
        accumulated = row["accumulated"]
        if accumulated <= contracted_qty:
            continue
        overflow_qty = accumulated - contracted_qty
        justifications = " | ".join(dict.fromkeys(row["justifications"])) if row["justifications"] else "-"
        overflow_rows.append(
            [
                _to_text(row["item"].eap_code),
                _to_text(row["item"].description),
                contracted_qty,
                accumulated,
                overflow_qty,
                justifications,
            ]
        )

    contracted_grouped, contracted_grouped_by_discipline = _order_grouped_rows(contracted_rows_raw)
    extra_grouped, extra_grouped_by_discipline = _order_grouped_rows(extra_rows_raw)

    return {
        "contracted_grouped": contracted_grouped,
        "extra_grouped": extra_grouped,
        "contracted_grouped_by_discipline": contracted_grouped_by_discipline,
        "extra_grouped_by_discipline": extra_grouped_by_discipline,
        "overflow_rows": overflow_rows,
        "has_rows": bool(contracted_rows_raw or extra_rows_raw),
    }


def _centered_start(num_cols: int) -> int:
    work_width = WORK_END_COL - WORK_START_COL + 1
    if num_cols >= work_width:
        return WORK_START_COL
    return WORK_START_COL + ((work_width - num_cols) // 2)


def _set_col_width(ws: Worksheet, col_index: int, width: float) -> None:
    col = ws.cell(row=1, column=col_index).column_letter
    current = ws.column_dimensions[col].width
    ws.column_dimensions[col].width = max(current or 0, width)


def _style_cell(cell, *, bold: bool = False, align: str = "center", fill_header: bool = False) -> None:
    cell.font = HEADER_FONT if bold else BODY_FONT
    cell.border = TABLE_BORDER
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=False)
    if fill_header:
        cell.fill = HEADER_FILL


def _write_block_title(ws: Worksheet, row: int, text: str) -> int:
    ws.merge_cells(start_row=row, start_column=WORK_START_COL, end_row=row, end_column=WORK_END_COL)
    cell = ws.cell(row=row, column=WORK_START_COL, value=text)
    cell.font = SUBTITLE_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = 20
    return row + 1


def _write_empty_message(ws: Worksheet, row: int, message: str) -> int:
    ws.merge_cells(start_row=row, start_column=WORK_START_COL, end_row=row, end_column=WORK_END_COL)
    cell = ws.cell(row=row, column=WORK_START_COL, value=message)
    cell.font = BODY_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = 18
    return row + 2


def _write_table(
    ws: Worksheet,
    row: int,
    *,
    headers: list[str],
    rows: list[list],
    column_widths: list[float],
    left_align_cols: set[int] | None = None,
    number_formats: dict[int, str] | None = None,
) -> int:
    left_align_cols = left_align_cols or set()
    number_formats = number_formats or {}

    start_col = _centered_start(len(headers))
    end_col = start_col + len(headers) - 1

    for idx, width in enumerate(column_widths):
        _set_col_width(ws, start_col + idx, width)

    for idx, header in enumerate(headers):
        cell = ws.cell(row=row, column=start_col + idx, value=header)
        _style_cell(cell, bold=True, align="center", fill_header=True)

    current_row = row + 1
    for data_row in rows:
        for idx, value in enumerate(data_row):
            cell = ws.cell(row=current_row, column=start_col + idx, value=value)
            align = "left" if idx in left_align_cols else "center"
            _style_cell(cell, bold=False, align=align, fill_header=False)
            if idx in number_formats and value not in (None, "-", ""):
                cell.number_format = number_formats[idx]
        current_row += 1

    for current in range(row, current_row):
        ws.row_dimensions[current].height = 18

    ws.auto_filter.ref = f"{ws.cell(row=row, column=start_col).coordinate}:{ws.cell(row=row, column=end_col).coordinate}"
    return current_row + 1


def _write_grouped_table_block(
    ws: Worksheet,
    row: int,
    *,
    title: str,
    grouped_rows: OrderedDict,
    show_groups: bool,
    headers: list[str],
    column_widths: list[float],
    left_align_cols: set[int] | None = None,
    number_formats: dict[int, str] | None = None,
    empty_message: str,
) -> int:
    row = _write_block_title(ws, row, title)
    has_rows = any(values for values in grouped_rows.values())
    if not has_rows:
        return _write_empty_message(ws, row, empty_message)

    for group_name, values in grouped_rows.items():
        if show_groups:
            row = _write_block_title(ws, row, f"Disciplina: {group_name}")
        row = _write_table(
            ws,
            row,
            headers=headers,
            rows=values,
            column_widths=column_widths,
            left_align_cols=left_align_cols,
            number_formats=number_formats,
        )
    return row + 1


def _render_header(ws: Worksheet, period: MeasurementPeriod) -> int:
    ws.merge_cells(start_row=1, start_column=WORK_START_COL, end_row=1, end_column=WORK_END_COL)
    title = ws.cell(row=1, column=WORK_START_COL, value="BOLETIM DE MEDICAO")
    title.font = TITLE_FONT
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    incc_value = (
        f"indice={_to_text(period.index_code_snapshot)}, "
        f"base={_format_month(period.index_base_month_snapshot)}, "
        f"ref={_format_month(period.index_ref_month_snapshot)}, "
        f"fator={_to_decimal(period.index_factor_snapshot):.6f}"
    )
    metadata_pairs = [
        ("Obra", _to_text(period.project.name)),
        ("Cliente", _to_text(period.project.client.name)),
        ("Periodo", _format_period(period.start_date, period.end_date)),
        ("Medicao n", f"{period.number:02d}"),
        ("Referencia", _format_month(period.ref_month)),
        ("Status operacional", _to_text(period.workflow_status)),
        ("Status financeiro", _to_text(period.financial_status)),
        ("INCC", incc_value),
    ]

    _set_col_width(ws, 3, 18)
    _set_col_width(ws, 4, 34)
    _set_col_width(ws, 9, 18)
    _set_col_width(ws, 10, 34)

    row = 3
    for idx in range(0, len(metadata_pairs), 2):
        left_label, left_value = metadata_pairs[idx]
        right_label, right_value = metadata_pairs[idx + 1]

        left_label_cell = ws.cell(row=row, column=3, value=left_label)
        left_value_cell = ws.cell(row=row, column=4, value=left_value)
        right_label_cell = ws.cell(row=row, column=9, value=right_label)
        right_value_cell = ws.cell(row=row, column=10, value=right_value)

        _style_cell(left_label_cell, bold=True, align="center", fill_header=True)
        _style_cell(left_value_cell, bold=False, align="left", fill_header=False)
        _style_cell(right_label_cell, bold=True, align="center", fill_header=True)
        _style_cell(right_value_cell, bold=False, align="left", fill_header=False)

        ws.row_dimensions[row].height = 18
        row += 1

    return row + 1


def _render_summary_and_signatures(ws: Worksheet, row: int, period: MeasurementPeriod) -> int:
    row = _write_block_title(ws, row, "D) RESUMO FINANCEIRO + ASSINATURAS")

    summary_rows = [
        ["Total material", _to_decimal(period.total_material_snapshot)],
        ["Total mao de obra", _to_decimal(period.total_labor_snapshot)],
        ["Total medicao", _to_decimal(period.total_total_snapshot)],
        ["Total corrigido (INCC)", _to_decimal(period.total_indexed_snapshot)],
        ["INCC indice", _to_text(period.index_code_snapshot)],
        ["INCC base", _format_month(period.index_base_month_snapshot)],
        ["INCC referencia", _format_month(period.index_ref_month_snapshot)],
        ["INCC fator", _to_decimal(period.index_factor_snapshot)],
    ]

    row = _write_table(
        ws,
        row,
        headers=["Campo", "Valor"],
        rows=summary_rows,
        column_widths=[30, 30],
        left_align_cols={0},
        number_formats={1: '#,##0.00'},
    )

    row = _write_block_title(ws, row, "Assinaturas")
    row = _write_table(
        ws,
        row,
        headers=["Responsavel Tecnico", "Engenheiro Responsavel", "Fiscal/Contratante"],
        rows=[["", "", ""], ["", "", ""]],
        column_widths=[22, 22, 22],
        left_align_cols=set(),
    )
    return row


def _render_sheet(ws: Worksheet, period: MeasurementPeriod, data: dict) -> None:
    ws.sheet_view.zoomScale = 90
    ws.page_margins.left = 0.4
    ws.page_margins.right = 0.4
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5

    row = _render_header(ws, period)

    row = _write_grouped_table_block(
        ws,
        row,
        title="A) ITENS CONTRATADOS",
        grouped_rows=data["contracted_grouped"],
        show_groups=data["contracted_grouped_by_discipline"],
        headers=[
            "EAP",
            "Descricao",
            "Local",
            "Und",
            "Contratado",
            "Anterior",
            "Periodo",
            "Acumulado",
            "%",
            "Total Periodo",
        ],
        column_widths=[12, 44, 10, 8, 12, 12, 12, 12, 8, 14],
        left_align_cols={1},
        number_formats={4: '#,##0.000', 5: '#,##0.000', 6: '#,##0.000', 7: '#,##0.000', 8: '0.00%', 9: '#,##0.00'},
        empty_message="Sem itens contratados com quantidade no periodo.",
    )

    row = _write_grouped_table_block(
        ws,
        row,
        title="B) ITENS EXTRAS",
        grouped_rows=data["extra_grouped"],
        show_groups=data["extra_grouped_by_discipline"],
        headers=["Descricao", "Local", "Und", "Qtd", "Total", "Justificativa"],
        column_widths=[44, 10, 8, 12, 14, 28],
        left_align_cols={0, 5},
        number_formats={3: '#,##0.000', 4: '#,##0.00'},
        empty_message="Sem itens extras com quantidade no periodo.",
    )

    if data["overflow_rows"]:
        row = _write_block_title(ws, row, "C) ITENS COM EXCEDENTE")
        row = _write_table(
            ws,
            row,
            headers=["EAP", "Descricao", "Contratado", "Acumulado", "Excedente", "Justificativa"],
            rows=data["overflow_rows"],
            column_widths=[12, 44, 12, 12, 12, 28],
            left_align_cols={1, 5},
            number_formats={2: '#,##0.000', 3: '#,##0.000', 4: '#,##0.000'},
        )

    _render_summary_and_signatures(ws, row, period)


@transaction.atomic
def generate_xlsx_boletim(period_id: int):
    period = (
        MeasurementPeriod.objects.select_related("project", "project__client")
        .prefetch_related("lines__item__unit", "lines__location", "lines__extra_unit")
        .get(pk=period_id)
    )

    project_id = period.project_id
    slug_project = slugify(period.project.name) or f"project-{project_id}"
    filename = f"Boletim_Medicao_{period.number:02d}_{period.ref_month:%Y-%m}_{slug_project}.xlsx"

    exports_dir = get_exports_dir()
    export_dir = exports_dir / str(project_id)
    export_dir.mkdir(parents=True, exist_ok=True)
    output_path = export_dir / filename
    relative_file_path = str(output_path.relative_to(exports_dir))

    try:
        if period.workflow_status == WorkflowStatus.DRAFT:
            raise ValidationError("Nao e permitido gerar boletim para periodo em DRAFT.")

        try:
            template_path = get_template_path(TEMPLATE_NAME)
        except FileNotFoundError as exc:
            raise ValidationError(
                "Template do boletim nao encontrado: assets/templates/boletim_template.xlsx."
            ) from exc

        data = _build_period_data(period)
        if not data["has_rows"]:
            raise ValidationError("Nao ha linhas contratadas ou extras para gerar o boletim.")

        copy2(template_path, output_path)

        workbook = load_workbook(output_path)
        sheet = workbook[workbook.sheetnames[0]]
        _render_sheet(sheet, period, data)
        workbook.save(output_path)

        export_record = MeasurementExport.objects.create(
            project=period.project,
            period=period,
            export_type=ExportType.XLSX_BOLETIM,
            file_path=relative_file_path,
            status=ExportStatus.OK,
        )
        return export_record, output_path

    except Exception as exc:
        export_record = MeasurementExport.objects.create(
            project=period.project,
            period=period,
            export_type=ExportType.XLSX_BOLETIM,
            file_path=relative_file_path,
            status=ExportStatus.ERROR,
            error_message=str(exc),
        )
        return export_record, None
