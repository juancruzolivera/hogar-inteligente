from datetime import date

from core.models import Dispositivo, EstadoDispositivo, EstadoSimulacion

from .llm import pedir_decision_json

# Los dos tipos de accion que puede pedir un dispositivo. Van tal cual al
# payload que arma el orquestador (ver orquestador._procesar_mantenimiento).
ACCION_REEMPLAZO = "REEMPLAZO"
ACCION_SERVICE_RUTINARIO = "SERVICE_RUTINARIO"

SYSTEM_PROMPT = """Sos el Agente de Mantenimiento de un sistema de hogar inteligente (SofIA).
Se te informa un electrodomestico que necesita una accion de mantenimiento, ya decidida por
el sistema (no la elegis vos): "REEMPLAZO" si cumplio toda su vida util estimada y hay que
comprar una unidad nueva, o "SERVICE_RUTINARIO" si le toca una revision periodica y sigue
funcionando igual. Redacta una justificacion tecnica breve (1-2 frases, en espanol) para el
mensaje que le llega al residente, mencionando la prioridad del dispositivo, el motivo (vida
util cumplida vs. revision de rutina) y el monto que se va a gastar.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"justificacion_tecnica": "<texto>"}
"""


def calcular_degradacion(dispositivo: Dispositivo, fecha_referencia: date | None = None) -> float:
    """Degradacion = dias desde el ultimo reemplazo sobre la vida util estimada (en dias).

    Un dispositivo marcado como `gustos` devuelve 0.0 siempre: esta fuera del ciclo
    de mantenimiento del hogar y no debe disparar nada nunca.

    fecha_referencia es el 'hoy' de la simulacion (EstadoSimulacion.fecha_actual), no la
    fecha real del servidor: el Pulso avanza 1 dia simulado por llamada, y necesitamos que
    la degradacion progrese con esos dias simulados para que la demo sea coherente.
    """
    if dispositivo.gustos:
        # Un capricho no se degrada: nunca alcanza el umbral critico.
        return 0.0
    if dispositivo.vida_util_estimada is None:
        # Sin el dato no se asume critico: un reemplazo cuesta plata, asi que
        # ante la duda no se gatilla. (La base solo permite NULL si gustos=True, o
        # sea que llegar aca ya seria una fila inconsistente.)
        return 0.0
    if fecha_referencia is None:
        fecha_referencia = EstadoSimulacion.actual().fecha_actual
    if not dispositivo.fecha_instalacion or dispositivo.vida_util_estimada <= 0:
        return 100.0
    dias_transcurridos = (fecha_referencia - dispositivo.fecha_instalacion).days
    return round(min(100.0, max(0.0, dias_transcurridos / dispositivo.vida_util_estimada * 100)), 1)


def determinar_accion(dispositivo: Dispositivo, fecha_referencia: date | None = None) -> str | None:
    """Que le toca a este dispositivo hoy, si algo. Dos relojes independientes:

    - REEMPLAZO: se cumplio toda la vida util (dias desde fecha_instalacion >=
      vida_util_estimada). Gana siempre que se cumple, aunque tambien le toque
      service rutinario ese mismo dia -- el reemplazo resetea los dos relojes.
    - SERVICE_RUTINARIO: no le toca reemplazo todavia, pero pasaron
      dias_entre_service desde la ultima revision (fecha_ultima_revision, o
      fecha_instalacion si nunca tuvo una revision registrada).

    None si no le toca nada, o si el dispositivo esta fuera del ciclo de
    mantenimiento (gustos=True o sin vida_util_estimada cargada).
    """
    if dispositivo.gustos or dispositivo.vida_util_estimada is None:
        return None
    if fecha_referencia is None:
        fecha_referencia = EstadoSimulacion.actual().fecha_actual

    if not dispositivo.fecha_instalacion:
        return ACCION_REEMPLAZO
    edad_total_dias = (fecha_referencia - dispositivo.fecha_instalacion).days
    if edad_total_dias >= dispositivo.vida_util_estimada:
        return ACCION_REEMPLAZO

    if dispositivo.dias_entre_service:
        fecha_ultima_revision = dispositivo.fecha_ultima_revision or dispositivo.fecha_instalacion
        dias_desde_revision = (fecha_referencia - fecha_ultima_revision).days
        if dias_desde_revision >= dispositivo.dias_entre_service:
            return ACCION_SERVICE_RUTINARIO

    return None


def detectar_dispositivos_criticos() -> list[tuple[Dispositivo, str]]:
    """CU-03 trigger: dispositivos a los que hoy les toca reemplazo o service
    rutinario. Devuelve pares (dispositivo, accion).

    Los dispositivos marcados como `gustos` quedan excluidos: no se les agenda
    nada aunque lleven anos sin mantenimiento. `FUERA_DE_SERVICIO` tambien
    queda afuera -- es la unica forma de sacar un dispositivo del ciclo
    automatico sin que sea un gusto (ej. se vendio, se dio de baja a mano).
    """
    fecha_referencia = EstadoSimulacion.actual().fecha_actual
    resultado = []
    consultados = Dispositivo.objects.filter(gustos=False).exclude(
        estado_actual=EstadoDispositivo.FUERA_DE_SERVICIO
    )
    for dispositivo in consultados:
        accion = determinar_accion(dispositivo, fecha_referencia)
        if accion:
            resultado.append((dispositivo, accion))
    return resultado


def evaluar(dispositivo: Dispositivo, accion: str, monto, saldo_disponible=None) -> dict:
    contexto = {
        "dispositivo": dispositivo.nombre,
        "prioridad": dispositivo.prioridad,
        "accion": accion,
        "fecha_instalacion": dispositivo.fecha_instalacion,
        "fecha_ultima_revision": dispositivo.fecha_ultima_revision,
        "degradacion_estimada_pct": calcular_degradacion(dispositivo),
        "monto_a_gastar": str(monto),
        "saldo_disponible_categoria": saldo_disponible,
    }
    return pedir_decision_json(SYSTEM_PROMPT, contexto)
