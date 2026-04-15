from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.views import BudgetItemViewSet, DailyWorkLogViewSet, MeasurementPeriodViewSet, ProjectViewSet

app_name = "api"

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("budget-items", BudgetItemViewSet, basename="budgetitem")
router.register("measurement-periods", MeasurementPeriodViewSet, basename="measurementperiod")
router.register("daily-work-logs", DailyWorkLogViewSet, basename="dailyworklog")

urlpatterns = [
    path("", include(router.urls)),
]
