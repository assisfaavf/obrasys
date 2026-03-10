import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from openpyxl import Workbook, load_workbook

from billing.models import MeasurementLine, MeasurementPeriod
from billing.services.measurement_calc import finalize_period
from catalog.models import BudgetItem, Unit
from core.models import Client, Project
from exports.models import ExportStatus, ExportType, MeasurementExport
from exports.services.xlsx_boletim import generate_xlsx_boletim


class XlsxBoletimServiceTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Export")
        self.project = Project.objects.create(name="Projeto Export", client=self.client_obj)
        self.unit = Unit.objects.create(code="M2_EXPORT", name="Metro quadrado")
        self.item = BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Item teste",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("10"),
            pu_labor=Decimal("5"),
        )
        self.period = MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month=date(2026, 2, 1),
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
        )
        MeasurementLine.objects.create(
            period=self.period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("5"),
            justification="Linha base",
        )
        MeasurementLine.objects.create(
            period=self.period,
            line_kind="EXTRA",
            extra_description="Servico extra",
            extra_unit=self.unit,
            qty_period=Decimal("2"),
            extra_pu_material=Decimal("12"),
            extra_pu_labor=Decimal("8"),
            justification="Necessidade adicional",
        )
        finalize_period(self.period.id)
        self.period.refresh_from_db()

    def _create_template(self, path: Path) -> Path:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Boletim"
        sheet["A1"] = "Template base"
        workbook.save(path)
        return path

    def test_generate_xlsx_boletim_creates_file(self):
        with tempfile.TemporaryDirectory(prefix="xlsx-template-") as tmp_dir:
            temp_root = Path(tmp_dir)
            template_path = self._create_template(temp_root / "boletim_template.xlsx")
            exports_dir = temp_root / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)

            with patch("exports.services.xlsx_boletim.get_template_path", return_value=template_path), patch(
                "exports.services.xlsx_boletim.get_exports_dir", return_value=exports_dir
            ):
                export_record, output_path = generate_xlsx_boletim(self.period.id)

            self.assertEqual(export_record.status, ExportStatus.OK)
            self.assertIsNotNone(output_path)
            self.assertTrue(Path(output_path).exists())
            self.assertTrue(str(output_path).endswith(".xlsx"))

            workbook = load_workbook(output_path)
            sheet = workbook[workbook.sheetnames[0]]
            self.assertEqual(sheet["B1"].value, "BOLETIM DE MEDICAO")
            self.assertEqual(sheet["D3"].value, self.project.name)

    def test_generate_xlsx_boletim_creates_measurement_export_record(self):
        with tempfile.TemporaryDirectory(prefix="xlsx-export-record-") as tmp_dir:
            temp_root = Path(tmp_dir)
            template_path = self._create_template(temp_root / "boletim_template.xlsx")
            exports_dir = temp_root / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)

            with patch("exports.services.xlsx_boletim.get_template_path", return_value=template_path), patch(
                "exports.services.xlsx_boletim.get_exports_dir", return_value=exports_dir
            ):
                export_record, _ = generate_xlsx_boletim(self.period.id)

            self.assertEqual(export_record.export_type, ExportType.XLSX_BOLETIM)
            self.assertEqual(export_record.status, ExportStatus.OK)
            self.assertTrue(export_record.file_path.endswith(".xlsx"))
            self.assertTrue(
                MeasurementExport.objects.filter(
                    period=self.period,
                    export_type=ExportType.XLSX_BOLETIM,
                    status=ExportStatus.OK,
                ).exists()
            )

    @patch("exports.services.xlsx_boletim.get_template_path")
    def test_generate_xlsx_boletim_missing_template_returns_error(self, template_mock):
        template_mock.side_effect = FileNotFoundError("missing")

        export_record, output_path = generate_xlsx_boletim(self.period.id)

        self.assertEqual(export_record.status, ExportStatus.ERROR)
        self.assertEqual(export_record.export_type, ExportType.XLSX_BOLETIM)
        self.assertIsNone(output_path)
        self.assertIn("boletim_template.xlsx", export_record.error_message)

    def test_generate_xlsx_boletim_blocks_draft_period(self):
        draft_period = MeasurementPeriod.objects.create(
            project=self.project,
            number=2,
            ref_month=date(2026, 3, 1),
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 31),
        )
        MeasurementLine.objects.create(
            period=draft_period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("1"),
        )

        with tempfile.TemporaryDirectory(prefix="xlsx-draft-") as tmp_dir:
            temp_root = Path(tmp_dir)
            template_path = self._create_template(temp_root / "boletim_template.xlsx")
            exports_dir = temp_root / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)

            with patch("exports.services.xlsx_boletim.get_template_path", return_value=template_path), patch(
                "exports.services.xlsx_boletim.get_exports_dir", return_value=exports_dir
            ):
                export_record, output_path = generate_xlsx_boletim(draft_period.id)

        self.assertEqual(export_record.status, ExportStatus.ERROR)
        self.assertIsNone(output_path)
        self.assertIn("DRAFT", export_record.error_message)
