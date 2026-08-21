from django.contrib import admin

from core.models import (
    ConsumoLog,
    Dispositivo,
    EstadoSimulacion,
    IngresosHogar,
    ItemDespensa,
    Presupuesto,
    Residente,
)


@admin.register(Residente)
class ResidenteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "telefono", "nivel_permiso", "created_at")


@admin.register(ItemDespensa)
class ItemDespensaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "stock_actual", "stock_minimo", "fecha_vencimiento", "presupuesto")


@admin.register(Dispositivo)
class DispositivoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "prioridad", "estado_actual", "fecha_ultimo_service")


@admin.register(Presupuesto)
class PresupuestoAdmin(admin.ModelAdmin):
    list_display = ("categoria", "limite_mensual", "monto_gastado", "es_esencial")


@admin.register(ConsumoLog)
class ConsumoLogAdmin(admin.ModelAdmin):
    list_display = ("tipo_servicio", "valor_medicion", "timestamp", "etiqueta")


@admin.register(EstadoSimulacion)
class EstadoSimulacionAdmin(admin.ModelAdmin):
    list_display = ("dia_numero", "fecha_actual", "actualizado_en")


@admin.register(IngresosHogar)
class IngresosHogarAdmin(admin.ModelAdmin):
    list_display = ("saldo_disponible", "actualizado_en")
