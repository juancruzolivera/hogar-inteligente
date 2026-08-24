"""Agente de Ahorro: responde si conviene hacer una compra puntual.

A diferencia de los demas agentes, no lo dispara el Pulso sino una consulta del
residente por Telegram (ver /api/consulta/).

El trabajo se reparte en tres pasos, y el reparto es lo importante de este modulo:

1. LLM #1 (extraccion): saca producto, precio y tipo del texto libre. Los modelos
   son muy buenos en esto -- entienden "900 lucas" y detectan cuando no hay precio.
2. Codigo (deterministico): compara el precio contra los umbrales y resuelve la
   situacion economica. Esto NO lo hace el LLM. Se probo pasarle los umbrales ya
   calculados y la regla explicita, con los montos como texto y como numero, y en
   los dos casos afirmo que $900.000 superaba un margen de $1.618.963. Es
   aritmetica: en codigo no falla nunca.
3. LLM #2 (criterio y redaccion): recibe la situacion YA resuelta y decide lo unico
   que requiere juicio -- si conviene esperar igual porque hay un electrodomestico
   caro por romperse, o si una oferta que menciono el residente inclina la balanza --
   y lo redacta natural.

Regla que atraviesa todo: el LLM nunca inventa un precio. Si el mensaje no lo trae,
la consulta se devuelve incompleta. Ese numero se le descuenta de verdad a la
billetera del hogar.
"""

import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from core.models import (
    ConsumoLog,
    Dispositivo,
    EstadoDispositivo,
    EstadoSimulacion,
    IngresosHogar,
    ItemDespensa,
    TipoServicio,
)

from ..models import DecisionLog
from .agente_mantenimiento import UMBRAL_DEGRADACION, calcular_degradacion
from .llm import pedir_decision_json

DIAS_DEL_MES = 30
# Cuantas lecturas por servicio se promedian para proyectar la factura que falta.
VENTANA_CONSUMO = 7

# El veredicto es la unica llamada del sistema que pondera varios factores a la vez
# (que tajada del disponible se lleva la compra, que tan justificada esta, riesgo de
# rotura, ahorro acumulado) y gpt-4o-mini no da la talla: en pruebas rechazaba una
# heladera rota argumentando que se llevaba "una parte importante" del ahorro cuando
# eran 6.036 pesos sobre un objetivo de 1.500.000 (0,4%), y a la vez aprobaba un
# regalo de cumpleanos. Con gpt-4o los mismos casos salen bien. La extraccion, en
# cambio, le sale perfecta al modelo barato y se queda ahi.
MODELO_VEREDICTO = os.getenv("OPENAI_MODEL_VEREDICTO", "gpt-4o")

# Resultados posibles de una consulta. Van tal cual a DecisionLog.accion_tomada,
# que es varchar (no el enum nativo agente_enum, que aplica solo a id_agente).
COMPRA_APROBADA = "COMPRA_APROBADA"
COMPRA_RECHAZADA = "COMPRA_RECHAZADA"
CONSULTA_INCOMPLETA = "CONSULTA_INCOMPLETA"

# Situacion economica de la compra, resuelta en codigo (ver _clasificar_situacion).
NO_ENTRA = "NO_ENTRA"
ALCANZA_TOCANDO_AHORRO = "ALCANZA_TOCANDO_AHORRO"
ENTRA_COMODO = "ENTRA_COMODO"

MENSAJE_FALTA_PRECIO = (
    "Me falta el precio para poder responderte. Escribime cuanto sale y lo evaluo, "
    'por ejemplo: "quiero comprar una Play 5 que sale 900000, recomendas comprar?"'
)

PROMPT_EXTRACCION = """Extraes datos del mensaje de un residente que consulta si comprar algo.
No opinas ni evaluas: solo devolves lo que el mensaje dice.

REGLA ABSOLUTA: nunca estimes ni inventes el precio. Si el mensaje no dice cuanto sale,
devolve "precio": null. No uses tu conocimiento de cuanto vale el producto.

Interpreta las formas coloquiales de escribir montos: "900 lucas" o "900 mil" son 900000,
"1.5 palos" son 1500000, "$4.500" son 4500.

Clasifica el tipo:
- "dispositivo" si es un aparato duradero (una consola, un televisor, un microondas).
- "item_despensa" si es algo consumible (comida, bebida, limpieza, higiene).

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"precio": <numero en pesos, sin simbolos ni separadores, o null si el mensaje no lo dice>,
 "producto": "<nombre corto del producto, o null si no se entiende que quiere comprar>",
 "tipo": "dispositivo" o "item_despensa"}
"""

PROMPT_VEREDICTO = """Sos el Agente de Ahorro de un sistema de hogar inteligente (SofIA).
Un residente consulto si conviene una compra. Respondes en espanol, natural, 2 o 3 frases,
como se lo explicarias a alguien de la casa.

LA PARTE FINANCIERA YA ESTA RESUELTA por el sistema. No hagas cuentas, no compares montos
y no cuestiones el veredicto economico: te llega en "situacion", que vale una de estas tres.

- "NO_ENTRA": la compra no entra en lo que queda del mes. Deci que ahora no se puede y que
  se puede encarar el mes que viene. Aca "esperar" no se tiene en cuenta.
- "ALCANZA_TOCANDO_AHORRO": la plata alcanza, pero se usa parte de lo que el hogar queria
  ahorrar este mes. OJO: que toque el ahorro NO alcanza por si solo para frenar la compra.
  Eso ya esta contemplado y es perfectamente aceptable cuando la compra se justifica. Sos
  vos quien decide si en este caso vale la pena.
- "ENTRA_COMODO": la plata alcanza sin tocar el ahorro del mes.

Cuando la plata alcanza (las dos ultimas situaciones), vos decidis "esperar": true si
conviene frenar la compra, false si conviene hacerla.

LA DECISION ES TUYA Y ES FINAL. Nunca pidas confirmacion ni le devuelvas la eleccion al
residente: nada de "si estas de acuerdo", "vos veras", "decidi vos" o "avisame si queres
seguir". Resolves y se lo explicas.

Que pesar para decidir:
- "porcentaje_del_margen_libre" te dice que tajada de TODO lo disponible se lleva esta
  compra. Es el dato mas importante para medir si es grande o chica para este hogar:
  por debajo del 30% es una compra chica y no deberias frenarla salvo que sea un capricho
  evidente o haya un dispositivo en riesgo; arriba del 70% se lleva casi todo y ahi si
  conviene frenar, salvo que sea algo urgente.
- "dispositivos_en_riesgo" es el motivo mas fuerte para frenar: si hay un electrodomestico
  caro por romperse, conviene guardar la plata para arreglarlo. Si frenas por esto, deci que
  dispositivo te preocupa.
- Que tan justificada esta la compra. Un capricho evitable (un adorno caro, un lujo que nadie
  necesita) se frena antes que algo necesario (se rompio algo que hace falta, lo necesita
  para trabajar) o una oferta real por algo que el hogar iba a comprar igual.
- "ahorros_del_hogar" alto te da MAS aire para aprobar, no menos: es un colchon que ya esta.
  Un ahorro en cero es lo que te tiene que poner cauteloso.
- Si la situacion es "ENTRA_COMODO", no hay dispositivos en riesgo y no hay nada raro,
  "esperar" es false.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"esperar": true o false,
 "justificacion_tecnica": "<2-3 frases en espanol dirigidas al residente>"}
"""


def _dias_restantes_del_mes(estado: EstadoSimulacion) -> int:
    """Dias que faltan para el proximo cierre de mes (el mes simulado dura 30 dias,
    ver core.services.ingresos.cerrar_mes)."""
    return DIAS_DEL_MES - (estado.dia_numero % DIAS_DEL_MES)


def _ingreso_del_ultimo_mes() -> Decimal:
    """Cuanta plata entro en el ultimo cierre de mes. Sale del payload del
    DecisionLog porque es la unica fuente real: `Residente.ingreso_mensual` es solo
    un fallback y hoy esta en 0 para todos (los montos los manda n8n en el body de
    /api/ingresos/). Sin cierres registrados todavia, devuelve 0.
    """
    ultimo = (
        DecisionLog.objects.filter(accion_tomada="CIERRE_DE_MES")
        .order_by("-timestamp")
        .first()
    )
    if not ultimo or not ultimo.detalles_payload:
        return Decimal("0")
    try:
        return Decimal(str(ultimo.detalles_payload.get("total_ingresos", "0")))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _proyectar_despensa(dias_restantes: int) -> tuple[Decimal, list]:
    """Items que van a tocar su stock minimo antes de que termine el mes: cada uno
    va a costar su precio_estimado cuando el Agente de Despensa lo reponga.
    Los gustos no entran: no se reponen solos.
    """
    total = Decimal("0")
    detalle = []
    items = ItemDespensa.objects.filter(gustos=False).exclude(precio_estimado=None)
    for item in items:
        if item.stock_minimo is None or not item.consumo_promedio_diario:
            continue
        margen = item.stock_actual - item.stock_minimo
        dias_hasta_critico = margen / item.consumo_promedio_diario
        if dias_hasta_critico <= dias_restantes:
            total += item.precio_estimado
            detalle.append(
                {
                    "item": item.nombre,
                    "dias_hasta_reponer": max(0, int(dias_hasta_critico)),
                    "costo": str(item.precio_estimado),
                }
            )
    return total, detalle


def _proyectar_servicios(dias_restantes: int) -> tuple[Decimal, dict]:
    """Factura de agua/luz/gas que falta pagar: promedio de las ultimas lecturas
    por servicio, por la tarifa, por los dias que quedan.
    """
    # Import diferido: orquestador importa este modulo, asi que importarlo arriba
    # armaria un ciclo. Las tarifas viven alla porque son del cobro diario.
    from .orquestador import PRECIO_SERVICIOS

    total = Decimal("0")
    detalle = {}
    for tipo in TipoServicio:
        precio = PRECIO_SERVICIOS.get(tipo)
        if not precio:
            continue
        valores = list(
            ConsumoLog.objects.filter(tipo_servicio=tipo)
            .order_by("-timestamp")
            .values_list("valor_medicion", flat=True)[:VENTANA_CONSUMO]
        )
        if not valores:
            continue
        media = sum(valores) / len(valores)
        monto = (media * precio * dias_restantes).quantize(Decimal("0.01"))
        total += monto
        detalle[str(tipo)] = {
            "consumo_diario_promedio": str(round(media, 2)),
            "costo": str(monto),
        }
    return total, detalle


def _proyectar_mantenimiento(
    dias_restantes: int, estado: EstadoSimulacion
) -> tuple[Decimal, list]:
    """Dispositivos que van a cruzar el umbral critico antes de fin de mes. Se
    reusa calcular_degradacion con una fecha futura: si para entonces supera el
    umbral, el Agente de Mantenimiento le va a agendar un service.
    """
    from .orquestador import COSTO_ESTIMADO_SERVICE

    fecha_futura = estado.fecha_actual + timedelta(days=dias_restantes)
    ya_atendidos = {
        EstadoDispositivo.REQUIERE_SERVICE,
        EstadoDispositivo.EN_MANTENIMIENTO,
        EstadoDispositivo.WAITING_HUMAN_APPROVAL,
        EstadoDispositivo.FUERA_DE_SERVICIO,
    }
    total = Decimal("0")
    detalle = []
    dispositivos = Dispositivo.objects.filter(gustos=False).exclude(
        estado_actual__in=ya_atendidos
    )
    for dispositivo in dispositivos:
        degradacion_futura = calcular_degradacion(dispositivo, fecha_futura)
        if degradacion_futura >= UMBRAL_DEGRADACION:
            total += COSTO_ESTIMADO_SERVICE
            detalle.append(
                {
                    "dispositivo": dispositivo.nombre,
                    "prioridad": dispositivo.prioridad,
                    "degradacion_hoy": calcular_degradacion(
                        dispositivo, estado.fecha_actual
                    ),
                    "degradacion_fin_de_mes": degradacion_futura,
                    "costo_service": str(COSTO_ESTIMADO_SERVICE),
                }
            )
    return total, detalle


def construir_contexto() -> dict:
    """Foto deterministica de las finanzas del hogar, con los umbrales de decision
    ya resueltos.
    """
    estado = EstadoSimulacion.actual()
    hogar = IngresosHogar.actual()
    dias_restantes = _dias_restantes_del_mes(estado)

    despensa, detalle_despensa = _proyectar_despensa(dias_restantes)
    servicios, detalle_servicios = _proyectar_servicios(dias_restantes)
    mantenimiento, detalle_mantenimiento = _proyectar_mantenimiento(
        dias_restantes, estado
    )
    proyectado = despensa + servicios + mantenimiento
    disponible = hogar.saldo_disponible + hogar.ahorros

    # Lo maximo que se puede gastar sin quedarse sin cubrir lo que falta del mes.
    margen_libre = disponible - proyectado
    # La meta blanda de ahorro, en pesos: no aparta plata ni bloquea nada, solo
    # marca cuanto le gustaria al hogar no tocar.
    objetivo_ahorro = (
        _ingreso_del_ultimo_mes() * hogar.porcentaje_ahorrable / Decimal("100")
    ).quantize(Decimal("0.01"))

    return {
        "dia_del_mes": estado.dia_numero % DIAS_DEL_MES,
        "dias_restantes_del_mes": dias_restantes,
        "saldo_disponible": str(hogar.saldo_disponible),
        "ahorros": str(hogar.ahorros),
        "deuda": str(hogar.deuda),
        "disponible_total": str(disponible),
        "gasto_proyectado_total": str(proyectado),
        "gasto_proyectado_despensa": str(despensa),
        "gasto_proyectado_servicios": str(servicios),
        "gasto_proyectado_mantenimiento": str(mantenimiento),
        "margen_libre": str(margen_libre),
        "porcentaje_ahorrable": str(hogar.porcentaje_ahorrable),
        "objetivo_ahorro": str(objetivo_ahorro),
        "margen_respetando_ahorro": str(margen_libre - objetivo_ahorro),
        "items_por_reponer": detalle_despensa,
        "servicios_por_pagar": detalle_servicios,
        "dispositivos_en_riesgo": detalle_mantenimiento,
    }


def _precio_valido(valor) -> Decimal | None:
    """El precio tiene que ser un numero positivo. Devuelve None si el modelo no
    encontro precio en el mensaje (null) o si mando algo que no se puede usar.
    """
    if valor is None:
        return None
    try:
        precio = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return precio if precio > 0 else None


def _clasificar_situacion(precio: Decimal, contexto: dict) -> str:
    """El veredicto economico, en codigo. Nunca lo decide el LLM."""
    margen_libre = Decimal(contexto["margen_libre"])
    margen_respetando_ahorro = Decimal(contexto["margen_respetando_ahorro"])
    if precio > margen_libre:
        return NO_ENTRA
    if precio > margen_respetando_ahorro:
        return ALCANZA_TOCANDO_AHORRO
    return ENTRA_COMODO


def evaluar(consulta: str, contexto: dict | None = None) -> dict:
    """Veredicto sobre la compra que consulto el residente.

    `consulta` es el texto libre tal como lo escribio: no se le exige formato. Lo
    unico obligatorio es que diga el precio; si no lo dice se devuelve
    CONSULTA_INCOMPLETA y no se evalua nada mas (no se gasta la segunda llamada).

    Devuelve siempre un dict con la clave `resultado` (COMPRA_APROBADA,
    COMPRA_RECHAZADA o CONSULTA_INCOMPLETA), listo para volcar a DecisionLog.
    """
    if contexto is None:
        contexto = construir_contexto()

    # --- Paso 1: extraccion (LLM) ---
    extraccion = pedir_decision_json(
        PROMPT_EXTRACCION, {"consulta_del_residente": consulta}
    )
    precio = _precio_valido(extraccion.get("precio"))

    if precio is None:
        return {
            "resultado": CONSULTA_INCOMPLETA,
            "producto": extraccion.get("producto"),
            "precio": None,
            "tipo": extraccion.get("tipo"),
            "situacion": None,
            # El texto lo pone el codigo, no el modelo: es siempre el mismo pedido
            # y conviene que la instruccion de como reformular sea consistente.
            "justificacion_tecnica": MENSAJE_FALTA_PRECIO,
        }

    # --- Paso 2: veredicto economico (codigo) ---
    situacion = _clasificar_situacion(precio, contexto)
    # Que tajada de lo disponible se lleva la compra. Se calcula aca porque es otra
    # cuenta: el modelo solo sabria que "toca el ahorro", sin distinguir si se lleva
    # el 12% o el 97% de todo lo que hay.
    margen_libre = Decimal(contexto["margen_libre"])
    porcentaje_margen = (
        (precio / margen_libre * 100).quantize(Decimal("0.1"))
        if margen_libre > 0
        else Decimal("100.0")
    )

    # --- Paso 3: criterio y redaccion (LLM) ---
    # Se le pasa la situacion ya resuelta y solo los datos que necesita para
    # redactar y para el unico juicio que le toca. No recibe los umbrales: no
    # tiene que rehacer ninguna comparacion.
    veredicto = pedir_decision_json(
        PROMPT_VEREDICTO,
        {
            "consulta_del_residente": consulta,
            "producto": extraccion.get("producto"),
            "precio": str(precio),
            "situacion": situacion,
            "porcentaje_del_margen_libre": str(porcentaje_margen),
            "ahorros_del_hogar": contexto["ahorros"],
            "objetivo_ahorro": contexto["objetivo_ahorro"],
            "dispositivos_en_riesgo": contexto["dispositivos_en_riesgo"],
        },
        modelo=MODELO_VEREDICTO,
    )

    if situacion == NO_ENTRA:
        resultado = COMPRA_RECHAZADA
    elif veredicto.get("esperar"):
        resultado = COMPRA_RECHAZADA
    else:
        resultado = COMPRA_APROBADA

    return {
        "resultado": resultado,
        "producto": extraccion.get("producto"),
        "precio": precio,
        "tipo": extraccion.get("tipo"),
        "situacion": situacion,
        "justificacion_tecnica": veredicto.get("justificacion_tecnica"),
    }
