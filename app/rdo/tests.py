import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from openpyxl import Workbook, load_workbook

from catalog.models import Discipline
from core.models import Client, Project, ProjectLocation
from exports.models import ExportStatus, ExportType, MeasurementExport
from rdo.forms import DailyWorkLogForm
from rdo.models import (
    DailyWorkActivityEntry,
    DailyWorkLog,
    DailyWorkOccurrence,
    DailyWorkTeamEntry,
    OccurrenceType,
    ProjectWorkOrderInfo,
    WeatherCondition,
)
from rdo.services.rdo_export import generate_rdo_xlsx


class RdoModelTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente RDO")
        self.project = Project.objects.create(
            name="Obra RDO",
            client=self.client_obj,
            address="Rua Teste, 100",
            start_date=date(2026, 1, 10),
            planned_end_date=date(2026, 12, 20),
        )

    def test_create_daily_log_by_project_and_date(self):
        daily_log = DailyWorkLog.objects.create(
            project=self.project,
            log_date=date(2026, 4, 13),
            responsible_name="Responsavel",
            weather_morning=WeatherCondition.CLEAR,
        )

        self.assertEqual(daily_log.project, self.project)
        self.assertEqual(str(daily_log), "Obra RDO - 13/04/2026")

    def test_daily_log_form_blocks_duplicate_for_same_project_and_date(self):
        DailyWorkLog.objects.create(
            project=self.project,
            log_date=date(2026, 4, 13),
            responsible_name="Responsavel",
        )

        form = DailyWorkLogForm(
            data={
                "log_date": "2026-04-13",
                "responsible_name": "Outro responsavel",
                "weather_morning": "",
                "weather_afternoon": "",
                "weather_night": "",
                "notes": "",
                "general_observation": "",
                "interruption_reason": "",
            },
            project=self.project,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("log_date", form.errors)

    def test_daily_log_unique_constraint_blocks_duplicate(self):
        DailyWorkLog.objects.create(
            project=self.project,
            log_date=date(2026, 4, 13),
            responsible_name="Responsavel",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DailyWorkLog.objects.create(
                    project=self.project,
                    log_date=date(2026, 4, 13),
                    responsible_name="Outro responsavel",
                )

    def test_create_team_activity_and_occurrence_entries(self):
        daily_log = DailyWorkLog.objects.create(
            project=self.project,
            log_date=date(2026, 4, 13),
            responsible_name="Responsavel",
        )
        location = ProjectLocation.objects.create(
            project=self.project,
            code="P1",
            name="Pavimento 1",
        )
        discipline = Discipline.objects.create(name="Eletrica", code="1")

        team = DailyWorkTeamEntry.objects.create(
            daily_log=daily_log,
            team_name="Equipe A",
            contractor_name="Contratada",
            role_or_service="Instalacao",
            worker_count=4,
        )
        activity = DailyWorkActivityEntry.objects.create(
            daily_log=daily_log,
            description="Instalacao de eletrodutos",
            location=location,
            discipline=discipline,
        )
        occurrence = DailyWorkOccurrence.objects.create(
            daily_log=daily_log,
            occurrence_type=OccurrenceType.INSPECAO,
            description="Inspecao da fiscalizacao",
        )

        self.assertEqual(team.worker_count, 4)
        self.assertEqual(activity.location, location)
        self.assertEqual(activity.discipline, discipline)
        self.assertEqual(occurrence.occurrence_type, OccurrenceType.INSPECAO)

    def test_create_and_edit_work_order_info(self):
        work_order = ProjectWorkOrderInfo.objects.create(
            project=self.project,
            art_number="ART-123",
            contractor_name="Construtora",
            contract_value=Decimal("150000.00"),
        )
        work_order.technical_manager_name = "Engenheira Responsavel"
        work_order.save(update_fields=["technical_manager_name"])

        work_order.refresh_from_db()
        self.assertEqual(work_order.project, self.project)
        self.assertEqual(work_order.technical_manager_name, "Engenheira Responsavel")


class RdoExportTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Export RDO")
        self.project = Project.objects.create(
            name="Obra Export RDO",
            client=self.client_obj,
            address="Avenida RDO, 200",
            start_date=date(2026, 1, 10),
            planned_end_date=date(2026, 12, 20),
        )
        self.daily_log = DailyWorkLog.objects.create(
            project=self.project,
            log_date=date(2026, 4, 13),
            responsible_name="Mestre de Obras",
            weather_morning=WeatherCondition.CLEAR,
            notes="Dia produtivo",
            general_observation="Sem restricoes",
        )
        ProjectWorkOrderInfo.objects.create(
            project=self.project,
            art_number="ART-999",
            contractor_name="Construtora RDO",
            technical_manager_name="Eng. RDO",
            address_snapshot="Endereco snapshot",
            contract_number="CT-01",
            contract_value=Decimal("200000.00"),
        )
        DailyWorkTeamEntry.objects.create(
            daily_log=self.daily_log,
            team_name="Equipe Civil",
            contractor_name="Construtora RDO",
            role_or_service="Alvenaria",
            worker_count=5,
        )
        DailyWorkActivityEntry.objects.create(
            daily_log=self.daily_log,
            description="Execucao de alvenaria",
        )
        DailyWorkOccurrence.objects.create(
            daily_log=self.daily_log,
            occurrence_type=OccurrenceType.VISITA,
            description="Visita tecnica",
        )

    def _create_template(self, path: Path) -> Path:
        workbook = Workbook()
        workbook.active.title = "Livro de Ordem"
        workbook.create_sheet("Diário de Obras")
        workbook.create_sheet("Relatório Fotográfico")
        workbook["Relatório Fotográfico"]["A1"] = "Template fotografico preservado"
        workbook.save(path)
        return path

    def test_generate_rdo_xlsx_creates_file_and_export_record(self):
        with tempfile.TemporaryDirectory(prefix="rdo-xlsx-") as tmp_dir:
            temp_root = Path(tmp_dir)
            template_path = self._create_template(temp_root / "modelo-de-diario-de-obras-2-0.xlsx")
            exports_dir = temp_root / "exports"

            with patch("rdo.services.rdo_export.get_template_path", return_value=template_path), patch(
                "rdo.services.rdo_export.get_exports_dir", return_value=exports_dir
            ):
                export_record, output_path = generate_rdo_xlsx(self.daily_log.id)

            self.assertEqual(export_record.status, ExportStatus.OK)
            self.assertEqual(export_record.export_type, ExportType.RDO_XLSX)
            self.assertIsNone(export_record.period)
            self.assertTrue(Path(output_path).exists())
            self.assertTrue(
                MeasurementExport.objects.filter(
                    project=self.project,
                    export_type=ExportType.RDO_XLSX,
                    status=ExportStatus.OK,
                ).exists()
            )

            workbook = load_workbook(output_path)
            self.assertIn("Livro de Ordem", workbook.sheetnames)
            self.assertIn("Diário de Obras", workbook.sheetnames)
            self.assertIn("Relatório Fotográfico", workbook.sheetnames)
            self.assertEqual(workbook["Livro de Ordem"]["B3"].value, self.project.name)
            self.assertEqual(workbook["Livro de Ordem"]["B6"].value, "ART-999")
            self.assertEqual(workbook["Diário de Obras"]["B4"].value, "13/04/2026")
            self.assertEqual(workbook["Diário de Obras"]["A15"].value, "Equipe Civil")
            self.assertEqual(workbook["Relatório Fotográfico"]["A1"].value, "Template fotografico preservado")

    def test_generate_rdo_xlsx_missing_template_returns_error_record(self):
        with patch("rdo.services.rdo_export.get_template_path", side_effect=FileNotFoundError("missing")):
            export_record, output_path = generate_rdo_xlsx(self.daily_log.id)

        self.assertEqual(export_record.status, ExportStatus.ERROR)
        self.assertEqual(export_record.export_type, ExportType.RDO_XLSX)
        self.assertIsNone(output_path)
        self.assertIn("missing", export_record.error_message)
