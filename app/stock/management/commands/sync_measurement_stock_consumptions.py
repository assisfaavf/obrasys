from django.core.management.base import BaseCommand

from billing.models import MeasurementPeriod
from stock.measurement_consumption import sync_pending_measurement_stock_consumptions


class Command(BaseCommand):
    help = "Sincroniza consumos de estoque pendentes dos materiais aplicados em medicoes em rascunho."

    def add_arguments(self, parser):
        parser.add_argument(
            "--measurement",
            type=int,
            dest="measurement_id",
            help="ID da medicao que deve ser sincronizada. Se omitido, sincroniza todas em rascunho.",
        )

    def handle(self, *args, **options):
        measurement = None
        measurement_id = options.get("measurement_id")
        if measurement_id:
            measurement = MeasurementPeriod.objects.get(pk=measurement_id)

        result = sync_pending_measurement_stock_consumptions(measurement=measurement)
        self.stdout.write(
            self.style.SUCCESS(
                f"Materiais sincronizados: {result['synced']}. Ignorados sem local de retirada: {result['skipped']}."
            )
        )
        for material in result["skipped_materials"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Ignorado MeasurementMaterial #{material.pk}: informe o local de retirada para sincronizar."
                )
            )
