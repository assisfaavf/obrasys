import subprocess
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from billing.models import MeasurementLine, MeasurementPeriod
from billing.services.measurement_calc import finalize_period
from catalog.models import BudgetItem, Unit
from core.models import Client, Project
from exports.models import ExportStatus, ExportType, MeasurementExport
from exports.services.pdf_boletim import convert_docx_to_pdf, generate_pdf_boletim


class PdfBoletimServiceTests(TestCase):
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
        finalize_period(self.period.id)
        self.period.refresh_from_db()

    def _fake_soffice_run(self, command, check, capture_output, text):
        outdir = Path(command[command.index("--outdir") + 1])
        docx_path = Path(command[-1])
        pdf_path = outdir / f"{docx_path.stem}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="converted",
            stderr="",
        )

    @patch("exports.services.pdf_boletim.subprocess.run")
    def test_convert_docx_to_pdf_calls_subprocess(self, run_mock):
        run_mock.side_effect = self._fake_soffice_run

        with tempfile.TemporaryDirectory(prefix="test-convert-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            docx_path = tmp_path / "sample.docx"
            docx_path.write_bytes(b"fake docx")

            pdf_path = convert_docx_to_pdf(docx_path, tmp_path)

        self.assertTrue(run_mock.called)
        self.assertTrue(pdf_path.name.endswith(".pdf"))

    @patch("exports.services.pdf_boletim.subprocess.run")
    def test_generate_pdf_boletim_creates_export_record(self, run_mock):
        run_mock.side_effect = self._fake_soffice_run

        export_record, pdf_path = generate_pdf_boletim(self.period.id, layout="portrait")

        self.assertEqual(export_record.status, ExportStatus.OK)
        self.assertEqual(export_record.export_type, ExportType.PDF_TIMBRADO)
        self.assertIsNotNone(pdf_path)
        self.assertTrue(Path(pdf_path).exists())
        self.assertIn(str(self.project.id), export_record.file_path)
        self.assertTrue(str(export_record.file_path).endswith("_portrait.pdf"))
        self.assertTrue(
            MeasurementExport.objects.filter(
                period=self.period,
                export_type=ExportType.PDF_TIMBRADO,
                status=ExportStatus.OK,
            ).exists()
        )

    @patch("exports.services.pdf_boletim.get_template_path")
    def test_generate_pdf_boletim_landscape_missing_template_returns_error(self, template_mock):
        template_mock.side_effect = FileNotFoundError("missing")

        export_record, pdf_path = generate_pdf_boletim(self.period.id, layout="landscape")

        self.assertEqual(export_record.status, ExportStatus.ERROR)
        self.assertIsNone(pdf_path)
        self.assertIn("modelo_boletim_landscape.docx", export_record.error_message)
