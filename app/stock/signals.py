from django.db.models.signals import pre_delete
from django.dispatch import receiver

from billing.models import MeasurementPeriod
from stock.measurement_consumption import reverse_measurement_stock_consumption


@receiver(pre_delete, sender=MeasurementPeriod)
def reverse_measurement_stock_before_delete(sender, instance, **kwargs):
    reverse_measurement_stock_consumption(instance)
