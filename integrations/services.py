import logging
import os

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 5.0

"""Las 'manos' y la 'voz' del sistema: llamadas salientes de Django hacia los
webhooks de n8n. No contienen logica de negocio (eso vive en agents/services),
solo empaquetan y mandan. Si el webhook no esta configurado o falla, se loguea
y se sigue: una notificacion caida no puede tumbar la decision ya guardada en
el Decision Log.
"""


def _post(env_var: str, payload: dict, nombre: str) -> bool:
    url = os.getenv(env_var)
    if not url:
        logger.warning("[%s] N8N %s no configurado, se omite la notificacion.", nombre, env_var)
        return False
    try:
        respuesta = httpx.post(url, json=payload, timeout=TIMEOUT)
        respuesta.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.error("[%s] fallo el webhook a n8n (%s): %s", nombre, url, exc)
        return False


def enviar_whatsapp(mensaje: str) -> bool:
    """Workflow 'Comunicaciones': texto plano hacia el grupo familiar."""
    return _post("N8N_WHATSAPP_WEBHOOK_URL", {"mensaje": mensaje}, "whatsapp")


def agregar_a_lista_compras(item: str, accion: str = "agregar", cantidad: str | None = None) -> bool:
    """Workflow 'Lista de Compras': JSON hacia Google Keep."""
    payload = {"item": item, "accion": accion}
    if cantidad:
        payload["cantidad"] = cantidad
    return _post("N8N_KEEP_WEBHOOK_URL", payload, "keep")


def agendar_evento(dispositivo: str, fecha: str, tarea: str) -> bool:
    """Workflow 'Agenda': programa el evento tecnico en Google Calendar."""
    payload = {"dispositivo": dispositivo, "fecha": fecha, "tarea": tarea}
    return _post("N8N_CALENDAR_WEBHOOK_URL", payload, "calendar")
