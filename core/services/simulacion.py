import random
from datetime import datetime, time
from decimal import Decimal

from django.utils import timezone

from core.models import ConsumoLog, EstadoSimulacion, ItemDespensa, TipoServicio

# Valores base y variacion normal por servicio, calibrados contra las lecturas
# reales ya cargadas en Supabase (agua ~12-13, luz ~5). Gas no tenia historial:
# se define un rango razonable para que el agente de consumo tenga con que comparar.
BASELINE_CONSUMO = {
    TipoServicio.AGUA: {"base": Decimal("13"), "jitter_pct": Decimal("0.15")},
    TipoServicio.LUZ: {"base": Decimal("5"), "jitter_pct": Decimal("0.15")},
    TipoServicio.GAS: {"base": Decimal("7"), "jitter_pct": Decimal("0.15")},
}
PROBABILIDAD_PICO = 0.15
RANGO_PICO = (Decimal("1.3"), Decimal("1.9"))


def _valor_dia(config: dict) -> Decimal:
    base = config["base"]
    jitter = config["jitter_pct"]
    factor = Decimal(str(random.uniform(float(1 - jitter), float(1 + jitter))))
    valor = base * factor
    if random.random() < PROBABILIDAD_PICO:
        pico = Decimal(str(random.uniform(float(RANGO_PICO[0]), float(RANGO_PICO[1]))))
        valor *= pico
    return valor.quantize(Decimal("0.01"))


def avanzar_dia() -> EstadoSimulacion:
    """Un 'dia simulado': baja el stock de despensa segun el consumo promedio
    y genera una nueva lectura de consumo por servicio. Se corre una vez por Pulso,
    antes de que los agentes evaluen el estado.
    """
    estado = EstadoSimulacion.actual()
    estado.avanzar_un_dia()

    for item in ItemDespensa.objects.all():
        nuevo_stock = item.stock_actual - item.consumo_promedio_diario
        item.stock_actual = max(nuevo_stock, Decimal("0"))
        item.save(update_fields=["stock_actual", "updated_at"])

    momento = timezone.make_aware(datetime.combine(estado.fecha_actual, time(hour=9)))
    for tipo, config in BASELINE_CONSUMO.items():
        ConsumoLog.objects.create(
            timestamp=momento,
            tipo_servicio=tipo,
            valor_medicion=_valor_dia(config),
            etiqueta=f"dia_simulado_{estado.dia_numero}",
        )

    return estado
