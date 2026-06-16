from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalog.models import Unit
from core.models import Client, Project
from stock.models import Material, StockBalance, StockLocation, StockLocationType, StockMovement, StockMovementType
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

    def _create_material(self):
        return Material.objects.create(code="MAT-001", name="Cabo flexivel 2,5mm", unit=self.unit)

    def _create_central_location(self):
        return StockLocation.objects.create(
            code="CENTRAL",
            name="Estoque central",
            location_type=StockLocationType.CENTRAL,
        )
