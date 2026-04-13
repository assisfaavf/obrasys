from decimal import Decimal

from django.core.exceptions import ValidationError
from django.forms.models import inlineformset_factory
from django.test import TestCase

from catalog.forms import BudgetItemAdditionalMaterialForm, BudgetItemAdditionalMaterialInlineFormSet
from catalog.models import BudgetImportMode, BudgetItem, BudgetItemAdditionalMaterial, Discipline, Unit
from catalog.services.budget_import import apply_import_job, create_preview_job, parse_decimal
from core.models import Client, Project


class DecimalNormalizationTests(TestCase):
    def test_parse_decimal_accepts_comma_and_dot_formats(self):
        self.assertEqual(parse_decimal("1.234,56", empty_is_zero=False), Decimal("1234.56"))
        self.assertEqual(parse_decimal("1,234.56", empty_is_zero=False), Decimal("1234.56"))
        self.assertEqual(parse_decimal("1234,56", empty_is_zero=False), Decimal("1234.56"))
        self.assertEqual(parse_decimal("", empty_is_zero=True), Decimal("0"))


class BudgetImportServiceTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Teste")
        self.project = Project.objects.create(name="Projeto Teste", client=self.client_obj)
        self.unit = Unit.objects.create(code="M2", name="Metro quadrado")

    def _make_csv(self, lines: list[str]) -> bytes:
        content = "\n".join(lines)
        return content.encode("utf-8")

    def test_upsert_by_eap_updates_existing_item(self):
        BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Descricao antiga",
            unit=self.unit,
            qty_contracted=Decimal("10"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
        )

        csv_content = self._make_csv(
            [
                "ITEM,MATERIAL / SERVICO,UD,QUANT,P. UNIT MAT,P. UNIT MDO",
                "1.1,Descricao nova,H,15,2.50,3.75",
            ]
        )
        job = create_preview_job(
            project=self.project,
            mode=BudgetImportMode.UPSERT_BY_EAP,
            original_filename="orcamento.csv",
            csv_content=csv_content,
            created_by=None,
        )

        summary = apply_import_job(job)

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["updated"], 1)

        item = BudgetItem.objects.get(project=self.project, eap_code="1.1")
        self.assertEqual(item.description, "Descricao nova")
        self.assertEqual(item.qty_contracted, Decimal("15"))
        self.assertEqual(item.pu_material, Decimal("2.50"))
        self.assertEqual(item.pu_labor, Decimal("3.75"))
        self.assertEqual(item.unit.code, "H")

    def test_replace_all_deactivates_missing_items(self):
        BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Item A",
            unit=self.unit,
            qty_contracted=Decimal("10"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
            is_active=True,
        )
        missing_item = BudgetItem.objects.create(
            project=self.project,
            eap_code="1.2",
            description="Item B",
            unit=self.unit,
            qty_contracted=Decimal("5"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
            is_active=True,
        )

        csv_content = self._make_csv(
            [
                "ITEM,MATERIAL / SERVICO,UD,QUANT,P. UNIT MAT,P. UNIT MDO",
                "1.1,Item A atualizado,M2,11,1.10,1.20",
            ]
        )
        job = create_preview_job(
            project=self.project,
            mode=BudgetImportMode.REPLACE_ALL,
            original_filename="orcamento_replace.csv",
            csv_content=csv_content,
            created_by=None,
        )

        summary = apply_import_job(job)

        self.assertEqual(summary["deactivated"], 1)
        missing_item.refresh_from_db()
        self.assertFalse(missing_item.is_active)


class BudgetItemDisplayTests(TestCase):
    def test_budget_item_can_reference_registered_discipline(self):
        client = Client.objects.create(name="Cliente Disciplina")
        project = Project.objects.create(name="Projeto Disciplina", client=client)
        unit = Unit.objects.create(code="un", name="Unidade")
        discipline = Discipline.objects.create(name="Eletrica", code="ELE")

        item = BudgetItem.objects.create(
            project=project,
            eap_code="1.1",
            description="Eletroduto",
            unit=unit,
            qty_contracted=Decimal("1"),
            pu_material=Decimal("10"),
            pu_labor=Decimal("5"),
            discipline=discipline,
        )

        self.assertEqual(item.discipline, discipline)
        self.assertEqual(str(item.discipline), "ELE - Eletrica")

    def test_budget_item_str_includes_eap_description_and_unit(self):
        client = Client.objects.create(name="Cliente Display")
        project = Project.objects.create(name="Projeto Display", client=client)
        unit = Unit.objects.create(code="un", name="Unidade")
        item = BudgetItem.objects.create(
            project=project,
            eap_code="1.3.2",
            description="Quadro de distribuicao - pavimento tipo",
            unit=unit,
            qty_contracted=Decimal("1"),
            pu_material=Decimal("10"),
            pu_labor=Decimal("5"),
        )

        self.assertEqual(str(item), "1.3.2 — Quadro de distribuicao - pavimento tipo [un]")


class BudgetItemAdditionalMaterialTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Adicional")
        self.project = Project.objects.create(name="Projeto Adicional", client=self.client_obj)
        self.other_project = Project.objects.create(name="Projeto Outro", client=self.client_obj)
        self.unit = Unit.objects.create(code="un", name="Unidade")
        self.parent_item = BudgetItem.objects.create(
            project=self.project,
            eap_code="2.1",
            description="Caixa octogonal",
            unit=self.unit,
            qty_contracted=Decimal("10"),
            pu_material=Decimal("2"),
            pu_labor=Decimal("1"),
        )
        self.same_project_item = BudgetItem.objects.create(
            project=self.project,
            eap_code="2.2",
            description="Parafuso",
            unit=self.unit,
            qty_contracted=Decimal("10"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("0"),
        )
        self.other_project_item = BudgetItem.objects.create(
            project=self.other_project,
            eap_code="2.2",
            description="Parafuso outro projeto",
            unit=self.unit,
            qty_contracted=Decimal("10"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("0"),
        )

    def test_blocks_parent_item_equal_to_additional_item(self):
        relation = BudgetItemAdditionalMaterial(
            parent_item=self.parent_item,
            additional_item=self.parent_item,
            quantity_per_unit=Decimal("2.0000"),
        )

        with self.assertRaises(ValidationError):
            relation.full_clean()

    def test_allows_additional_material_from_same_project(self):
        relation = BudgetItemAdditionalMaterial.objects.create(
            parent_item=self.parent_item,
            additional_item=self.same_project_item,
            quantity_per_unit=Decimal("2.0000"),
        )

        self.assertEqual(relation.additional_item, self.same_project_item)

    def test_blocks_additional_material_from_different_project(self):
        relation = BudgetItemAdditionalMaterial(
            parent_item=self.parent_item,
            additional_item=self.other_project_item,
            quantity_per_unit=Decimal("2.0000"),
        )

        with self.assertRaises(ValidationError):
            relation.full_clean()

    def test_additional_material_form_queryset_is_limited_to_parent_project(self):
        form = BudgetItemAdditionalMaterialForm(parent_item=self.parent_item)

        self.assertIn(self.same_project_item, form.fields["additional_item"].queryset)
        self.assertNotIn(self.parent_item, form.fields["additional_item"].queryset)
        self.assertNotIn(self.other_project_item, form.fields["additional_item"].queryset)

    def test_additional_material_inline_empty_form_keeps_project_limited_queryset(self):
        formset_class = inlineformset_factory(
            BudgetItem,
            BudgetItemAdditionalMaterial,
            fk_name="parent_item",
            form=BudgetItemAdditionalMaterialForm,
            formset=BudgetItemAdditionalMaterialInlineFormSet,
            fields=("additional_item", "quantity_per_unit", "note", "is_active"),
            extra=1,
        )
        formset = formset_class(instance=self.parent_item)

        queryset = formset.empty_form.fields["additional_item"].queryset
        self.assertIn(self.same_project_item, queryset)
        self.assertNotIn(self.parent_item, queryset)
        self.assertNotIn(self.other_project_item, queryset)
