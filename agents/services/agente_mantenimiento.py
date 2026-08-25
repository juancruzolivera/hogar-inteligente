from datetime import date

from core.models import Dispositivo, EstadoDispositivo, EstadoSimulacion

from .llm import pedir_decision_json

UMBRAL_DEGRADACION = 90  # % de vida util consumida para considerar el service critico

SYSTEM_PROMPT = """Sos el Agente de Mantenimiento de un sistema de hogar inteligente (SofIA).
Se te informa un electrodomestico cuya degradacion estimada supero el umbral critico.
Redacta una justificacion tecnica breve (1-2 frases, en espanol) para agendar el service,
mencionando la prioridad del dispositivo, hace cuanto no recibe mantenimiento, y el saldo
disponible en el presupuesto de Mantenimiento.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"justificacion_tecnica": "<texto>"}
"""


def calcular_degradacion(dispositivo: Dispositivo, fecha_referencia: date | None = None) -> float:
    """Degradacion = dias desde el ultimo service sobre la vida util estimada (en dias).

    Un dispositivo marcado como `gustos` devuelve 0.0 siempre: esta fuera del ciclo
    de mantenimiento del hogar y no debe disparar un service nunca.

    fecha_referencia es el 'hoy' de la simulacion (EstadoSimulacion.fecha_actual), no la
    fecha real del servidor: el Pulso avanza 1 dia simulado por llamada, y necesitamos que
    la degradacion progrese con esos dias simulados para que la demo sea coherente.
    """
    if dispositivo.gustos:
        # Un capricho no se degrada: nunca alcanza el umbral critico.
        return 0.0
    if dispositivo.vida_util_estimada is None:
        # Sin el dato no se asume critico: agendar un service cuesta plata, asi que
        # ante la duda no se gatilla. (La base solo permite NULL si gustos=True, o
        # sea que llegar aca ya seria una fila inconsistente.)
        return 0.0
    if fecha_referencia is None:
        fecha_referencia = EstadoSimulacion.actual().fecha_actual
    if not dispositivo.fecha_ultimo_service or dispositivo.vida_util_estimada <= 0:
        return 100.0
    dias_transcurridos = (fecha_referencia - dispositivo.fecha_ultimo_service).days
    return round(min(100.0, max(0.0, dias_transcurridos / dispositivo.vida_util_estimada * 100)), 1)


def detectar_dispositivos_criticos():
    """CU-03 trigger: degradacion >= umbral y todavia no fue marcado/atendido.

    Los dispositivos marcados como `gustos` quedan excluidos: no se les agenda
    service aunque lleven anos sin mantenimiento.
    """
    fecha_referencia = EstadoSimulacion.actual().fecha_actual
    criticos = []
    estados_ya_atendidos = {
        EstadoDispositivo.REQUIERE_SERVICE,
        EstadoDispositivo.EN_MANTENIMIENTO,
        EstadoDispositivo.WAITING_HUMAN_APPROVAL,
        EstadoDispositivo.FUERA_DE_SERVICIO,
    }
    consultados = Dispositivo.objects.filter(gustos=False).exclude(
        estado_actual__in=estados_ya_atendidos
    )
    for dispositivo in consultados:
        if calcular_degradacion(dispositivo, fecha_referencia) >= UMBRAL_DEGRADACION:
            criticos.append(dispositivo)
    return criticos


def evaluar(dispositivo: Dispositivo, saldo_disponible=None) -> dict:
    contexto = {
        "dispositivo": dispositivo.nombre,
        "prioridad": dispositivo.prioridad,
        "fecha_ultimo_service": dispositivo.fecha_ultimo_service,
        "degradacion_estimada_pct": calcular_degradacion(dispositivo),
        "saldo_disponible_categoria": saldo_disponible,
    }
    return pedir_decision_json(SYSTEM_PROMPT, contexto)
