from django.db.models import F

from core.models import ItemDespensa

from .llm import pedir_decision_json

SYSTEM_PROMPT = """Sos el Agente de Despensa de un sistema de hogar inteligente (SofIA).
Se te informa un producto cuyo stock llego al minimo. Tu trabajo es decidir cuanto reponer
y redactar una justificacion tecnica breve (1-2 frases, en espanol) basada en el stock
actual, el consumo promedio diario y la fecha de vencimiento si existe.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"cantidad_sugerida": "<texto legible, ej. '2L' o '3 unidades'>",
 "justificacion_tecnica": "<texto>"}
"""


def detectar_items_criticos():
    """CU-01 trigger: stock_actual <= stock_minimo."""
    return list(ItemDespensa.objects.filter(stock_actual__lte=F("stock_minimo")))


def evaluar(item: ItemDespensa) -> dict:
    contexto = {
        "item": item.nombre,
        "stock_actual": item.stock_actual,
        "stock_minimo": item.stock_minimo,
        "consumo_promedio_diario": item.consumo_promedio_diario,
        "fecha_vencimiento": item.fecha_vencimiento,
        "precio_estimado": item.precio_estimado,
    }
    return pedir_decision_json(SYSTEM_PROMPT, contexto)
