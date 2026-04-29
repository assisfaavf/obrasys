from __future__ import annotations

import unicodedata
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing.models import MeasurementLine, MeasurementLineHistory, MeasurementPeriod, WorkflowStatus
from billing.services.measurement_lines import _sync_line_quantity_from_history, add_or_merge_contracted_line
from catalog.models import BudgetItem, Discipline
from core.models import Project, ProjectLocation
from rdo.models import (
    DailyWorkActivityEntry,
    DailyWorkLog,
    DailyWorkMaterialEntry,
    DailyWorkOccurrence,
    DailyWorkTeamEntry,
    OccurrenceType,
    WeatherCondition,
)

IMPORT_TAG = "IMPORT_RDO_APR2026"
MEASUREMENT_IMPORT_NOTE = "Importado do diario de obra (IMPORT_RDO_APR2026)"

DATASET = [
    {
        "log_date": "2026-04-01",
        "responsible_name": "Francisco de Assis Alves Vasconcelos Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "nublado", "night": ""},
        "teams": [
            {"team_name": "Anatonio Valdir", "worker_count": 1, "location": None, "activity_description": ""},
            {"team_name": "Paulinha", "worker_count": 1, "location": None, "activity_description": ""},
        ],
        "activities": [
            {
                "description": "Marcacao, corte e abertura das locacoes de gas",
                "location": None,
                "discipline": None,
                "notes": "Ate o 4o pavimento (6 unidades)",
            },
            {
                "description": "Instalacao dos tubos multicamadas e conexoes",
                "location": None,
                "discipline": None,
                "notes": "2o e 3o pavimento (4 unidades)",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "6.6.1", "quantity": "76.000", "location": None},
            {"eap_code": "6.6.5", "quantity": "12.000", "location": None},
            {"eap_code": "6.6.6", "quantity": "8.000", "location": None},
        ],
        "materials_for_measurement": [
            {"eap_code": "6.6.1", "quantity": "38.000", "location_code": "1tp"},
            {"eap_code": "6.6.1", "quantity": "38.000", "location_code": "tipo"},
            {"eap_code": "6.6.5", "quantity": "6.000", "location_code": "1tp"},
            {"eap_code": "6.6.5", "quantity": "6.000", "location_code": "tipo"},
            {"eap_code": "6.6.6", "quantity": "4.000", "location_code": "1tp"},
            {"eap_code": "6.6.6", "quantity": "4.000", "location_code": "tipo"},
        ],
    },
    {
        "log_date": "2026-04-02",
        "responsible_name": "Francisco de Assis Alves Vasconcelos Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "nublado", "night": ""},
        "teams": [
            {"team_name": "Anatonio Valdir", "worker_count": 1, "location": None, "activity_description": ""},
            {"team_name": "Paulinha", "worker_count": 1, "location": None, "activity_description": ""},
        ],
        "activities": [
            {
                "description": "Marcacao, corte e abertura das locacoes de gas",
                "location": None,
                "discipline": None,
                "notes": "Ate o 8o pavimento (8 unidades)",
            }
        ],
        "occurrences": [],
        "materials_for_daily_log": [],
        "materials_for_measurement": [],
    },
    {
        "log_date": "2026-04-06",
        "responsible_name": "Francisco de Assis Alves Vasconcelos Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "nublado", "night": ""},
        "teams": [
            {"team_name": "Anatonio Valdir", "worker_count": 1, "location": None, "activity_description": ""},
            {"team_name": "Paulinha", "worker_count": 1, "location": None, "activity_description": ""},
        ],
        "activities": [
            {
                "description": "Marcacao, corte e abertura das locacoes de interruptores e tomadas",
                "location": None,
                "discipline": None,
                "notes": "Apartamento modelo (apenas as localizadas nas paredes existentes)",
            }
        ],
        "occurrences": [],
        "materials_for_daily_log": [],
        "materials_for_measurement": [],
    },
    {
        "log_date": "2026-04-07",
        "responsible_name": "Francisco de Assis Alves Vasconcelos Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "sol", "night": ""},
        "teams": [
            {"team_name": "Anatonio Valdir", "worker_count": 1, "location": None, "activity_description": ""},
            {"team_name": "Paulinha", "worker_count": 1, "location": None, "activity_description": ""},
        ],
        "activities": [
            {
                "description": "instalacao das caixas 4x2 de tomada e interruptores",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo (apenas as localizadas nas paredes existentes)",
            },
            {
                "description": "Fixacao das caixas octogonais de passagem",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo (Apenas banheiros)",
            },
            {
                "description": "Fixacao de eletrodutos",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo (Apenas entre as caixas dos banheiros)",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "1.2.4", "quantity": "10.000", "location": "1tp"},
            {"eap_code": "1.1.14", "quantity": "7.000", "location": "1tp"},
            {"eap_code": "1.1.1", "quantity": "1.000", "location": "1tp"},
            {"eap_code": "1.1.9", "quantity": "16.000", "location": "1tp"},
            {"eap_code": "1.1.10", "quantity": "2.000", "location": "1tp"},
            {"eap_code": "1.1.7", "quantity": "16.000", "location": "1tp"},
        ],
        "materials_for_measurement": [
            {"eap_code": "1.2.4", "quantity": "10.000", "location_code": "1tp"},
            {"eap_code": "1.1.14", "quantity": "7.000", "location_code": "1tp"},
            {"eap_code": "1.1.1", "quantity": "1.000", "location_code": "1tp"},
            {"eap_code": "1.1.9", "quantity": "16.000", "location_code": "1tp"},
            {"eap_code": "1.1.10", "quantity": "2.000", "location_code": "1tp"},
            {"eap_code": "1.1.7", "quantity": "16.000", "location_code": "1tp"},
        ],
    },
    {
        "log_date": "2026-04-08",
        "responsible_name": "Francisco de Assis Alves Vasconcelos Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "sol", "night": ""},
        "teams": [
            {"team_name": "Anatonio Valdir", "worker_count": 1, "location": None, "activity_description": ""},
            {"team_name": "Paulinha", "worker_count": 1, "location": None, "activity_description": ""},
        ],
        "activities": [
            {
                "description": "instalacao das caixas 4x2 de tomada e interruptores",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo (apenas as localizadas nas paredes existentes)",
            },
            {
                "description": "Fixacao das caixas octogonais de passagem",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo",
            },
            {
                "description": "Fixacao de eletrodutos",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo (Apenas banheiros)",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "1.2.4", "quantity": "13.000", "location": "1tp"},
            {"eap_code": "1.1.14", "quantity": "13.000", "location": "1tp"},
            {"eap_code": "1.1.1", "quantity": "1.000", "location": "1tp"},
            {"eap_code": "1.1.9", "quantity": "30.000", "location": "1tp"},
            {"eap_code": "1.1.10", "quantity": "4.000", "location": "1tp"},
            {"eap_code": "1.1.7", "quantity": "30.000", "location": "1tp"},
        ],
        "materials_for_measurement": [
            {"eap_code": "1.2.4", "quantity": "13.000", "location_code": "1tp"},
            {"eap_code": "1.1.14", "quantity": "13.000", "location_code": "1tp"},
            {"eap_code": "1.1.1", "quantity": "1.000", "location_code": "1tp"},
            {"eap_code": "1.1.9", "quantity": "30.000", "location_code": "1tp"},
            {"eap_code": "1.1.10", "quantity": "4.000", "location_code": "1tp"},
            {"eap_code": "1.1.7", "quantity": "30.000", "location_code": "1tp"},
        ],
    },
    {
        "log_date": "2026-04-09",
        "responsible_name": "Assis Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "sol", "night": ""},
        "teams": [
            {
                "team_name": "Spate",
                "worker_count": 2,
                "location": "1tp",
                "activity_description": "Instalacoes de caixas e eletrodutos no apartamento",
            }
        ],
        "activities": [
            {
                "description": "Instalacao das caixas 4x2 de tomadas e interruptores",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "",
            },
            {
                "description": "Fixacao das caixas octogonais de passagem",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "",
            },
            {
                "description": "Fixacao de eletrodutos",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "1.2.4", "quantity": "6.000", "location": "1tp"},
            {"eap_code": "1.1.14", "quantity": "1.000", "location": "1tp"},
            {"eap_code": "1.1.1", "quantity": "16.000", "location": "1tp"},
            {"eap_code": "1.1.10", "quantity": "15.000", "location": "1tp"},
            {"eap_code": "1.1.7", "quantity": "17.000", "location": "1tp"},
            {"eap_code": "1.1.9", "quantity": "17.000", "location": "1tp"},
            {"eap_code": "1.2.1", "quantity": "19.800", "location": "1tp"},
        ],
        "materials_for_measurement": [
            {"eap_code": "1.2.4", "quantity": "6.000", "location_code": "1tp"},
            {"eap_code": "1.1.14", "quantity": "1.000", "location_code": "1tp"},
            {"eap_code": "1.1.1", "quantity": "16.000", "location_code": "1tp"},
            {"eap_code": "1.1.10", "quantity": "15.000", "location_code": "1tp"},
            {"eap_code": "1.1.7", "quantity": "17.000", "location_code": "1tp"},
            {"eap_code": "1.1.9", "quantity": "17.000", "location_code": "1tp"},
            {"eap_code": "1.2.1", "quantity": "19.800", "location_code": "1tp"},
        ],
    },
    {
        "log_date": "2026-04-10",
        "responsible_name": "Assis Filho",
        "general_observation": "",
        "weather": {"morning": "nublado", "afternoon": "", "night": "chuva"},
        "teams": [
            {
                "team_name": "Spate (Paulinho)",
                "worker_count": 1,
                "location": "1tp",
                "activity_description": "fixacao de eletrodutos",
            }
        ],
        "activities": [
            {
                "description": "Instalacao de eletrodutos rigidos e flexiveis",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Apartamento modelo",
            }
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "1.1.1", "quantity": "2.400", "location": "1tp"},
            {"eap_code": "1.2.1", "quantity": "19.450", "location": "1tp"},
            {"eap_code": "1.1.10", "quantity": "6.000", "location": "1tp"},
            {"eap_code": "1.1.7", "quantity": "6.000", "location": "1tp"},
            {"eap_code": "1.1.9", "quantity": "6.000", "location": "1tp"},
        ],
        "materials_for_measurement": [
            {"eap_code": "1.1.1", "quantity": "2.400", "location_code": "1tp"},
            {"eap_code": "1.2.1", "quantity": "19.450", "location_code": "1tp"},
            {"eap_code": "1.1.10", "quantity": "6.000", "location_code": "1tp"},
            {"eap_code": "1.1.7", "quantity": "6.000", "location_code": "1tp"},
            {"eap_code": "1.1.9", "quantity": "6.000", "location_code": "1tp"},
        ],
    },
    {
        "log_date": "2026-04-18",
        "responsible_name": "Assis Filho",
        "general_observation": "Nao teve expediente, itens listados foram feitos ate essa data",
        "weather": {"morning": "sol", "afternoon": "nublado", "night": ""},
        "teams": [
            {
                "team_name": "Spate",
                "worker_count": 2,
                "location": "tipo",
                "activity_description": "Instalacao da tubulacao de gas",
            }
        ],
        "activities": [
            {
                "description": "Instalacao das tubulacoes gas multicamadas nos pavimentos tipo.",
                "location": "tipo",
                "discipline": "6 - Combate a incendio",
                "notes": "Concluido ate o 702 (701 faltando a derivacao do aquecedor e do fogao).",
            },
            {
                "description": "Cobertura das tubulacoes com massa para protecao e fixacao.",
                "location": "tipo",
                "discipline": "6 - Combate a incendio",
                "notes": "Concluido ate o 5o pavimento.",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "6.6.1", "quantity": "144.000", "location": "tipo"},
            {"eap_code": "6.6.5", "quantity": "22.000", "location": "tipo"},
            {"eap_code": "6.6.6", "quantity": "16.000", "location": "tipo"},
        ],
        "materials_for_measurement": [
            {"eap_code": "6.6.1", "quantity": "144.000", "location_code": "tipo"},
            {"eap_code": "6.6.5", "quantity": "22.000", "location_code": "tipo"},
            {"eap_code": "6.6.6", "quantity": "16.000", "location_code": "tipo"},
        ],
    },
    {
        "log_date": "2026-04-22",
        "responsible_name": "Assis Filho",
        "general_observation": "Gas finalizado ate onde existe alvenaria.",
        "weather": {"morning": "nublado", "afternoon": "nublado", "night": ""},
        "teams": [
            {"team_name": "Spate", "worker_count": 2, "location": "tipo", "activity_description": "Gas"}
        ],
        "activities": [
            {
                "description": "Instalacao de tubulacoes e conexoes de gas",
                "location": "tipo",
                "discipline": "6 - Combate a incendio",
                "notes": "701 concluido (derivacoes de fogao e aquecedor que faltava) e 8o andar completo concluido",
            },
            {
                "description": "Fixacao e cobrimento das tubulacoes instaladas",
                "location": "tipo",
                "discipline": "6 - Combate a incendio",
                "notes": "",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "6.6.1", "quantity": "42.000", "location": "tipo"},
            {"eap_code": "6.6.5", "quantity": "8.000", "location": "tipo"},
            {"eap_code": "6.6.6", "quantity": "4.000", "location": "tipo"},
        ],
        "materials_for_measurement": [
            {"eap_code": "6.6.1", "quantity": "42.000", "location_code": "tipo"},
            {"eap_code": "6.6.5", "quantity": "8.000", "location_code": "tipo"},
            {"eap_code": "6.6.6", "quantity": "4.000", "location_code": "tipo"},
        ],
    },
    {
        "log_date": "2026-04-23",
        "responsible_name": "Assis Filho",
        "general_observation": "",
        "weather": {"morning": "sol", "afternoon": "nublado", "night": ""},
        "teams": [
            {
                "team_name": "Spate",
                "worker_count": 2,
                "location": "1tp",
                "activity_description": "Corte e fixacao de caixas",
            }
        ],
        "activities": [
            {
                "description": "Corte dos locais de caixa 4x2 e eletrodutos flexiveis.",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "Marcadas apenas caixas em que as paredes de referencia estavam presentes.",
            },
            {
                "description": "Fixacao de caixas octogonais.",
                "location": "1tp",
                "discipline": "1 - Eletrica",
                "notes": "",
            },
        ],
        "occurrences": [],
        "materials_for_daily_log": [
            {"eap_code": "1.1.14", "quantity": "15.000", "location": "1tp"},
            {"eap_code": "1.1.9", "quantity": "30.000", "location": "1tp"},
        ],
        "materials_for_measurement": [
            {"eap_code": "1.1.14", "quantity": "15.000", "location_code": "1tp"},
            {"eap_code": "1.1.9", "quantity": "30.000", "location_code": "1tp"},
        ],
    },
]

WEATHER_MAP = {
    "": "",
    "sol": WeatherCondition.CLEAR,
    "limpo": WeatherCondition.CLEAR,
    "nublado": WeatherCondition.CLOUDY,
    "chuva": WeatherCondition.RAINY,
    "chuvoso": WeatherCondition.RAINY,
    "vento": WeatherCondition.WINDY,
    "ventoso": WeatherCondition.WINDY,
    "paralisado": WeatherCondition.STOPPED,
}

OCCURRENCE_TYPE_MAP = {
    "orientacao": OccurrenceType.ORIENTACAO,
    "acidente": OccurrenceType.ACIDENTE,
    "interrupcao": OccurrenceType.INTERRUPCAO,
    "visita": OccurrenceType.VISITA,
    "inspecao": OccurrenceType.INSPECAO,
    "outro": OccurrenceType.OUTRO,
}


def _normalize_text(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().strip().split())


def _parse_date(raw_value: str) -> date:
    return date.fromisoformat(raw_value)


def _parse_decimal(raw_value: str) -> Decimal:
    return Decimal(str(raw_value))


def _resolve_weather(raw_value: str) -> str:
    key = _normalize_text(raw_value)
    if key not in WEATHER_MAP:
        raise CommandError(f"Condicao de tempo nao suportada: {raw_value!r}")
    return WEATHER_MAP[key]


def _discipline_candidates(discipline: Discipline) -> set[str]:
    values = {
        _normalize_text(discipline.name),
        _normalize_text(discipline.code),
        _normalize_text(str(discipline)),
    }
    if discipline.code and discipline.name:
        values.add(_normalize_text(f"{discipline.code} - {discipline.name}"))
    return {value for value in values if value}


class Command(BaseCommand):
    help = "Recria os diarios de abril/2026 do projeto Palatium e relanca os materiais na medicao 1."

    def handle(self, *args, **options):
        with transaction.atomic():
            project = self._resolve_project()
            period = self._resolve_period(project)
            locations = self._resolve_locations(project)
            disciplines = self._resolve_disciplines()
            budget_items = self._resolve_budget_items(project)

            deleted_histories, deleted_lines = self._cleanup_previous_measurement_import(period)

            created_logs = 0
            updated_logs = 0
            created_histories = 0

            for payload in DATASET:
                _, created = self._sync_daily_log(
                    project=project,
                    payload=payload,
                    locations=locations,
                    disciplines=disciplines,
                    budget_items=budget_items,
                )
                if created:
                    created_logs += 1
                else:
                    updated_logs += 1

                created_histories += self._sync_measurement_materials(
                    period=period,
                    payload=payload,
                    locations=locations,
                    budget_items=budget_items,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Importacao concluida com sucesso: "
                f"logs_criados={created_logs}, "
                f"logs_atualizados={updated_logs}, "
                f"historicos_recriados={created_histories}, "
                f"historicos_removidos={deleted_histories}, "
                f"linhas_removidas={deleted_lines}."
            )
        )

    def _resolve_project(self) -> Project:
        project = Project.objects.filter(name__iexact="Condominio palatium").first()
        if project is not None:
            return project

        project = Project.objects.filter(name__icontains="Palatium").order_by("id").first()
        if project is not None:
            return project

        raise CommandError("Projeto Palatium nao encontrado.")

    def _resolve_period(self, project: Project) -> MeasurementPeriod:
        period = MeasurementPeriod.objects.filter(project=project, number=1).first()
        if period is None:
            raise CommandError("Medicao 1 do projeto Palatium nao encontrada.")
        if period.workflow_status != WorkflowStatus.DRAFT:
            raise CommandError("A medicao 1 precisa estar em DRAFT para receber os lancamentos.")
        return period

    def _resolve_locations(self, project: Project) -> dict[str, ProjectLocation]:
        location_map = {
            _normalize_text(location.code): location
            for location in project.locations.filter(is_active=True).order_by("order_index", "code")
        }
        for required_code in ("1tp", "tipo"):
            normalized_code = _normalize_text(required_code)
            if normalized_code not in location_map:
                raise CommandError(f"Location obrigatoria nao encontrada: {required_code}")
        return location_map

    def _resolve_disciplines(self) -> dict[str, Discipline]:
        discipline_map: dict[str, Discipline] = {}
        for discipline in Discipline.objects.filter(is_active=True).order_by("code", "name"):
            for candidate in _discipline_candidates(discipline):
                discipline_map.setdefault(candidate, discipline)
        return discipline_map

    def _resolve_budget_items(self, project: Project) -> dict[str, BudgetItem]:
        budget_items = {
            item.eap_code: item
            for item in project.budget_items.filter(is_active=True).select_related("unit").order_by("eap_code")
        }

        required_codes = {
            material["eap_code"]
            for payload in DATASET
            for group_name in ("materials_for_daily_log", "materials_for_measurement")
            for material in payload[group_name]
        }
        missing_codes = sorted(required_codes - set(budget_items))
        if missing_codes:
            raise CommandError(
                f"Itens contratuais nao encontrados no projeto Palatium: {', '.join(missing_codes)}"
            )
        return budget_items

    def _resolve_location(self, locations: dict[str, ProjectLocation], raw_code: str | None) -> ProjectLocation | None:
        if not raw_code:
            return None
        location = locations.get(_normalize_text(raw_code))
        if location is None:
            raise CommandError(f"Location nao encontrada: {raw_code}")
        return location

    def _resolve_discipline(self, disciplines: dict[str, Discipline], raw_value: str | None) -> Discipline | None:
        if not raw_value:
            return None
        discipline = disciplines.get(_normalize_text(raw_value))
        if discipline is not None:
            return discipline

        raise CommandError(f"Disciplina nao encontrada: {raw_value}")

    def _resolve_occurrence_type(self, raw_value: str) -> str:
        key = _normalize_text(raw_value)
        if key not in OCCURRENCE_TYPE_MAP:
            raise CommandError(f"Tipo de ocorrencia nao suportado: {raw_value}")
        return OCCURRENCE_TYPE_MAP[key]

    def _sync_daily_log(
        self,
        *,
        project: Project,
        payload: dict,
        locations: dict[str, ProjectLocation],
        disciplines: dict[str, Discipline],
        budget_items: dict[str, BudgetItem],
    ) -> tuple[DailyWorkLog, bool]:
        log_date = _parse_date(payload["log_date"])
        daily_log, created = DailyWorkLog.objects.update_or_create(
            project=project,
            log_date=log_date,
            defaults={
                "responsible_name": payload["responsible_name"],
                "weather_morning": _resolve_weather(payload["weather"]["morning"]),
                "weather_afternoon": _resolve_weather(payload["weather"]["afternoon"]),
                "weather_night": _resolve_weather(payload["weather"]["night"]),
                "notes": "",
                "general_observation": payload.get("general_observation", ""),
                "interruption_reason": "",
            },
        )

        DailyWorkTeamEntry.objects.filter(daily_log=daily_log, import_tag=IMPORT_TAG).delete()
        DailyWorkActivityEntry.objects.filter(daily_log=daily_log, import_tag=IMPORT_TAG).delete()
        DailyWorkOccurrence.objects.filter(daily_log=daily_log, import_tag=IMPORT_TAG).delete()
        DailyWorkMaterialEntry.objects.filter(daily_log=daily_log, import_tag=IMPORT_TAG).delete()

        for team in payload["teams"]:
            DailyWorkTeamEntry.objects.create(
                daily_log=daily_log,
                import_tag=IMPORT_TAG,
                team_name=team["team_name"],
                worker_count=team["worker_count"],
                location=self._resolve_location(locations, team.get("location")),
                activity_description=team.get("activity_description") or "-",
                notes="",
            )

        for activity in payload["activities"]:
            DailyWorkActivityEntry.objects.create(
                daily_log=daily_log,
                import_tag=IMPORT_TAG,
                description=activity["description"],
                location=self._resolve_location(locations, activity.get("location")),
                discipline=self._resolve_discipline(disciplines, activity.get("discipline")),
                notes=activity.get("notes", ""),
            )

        for occurrence in payload["occurrences"]:
            DailyWorkOccurrence.objects.create(
                daily_log=daily_log,
                import_tag=IMPORT_TAG,
                occurrence_type=self._resolve_occurrence_type(occurrence["occurrence_type"]),
                description=occurrence["description"],
                notes=occurrence.get("notes", ""),
            )

        for material in payload["materials_for_daily_log"]:
            DailyWorkMaterialEntry.objects.create(
                daily_log=daily_log,
                import_tag=IMPORT_TAG,
                item=budget_items[material["eap_code"]],
                location=self._resolve_location(locations, material.get("location")),
                quantity=_parse_decimal(material["quantity"]),
                notes="",
            )

        return daily_log, created

    def _cleanup_previous_measurement_import(self, period: MeasurementPeriod) -> tuple[int, int]:
        imported_histories = list(
            MeasurementLineHistory.objects.select_related("line")
            .filter(
                line__period=period,
                line__line_kind="CONTRACTED",
                line__is_generated_additional=False,
                note=MEASUREMENT_IMPORT_NOTE,
                is_generated_additional_entry=False,
            )
            .order_by("line_id", "id")
        )
        if not imported_histories:
            return 0, 0

        affected_line_ids = sorted({history.line_id for history in imported_histories})
        deleted_histories = len(imported_histories)
        MeasurementLineHistory.objects.filter(pk__in=[history.pk for history in imported_histories]).delete()

        deleted_lines = 0
        for line in MeasurementLine.objects.select_related("period").filter(pk__in=affected_line_ids).order_by("id"):
            has_manual_history = line.histories.filter(is_generated_additional_entry=False).exists()
            if has_manual_history:
                _sync_line_quantity_from_history(line=line)
                continue
            line.delete()
            deleted_lines += 1

        return deleted_histories, deleted_lines

    def _sync_measurement_materials(
        self,
        *,
        period: MeasurementPeriod,
        payload: dict,
        locations: dict[str, ProjectLocation],
        budget_items: dict[str, BudgetItem],
    ) -> int:
        application_date = _parse_date(payload["log_date"])
        created_histories = 0
        for material in payload["materials_for_measurement"]:
            add_or_merge_contracted_line(
                period=period,
                item=budget_items[material["eap_code"]],
                location=self._resolve_location(locations, material["location_code"]),
                qty_period=_parse_decimal(material["quantity"]),
                application_date=application_date,
                note=MEASUREMENT_IMPORT_NOTE,
                use_additional_materials=False,
                created_by=None,
            )
            created_histories += 1
        return created_histories
