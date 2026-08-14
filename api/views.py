from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from agents.services.orquestador import ejecutar_ciclo, procesar_comando_manual

from .auth import secret_valido


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pulso(request):
    """Recibe el POST del nodo 'El Pulso' (Cron) de n8n y avanza un dia simulado:
    corre los triggers deterministicos de cada agente y, si algo dispara, pide
    razonamiento al LLM y deja todo en el Decision Log.
    """
    if not secret_valido(request):
        return Response({"detail": "No autorizado."}, status=401)

    logs = ejecutar_ciclo()
    resumen = [
        {
            "id_agente": log.id_agente,
            "accion_tomada": log.accion_tomada,
            "justificacion_tecnica": log.justificacion_tecnica,
        }
        for log in logs
    ]
    return Response({"decisiones": resumen, "total": len(resumen)}, status=200)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def comando(request):
    """Recibe el POST del workflow 'Comunicaciones' de n8n cuando un residente le
    escribe al grupo de WhatsApp. Espera {"telefono": "...", "mensaje": "..."}.
    """
    if not secret_valido(request):
        return Response({"detail": "No autorizado."}, status=401)

    telefono = request.data.get("telefono")
    mensaje = request.data.get("mensaje")
    if not telefono or not mensaje:
        return Response({"detail": "Faltan 'telefono' y/o 'mensaje'."}, status=400)

    log = procesar_comando_manual(telefono, mensaje)
    if log is None:
        return Response({"procesado": False}, status=200)

    return Response(
        {
            "procesado": True,
            "accion_tomada": log.accion_tomada,
            "justificacion_tecnica": log.justificacion_tecnica,
        },
        status=200,
    )
