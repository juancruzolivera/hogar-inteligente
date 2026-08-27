from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core.models import (
    LIMITE_DEUDA,
    ConsumoLog,
    Dispositivo,
    EstadoDispositivo,
    EstadoSimulacion,
    IngresosHogar,
    ItemDespensa,
    Presupuesto,
    Residente,
    TipoServicio,
)
from core.services.ingresos import cerrar_mes
from core.services.simulacion import avanzar_dia
from integrations import services as n8n

from . import agente_ahorro, agente_consumo, agente_despensa, agente_mantenimiento
from ..models import AgenteEnum, DecisionLog

CATEGORIA_MANTENIMIENTO = "Mantenimiento"
CATEGORIA_SERVICIOS = "Servicios"
CATEGORIA_OCIO = "Ocio"
# Prioridad con la que entra un dispositivo comprado. La base exige entre 1 y 5
# (CHECK) y 1 es la mas urgente: un capricho va al fondo de la cola, y un bien de
# uso nuevo al medio (no sabemos si es critico como la heladera).
PRIORIDAD_GUSTO = 5
PRIORIDAD_BIEN_DE_USO = 3
# Precio por unidad de cada servicio (pesos), para convertir el consumo fisico
# diario (litros/kWh/m3) en una factura real. Calibrados para que un mes de
# consumo baseline ronde el limite_mensual actual de la categoria Servicios.
PRECIO_SERVICIOS = {
    TipoServicio.AGUA: Decimal("100"),
    TipoServicio.LUZ: Decimal("250"),
    TipoServicio.GAS: Decimal("100"),
}


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
    """CU-01: el agente decide por si mismo cuanto reponer, ya viendo el saldo
    disponible de su categoria (ver agente_despensa.evaluar). El gasto se valida
    contra el saldo real del hogar (IngresosHogar): si la categoria es esencial y
    no alcanza, se compra igual y la diferencia se acumula como deuda; si NO es
    esencial y no alcanza, la compra se rechaza.
    """
    logs = []
    for item in agente_despensa.detectar_items_criticos():
        if _ya_registrado_en_dia(dia_numero, "AGREGAR_A_LISTA_COMPRAS", item_afectado=item) or \
           _ya_registrado_en_dia(dia_numero, "SOLICITUD_RECHAZADA_SIN_FONDOS", item_afectado=item):
            continue

        presupuesto = item.presupuesto
        saldo_disponible = presupuesto.saldo_disponible if presupuesto else None
        decision = agente_despensa.evaluar(item, saldo_disponible)
        cantidad = decision.get("cantidad_sugerida")
        cantidad_reponer = decision.get("cantidad_reponer")

        monto = item.precio_estimado
        es_esencial = presupuesto.es_esencial if presupuesto else True
        if monto and not IngresosHogar.actual().pagar(monto, es_esencial):
            motivo = (
                f"no es esencial (categoria '{presupuesto.categoria}') y no hay saldo ni ahorros"
                if not es_esencial
                else f"es esencial pero financiarlo superaria el limite de deuda del hogar "
                     f"(${LIMITE_DEUDA})"
            )
            logs.append(_log(
                AgenteEnum.AGENTE_DESPENSA,
                "SOLICITUD_RECHAZADA_SIN_FONDOS",
                f"'{item.nombre}' {motivo}.",
                {"item": item.nombre, "dia_simulado": dia_numero},
                item_afectado=item,
                presupuesto_afectado=presupuesto,
            ))
            n8n.enviar_whatsapp(f"⚠️ No se pudo reponer '{item.nombre}': {motivo}.")
            continue

        if presupuesto and monto:
            presupuesto.monto_gastado += monto
            presupuesto.save(update_fields=["monto_gastado", "updated_at"])

        if cantidad_reponer:
            item.stock_actual += Decimal(str(cantidad_reponer))
            item.save(update_fields=["stock_actual", "updated_at"])

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
    return logs


def _procesar_factura_servicios(dia_numero: int, consumo_dia: dict) -> list[DecisionLog]:
    """Convierte el consumo fisico del dia (agua/luz/gas, ya generado por
    avanzar_dia) en una factura real, cobrada contra el presupuesto de Servicios.
    A diferencia de despensa/mantenimiento, este cargo no depende de que un LLM
    "decida" comprar algo: los servicios se pagan siempre que se consumen, todos
    los dias, de forma automatica y deterministica.
    """
    presupuesto = Presupuesto.objects.filter(categoria=CATEGORIA_SERVICIOS).first()
    es_esencial = presupuesto.es_esencial if presupuesto else True

    total = Decimal("0")
    detalle = {}
    for tipo, valor in consumo_dia.items():
        precio = PRECIO_SERVICIOS.get(tipo)
        if not precio:
            continue
        monto = (valor * precio).quantize(Decimal("0.01"))
        total += monto
        detalle[tipo] = {"consumo": str(valor), "monto": str(monto)}

    if total <= 0:
        return []

    if not IngresosHogar.actual().pagar(total, es_esencial):
        log = _log(
            AgenteEnum.AGENTE_CONSUMO,
            "FACTURA_SERVICIOS_SIN_FONDOS",
            f"La factura de servicios del dia (${total}) no se pudo cubrir: la categoria "
            f"'{CATEGORIA_SERVICIOS}' superaria el limite de deuda del hogar (${LIMITE_DEUDA}).",
            {"detalle": detalle, "dia_simulado": dia_numero},
            presupuesto_afectado=presupuesto,
        )
        n8n.enviar_whatsapp(f"⚠️ No se pudo cubrir la factura de servicios de hoy (${total}).")
        return [log]

    if presupuesto:
        presupuesto.monto_gastado += total
        presupuesto.save(update_fields=["monto_gastado", "updated_at"])

    log = _log(
        AgenteEnum.AGENTE_CONSUMO,
        "FACTURA_SERVICIOS",
        f"Consumo del dia facturado: ${total} (agua/luz/gas segun tarifa vigente).",
        {"detalle": detalle, "dia_simulado": dia_numero},
        presupuesto_afectado=presupuesto,
    )
    return [log]


def _procesar_ocio(payload: dict | None, dia_numero: int) -> list[DecisionLog]:
    """Gastos de ocio declarados en el body de /api/pulso/ (hoy, via el 'evento
    inesperado' que genera la IA del lado de n8n -- ver n8n/generador_pulso.js).
    Ocio no es esencial: si no hay saldo ni ahorros en el hogar, el gasto se
    rechaza directamente (no se acumula como deuda, a diferencia de las
    categorias esenciales).
    """
    logs = []
    presupuesto = Presupuesto.objects.filter(categoria=CATEGORIA_OCIO).first()
    for residente in (payload or {}).get("residentes_en_casa", []):
        gasto = residente.get("gasto_ocio")
        if not gasto or not gasto.get("monto"):
            continue

        monto = Decimal(str(gasto["monto"]))
        motivo = gasto.get("motivo") or "Gasto de ocio"
        telefono = residente.get("telefono")

        if not IngresosHogar.actual().pagar(monto, es_esencial=False):
            logs.append(_log(
                AgenteEnum.ORQUESTADOR,
                "GASTO_OCIO_RECHAZADO",
                f'Gasto de ocio ("{motivo}", ${monto}) rechazado: la categoria "{CATEGORIA_OCIO}" '
                f"no es esencial y no hay saldo ni ahorros disponibles en el hogar.",
                {"telefono": telefono, "motivo": motivo, "dia_simulado": dia_numero},
                presupuesto_afectado=presupuesto,
            ))
            n8n.enviar_whatsapp(f'⚠️ No se pudo cubrir un gasto de ocio ("{motivo}"): sin saldo ni ahorros.')
            continue

        if presupuesto:
            presupuesto.monto_gastado += monto
            presupuesto.save(update_fields=["monto_gastado", "updated_at"])

        logs.append(_log(
            AgenteEnum.ORQUESTADOR,
            "GASTO_OCIO",
            f'Gasto de ocio: "{motivo}" (${monto}).',
            {"telefono": telefono, "motivo": motivo, "monto": str(monto), "dia_simulado": dia_numero},
            presupuesto_afectado=presupuesto,
        ))
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
    """CU-03: por cada dispositivo al que le toque algo hoy (ver
    agente_mantenimiento.detectar_dispositivos_criticos), se cobra y se resuelve
    en el momento -- no queda ningun dispositivo esperando una accion futura.
    Igual que en despensa, el gasto se valida contra el saldo real del hogar:
    esencial y sin fondos -> se hace igual y se acumula como deuda; no esencial
    y sin fondos -> se rechaza (y se reintenta el proximo Pulso, el dispositivo
    sigue "vencido" hasta que se pueda pagar).
    """
    logs = []
    presupuesto = Presupuesto.objects.filter(categoria=CATEGORIA_MANTENIMIENTO).first()
    es_esencial = presupuesto.es_esencial if presupuesto else True
    fecha_hoy = EstadoSimulacion.actual().fecha_actual

    for dispositivo, accion in agente_mantenimiento.detectar_dispositivos_criticos():
        es_reemplazo = accion == agente_mantenimiento.ACCION_REEMPLAZO
        monto = dispositivo.costo_reemplazo if es_reemplazo else dispositivo.costo_service
        saldo_disponible = presupuesto.saldo_disponible if presupuesto else None
        decision = agente_mantenimiento.evaluar(dispositivo, accion, monto, saldo_disponible)

        if not IngresosHogar.actual().pagar(monto, es_esencial):
            motivo = (
                "la categoria 'Mantenimiento' no es esencial y no hay saldo ni ahorros"
                if not es_esencial
                else f"es esencial pero financiarlo superaria el limite de deuda del hogar (${LIMITE_DEUDA})"
            )
            logs.append(_log(
                AgenteEnum.AGENTE_MANTENIMIENTO,
                "SOLICITUD_RECHAZADA_SIN_FONDOS",
                f"{accion.capitalize()} de '{dispositivo.nombre}' rechazado: {motivo}.",
                {"dispositivo": dispositivo.nombre, "accion": accion, "monto": str(monto), "dia_simulado": dia_numero},
                dispositivo_afectado=dispositivo,
                presupuesto_afectado=presupuesto,
            ))
            n8n.enviar_whatsapp(f"⚠️ No se pudo cubrir el {accion.lower()} de '{dispositivo.nombre}': {motivo}.")
            continue

        if presupuesto:
            presupuesto.monto_gastado += monto
            presupuesto.save(update_fields=["monto_gastado", "updated_at"])

        if es_reemplazo:
            # Reemplazo: unidad nueva, arrancan los dos relojes de cero. Se
            # conserva vida_util_estimada/dias_entre_service/costos: son specs
            # del modelo de electrodomestico, no cambian con la unidad fisica.
            dispositivo.fecha_instalacion = fecha_hoy
            dispositivo.fecha_ultima_revision = fecha_hoy
            dispositivo.estado_actual = EstadoDispositivo.OPERATIVO
            dispositivo.save(update_fields=["fecha_instalacion", "fecha_ultima_revision", "estado_actual"])
            accion_log = "DISPOSITIVO_REEMPLAZADO"
            mensaje_whatsapp = f"🔧 Se reemplazo '{dispositivo.nombre}' (cumplio su vida util). Costo: ${monto}."
            fecha_evento = (timezone.localdate() + timedelta(days=2)).isoformat()
            n8n.agendar_evento(dispositivo.nombre, fecha_evento, "Reemplazo de electrodomestico")
        else:
            # Service rutinario: solo se resetea el reloj de revision, la vida
            # util total sigue contando desde el ultimo reemplazo.
            dispositivo.fecha_ultima_revision = fecha_hoy
            dispositivo.estado_actual = EstadoDispositivo.OPERATIVO
            dispositivo.save(update_fields=["fecha_ultima_revision", "estado_actual"])
            accion_log = "SERVICE_RUTINARIO_REALIZADO"
            mensaje_whatsapp = f"🔧 Se hizo el service de rutina de '{dispositivo.nombre}'. Costo: ${monto}."

        logs.append(_log(
            AgenteEnum.AGENTE_MANTENIMIENTO,
            accion_log,
            decision["justificacion_tecnica"],
            {"dispositivo": dispositivo.nombre, "accion": accion, "monto": str(monto), "dia_simulado": dia_numero},
            dispositivo_afectado=dispositivo,
            presupuesto_afectado=presupuesto,
        ))
        n8n.enviar_whatsapp(mensaje_whatsapp)
    return logs


def ejecutar_ciclo(payload: dict | None = None) -> list[DecisionLog]:
    """Un 'pulso': avanza 1 dia simulado (baja stock, genera consumo nuevo), corre los
    triggers deterministicos de los sub-agentes y, para lo que dispare, pide
    razonamiento al LLM dejando todo asentado en el Decision Log.

    `payload` es el body opcional de /api/pulso/ con el consumo simulado de los
    residentes en casa ese dia (ver core.services.simulacion.avanzar_dia).
    """
    estado, consumo_dia = avanzar_dia(payload)
    dia = estado.dia_numero
    return (
        _procesar_despensa(dia)
        + _procesar_consumo(dia)
        + _procesar_mantenimiento(dia)
        + _procesar_factura_servicios(dia, consumo_dia)
        + _procesar_ocio(payload, dia)
    )


def procesar_cierre_mes(payload: dict | None = None) -> DecisionLog:
    """Punto de entrada de /api/ingresos/: cierre de mes disparado por el segundo
    Schedule Trigger de n8n (cada 30 dias simulados). Suma ingresos y renueva el
    presupuesto (ver core.services.ingresos.cerrar_mes).
    """
    resumen = cerrar_mes(payload)
    justificacion = (
        f"Cierre de mes simulado #{resumen['mes_numero']}: ingresaron ${resumen['total_ingresos']} "
        f"entre {len(resumen['por_residente'])} residente(s). Se barrio "
        f"${resumen['ahorrado_del_mes_anterior']} de saldo sobrante a ahorros (acumulado: "
        f"${resumen['ahorros_hogar']}) y se cancelaron ${resumen['deuda_cancelada']} de deuda. "
        f"Se reinicia monto_gastado en {len(resumen['categorias_reseteadas'])} "
        f"categoria(s) de presupuesto."
    )
    log = _log(AgenteEnum.ORQUESTADOR, "CIERRE_DE_MES", justificacion, resumen)
    n8n.enviar_whatsapp(
        f"💰 Cierre de mes: ingresaron ${resumen['total_ingresos']}. "
        f"Ahorros del hogar: ${resumen['ahorros_hogar']}. Los presupuestos se renovaron."
    )
    return log


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


def _dar_de_alta_compra(decision: dict) -> tuple:
    """Suma la compra aprobada al inventario del hogar.

    Busca por nombre antes de crear, porque `nombre` NO es unico en ninguna de las
    dos tablas y una fila duplicada rompe cosas: `avanzar_dia()` matchea los items
    por nombre en minusculas, asi que dos "Leche Entera 1L" reciben cada una el
    consumo completo del dia (consumo doble) y las dos se reponen.

    - Item que ya existe -> reposicion: se le suma al stock.
    - Dispositivo que ya existe -> reemplazo: vuelve a arrancar su degradacion y
      conserva la `vida_util_estimada` que ya tenia (no hay que estimar nada).
    - No existe -> alta, con `gustos` y los parametros operativos que decidio el
      agente (ver agente_ahorro._sanear_parametros).

    Devuelve (item, dispositivo, accion) con accion en {alta, reposicion, reemplazo}.
    """
    nombre = (decision.get("producto") or "Compra sin nombre").strip()[:255]
    params = decision.get("parametros") or {}
    es_gusto = bool(params.get("es_gusto", decision.get("es_gusto")))
    cantidad = Decimal(str(params.get("cantidad") or 1))

    if decision.get("tipo") == "dispositivo":
        existente = Dispositivo.objects.filter(nombre__iexact=nombre).first()
        if existente:
            fecha_hoy = EstadoSimulacion.actual().fecha_actual
            # Reemplazo manual (compra por Telegram): arrancan los dos relojes
            # de mantenimiento de cero, igual que el reemplazo automatico del
            # Pulso (ver orquestador._procesar_mantenimiento). Se conservan
            # dias_entre_service/costo_service/costo_reemplazo: son specs del
            # modelo de electrodomestico, no cambian con la unidad fisica.
            existente.fecha_instalacion = fecha_hoy
            existente.fecha_ultima_revision = fecha_hoy
            existente.estado_actual = EstadoDispositivo.OPERATIVO
            existente.save(
                update_fields=["fecha_instalacion", "fecha_ultima_revision", "estado_actual", "updated_at"]
            )
            return None, existente, "reemplazo"

        dispositivo = Dispositivo.objects.create(
            nombre=nombre,
            prioridad=PRIORIDAD_GUSTO if es_gusto else PRIORIDAD_BIEN_DE_USO,
            fecha_instalacion=EstadoSimulacion.actual().fecha_actual,
            fecha_ultima_revision=EstadoSimulacion.actual().fecha_actual,
            vida_util_estimada=params.get("vida_util_dias"),
            # NULL solo si es_gusto (lo exige el CHECK de la base, ver
            # sql/agente_mantenimiento.sql): un bien de uso nuevo necesita
            # entrar al ciclo de mantenimiento con estos 3 campos calculados
            # en codigo (ver agente_ahorro._sanear_parametros).
            dias_entre_service=params.get("dias_entre_service"),
            costo_service=params.get("costo_service"),
            costo_reemplazo=params.get("costo_reemplazo"),
            estado_actual=EstadoDispositivo.OPERATIVO,
            gustos=es_gusto,
        )
        return None, dispositivo, "alta"

    existente = ItemDespensa.objects.filter(nombre__iexact=nombre).first()
    if existente:
        existente.stock_actual += cantidad
        existente.save(update_fields=["stock_actual", "updated_at"])
        return existente, None, "reposicion"

    item = ItemDespensa.objects.create(
        nombre=nombre,
        unidad_medida=params.get("unidad_medida") or "unidades",
        stock_actual=cantidad,
        stock_minimo=params.get("stock_minimo"),
        # NOT NULL en la base y avanzar_dia() se lo resta a TODOS los items cada
        # dia. Un gusto va en 0: queda registrado sin consumirse ni reponerse solo.
        consumo_promedio_diario=params.get("consumo_promedio_diario") or Decimal("0"),
        precio_estimado=decision["precio"],
        gustos=es_gusto,
    )
    return item, None, "alta"


def procesar_consulta_compra(telegram_id, mensaje: str) -> dict:
    """Punto de entrada de /api/consulta/: un residente pregunta por Telegram si
    conviene una compra.

    Devuelve un dict que la view serializa tal cual. `respuesta` es el texto que
    n8n manda de vuelta al chat, asi el residente lo lee en el mismo hilo donde
    pregunto (por eso este flujo no usa un webhook saliente).
    """
    try:
        residente = Residente.objects.get(telegram_id=telegram_id)
    except (Residente.DoesNotExist, ValueError, TypeError):
        return {
            "procesado": False,
            "resultado": "RESIDENTE_DESCONOCIDO",
            "respuesta": "No reconozco esta cuenta de Telegram, no puedo responder consultas.",
        }

    decision = agente_ahorro.evaluar(mensaje)
    resultado = decision["resultado"]
    respuesta = decision["justificacion_tecnica"]
    payload = {
        "consulta": mensaje,
        "producto": decision.get("producto"),
        "precio": str(decision["precio"]) if decision.get("precio") is not None else None,
        "tipo": decision.get("tipo"),
        "situacion": decision.get("situacion"),
        "es_gusto": decision.get("es_gusto"),
    }

    # Falta el precio: no se evalua nada y no se mueve plata, pero queda asentado
    # que el agente contesto.
    if resultado == agente_ahorro.CONSULTA_INCOMPLETA:
        _log(AgenteEnum.AGENTE_AHORRO, resultado, respuesta, payload,
             residente_autorizador=residente)
        return {"procesado": True, "resultado": resultado, "respuesta": respuesta, **payload}

    if resultado == agente_ahorro.COMPRA_RECHAZADA:
        _log(AgenteEnum.AGENTE_AHORRO, resultado, respuesta, payload,
             residente_autorizador=residente)
        return {"procesado": True, "resultado": resultado, "respuesta": respuesta, **payload}

    # Aprobada: recien aca se toca la plata. El contexto que uso el agente se
    # calculo unos segundos antes, asi que el saldo pudo cambiar en el medio (un
    # Pulso que corrio y cobro la factura del dia, por ejemplo). pagar() es la
    # unica fuente de verdad: si dice que no, la compra no se hace.
    precio = decision["precio"]
    if not IngresosHogar.actual().pagar(precio, es_esencial=False):
        respuesta = (
            f"Te habia dicho que si, pero cuando fui a pagar la plata ya no alcanzaba "
            f"(algun gasto entro en el medio). '{decision.get('producto')}' queda para mas adelante."
        )
        _log(AgenteEnum.AGENTE_AHORRO, agente_ahorro.COMPRA_RECHAZADA, respuesta,
             {**payload, "motivo": "sin fondos al momento de pagar"},
             residente_autorizador=residente)
        n8n.enviar_whatsapp(f"⚠️ No se pudo cerrar la compra de '{decision.get('producto')}': sin fondos.")
        return {"procesado": True, "resultado": agente_ahorro.COMPRA_RECHAZADA,
                "respuesta": respuesta, **payload}

    item, dispositivo, accion = _dar_de_alta_compra(decision)
    _log(AgenteEnum.AGENTE_AHORRO, resultado, respuesta,
         {**payload, "alta": accion, "es_gusto": bool((decision.get("parametros") or {}).get("es_gusto"))},
         residente_autorizador=residente, item_afectado=item, dispositivo_afectado=dispositivo)
    n8n.enviar_whatsapp(
        f"🛍️ {residente.nombre} compro '{decision.get('producto')}' (${precio}). {respuesta}"
    )
    return {"procesado": True, "resultado": resultado, "respuesta": respuesta, **payload}
