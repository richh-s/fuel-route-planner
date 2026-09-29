from django.contrib import admin

from apps.geodata.models import Place


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "state", "latitude", "longitude", "source")
    list_filter = ("state", "source")
    search_fields = ("name", "key")
