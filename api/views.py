from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from agents.services.orquestador import (
    ejecutar_ciclo,
    procesar_cierre_mes,
    procesar_comando_manual,
    procesar_consulta_compra,
)

from .auth import secret_valido


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def pulso(request):
    """Recibe el POST del nodo 'El Pulso' (Cron) de n8n y avanza un dia simulado:
    corre los triggers deterministicos de cada agente y, si algo dispara, pide
    razonamiento al LLM y deja todo en el Decision Log.

    Acepta opcionalmente un body con el consumo simulado de residentes en casa:
    {"residentes_en_casa": [{"telefono": "...", "consumo_despensa": [...], "consumo_servicios": {...}}]}
    Sin body (o con body vacio), se comporta igual que antes (baseline random).
    """
    if not secret_valido(request):
        return Response({"detail": "No autorizado."}, status=401)

    if request.data and not isinstance(request.data, dict):
        return Response(
            {"detail": "El body tiene que ser un objeto: {\"residentes_en_casa\": [...]}."},
            status=400,
        )

    logs = ejecutar_ciclo(request.data or None)
    resumen = [
        {
            "id_agente": log.id_agente,
            "accion_tomada": log.accion_tomada,
            "justificacion_tecnica": log.justificacion_tecnica,
            "detalles": log.detalles_payload,
        }
        for log in logs
    ]
    return Response({"decisiones": resumen, "total": len(resumen)}, status=200)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def ingresos(request):
    """Recibe el POST del segundo Schedule Trigger de n8n (cada 30 dias simulados =
    1 mes) y cierra el mes: suma ingresos y renueva el presupuesto.

    Acepta opcionalmente un body con los ingresos de cada residente ese mes:
    {"ingresos": [{"telefono": "...", "monto": N}]}
    Un residente que no aparece en el body cae al fallback `ingreso_mensual` cargado
    en su fila (0 por defecto: sin body, no entra plata).
    """
    if not secret_valido(request):
        return Response({"detail": "No autorizado."}, status=401)

    if request.data and not isinstance(request.data, dict):
        return Response(
            {"detail": "El body tiene que ser un objeto: {\"ingresos\": [...]}."},
            status=400,
        )

    log = procesar_cierre_mes(request.data or None)
    return Response(
        {
            "accion_tomada": log.accion_tomada,
            "justificacion_tecnica": log.justificacion_tecnica,
            "detalles": log.detalles_payload,
        },
        status=200,
    )


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


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def consulta(request):
    """Recibe el POST del workflow de Telegram cuando un residente consulta si
    conviene una compra. Espera {"telegram_id": <numero>, "mensaje": "..."}.

    A diferencia de los otros endpoints, la respuesta al residente viaja en el body
    (campo `respuesta`): el mismo workflow que trajo la consulta la manda de vuelta
    al chat, asi la lee en el hilo donde pregunto.

    El mensaje tiene que incluir el precio. Si no lo trae, se responde
    CONSULTA_INCOMPLETA pidiendo que lo aclare, sin evaluar ni mover plata.
    """
    if not secret_valido(request):
        return Response({"detail": "No autorizado."}, status=401)

    telegram_id = request.data.get("telegram_id")
    mensaje = request.data.get("mensaje")
    if not telegram_id or not mensaje:
        return Response({"detail": "Faltan 'telegram_id' y/o 'mensaje'."}, status=400)

    return Response(procesar_consulta_compra(telegram_id, mensaje), status=200)
