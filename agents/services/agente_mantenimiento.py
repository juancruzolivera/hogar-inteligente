from datetime import date

from core.models import Dispositivo, EstadoDispositivo, EstadoSimulacion

from .llm import pedir_decision_json

UMBRAL_DEGRADACION = 90  # % de vida util consumida para considerar el service critico

SYSTEM_PROMPT = """Sos el Agente de Mantenimiento de un sistema de hogar inteligente (SofIA).
Se te informa un electrodomestico cuya degradacion estimada supero el umbral critico.
Redacta una justificacion tecnica breve (1-2 frases, en espanol) para agendar el service,
mencionando la prioridad del dispositivo y hace cuanto no recibe mantenimiento.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"justificacion_tecnica": "<texto>"}
"""


def calcular_degradacion(dispositivo: Dispositivo, fecha_referencia: date | None = None) -> float:
    """Degradacion = dias desde el ultimo service sobre la vida util estimada (en dias).

    fecha_referencia es el 'hoy' de la simulacion (EstadoSimulacion.fecha_actual), no la
    fecha real del servidor: el Pulso avanza 1 dia simulado por llamada, y necesitamos que
    la degradacion progrese con esos dias simulados para que la demo sea coherente.
    """
    if fecha_referencia is None:
        fecha_referencia = EstadoSimulacion.actual().fecha_actual
    if not dispositivo.fecha_ultimo_service or dispositivo.vida_util_estimada <= 0:
        return 100.0
    dias_transcurridos = (fecha_referencia - dispositivo.fecha_ultimo_service).days
    return round(min(100.0, max(0.0, dias_transcurridos / dispositivo.vida_util_estimada * 100)), 1)


def detectar_dispositivos_criticos():
    """CU-03 trigger: degradacion >= umbral y todavia no fue marcado/atendido."""
    fecha_referencia = EstadoSimulacion.actual().fecha_actual
    criticos = []
    estados_ya_atendidos = {
        EstadoDispositivo.REQUIERE_SERVICE,
        EstadoDispositivo.EN_MANTENIMIENTO,
        EstadoDispositivo.WAITING_HUMAN_APPROVAL,
        EstadoDispositivo.FUERA_DE_SERVICIO,
    }
    for dispositivo in Dispositivo.objects.exclude(estado_actual__in=estados_ya_atendidos):
        if calcular_degradacion(dispositivo, fecha_referencia) >= UMBRAL_DEGRADACION:
            criticos.append(dispositivo)
    return criticos


def evaluar(dispositivo: Dispositivo) -> dict:
    contexto = {
        "dispositivo": dispositivo.nombre,
        "prioridad": dispositivo.prioridad,
        "fecha_ultimo_service": dispositivo.fecha_ultimo_service,
        "degradacion_estimada_pct": calcular_degradacion(dispositivo),
    }
    return pedir_decision_json(SYSTEM_PROMPT, contexto)
