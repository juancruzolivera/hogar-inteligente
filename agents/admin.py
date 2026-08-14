from django.contrib import admin

from agents.models import DecisionLog


@admin.register(DecisionLog)
class DecisionLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "id_agente", "accion_tomada", "residente_autorizador")
    list_filter = ("id_agente",)
