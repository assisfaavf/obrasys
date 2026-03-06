from django.contrib import admin

from core.models import Client, LocationTemplate, LocationTemplateItem, Project, ProjectLocation


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "document", "email", "phone")
    search_fields = ("name", "document", "email", "phone")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "client", "start_date", "planned_end_date")
    list_filter = ("status", "client")
    search_fields = ("name", "client__name")


@admin.register(ProjectLocation)
class ProjectLocationAdmin(admin.ModelAdmin):
    list_display = ("project", "code", "name", "order_index", "is_active")
    list_filter = ("project", "is_active")
    search_fields = ("project__name", "code", "name")
    ordering = ("project", "order_index", "code")


class LocationTemplateItemInline(admin.TabularInline):
    model = LocationTemplateItem
    extra = 0
    ordering = ("order_index", "code")


@admin.register(LocationTemplate)
class LocationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    inlines = (LocationTemplateItemInline,)


@admin.register(LocationTemplateItem)
class LocationTemplateItemAdmin(admin.ModelAdmin):
    list_display = ("template", "code", "name", "order_index")
    list_filter = ("template",)
    search_fields = ("template__name", "code", "name")
    ordering = ("template", "order_index", "code")
