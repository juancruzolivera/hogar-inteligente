from datetime import timedelta

from django.utils import timezone

from core.models import ConsumoLog, Dispositivo, EstadoDispositivo, Residente
from core.services.simulacion import avanzar_dia
from integrations import services as n8n

from . import agente_consumo, agente_despensa, agente_mantenimiento, agente_presupuesto
from ..models import AgenteEnum, DecisionLog

CATEGORIA_MANTENIMIENTO = "Mantenimiento"


def _log(agente, accion, justificacion, payload=None, **fks) -> DecisionLog:
    return DecisionLog.objects.create(
        timestamp=timezone.now(),
        id_agente=agente,
        accion_tomada=accion,
        justificacion_tecnica=justificacion,
        detalles_payload=payload,
        **fks,
    )


def _ya_registrado_en_dia(dia_numero: int, accion: str, **fks) -> bool:
    """Evita duplicar la misma decision si el Pulso se dispara mas de una vez para
    el mismo dia simulado (ej. reintento de n8n). El dia simulado se guarda dentro
    de detalles_payload (no hay columna propia en decision_log para esto).
    """
    return DecisionLog.objects.filter(
        detalles_payload__dia_simulado=dia_numero, accion_tomada=accion, **fks
    ).exists()


def _procesar_despensa(dia_numero: int) -> list[DecisionLog]:
    logs = []
    for item in agente_despensa.detectar_items_criticos():
        if _ya_registrado_en_dia(dia_numero, "AGREGAR_A_LISTA_COMPRAS", item_afectado=item) or \
           _ya_registrado_en_dia(dia_numero, "SOLICITUD_RECHAZADA_SIN_FONDOS", item_afectado=item):
            continue
        decision = agente_despensa.evaluar(item)
        categoria = item.presupuesto.categoria if item.presupuesto else None
        aprobado, presupuesto = agente_presupuesto.aprobar_gasto(categoria, item.precio_estimado or 0)

        if aprobado:
            cantidad = decision.get("cantidad_sugerida")
            logs.append(_log(
                AgenteEnum.AGENTE_DESPENSA,
                "AGREGAR_A_LISTA_COMPRAS",
                decision["justificacion_tecnica"],
                {"item": item.nombre, "cantidad_sugerida": cantidad, "dia_simulado": dia_numero},
                item_afectado=item,
                presupuesto_afectado=presupuesto,
            ))
            n8n.agregar_a_lista_compras(item.nombre, "agregar", cantidad)
            n8n.enviar_whatsapp(f"🛒 Se agrego '{item.nombre}' ({cantidad}) a la lista de compras.")
        else:
            logs.append(_log(
                AgenteEnum.AGENTE_DESPENSA,
                "SOLICITUD_RECHAZADA_SIN_FONDOS",
                f"Presupuesto de '{categoria}' sin saldo disponible para reponer {item.nombre}.",
                {"item": item.nombre, "dia_simulado": dia_numero},
                item_afectado=item,
                presupuesto_afectado=presupuesto,
            ))
            n8n.enviar_whatsapp(f"⚠️ No se pudo reponer '{item.nombre}': presupuesto de '{categoria}' sin saldo.")
    return logs


def _procesar_consumo(dia_numero: int) -> list[DecisionLog]:
    logs = []
    for ultimo, media_movil in agente_consumo.detectar_anomalias():
        decision = agente_consumo.evaluar(ultimo, media_movil)
        logs.append(_log(
            AgenteEnum.AGENTE_CONSUMO,
            "ALERTA_CONSUMO_ANOMALO",
            decision["justificacion_tecnica"],
            {
                "clasificacion": decision.get("clasificacion"),
                "valor_medicion": str(ultimo.valor_medicion),
                "dia_simulado": dia_numero,
            },
            consumo_asociado=ultimo,
        ))
        n8n.enviar_whatsapp(
            f"🚨 Consumo anomalo de {ultimo.tipo_servicio}: {ultimo.valor_medicion} "
            f"({decision.get('clasificacion')}). Responde 'Ignorar, es intencional' si no es un error."
        )
    return logs


def _procesar_mantenimiento(dia_numero: int) -> list[DecisionLog]:
    logs = []
    for dispositivo in agente_mantenimiento.detectar_dispositivos_criticos():
        decision = agente_mantenimiento.evaluar(dispositivo)
        aprobado, presupuesto = agente_presupuesto.tiene_saldo_disponible(CATEGORIA_MANTENIMIENTO)

        if aprobado:
            dispositivo.estado_actual = EstadoDispositivo.REQUIERE_SERVICE
            dispositivo.save(update_fields=["estado_actual"])
            logs.append(_log(
                AgenteEnum.AGENTE_MANTENIMIENTO,
                "AGENDAR_SERVICE",
                decision["justificacion_tecnica"],
                {"dispositivo": dispositivo.nombre, "dia_simulado": dia_numero},
                dispositivo_afectado=dispositivo,
                presupuesto_afectado=presupuesto,
            ))
            fecha = (timezone.localdate() + timedelta(days=2)).isoformat()
            n8n.agendar_evento(dispositivo.nombre, fecha, "Service de mantenimiento")
            n8n.enviar_whatsapp(f"🔧 Se agendo un service para '{dispositivo.nombre}'.")
        else:
            # CU-03: deadlock estructural. Queda esperando aprobacion manual del residente.
            dispositivo.estado_actual = EstadoDispositivo.WAITING_HUMAN_APPROVAL
            dispositivo.save(update_fields=["estado_actual"])
            mensaje = (
                f"Deadlock: {dispositivo.nombre} (prioridad {dispositivo.prioridad}) requiere "
                f"service critico pero el presupuesto de '{CATEGORIA_MANTENIMIENTO}' tiene saldo 0."
            )
            logs.append(_log(
                AgenteEnum.ORQUESTADOR,
                "WAITING_HUMAN_APPROVAL",
                mensaje,
                {"dispositivo": dispositivo.nombre, "dia_simulado": dia_numero},
                dispositivo_afectado=dispositivo,
                presupuesto_afectado=presupuesto,
            ))
            n8n.enviar_whatsapp(
                f"⛔ {mensaje} Responde 'Forzar arreglo' si queres autorizar el gasto igual."
            )
    return logs


def ejecutar_ciclo(payload: dict | None = None) -> list[DecisionLog]:
    """Un 'pulso': avanza 1 dia simulado (baja stock, genera consumo nuevo), corre los
    triggers deterministicos de los 3 sub-agentes y, para lo que dispare, pide
    razonamiento al LLM dejando todo asentado en el Decision Log.

    `payload` es el body opcional de /api/pulso/ con el consumo simulado de los
    residentes en casa ese dia (ver core.services.simulacion.avanzar_dia).
    """
    estado = avanzar_dia(payload)
    dia = estado.dia_numero
    return (
        _procesar_despensa(dia)
        + _procesar_consumo(dia)
        + _procesar_mantenimiento(dia)
    )


def _resolver_forzar_arreglo(residente: Residente, mensaje: str) -> DecisionLog | None:
    """CU-03 resolucion: el residente fuerza el service ignorando la restriccion
    del Agente de Presupuesto. Se registra quien autorizo la excepcion.
    """
    dispositivo = Dispositivo.objects.filter(
        estado_actual=EstadoDispositivo.WAITING_HUMAN_APPROVAL
    ).order_by("prioridad").first()

    if not dispositivo:
        n8n.enviar_whatsapp(f"{residente.nombre}: no hay ningun dispositivo esperando aprobacion.")
        return None

    dispositivo.estado_actual = EstadoDispositivo.REQUIERE_SERVICE
    dispositivo.save(update_fields=["estado_actual"])

    log = _log(
        AgenteEnum.ORQUESTADOR,
        "MANUAL_OVERRIDE_EJECUTAR_SERVICE",
        (
            f"Residente identificado por numero de telefono ejecuto 'Forzar arreglo'. "
            f"Se ignora la restriccion del Agente de Presupuesto y se agenda el service "
            f"critico de {dispositivo.nombre} via webhook a Calendar."
        ),
        {"dispositivo": dispositivo.nombre, "canal": "whatsapp", "comando": mensaje},
        residente_autorizador=residente,
        dispositivo_afectado=dispositivo,
    )

    fecha = (timezone.localdate() + timedelta(days=1)).isoformat()
    n8n.agendar_evento(dispositivo.nombre, fecha, "Service critico (override manual)")
    n8n.enviar_whatsapp(f"✅ {residente.nombre} forzo el arreglo de '{dispositivo.nombre}'. Service agendado.")
    return log


def _resolver_ignorar_anomalia(residente: Residente, mensaje: str) -> DecisionLog | None:
    """CU-02 flujo alternativo: falso positivo. Etiqueta el ultimo log de consumo
    anomalo como EXCEPCION_CONOCIDA (silencia ese servicio por 24hs, ver agente_consumo).
    """
    ultima_alerta = (
        DecisionLog.objects.filter(id_agente=AgenteEnum.AGENTE_CONSUMO, accion_tomada="ALERTA_CONSUMO_ANOMALO")
        .exclude(consumo_asociado__etiqueta=agente_consumo.ETIQUETA_EXCEPCION)
        .order_by("-timestamp")
        .first()
    )
    if not ultima_alerta or not ultima_alerta.consumo_asociado:
        n8n.enviar_whatsapp(f"{residente.nombre}: no hay ninguna alerta de consumo activa para ignorar.")
        return None

    consumo = ultima_alerta.consumo_asociado
    consumo.etiqueta = agente_consumo.ETIQUETA_EXCEPCION
    consumo.save(update_fields=["etiqueta"])

    log = _log(
        AgenteEnum.ORQUESTADOR,
        "EXCEPCION_CONOCIDA",
        (
            f"Residente identificado por numero de telefono marco la anomalia de "
            f"{consumo.tipo_servicio} como intencional. Se silencian alertas de ese "
            f"servicio por {agente_consumo.HORAS_SILENCIO}hs."
        ),
        {"canal": "whatsapp", "comando": mensaje},
        residente_autorizador=residente,
        consumo_asociado=consumo,
    )
    n8n.enviar_whatsapp(f"🔕 Ok, silencio las alertas de {consumo.tipo_servicio} por 24hs.")
    return log


def procesar_comando_manual(telefono: str, mensaje: str) -> DecisionLog | None:
    """Punto de entrada del workflow 'Comunicaciones' de n8n: un residente le
    escribio algo por WhatsApp. Lo identifica por numero de telefono e interpreta
    los comandos conocidos (CU-03 y CU-02).
    """
    try:
        residente = Residente.objects.get(telefono=telefono)
    except Residente.DoesNotExist:
        n8n.enviar_whatsapp("No reconozco este numero, no puedo autorizar acciones.")
        return None

    texto = mensaje.strip().lower()
    if "forzar arreglo" in texto:
        return _resolver_forzar_arreglo(residente, mensaje)
    if "ignorar" in texto:
        return _resolver_ignorar_anomalia(residente, mensaje)

    n8n.enviar_whatsapp(f"{residente.nombre}: no reconozco ese comando.")
    return None
