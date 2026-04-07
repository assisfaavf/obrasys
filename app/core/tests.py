from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import Client, LocationTemplate, LocationTemplateItem, Project, ProjectLocation
from core.services import apply_location_template_to_project


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
