"""Envio por Telegram de las decisiones que ya quedaron guardadas en la base.

El texto NO se arma con los datos "sueltos" del momento en que el agente decide:
se arma leyendo la fila de `decision_log` (ver agents.models.DecisionLog), que es
la unica fuente de verdad de lo que se decidio. Dos consecuencias buscadas:

1. El mensaje del chat y el registro de la base nunca pueden contar cosas
   distintas: si algo esta en el mensaje, esta en la base.
2. Una decision vieja se puede reenviar tal cual, sin volver a llamar al LLM ni
   volver a mover plata (ver el management command `enviar_decisiones_telegram`).

Solo se notifican las decisiones del Agente de Despensa (CU-01) y del Agente de
Mantenimiento (CU-03). El resto de los agentes sigue avisando por WhatsApp.
"""

import logging

from core.models import NivelPermiso, Residente
from integrations import services as n8n

from ..models import AgenteEnum, DecisionLog

logger = logging.getLogger(__name__)

AGENTES_NOTIFICABLES = (
    AgenteEnum.AGENTE_DESPENSA,
    AgenteEnum.AGENTE_MANTENIMIENTO,
)

TITULOS_AGENTE = {
    AgenteEnum.AGENTE_DESPENSA: "Agente de Despensa",
    AgenteEnum.AGENTE_MANTENIMIENTO: "Agente de Mantenimiento",
}

# Como se lee cada `accion_tomada` en el chat. El valor crudo (AGREGAR_A_LISTA_COMPRAS)
# es el que va a la base; esto es solo la version legible para una persona.
ACCIONES = {
    "AGREGAR_A_LISTA_COMPRAS": ("🛒", "Reposicion de stock"),
    "AGENDAR_SERVICE": ("🔧", "Service agendado"),
    "SOLICITUD_RECHAZADA_SIN_FONDOS": ("⚠️", "Solicitud rechazada por falta de fondos"),
}
ACCION_DEFAULT = ("🤖", None)


def _encabezado(log: DecisionLog) -> str:
    icono, legible = ACCIONES.get(log.accion_tomada, ACCION_DEFAULT)
    titulo = TITULOS_AGENTE.get(log.id_agente, log.id_agente)
    return f"{icono} {titulo} — {legible or log.accion_tomada.replace('_', ' ').capitalize()}"


def _asunto(log: DecisionLog) -> str | None:
    """Que cosa del hogar toco la decision, tomado de las FK de la propia fila."""
    payload = log.detalles_payload or {}
    if log.item_afectado_id:
        cantidad = payload.get("cantidad_sugerida")
        detalle = f" ({cantidad})" if cantidad else ""
        return f"Producto: {log.item_afectado.nombre}{detalle}"
    if log.dispositivo_afectado_id:
        return f"Dispositivo: {log.dispositivo_afectado.nombre}"
    return None


def formatear_decision(log: DecisionLog) -> str:
    """Convierte una fila del Decision Log en el texto que se manda al chat."""
    lineas = [_encabezado(log)]

    asunto = _asunto(log)
    if asunto:
        lineas.append(asunto)

    if log.presupuesto_afectado_id:
        lineas.append(f"Categoria: {log.presupuesto_afectado.categoria}")

    lineas.append("")
    lineas.append(log.justificacion_tecnica)

    pie = [f"Decision #{log.id_decision}"]
    dia = (log.detalles_payload or {}).get("dia_simulado")
    if dia:
        pie.append(f"dia simulado {dia}")
    lineas.append("")
    lineas.append(" · ".join(pie))

    return "\n".join(lineas)


def destinatarios() -> list[int]:
    """`telegram_id` de quienes reciben las decisiones del hogar.

    Los INVITADO quedan afuera a proposito: una visita frecuente no tiene por que
    enterarse de en que se gasta el presupuesto de la casa (si hace falta que reciba,
    alcanza con subirle el `nivel_permiso` a RESIDENTE).
    """
    return list(
        Residente.objects.filter(
            nivel_permiso__in=[NivelPermiso.ADMIN, NivelPermiso.RESIDENTE]
        )
        .exclude(telegram_id=None)
        .values_list("telegram_id", flat=True)
    )


def notificar_decision(log: DecisionLog) -> int:
    """Manda una decision ya persistida al Telegram de cada residente.

    Devuelve la cantidad de envios que n8n acepto. No levanta excepciones: si el
    webhook no esta configurado o falla, `integrations.services` loguea y devuelve
    False -- una notificacion caida no puede tumbar el pulso ni invalidar la
    decision que ya quedo asentada en la base.
    """
    if log.id_agente not in AGENTES_NOTIFICABLES:
        return 0

    mensaje = formatear_decision(log)
    chats = destinatarios()
    if not chats:
        logger.warning(
            "[telegram] ningun residente tiene telegram_id cargado; se manda al chat "
            "por defecto del workflow de n8n."
        )
        return 1 if n8n.enviar_telegram(mensaje) else 0

    return sum(1 for chat_id in chats if n8n.enviar_telegram(mensaje, chat_id))


def notificar_decisiones(logs) -> int:
    """Igual que `notificar_decision` pero para una tanda (filtra sola lo que no aplica)."""
    return sum(notificar_decision(log) for log in logs)
