from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.models import Client, LocationTemplate, LocationTemplateItem, Project, ProjectLocation, ProjectStage
from core.services import (
    apply_location_template_to_project,
    get_stage_prefix_from_eap,
    import_project_stages_from_csv,
    resolve_project_stage,
)


class LocationTemplateServiceTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Template")
        self.project = Project.objects.create(name="Projeto Template", client=self.client_obj)
        self.template = LocationTemplate.objects.create(name="Template Base")
        LocationTemplateItem.objects.create(template=self.template, code="BL1", name="Bloco 1", order_index=10)
        LocationTemplateItem.objects.create(template=self.template, code="BL2", name="Bloco 2", order_index=20)

    def test_apply_location_template_creates_missing_locations_and_preserves_order(self):
        ProjectLocation.objects.create(
            project=self.project,
            code="BL1",
            name="Bloco existente",
            order_index=99,
        )

        result = apply_location_template_to_project(project=self.project, template=self.template)

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["skipped_codes"], ["BL1"])

        locations = list(self.project.locations.order_by("order_index", "code"))
        self.assertEqual([(item.code, item.order_index) for item in locations], [("BL2", 20), ("BL1", 99)])

    def test_apply_location_template_requires_selected_template(self):
        with self.assertRaises(ValidationError):
            apply_location_template_to_project(project=self.project)


class ProjectStageServiceTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Etapas")
        self.project = Project.objects.create(name="Projeto Etapas", client=self.client_obj)

    def test_get_stage_prefix_from_eap_removes_last_block(self):
        self.assertEqual(get_stage_prefix_from_eap("1.1.14"), "1.1")
        self.assertEqual(get_stage_prefix_from_eap("6.6.3"), "6.6")
        self.assertEqual(get_stage_prefix_from_eap("2.3"), "2")
        self.assertEqual(get_stage_prefix_from_eap("5"), "5")
        self.assertEqual(get_stage_prefix_from_eap(None), "")

    def test_resolve_project_stage_returns_active_stage_by_prefix(self):
        stage = ProjectStage.objects.create(
            project=self.project,
            code="1.1",
            name="Infraestrutura elétrica",
        )

        self.assertEqual(resolve_project_stage(self.project, "1.1.14"), stage)

    def test_resolve_project_stage_ignores_missing_or_inactive_stage(self):
        ProjectStage.objects.create(
            project=self.project,
            code="6.6",
            name="Gás",
            is_active=False,
        )

        self.assertIsNone(resolve_project_stage(self.project, "6.6.3"))
        self.assertIsNone(resolve_project_stage(self.project, "9.1.1"))

    def test_import_project_stages_from_csv_creates_stages(self):
        csv_content = (
            "code,name,order_index,is_active\n"
            "1.1,Infraestrutura elétrica,1,True\n"
            "1.2,Cabeamento,2,True\n"
            "6.6,Gás,3,False\n"
        ).encode("utf-8")

        summary = import_project_stages_from_csv(csv_content, self.project)

        self.assertEqual(summary, {"total": 3, "created": 3, "updated": 0, "errors": []})
        self.assertEqual(
            list(self.project.stages.order_by("order_index").values_list("code", "name", "order_index", "is_active")),
            [
                ("1.1", "Infraestrutura elétrica", 1, True),
                ("1.2", "Cabeamento", 2, True),
                ("6.6", "Gás", 3, False),
            ],
        )

    def test_import_project_stages_from_csv_updates_existing_stage(self):
        ProjectStage.objects.create(
            project=self.project,
            code="1.1",
            name="Nome antigo",
            order_index=9,
            is_active=False,
        )
        csv_content = "code,name,order_index,is_active\n1.1,Infraestrutura elétrica,1,True\n".encode("utf-8")

        summary = import_project_stages_from_csv(csv_content, self.project)

        self.assertEqual(summary, {"total": 1, "created": 0, "updated": 1, "errors": []})
        stage = self.project.stages.get(code="1.1")
        self.assertEqual(stage.name, "Infraestrutura elétrica")
        self.assertEqual(stage.order_index, 1)
        self.assertTrue(stage.is_active)

    def test_import_project_stages_from_csv_uses_defaults_for_optional_fields(self):
        csv_content = "code,name\n2.1,Pintura\n".encode("utf-8")

        summary = import_project_stages_from_csv(csv_content, self.project)

        self.assertEqual(summary, {"total": 1, "created": 1, "updated": 0, "errors": []})
        stage = self.project.stages.get(code="2.1")
        self.assertEqual(stage.order_index, 0)
        self.assertTrue(stage.is_active)

    def test_import_project_stages_from_csv_allows_partial_import_with_invalid_lines(self):
        csv_content = (
            "code,name,order_index,is_active\n"
            "1.1,Infraestrutura elétrica,1,True\n"
            ",Sem codigo,2,True\n"
            "1.2,,3,True\n"
            "1.3,Cabeamento,ordem,True\n"
            "1.4,Gás,4,talvez\n"
            "1.5,Finalização,5,False\n"
        ).encode("utf-8")

        summary = import_project_stages_from_csv(csv_content, self.project)

        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["created"], 2)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(len(summary["errors"]), 4)
        self.assertEqual(set(self.project.stages.values_list("code", flat=True)), {"1.1", "1.5"})
        self.assertFalse(self.project.stages.get(code="1.5").is_active)

    def test_import_project_stages_from_csv_reports_invalid_header(self):
        csv_content = "codigo,descricao\n1.1,Infraestrutura elétrica\n".encode("utf-8")

        summary = import_project_stages_from_csv(csv_content, self.project)

        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["errors"], [{"line": 1, "error": "colunas obrigatorias ausentes: code, name"}])

    def test_project_stage_admin_import_csv_view_imports_file(self):
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            username="admin-etapas",
            password="secret123",
            email="admin@example.com",
        )
        self.client.force_login(user)
        csv_file = SimpleUploadedFile(
            "etapas.csv",
            "code,name,order_index,is_active\n1.1,Infraestrutura elétrica,1,True\n".encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("admin:core_projectstage_import_csv"),
            {
                "project": str(self.project.id),
                "csv_file": csv_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Criadas: 1")
        self.assertTrue(self.project.stages.filter(code="1.1", name="Infraestrutura elétrica").exists())


class ProjectDetailLocationTemplateTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="staff-template",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(self.user)

        self.client_obj = Client.objects.create(name="Cliente Tela")
        self.project = Project.objects.create(name="Projeto Tela", client=self.client_obj)
        self.template = LocationTemplate.objects.create(name="Template Tela")
        LocationTemplateItem.objects.create(template=self.template, code="TOR", name="Torre", order_index=1)
        LocationTemplateItem.objects.create(template=self.template, code="GAR", name="Garagem", order_index=2)

    def test_project_detail_apply_template_updates_project_and_creates_locations(self):
        url = reverse("billing:project_detail", args=[self.project.id])

        response = self.client.post(
            url,
            {
                "action": "apply_location_template",
                "project-location_template": str(self.template.id),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.location_template_id, self.template.id)
        self.assertEqual(
            list(self.project.locations.order_by("order_index").values_list("code", flat=True)),
            ["TOR", "GAR"],
        )


class ProjectLocationDisplayTests(TestCase):
    def test_project_location_str_returns_only_code(self):
        client = Client.objects.create(name="Cliente Local")
        project = Project.objects.create(name="Projeto Local", client=client)
        location = ProjectLocation.objects.create(
            project=project,
            code="APT-101",
            name="Apartamento 101",
            order_index=1,
        )

        self.assertEqual(str(location), "APT-101")
