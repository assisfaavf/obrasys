from datetime import date
from io import BytesIO
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from billing.models import MeasurementLine, MeasurementPeriod, WorkflowStatus
from billing.services.measurement_calc import finalize_period
from billing.services.workflow import transition_measurement_status
from catalog.models import BudgetItem
from catalog.models import Unit
from core.models import Client, Project
from stock.measurement_consumption import apply_measurement_stock_consumption
from stock.models import (
    InitialStockImport,
    InitialStockImportItemStatus,
    InitialStockImportStatus,
    Material,
    MeasurementMaterial,
    MeasurementMaterialStatus,
    MeasurementStockConsumption,
    MeasurementStockConsumptionType,
    StockBalance,
    StockLocation,
    StockLocationType,
    StockMovement,
    StockMovementType,
)
from stock.initial_import import confirm_initial_stock_import, parse_initial_stock_file
from stock.services import register_stock_movement


class StockCoreTests(TestCase):
    def setUp(self):
        self.unit = Unit.objects.create(code="un", name="Unidade")
        self.client_obj = Client.objects.create(name="Cliente Estoque")
        self.project = Project.objects.create(name="Obra Estoque", client=self.client_obj)
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin-stock",
            password="secret123",
            email="admin-stock@example.com",
        )

    def test_create_material(self):
        material = Material.objects.create(
            code="MAT-001",
            name="Cabo flexivel 2,5mm",
            brand="Prysmian",
            unit=self.unit,
        )

        self.assertEqual(str(material), "MAT-001 - Cabo flexivel 2,5mm")
        self.assertEqual(material.brand, "Prysmian")
        self.assertTrue(material.is_active)

    def test_create_central_stock_location(self):
        location = StockLocation.objects.create(
            code="CENTRAL",
            name="Estoque central",
            location_type=StockLocationType.CENTRAL,
        )

        self.assertIsNone(location.project_id)
        self.assertEqual(location.location_type, StockLocationType.CENTRAL)

    def test_create_project_stock_location(self):
        location = StockLocation.objects.create(
            code="OBRA-01",
            name="Almoxarifado da obra",
            location_type=StockLocationType.PROJECT,
            project=self.project,
        )

        self.assertEqual(location.project, self.project)
        self.assertEqual(location.location_type, StockLocationType.PROJECT)

    def test_stock_entry_creates_balance_and_movement(self):
        material = self._create_material()
        location = self._create_central_location()

        movement = register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
            note="entrada inicial",
        )

        balance = StockBalance.objects.get(material=material, location=location)
        self.assertEqual(balance.quantity, Decimal("10.000"))
        self.assertEqual(movement.balance_after, Decimal("10.000"))
        self.assertEqual(movement.note, "entrada inicial")

    def test_positive_adjustment_increases_balance(self):
        material = self._create_material()
        location = self._create_central_location()
        register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )

        register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.ADJUST_POSITIVE,
            quantity=Decimal("2.5"),
        )

        balance = StockBalance.objects.get(material=material, location=location)
        self.assertEqual(balance.quantity, Decimal("12.500"))
        self.assertEqual(StockMovement.objects.filter(material=material, location=location).count(), 2)

    def test_negative_adjustment_decreases_balance(self):
        material = self._create_material()
        location = self._create_central_location()
        register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )

        movement = register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.ADJUST_NEGATIVE,
            quantity=Decimal("3"),
        )

        balance = StockBalance.objects.get(material=material, location=location)
        self.assertEqual(balance.quantity, Decimal("7.000"))
        self.assertEqual(movement.balance_after, Decimal("7.000"))

    def test_movement_cannot_make_balance_negative(self):
        material = self._create_material()
        location = self._create_central_location()

        with self.assertRaisesMessage(ValidationError, "saldo de estoque negativo"):
            register_stock_movement(
                material=material,
                location=location,
                movement_type=StockMovementType.OUT,
                quantity=Decimal("1"),
            )

        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(StockBalance.objects.filter(material=material, location=location).exists())

    def test_admin_available_balance_endpoint_returns_current_and_after_quantities(self):
        self.client.force_login(self.user)
        material = self._create_material()
        location = self._create_central_location()
        register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )

        response = self.client.get(
            reverse("admin:stock_stockmovement_available_balance"),
            {
                "material": str(material.id),
                "location": str(location.id),
                "movement_type": StockMovementType.OUT,
                "quantity": "3",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["available_quantity"], "10.000")
        self.assertEqual(response.json()["balance_after"], "7.000")

    def test_admin_movement_add_form_includes_transfer_destination(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:stock_stockmovement_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="target_location"')
        self.assertContains(response, "stock/admin_stock_movement.css")
        self.assertContains(response, "stock/admin_stock_movement.js")
        css_path = finders.find("stock/admin_stock_movement.css")
        self.assertIsNotNone(css_path)
        with open(css_path, encoding="utf-8") as css_file:
            self.assertIn(".field-target_location.stock-transfer-visible", css_file.read())

    def test_admin_available_balance_endpoint_calculates_transfer_origin_balance_after(self):
        self.client.force_login(self.user)
        material = self._create_material()
        location = self._create_central_location()
        register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )

        response = self.client.get(
            reverse("admin:stock_stockmovement_available_balance"),
            {
                "material": str(material.id),
                "location": str(location.id),
                "movement_type": StockMovementType.TRANSFER,
                "quantity": "4",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["available_quantity"], "10.000")
        self.assertEqual(response.json()["balance_after"], "6.000")

    def test_admin_add_movement_uses_service_to_calculate_balance_after(self):
        self.client.force_login(self.user)
        material = self._create_material()
        location = self._create_central_location()
        register_stock_movement(
            material=material,
            location=location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )

        response = self.client.post(
            reverse("admin:stock_stockmovement_add"),
            {
                "material": str(material.id),
                "location": str(location.id),
                "movement_type": StockMovementType.OUT,
                "quantity": "4.000",
                "note": "saida via admin",
                "_save": "Salvar",
            },
        )

        self.assertEqual(response.status_code, 302)
        balance = StockBalance.objects.get(material=material, location=location)
        movement = StockMovement.objects.latest("id")
        self.assertEqual(balance.quantity, Decimal("6.000"))
        self.assertEqual(movement.balance_after, Decimal("6.000"))
        self.assertEqual(movement.created_by, self.user)
        self.assertEqual(movement.note, "saida via admin")

    def test_admin_add_transfer_updates_origin_and_destination_balances(self):
        self.client.force_login(self.user)
        material = self._create_material()
        origin = self._create_central_location()
        destination = StockLocation.objects.create(
            code="OBRA-01",
            name="Almoxarifado da obra",
            location_type=StockLocationType.PROJECT,
            project=self.project,
        )
        register_stock_movement(
            material=material,
            location=origin,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )

        response = self.client.post(
            reverse("admin:stock_stockmovement_add"),
            {
                "material": str(material.id),
                "location": str(origin.id),
                "target_location": str(destination.id),
                "movement_type": StockMovementType.TRANSFER,
                "quantity": "4.000",
                "note": "transferencia para obra",
                "_save": "Salvar",
            },
        )

        self.assertEqual(response.status_code, 302)
        movement = StockMovement.objects.latest("id")
        self.assertEqual(movement.target_location, destination)
        self.assertEqual(movement.balance_after, Decimal("6.000"))
        self.assertEqual(
            StockBalance.objects.get(material=material, location=origin).quantity,
            Decimal("6.000"),
        )
        self.assertEqual(
            StockBalance.objects.get(material=material, location=destination).quantity,
            Decimal("4.000"),
        )

    def test_measurement_admin_material_inline_includes_stock_location_and_available_quantity(self):
        self.client.force_login(self.user)
        period = self._create_measurement_period()

        response = self.client.get(reverse("admin:billing_measurementperiod_change", args=[period.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="stock_materials-0-stock_location"')
        self.assertContains(response, 'name="stock_materials-0-available_quantity"')
        self.assertContains(response, "stock/admin_measurement_material.js")
        self.assertIsNotNone(finders.find("stock/admin_measurement_material.js"))

    def test_create_measurement_material(self):
        material = self._create_material()
        project_location = self._create_project_location()
        period = self._create_measurement_period()

        applied = MeasurementMaterial.objects.create(
            measurement=period,
            material=material,
            stock_location=project_location,
            quantity=Decimal("3"),
            note="aplicado no terreo",
        )

        self.assertEqual(applied.status, MeasurementMaterialStatus.PENDING)
        self.assertEqual(applied.quantity, Decimal("3"))
        self.assertEqual(applied.unit, material.unit)

    def test_finalize_measurement_deducts_project_stock_and_keeps_central_stock(self):
        material = self._create_material()
        central_location = self._create_central_location()
        project_location = self._create_project_location()
        period = self._create_measurement_period_with_line()
        register_stock_movement(
            material=material,
            location=central_location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("20"),
        )
        register_stock_movement(
            material=material,
            location=project_location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )
        MeasurementMaterial.objects.create(
            measurement=period,
            material=material,
            stock_location=project_location,
            quantity=Decimal("4"),
        )

        finalize_period(period.id, user=self.user)

        self.assertEqual(
            StockBalance.objects.get(material=material, location=project_location).quantity,
            Decimal("6.000"),
        )
        self.assertEqual(
            StockBalance.objects.get(material=material, location=central_location).quantity,
            Decimal("20.000"),
        )
        consumption = MeasurementStockConsumption.objects.get(measurement=period)
        self.assertEqual(consumption.stock_movement.movement_type, StockMovementType.MEASUREMENT_OUT)
        self.assertEqual(consumption.stock_movement.location, project_location)

    def test_finalize_measurement_blocks_when_project_stock_is_insufficient(self):
        material = self._create_material()
        project_location = self._create_project_location()
        period = self._create_measurement_period_with_line()
        register_stock_movement(
            material=material,
            location=project_location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("2"),
        )
        MeasurementMaterial.objects.create(
            measurement=period,
            material=material,
            stock_location=project_location,
            quantity=Decimal("4"),
        )

        with self.assertRaisesMessage(ValidationError, "saldo insuficiente"):
            finalize_period(period.id, user=self.user)

        period.refresh_from_db()
        self.assertEqual(period.workflow_status, WorkflowStatus.DRAFT)
        self.assertFalse(MeasurementStockConsumption.objects.exists())
        self.assertEqual(
            StockBalance.objects.get(material=material, location=project_location).quantity,
            Decimal("2.000"),
        )

    def test_measurement_consumption_is_not_duplicated(self):
        material = self._create_material()
        project_location = self._create_project_location()
        period = self._create_measurement_period_with_line()
        register_stock_movement(
            material=material,
            location=project_location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )
        MeasurementMaterial.objects.create(
            measurement=period,
            material=material,
            stock_location=project_location,
            quantity=Decimal("4"),
        )
        finalize_period(period.id, user=self.user)

        created = apply_measurement_stock_consumption(period, user=self.user)

        self.assertEqual(created, [])
        self.assertEqual(
            MeasurementStockConsumption.objects.filter(
                measurement=period,
                consumption_type=MeasurementStockConsumptionType.OUT,
            ).count(),
            1,
        )
        self.assertEqual(
            StockBalance.objects.get(material=material, location=project_location).quantity,
            Decimal("6.000"),
        )

    def test_reopen_measurement_reverses_stock_consumption(self):
        material = self._create_material()
        project_location = self._create_project_location()
        period = self._create_measurement_period_with_line()
        register_stock_movement(
            material=material,
            location=project_location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )
        applied = MeasurementMaterial.objects.create(
            measurement=period,
            material=material,
            stock_location=project_location,
            quantity=Decimal("4"),
        )
        finalize_period(period.id, user=self.user)

        transition_measurement_status(period, WorkflowStatus.DRAFT, user=self.user)

        applied.refresh_from_db()
        self.assertEqual(applied.status, MeasurementMaterialStatus.REVERSED)
        self.assertEqual(
            StockBalance.objects.get(material=material, location=project_location).quantity,
            Decimal("10.000"),
        )
        self.assertEqual(
            MeasurementStockConsumption.objects.filter(
                measurement=period,
                consumption_type=MeasurementStockConsumptionType.REVERSAL,
            ).count(),
            1,
        )

    def test_cancel_measurement_reverses_stock_consumption(self):
        material = self._create_material()
        project_location = self._create_project_location()
        period = self._create_measurement_period_with_line()
        register_stock_movement(
            material=material,
            location=project_location,
            movement_type=StockMovementType.IN,
            quantity=Decimal("10"),
        )
        MeasurementMaterial.objects.create(
            measurement=period,
            material=material,
            stock_location=project_location,
            quantity=Decimal("4"),
        )
        finalize_period(period.id, user=self.user)

        transition_measurement_status(period, WorkflowStatus.CANCELLED, user=self.user, note="cancelamento")

        self.assertEqual(
            StockBalance.objects.get(material=material, location=project_location).quantity,
            Decimal("10.000"),
        )
        self.assertEqual(
            StockMovement.objects.filter(
                material=material,
                location=project_location,
                movement_type=StockMovementType.MEASUREMENT_OUT_REVERSAL,
            ).count(),
            1,
        )

    def _create_material(self):
        return Material.objects.create(code="MAT-001", name="Cabo flexivel 2,5mm", unit=self.unit)

    def _create_central_location(self):
        return StockLocation.objects.create(
            code="CENTRAL",
            name="Estoque central",
            location_type=StockLocationType.CENTRAL,
        )

    def _create_project_location(self):
        return StockLocation.objects.create(
            code="OBRA-01",
            name="Almoxarifado da obra",
            location_type=StockLocationType.PROJECT,
            project=self.project,
        )

    def _create_measurement_period(self):
        return MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month=date(2026, 1, 1),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )

    def _create_measurement_period_with_line(self):
        period = self._create_measurement_period()
        item = BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Servico teste",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("10"),
            pu_labor=Decimal("5"),
        )
        MeasurementLine.objects.create(
            period=period,
            line_kind="CONTRACTED",
            item=item,
            qty_period=Decimal("1"),
        )
        return period


class InitialStockImportTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Carga Inicial")
        self.project = Project.objects.create(name="Obra Carga Inicial", client=self.client_obj)
        self.central_location = StockLocation.objects.create(
            code="CENTRAL-INI",
            name="Estoque Central da Empresa",
            location_type=StockLocationType.CENTRAL,
        )
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="initial-stock", password="secret123")

    def test_import_valid_xlsx_file(self):
        import_batch = self._create_import(
            self._xlsx_file(
                "estoque.xlsx",
                [
                    ["CODIGO_ITEM", "DESCRICAO", "UNIDADE", "ESTOQUE_EMPRESA"],
                    ["01.01.0004", "Tubo 100mm", "VARA", 3],
                ],
            )
        )

        item = import_batch.items.get()
        self.assertEqual(item.original_code, "01.01.0004")
        self.assertEqual(item.original_quantity, Decimal("3.000"))
        self.assertEqual(item.status, InitialStockImportItemStatus.NEW_MATERIAL)

    def test_import_valid_csv_file(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 40mm,VARA,0\n")
        )

        item = import_batch.items.get()
        self.assertEqual(item.original_description, "Tubo 40mm")
        self.assertEqual(item.confirmed_quantity, Decimal("0"))

    def test_confirm_creates_material_with_positive_stock(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        material = Material.objects.get(code="01")
        self.assertEqual(material.name, "Tubo 100mm")
        self.assertEqual(material.unit.code, "VARA")

    def test_confirm_creates_material_with_zero_stock(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n02,Joelho 100mm,UN,0\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        self.assertTrue(Material.objects.filter(code="02", name="Joelho 100mm").exists())

    def test_zero_stock_does_not_create_movement(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n02,Joelho 100mm,UN,0\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        self.assertFalse(StockMovement.objects.exists())

    def test_positive_stock_creates_initial_in_movement(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, StockMovementType.INITIAL_IN)
        self.assertEqual(movement.quantity, Decimal("3.000"))

    def test_central_stock_balance_is_updated(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        material = Material.objects.get(code="01")
        balance = StockBalance.objects.get(material=material, location=self.central_location)
        self.assertEqual(balance.quantity, Decimal("3.000"))

    def test_unconfirmed_import_does_not_change_stock_balance(self):
        self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )

        self.assertFalse(Material.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(StockBalance.objects.exists())

    def test_confirmed_import_cannot_be_confirmed_again(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )
        confirm_initial_stock_import(import_batch, user=self.user)

        with self.assertRaisesMessage(ValidationError, "Importacao ja confirmada"):
            confirm_initial_stock_import(import_batch, user=self.user)

        self.assertEqual(StockMovement.objects.count(), 1)

    def test_existing_material_is_not_duplicated(self):
        unit = Unit.objects.create(code="VARA", name="VARA")
        Material.objects.create(code="01", name="Tubo existente", unit=unit)
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        self.assertEqual(Material.objects.filter(code="01").count(), 1)

    def test_existing_material_is_reused_by_code(self):
        unit = Unit.objects.create(code="VARA", name="VARA")
        material = Material.objects.create(code="01", name="Tubo existente", unit=unit)
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )
        item = import_batch.items.get()

        self.assertEqual(item.material, material)

    def test_negative_quantity_marks_item_as_error(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,-2\n")
        )
        item = import_batch.items.get()

        self.assertEqual(item.status, InitialStockImportItemStatus.ERROR)
        with self.assertRaisesMessage(ValidationError, "Quantidade negativa"):
            confirm_initial_stock_import(import_batch, user=self.user)

    def test_empty_quantity_creates_material_without_entry(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        self.assertTrue(Material.objects.filter(code="01").exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_stock_movement_uses_material_unit(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        movement = StockMovement.objects.get()
        self.assertEqual(movement.material.unit.code, "VARA")

    def test_ignored_item_does_not_create_material_or_movement(self):
        import_batch = self._create_import(
            self._csv_file("estoque.csv", "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n")
        )
        item = import_batch.items.get()
        item.status = InitialStockImportItemStatus.IGNORED
        item.save(update_fields=["status", "updated_at"])

        confirm_initial_stock_import(import_batch, user=self.user)

        self.assertFalse(Material.objects.exists())
        self.assertFalse(StockMovement.objects.exists())

    def test_duplicate_header_and_observation_lines_do_not_break_import(self):
        import_batch = self._create_import(
            self._csv_file(
                "estoque.csv",
                (
                    "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n"
                    "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n"
                    ",Observacao,,\n"
                    "01,Tubo 100mm,VARA,3\n"
                ),
            )
        )

        self.assertEqual(import_batch.items.count(), 1)
        self.assertEqual(import_batch.items.get().original_code, "01")

    def test_status_and_counters_are_updated_on_confirmation(self):
        import_batch = self._create_import(
            self._csv_file(
                "estoque.csv",
                "CODIGO_ITEM,DESCRICAO,UNIDADE,ESTOQUE_EMPRESA\n01,Tubo 100mm,VARA,3\n02,Joelho,UN,0\n",
            )
        )

        confirm_initial_stock_import(import_batch, user=self.user)

        import_batch.refresh_from_db()
        self.assertEqual(import_batch.status, InitialStockImportStatus.CONFIRMED)
        self.assertEqual(import_batch.total_rows, 2)
        self.assertEqual(import_batch.total_materials_created, 2)
        self.assertEqual(import_batch.total_movements_created, 1)

    def _create_import(self, uploaded_file):
        import_batch = InitialStockImport.objects.create(
            original_file=uploaded_file,
            destination_location=self.central_location,
            created_by=self.user,
        )
        parse_initial_stock_file(import_batch)
        return import_batch

    def _csv_file(self, name, content):
        return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")

    def _xlsx_file(self, name, rows):
        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return SimpleUploadedFile(
            name,
            output.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
