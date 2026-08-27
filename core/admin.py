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
    list_display = ("nombre", "telefono", "telegram_id", "nivel_permiso", "created_at")


@admin.register(ItemDespensa)
class ItemDespensaAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "stock_actual",
        "stock_minimo",
        "gustos",
        "fecha_vencimiento",
        "presupuesto",
    )
    list_filter = ("gustos",)


@admin.register(Dispositivo)
class DispositivoAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "prioridad",
        "estado_actual",
        "vida_util_estimada",
        "fecha_instalacion",
        "dias_entre_service",
        "fecha_ultima_revision",
        "costo_service",
        "costo_reemplazo",
        "gustos",
    )
    list_filter = ("gustos", "estado_actual")


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
    list_display = (
        "saldo_disponible",
        "ahorros",
        "deuda",
        "porcentaje_ahorrable",
        "actualizado_en",
    )
