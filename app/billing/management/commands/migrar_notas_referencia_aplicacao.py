import csv
import re
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from billing.models import MeasurementLine, MeasurementLineHistory

REFERENCE_SEPARATOR_RE = re.compile(r"\s*[-\u2013\u2014]\s*")
REFERENCE_END_RE = re.compile(
    r"(?i)\b("
    r"(?:apartamento|apto|unidade|bloco|prumada|sala|loja|casa)\s+[a-z0-9]+"
    r"|(?:terreo|t[e\u00e9]rreo|subsolo|cobertura)"
    r"|[a-z]?\d{1,4}[a-z]?"
    r"|[a-z]"
    r")$"
)
REFERENCE_START_RE = re.compile(
    r"(?i)\b("
    r"banheiro|cozinha|suite|su[i\u00ed]te|shaft|lavanderia|area|[a\u00e1]rea|casa|sala|loja|quarto|prumada|hall|varanda"
    r")\b"
)
ACTION_WORD_RE = re.compile(
    r"(?i)\b("
    r"trocar|substitu[i\u00ed]d[oa]|danificado|aplicad[oa]|parcial|falta|concluir|verificar|alterar|ajustad[oa]|trajeto"
    r")\b"
)


@dataclass
class Candidate:
    source: str
    obj: MeasurementLine | MeasurementLineHistory
    status: str
    reason: str
    note: str
    suggested_reference: str = ""


class Command(BaseCommand):
    help = "Migra notas que representam referencias de aplicacao para campo proprio."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Analisa sem alterar dados. Padrao seguro.")
        mode.add_argument("--apply", action="store_true", help="Aplica apenas registros classificados como seguros.")
        parser.add_argument("--export-csv", help="Caminho para exportar a analise em CSV.")

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        candidates = self._collect_candidates()
        export_csv = options["export_csv"]
        if apply_changes and not export_csv:
            export_csv = f"backup_migracao_referencia_aplicacao_{timezone.now():%Y%m%d_%H%M%S}.csv"
        if export_csv:
            self._export_csv(Path(export_csv), candidates)

        if apply_changes:
            with transaction.atomic():
                for candidate in candidates:
                    if candidate.status != "migrar":
                        continue
                    candidate.obj.application_reference = candidate.suggested_reference
                    candidate.obj.note = ""
                    candidate.obj.save(update_fields=["application_reference", "note"])

        self._print_summary(candidates, apply_changes=apply_changes, export_csv=export_csv)

    def _collect_candidates(self) -> list[Candidate]:
        candidates: list[Candidate] = []
        line_qs = (
            MeasurementLine.objects.select_related("period", "item", "location")
            .exclude(note="")
            .order_by("id")
        )
        history_qs = (
            MeasurementLineHistory.objects.select_related("line", "line__period", "line__item", "line__location")
            .exclude(note="")
            .order_by("id")
        )
        for line in line_qs:
            candidates.append(self._classify("line", line))
        for history in history_qs:
            candidates.append(self._classify("history", history))
        return candidates

    def _classify(self, source: str, obj) -> Candidate:
        note = obj.note or ""
        if (obj.application_reference or "").strip():
            return Candidate(source, obj, "referencia_existente", "Referencia ja preenchida.", note)

        normalized = _normalize_reference(note)
        separator_count = len(re.findall(r"[-\u2013\u2014]", normalized))
        if separator_count != 1:
            return Candidate(source, obj, "preservar", "Sem separador unico de referencia.", note)
        if "." in normalized or ACTION_WORD_RE.search(normalized):
            return Candidate(source, obj, "ambiguo", "Contem frase ou palavra de acao.", note)

        start, end = REFERENCE_SEPARATOR_RE.split(normalized, maxsplit=1)
        if not start or not end:
            return Candidate(source, obj, "preservar", "Referencia incompleta.", note)
        if not REFERENCE_START_RE.search(start):
            return Candidate(source, obj, "preservar", "Inicio nao parece ambiente.", note)
        if not REFERENCE_END_RE.search(end):
            return Candidate(source, obj, "ambiguo", "Final nao parece identificador seguro.", note)

        return Candidate(source, obj, "migrar", "Referencia segura.", note, normalized)

    def _export_csv(self, path: Path, candidates: list[Candidate]) -> None:
        try:
            with path.open("w", newline="", encoding="utf-8-sig") as csvfile:
                writer = csv.DictWriter(
                    csvfile,
                    fieldnames=[
                        "source",
                        "material_id",
                        "medicao_id",
                        "item",
                        "localizacao",
                        "notas_atuais",
                        "referencia_sugerida",
                        "status",
                        "motivo",
                    ],
                )
                writer.writeheader()
                for candidate in candidates:
                    line = candidate.obj if candidate.source == "line" else candidate.obj.line
                    writer.writerow(
                        {
                            "source": candidate.source,
                            "material_id": candidate.obj.id,
                            "medicao_id": line.period_id,
                            "item": line.item.description if line.item_id else "",
                            "localizacao": line.location.code if line.location_id else "",
                            "notas_atuais": candidate.note,
                            "referencia_sugerida": candidate.suggested_reference,
                            "status": candidate.status,
                            "motivo": candidate.reason,
                        }
                    )
        except OSError as exc:
            raise CommandError(f"Nao foi possivel exportar CSV: {exc}") from exc

    def _print_summary(self, candidates: list[Candidate], *, apply_changes: bool, export_csv: str | None) -> None:
        totals = {
            "migrar": 0,
            "preservar": 0,
            "ambiguo": 0,
            "referencia_existente": 0,
        }
        for candidate in candidates:
            totals[candidate.status] += 1

        self.stdout.write(f"Modo: {'apply' if apply_changes else 'dry-run'}")
        self.stdout.write(f"Materiais/lancamentos analisados: {len(candidates)}")
        self.stdout.write(f"Referencias identificadas: {totals['migrar']}")
        self.stdout.write(f"Notas preservadas: {totals['preservar']}")
        self.stdout.write(f"Registros ambiguos: {totals['ambiguo']}")
        self.stdout.write(f"Registros com referencia existente: {totals['referencia_existente']}")
        if export_csv:
            self.stdout.write(f"CSV exportado: {export_csv}")

        for candidate in candidates:
            if candidate.status == "migrar":
                self.stdout.write(
                    f"{candidate.source}:{candidate.obj.id} | notas='{candidate.note}' -> referencia='{candidate.suggested_reference}'"
                )


def _normalize_reference(value: str) -> str:
    value = " ".join((value or "").strip().split())
    value = re.sub(r"\s*([-\u2013\u2014])\s*", r" \1 ", value)
    return " ".join(value.split())
