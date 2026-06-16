import decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0006_cleanup_budget_item_manufacturer_hints"),
        ("core", "0004_projectstage"),
    ]

    operations = [
        migrations.CreateModel(
            name="Material",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_materials",
                        to="catalog.unit",
                    ),
                ),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="StockLocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=255)),
                (
                    "location_type",
                    models.CharField(
                        choices=[("CENTRAL", "Central"), ("PROJECT", "Obra")],
                        default="CENTRAL",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stock_locations",
                        to="core.project",
                    ),
                ),
            ],
            options={"ordering": ["location_type", "code"]},
        ),
        migrations.CreateModel(
            name="StockBalance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "quantity",
                    models.DecimalField(decimal_places=3, default=decimal.Decimal("0"), max_digits=14),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="balances",
                        to="stock.stocklocation",
                    ),
                ),
                (
                    "material",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="balances",
                        to="stock.material",
                    ),
                ),
            ],
            options={"ordering": ["location_id", "material_id"]},
        ),
        migrations.CreateModel(
            name="StockMovement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "movement_type",
                    models.CharField(
                        choices=[
                            ("IN", "Entrada"),
                            ("OUT", "Saida"),
                            ("ADJUST_POSITIVE", "Ajuste positivo"),
                            ("ADJUST_NEGATIVE", "Ajuste negativo"),
                            ("TRANSFER", "Transferencia"),
                        ],
                        max_length=20,
                    ),
                ),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("balance_after", models.DecimalField(decimal_places=3, max_digits=14)),
                ("note", models.TextField(blank=True)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="stock_movements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="stock.stocklocation",
                    ),
                ),
                (
                    "material",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="stock.material",
                    ),
                ),
                (
                    "target_location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="incoming_transfer_movements",
                        to="stock.stocklocation",
                    ),
                ),
            ],
            options={"ordering": ["-occurred_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="stocklocation",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("location_type", "CENTRAL"), ("project__isnull", True))
                    | models.Q(("location_type", "PROJECT"), ("project__isnull", False))
                ),
                name="stock_location_type_project_consistency",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockbalance",
            constraint=models.UniqueConstraint(
                fields=("material", "location"),
                name="uniq_stock_balance_material_location",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockbalance",
            constraint=models.CheckConstraint(condition=models.Q(("quantity__gte", 0)), name="stock_balance_quantity_non_negative"),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["material", "location"], name="idx_stock_mov_material_loc"),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["movement_type", "occurred_at"], name="idx_stock_mov_type_date"),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="stock_movement_quantity_positive"),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(condition=models.Q(("balance_after__gte", 0)), name="stock_movement_balance_non_negative"),
        ),
    ]
