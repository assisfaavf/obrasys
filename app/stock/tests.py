from datetime import date
from io import BytesIO
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
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
    Material,
    MaterialAlias,
    MeasurementMaterial,
    MeasurementMaterialStatus,
    MeasurementStockConsumption,
    MeasurementStockConsumptionType,
    StockBalance,
    StockImport,
    StockImportItemStatus,
    StockImportStatus,
    StockLocation,
    StockLocationType,
    StockMovement,
    StockMovementType,
)
from stock.purchase_import import confirm_stock_import, match_import_items, parse_stock_import_file
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


class StockImportTests(TestCase):
    def setUp(self):
        self.unit = Unit.objects.create(code="un", name="Unidade")
        self.meter_unit = Unit.objects.create(code="m", name="Metro")
        self.client_obj = Client.objects.create(name="Cliente Importacao")
        self.project = Project.objects.create(name="Obra Importacao", client=self.client_obj)
        self.material = Material.objects.create(code="MAT-001", name="Tubo PVC 100mm", unit=self.unit)
        self.alias_material = Material.objects.create(code="MAT-002", name="Adesivo CPVC / Aquaterm", unit=self.unit)
        self.central_location = StockLocation.objects.create(
            code="CENTRAL-IMP",
            name="Estoque central importacao",
            location_type=StockLocationType.CENTRAL,
        )
        self.project_location = StockLocation.objects.create(
            code="OBRA-IMP",
            name="Almoxarifado da obra importacao",
            location_type=StockLocationType.PROJECT,
            project=self.project,
        )
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="import-user", password="secret123")

    def test_create_stock_import(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade\nTubo PVC 100mm,2\n"))

        self.assertEqual(import_batch.status, StockImportStatus.PENDING_REVIEW)
        self.assertEqual(import_batch.destination_location, self.central_location)
        self.assertEqual(import_batch.items.count(), 1)

    def test_parse_valid_csv_file(self):
        import_batch = self._create_import(
            self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,2,un\n")
        )
        item = import_batch.items.get()

        self.assertEqual(item.original_description, "Tubo PVC 100mm")
        self.assertEqual(item.original_quantity, Decimal("2.000"))
        self.assertEqual(item.status, StockImportItemStatus.OK)

    def test_parse_valid_xlsx_file(self):
        import_batch = self._create_import(
            self._xlsx_file("pedido.xlsx", [["descricao", "quantidade"], ["Tubo PVC 100mm", 3]])
        )
        item = import_batch.items.get()

        self.assertEqual(item.original_description, "Tubo PVC 100mm")
        self.assertEqual(item.original_quantity, Decimal("3.000"))
        self.assertEqual(item.status, StockImportItemStatus.OK)

    def test_match_material_by_exact_description(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade\nTubo PVC 100mm,2\n"))
        item = import_batch.items.get()

        self.assertEqual(item.material, self.material)

    def test_match_material_by_alias(self):
        MaterialAlias.objects.create(material=self.alias_material, alias="Adesivo Aquaterm")

        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade\nAdesivo Aquaterm,5\n"))
        item = import_batch.items.get()

        self.assertEqual(item.material, self.alias_material)
        self.assertEqual(item.status, StockImportItemStatus.OK)

    def test_marks_item_pending_when_material_not_found(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade\nRegistro gaveta,2\n"))
        item = import_batch.items.get()

        self.assertIsNone(item.material)
        self.assertEqual(item.status, StockImportItemStatus.PENDING_MATERIAL)

    def test_marks_item_pending_when_original_unit_diverges(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,60,m\n"))
        item = import_batch.items.get()

        self.assertEqual(item.material, self.material)
        self.assertEqual(item.status, StockImportItemStatus.PENDING_UNIT)
        self.assertIsNone(item.confirmed_quantity)

    def test_confirm_valid_import_generates_purchase_in_movement_and_central_balance(self):
        import_batch = self._create_import(
            self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,2,un\n"),
            destination_location=self.central_location,
        )

        confirmed = confirm_stock_import(import_batch, user=self.user)

        self.assertEqual(len(confirmed), 1)
        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, StockMovementType.PURCHASE_IN)
        self.assertEqual(movement.material, self.material)
        self.assertEqual(movement.location, self.central_location)
        self.assertEqual(
            StockBalance.objects.get(material=self.material, location=self.central_location).quantity,
            Decimal("2.000"),
        )
        import_batch.refresh_from_db()
        self.assertEqual(import_batch.status, StockImportStatus.CONFIRMED)

    def test_confirm_valid_import_updates_project_stock_balance(self):
        import_batch = self._create_import(
            self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,4,un\n"),
            destination_location=self.project_location,
        )

        confirm_stock_import(import_batch, user=self.user)

        self.assertEqual(
            StockBalance.objects.get(material=self.material, location=self.project_location).quantity,
            Decimal("4.000"),
        )
        self.assertFalse(StockBalance.objects.filter(material=self.material, location=self.central_location).exists())

    def test_blocks_duplicate_confirmation(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,2,un\n"))
        confirm_stock_import(import_batch, user=self.user)

        with self.assertRaisesMessage(ValidationError, "Importacao ja confirmada"):
            confirm_stock_import(import_batch, user=self.user)

        self.assertEqual(StockMovement.objects.count(), 1)

    def test_blocks_invalid_quantity(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade\nTubo PVC 100mm,abc\n"))

        item = import_batch.items.get()
        self.assertEqual(item.status, StockImportItemStatus.PENDING_QUANTITY)
        with self.assertRaisesMessage(ValidationError, "item pendente"):
            confirm_stock_import(import_batch, user=self.user)

    def test_confirmed_quantity_uses_material_unit_not_file_unit_after_manual_adjustment(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,60,m\n"))
        item = import_batch.items.get()
        item.confirmed_quantity = Decimal("10")
        item.status = StockImportItemStatus.OK
        item.manual_adjustment = True
        item.save()

        confirm_stock_import(import_batch, user=self.user)

        movement = StockMovement.objects.get()
        self.assertEqual(movement.quantity, Decimal("10.000"))
        self.assertEqual(item.material.unit, self.unit)
        self.assertIn("Quantidade ajustada manualmente", movement.note)

    def test_unconfirmed_import_does_not_change_stock_balance(self):
        self._create_import(self._csv_file("pedido.csv", "descricao,quantidade,unidade\nTubo PVC 100mm,2,un\n"))

        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(StockBalance.objects.exists())

    def test_match_import_items_after_manual_alias_creation(self):
        import_batch = self._create_import(self._csv_file("pedido.csv", "descricao,quantidade\nAdesivo Aquaterm,5\n"))
        item = import_batch.items.get()
        self.assertEqual(item.status, StockImportItemStatus.PENDING_MATERIAL)
        MaterialAlias.objects.create(material=self.alias_material, alias="Adesivo Aquaterm")

        match_import_items(import_batch)

        item.refresh_from_db()
        self.assertEqual(item.material, self.alias_material)
        self.assertEqual(item.status, StockImportItemStatus.OK)

    def _create_import(self, uploaded_file, *, destination_location=None):
        import_batch = StockImport.objects.create(
            original_file=uploaded_file,
            supplier="Fornecedor Teste",
            destination_location=destination_location or self.central_location,
            created_by=self.user,
        )
        parse_stock_import_file(import_batch)
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
