from decimal import Decimal

from django.utils import timezone

from core.models import ConsumoLog, TipoServicio

from .llm import pedir_decision_json

UMBRAL_DESVIACION = Decimal("1.2")  # 20% sobre la media movil
ETIQUETA_EXCEPCION = "EXCEPCION_CONOCIDA"
HORAS_SILENCIO = 24


def _servicio_silenciado(tipo: str) -> bool:
    """CU-02 flujo alternativo: el residente respondio 'Ignorar, es intencional' y
    el Orquestador etiqueto un log reciente como EXCEPCION_CONOCIDA. Mientras esa
    etiqueta tenga menos de 24hs, no se vuelve a alertar para ese servicio.
    """
    limite = timezone.now() - timezone.timedelta(hours=HORAS_SILENCIO)
    return ConsumoLog.objects.filter(
        tipo_servicio=tipo, etiqueta=ETIQUETA_EXCEPCION, timestamp__gte=limite
    ).exists()

SYSTEM_PROMPT = """Sos el Agente de Consumo de un sistema de hogar inteligente (SofIA).
Se te informa una lectura de un servicio (agua, luz o gas) que supero en mas de 20% la
media movil reciente. Clasifica la anomalia y redacta una justificacion tecnica breve
(1-2 frases, en espanol).

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"clasificacion": "POSIBLE_PERDIDA" | "USO_ELEVADO_NORMAL",
 "justificacion_tecnica": "<texto>"}
"""


def detectar_anomalias():
    """CU-02 trigger: ultima lectura > 20% sobre la media movil de lecturas previas."""
    anomalias = []
    for tipo in TipoServicio.values:
        if _servicio_silenciado(tipo):
            continue
        logs = list(ConsumoLog.objects.filter(tipo_servicio=tipo).order_by("-timestamp")[:8])
        if len(logs) < 2:
            continue
        ultimo, historicos = logs[0], logs[1:]
        media_movil = sum(l.valor_medicion for l in historicos) / len(historicos)
        if media_movil and ultimo.valor_medicion > media_movil * UMBRAL_DESVIACION:
            anomalias.append((ultimo, media_movil))
    return anomalias


def evaluar(ultimo: ConsumoLog, media_movil: Decimal) -> dict:
    contexto = {
        "tipo_servicio": ultimo.tipo_servicio,
        "valor_medicion": ultimo.valor_medicion,
        "media_movil_historica": round(media_movil, 2),
        "desviacion_pct": round((ultimo.valor_medicion / media_movil - 1) * 100, 1),
    }
    return pedir_decision_json(SYSTEM_PROMPT, contexto)
