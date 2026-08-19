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


def _consumo_despensa_por_item(residentes: list) -> dict:
    """Suma las cantidades consumidas por todos los residentes, agrupadas por
    nombre de item (case-insensitive), a partir del body de /api/pulso/.
    """
    totales: dict[str, Decimal] = {}
    for residente in residentes:
        for consumo in residente.get("consumo_despensa", []):
            nombre = str(consumo.get("item", "")).strip().lower()
            cantidad = consumo.get("cantidad")
            if not nombre or cantidad is None:
                continue
            totales[nombre] = totales.get(nombre, Decimal("0")) + Decimal(str(cantidad))
    return totales


def _consumo_servicios_por_tipo(residentes: list) -> dict:
    """Suma el consumo de servicios reportado por todos los residentes, agrupado
    por tipo de servicio (AGUA/LUZ/GAS)."""
    totales: dict[str, Decimal] = {}
    for residente in residentes:
        for tipo, cantidad in residente.get("consumo_servicios", {}).items():
            tipo = str(tipo).strip().upper()
            if cantidad is None:
                continue
            totales[tipo] = totales.get(tipo, Decimal("0")) + Decimal(str(cantidad))
    return totales


def avanzar_dia(payload: dict | None = None) -> EstadoSimulacion:
    """Un 'dia simulado': baja el stock de despensa y genera una nueva lectura de
    consumo por servicio. Se corre una vez por Pulso, antes de que los agentes
    evaluen el estado.

    `payload` es el body opcional de /api/pulso/, con la forma:
    {"residentes_en_casa": [{"telefono": "...", "consumo_despensa": [{"item": "...", "cantidad": N}],
     "consumo_servicios": {"AGUA": N, "LUZ": N, "GAS": N}}]}
    Si un item o servicio no aparece en el payload (o no se manda payload), se usa
    el comportamiento baseline de siempre (consumo_promedio_diario / valor random).
    """
    estado = EstadoSimulacion.actual()
    estado.avanzar_un_dia()

    residentes = (payload or {}).get("residentes_en_casa", [])
    consumo_despensa = _consumo_despensa_por_item(residentes)
    consumo_servicios = _consumo_servicios_por_tipo(residentes)

    for item in ItemDespensa.objects.all():
        consumido = consumo_despensa.get(item.nombre.strip().lower())
        delta = consumido if consumido is not None else item.consumo_promedio_diario
        item.stock_actual = max(item.stock_actual - delta, Decimal("0"))
        item.save(update_fields=["stock_actual", "updated_at"])

    momento = timezone.make_aware(datetime.combine(estado.fecha_actual, time(hour=9)))
    for tipo, config in BASELINE_CONSUMO.items():
        valor = consumo_servicios.get(tipo)
        if valor is None:
            valor = _valor_dia(config)
        else:
            valor = valor.quantize(Decimal("0.01"))
        ConsumoLog.objects.create(
            timestamp=momento,
            tipo_servicio=tipo,
            valor_medicion=valor,
            etiqueta=f"dia_simulado_{estado.dia_numero}",
        )

    return estado
