from django.db.models import F

from core.models import ItemDespensa

from .llm import pedir_decision_json

SYSTEM_PROMPT = """Sos el Agente de Despensa de un sistema de hogar inteligente (SofIA).
Se te informa un producto cuyo stock llego al minimo. Tu trabajo es decidir cuanto reponer
y redactar una justificacion tecnica breve (1-2 frases, en espanol) basada en el stock
actual, el consumo promedio diario, la unidad de medida del producto, la fecha de
vencimiento si existe, y el saldo disponible en el presupuesto de la categoria. Vos tomas
la decision final: si el saldo es bajo o no alcanza para el precio estimado, ajusta la
cantidad sugerida a la baja (la minima cantidad razonable) en vez de ignorar el saldo,
pero la decision es tuya.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"cantidad_sugerida": "<texto legible con la unidad correcta, ej. '2L' o '3 unidades' o '0.5kg'>",
 "justificacion_tecnica": "<texto>"}
"""


def detectar_items_criticos():
    """CU-01 trigger: stock_actual <= stock_minimo."""
    return list(ItemDespensa.objects.filter(stock_actual__lte=F("stock_minimo")))


def evaluar(item: ItemDespensa, saldo_disponible=None) -> dict:
    contexto = {
        "item": item.nombre,
        "unidad_medida": item.unidad_medida or None,
        "stock_actual": item.stock_actual,
        "stock_minimo": item.stock_minimo,
        "consumo_promedio_diario": item.consumo_promedio_diario,
        "fecha_vencimiento": item.fecha_vencimiento,
        "precio_estimado": item.precio_estimado,
        "saldo_disponible_categoria": saldo_disponible,
    }
    return pedir_decision_json(SYSTEM_PROMPT, contexto)
