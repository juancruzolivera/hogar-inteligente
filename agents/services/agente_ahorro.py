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

Y decidi si es un gusto:
- "es_gusto": true si es un capricho del que el hogar puede prescindir, false si es un
  bien de uso real de la casa. Define si el objeto entra a los ciclos automaticos: un
  gusto no recibe service ni se repone solo, un bien de uso si.

  La comida cae en los dos lados, segun para que sirve. Son BIENES DE USO los basicos de
  la alacena, lo que el hogar repone siempre: leche, arroz, aceite, azucar, huevos, yerba,
  el pan de todos los dias, papel higienico, detergente, shampoo. Son GUSTOS los antojos
  puntuales, lo que nadie necesita reponer: un pan artesanal o de panaderia, un helado
  importado, chocolates finos, una cerveza especial, snacks premium.

  Con los aparatos pasa lo mismo. Son BIENES DE USO los que la casa necesita para
  funcionar: microondas, lavarropas, heladera, ventilador, termotanque. Son GUSTOS los de
  entretenimiento o decoracion: una consola, un parlante de audiofilo, un adorno, algo de
  coleccion.

Cuando es_gusto es false hacen falta los parametros operativos del objeto. Estimalos con
tu conocimiento general (a diferencia del precio, que solo puede salir del mensaje):
- Si es "dispositivo": "vida_util_dias", cuantos dias dura razonablemente antes de
  necesitar service, en escala REAL. De referencia: heladera ~3600, lavarropas ~2900,
  microondas ~2900, ventilador ~1800, notebook ~1500.
- Si es "item_despensa": "stock_minimo" y "consumo_promedio_diario", en la unidad del
  producto: cuanto conviene tener siempre en casa y cuanto consume un hogar por dia.
Cuando es_gusto es true, los tres van en null: un gusto no tiene ciclo que parametrizar.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"precio": <numero en pesos, sin simbolos ni separadores, o null si el mensaje no lo dice>,
 "producto": "<nombre corto del producto, o null si no se entiende que quiere comprar>",
 "tipo": "dispositivo" o "item_despensa",
 "es_gusto": true o false,
 "cantidad": <cuantas unidades se compran; 1 si el mensaje no lo aclara>,
 "unidad_medida": "<unidad del producto (L, kg, unidades); null si es un dispositivo>",
 "vida_util_dias": <numero, o null>,
 "stock_minimo": <numero, o null>,
 "consumo_promedio_diario": <numero, o null>}
"""

PROMPT_VEREDICTO = """Sos el Agente de Ahorro de un sistema de hogar inteligente (SofIA).
Un residente consulto si conviene una compra. NO decidis vos si se aprueba: tu trabajo es
CLASIFICAR el pedido y redactar la respuesta. El sistema combina tu clasificacion con los
numeros y saca el veredicto.

Clasificas tres cosas:

1. "fuerza_argumento" -- que tan buena es la razon que dio el residente para esta compra.
   - "ninguno": no dio ninguna razon, solo dijo que la quiere o pregunto el precio.
   - "debil": una razon vaga o de puro deseo ("hace mucho que la quiero", "me la merezco").
   - "solido": una razon concreta que explica POR QUE conviene esta compra. Entran aca: se
     rompio algo que hace falta, lo necesita para trabajar, estudiar o generar ingresos, es
     un regalo para alguien, reemplaza algo viejo que ya no rinde, hay un descuento real y
     grande, es una oportunidad que no se va a repetir, o viene ahorrando especificamente
     para esto.
   - "debil": deseo puro, o afirmaciones que no dicen nada sobre por que conviene la compra:
     "hace mucho que la quiero", "me la merezco", "es mi plata y la gasto en lo que quiero",
     "me lo gane", "para eso trabajo". Que la plata sea suya es cierto, pero no es un
     argumento: no explica por que ESTA compra, AHORA.

   NO juzgues el producto en si. Que sea entretenimiento, un lujo o un capricho no vuelve
   debil al argumento: una consola que el residente usa para generar ingresos haciendo
   streams es una razon tan solida como un lavarropas. Lo que evaluas es la RAZON, no la
   categoria del objeto ni si a vos te parece una buena idea gastar en eso.

   Si el residente mezcla una afirmacion dudosa con razones que si se sostienen, evalua la
   razon mas fuerte que se sostiene por si sola. Una parte poco creible no invalida al resto
   del argumento.

2. "argumento_creible" -- si lo que dice puede ser verdad.
   - true por defecto. Un producto que existe y un descuento plausible son creibles.
   - false SOLO si no se sostiene: un producto inventado o imposible ("una play 1000 unica
     en el mundo"), un descuento que no puede ser real, o pura presion sin contenido.
   Un descuento del 90% en un producto normal es sospechoso pero posible (liquidacion,
   usado, remate). Un 90% en algo "unico en el mundo" no.

3. "ya_lo_tenemos" -- true si el producto que pide ya esta en "dispositivos_del_hogar".
   Compara por significado, no por texto exacto: "play 5", "PlayStation 5" y "ps5" son lo
   mismo; "microondas" y "horno microondas" tambien. Un televisor y un monitor no.
   Solo aplica a aparatos. Si la compra es de despensa, siempre false: volver a comprar
   leche o pan es lo normal.

4. "justifica_tener_otro" -- solo importa cuando "ya_lo_tenemos" es true. Es true si el
   residente dio una razon para tener OTRO ademas del que hay: es un regalo, es para otra
   persona de la casa, el que tienen se rompio o anda mal, lo quiere para otro ambiente,
   el actual es viejo y lo va a reemplazar. Es false si pide otro igual sin explicar para
   que: ahi conviene recordarle que ya tienen uno.

5. "frenar_por_dispositivo" -- true solo si hay algo en "dispositivos_en_riesgo" que conviene
   priorizar por sobre esta compra (un electrodomestico caro por romperse). Si la lista esta
   vacia, es false siempre.

Como se combina tu clasificacion con los numeros (para que tu respuesta sea coherente con lo
que va a pasar):
- "porcentaje_del_margen_libre" bajo (menos del 30%): se aprueba.
- Medio (30% a 70%): se aprueba, salvo que sea un capricho y no hayas dado ningun argumento.
- Alto (mas del 70%, casi toda la plata del hogar): se aprueba SOLO si el argumento es
  "solido" Y creible. Con argumento solido, SE APRUEBA -- los ahorros del hogar existen para
  gastarse cuando vale la pena.
- Si "situacion" es "NO_ENTRA", se rechaza siempre: no hay plata, no importa el argumento.
- Si "frenar_por_dispositivo" es true, se rechaza.
- Si "ya_lo_tenemos" es true y "justifica_tener_otro" es false, se rechaza aunque sobre la
  plata: no tiene sentido comprar dos veces lo mismo sin un motivo. Decile que ya tienen uno.

Y redactas "justificacion_tecnica", que es el mensaje que le llega al residente por
Telegram. En espanol, natural, como se lo dirias a alguien de la casa. Reglas del texto:
- BREVE. Una o dos frases si aprobas. Hasta tres solo si tenes que explicar un rechazo.
- NO arranques repitiendo lo que pidio, ni con "Entiendo que necesitas...". Ya sabe lo que
  pidio: contestale directo. Mal: "Entiendo que necesitas la PC para tu trabajo de streaming,
  es una inversion importante, pero si es esencial para generar ingresos tiene sentido.
  Adelante." Bien: "Dale, si es para laburar se justifica."
- NO narres tu clasificacion interna. Nada de "no diste ninguna razon", "el argumento es
  debil", "entra comodo en el presupuesto". Eso es cocina del sistema, al residente no le
  aporta nada. Mal: "No diste ninguna razon, pero como es un gasto chico no hay problema."
  Bien: "Dale, comprala."
- Si aprobas algo chico y sin vueltas, con una frase alcanza.
- Si rechazas, deci el motivo concreto que lo frena y, si se puede, que hacer al respecto.
- Cuando el argumento del residente es lo que define la decision (te convencio, o no te
  alcanzo), mencionalo -- pero CONTESTANDOLO, no repitiendolo.
- Escribilo coherente con el resultado que se desprende de tu clasificacion. Si clasificaste
  el argumento como solido y la compra se lleva mucho, el mensaje tiene que sonar a "dale,
  vamos", no a "mejor esperemos".
- NUNCA uses "no hay ahorros acumulados" como motivo para frenar. Un ahorro en cero no es un
  problema: puede ser que todavia no cerro ningun mes y la plata este en el saldo actual.
- Nunca pidas confirmacion ni le devuelvas la eleccion ("si estas de acuerdo", "vos decidis").

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional:
{"fuerza_argumento": "ninguno" o "debil" o "solido",
 "argumento_creible": true o false,
 "ya_lo_tenemos": true o false,
 "justifica_tener_otro": true o false,
 "frenar_por_dispositivo": true o false,
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
        "dispositivos_del_hogar": list(
            Dispositivo.objects.order_by("nombre").values_list("nombre", flat=True)
        ),
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


# Umbrales de proporcion sobre el margen libre, en %. Definen cuanta razon hace falta
# para aprobar: hasta BAJO no hace falta ninguna, arriba de ALTO hace falta un argumento
# solido y creible (ver _decidir_resultado).
PCT_BAJO = 30
PCT_ALTO = 70

VIDA_UTIL_MIN_DIAS = 30
VIDA_UTIL_MAX_DIAS = 7300  # 20 anos
VIDA_UTIL_FALLBACK_DIAS = 1825  # 5 anos: si el LLM no estimo o mando algo inusable

# DECISION TOMADA A PROPOSITO, no la "corrijas": el LLM estima la vida util en escala
# REAL (un microondas ~2900 dias), aunque los dispositivos precargados en Supabase usan
# una escala comprimida para la demo (la Heladera Inverter figura con 365 dias, un decimo
# de lo real). Consecuencia conocida: con 1 minuto = 1 dia simulado, un aparato comprado
# durante la demo no alcanza a degradarse, asi que en la practica no va a pedir service
# aunque tenga gustos=False. Se acepta porque CU-03 se demuestra con los dispositivos
# precargados, que si degradan en pocas horas de corrida.


def _numero(valor, minimo=None, maximo=None) -> Decimal | None:
    """Convierte a Decimal y acota al rango. None si no es usable."""
    if valor is None:
        return None
    try:
        n = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if minimo is not None and n < minimo:
        n = Decimal(str(minimo))
    if maximo is not None and n > maximo:
        n = Decimal(str(maximo))
    return n


def _sanear_parametros(extraccion: dict) -> dict:
    """Acota lo que estimo el LLM. Es lo que separa "la IA estima" de "la IA decide
    cuanto gasta la casa": una vida util de 3 dias programaria un service inmediato
    de $5.000, y un consumo diario negativo reventaria avanzar_dia().
    """
    es_gusto = bool(extraccion.get("es_gusto"))
    vida_util = _numero(
        extraccion.get("vida_util_dias"), VIDA_UTIL_MIN_DIAS, VIDA_UTIL_MAX_DIAS
    )
    return {
        "es_gusto": es_gusto,
        "cantidad": _numero(extraccion.get("cantidad"), 0) or Decimal("1"),
        "unidad_medida": (extraccion.get("unidad_medida") or "unidades")[:20],
        # Un gusto va sin vida util (la base lo permite solo en ese caso). Un bien de
        # uso siempre tiene que tener una: si el LLM no la dio, cae al fallback.
        "vida_util_dias": None if es_gusto else int(vida_util or VIDA_UTIL_FALLBACK_DIAS),
        "stock_minimo": None if es_gusto else (_numero(extraccion.get("stock_minimo"), 0) or Decimal("0")),
        "consumo_promedio_diario": _numero(extraccion.get("consumo_promedio_diario"), 0) or Decimal("0"),
    }


def _clasificar_situacion(precio: Decimal, contexto: dict) -> str:
    """El veredicto economico, en codigo. Nunca lo decide el LLM."""
    margen_libre = Decimal(contexto["margen_libre"])
    margen_respetando_ahorro = Decimal(contexto["margen_respetando_ahorro"])
    if precio > margen_libre:
        return NO_ENTRA
    if precio > margen_respetando_ahorro:
        return ALCANZA_TOCANDO_AHORRO
    return ENTRA_COMODO


def _decidir_resultado(
    situacion: str, porcentaje: Decimal, veredicto: dict, tipo: str | None = None
) -> str:
    """Combina la clasificacion del LLM con la proporcion del gasto. El veredicto sale
    de aca, no del modelo.

    Se llego a esto despues de que el modelo ignorara la instruccion de dejarse
    convencer: midiendo el punto de quiebre se vio que el umbral del 70% decidia el
    100% de los casos (abajo aprobaba sin razon alguna, arriba rechazaba con la mejor
    razon). El LLM clasifica -- que hace bien -- y el codigo combina.
    """
    if situacion == NO_ENTRA:
        return COMPRA_RECHAZADA
    if veredicto.get("frenar_por_dispositivo"):
        return COMPRA_RECHAZADA

    # Duplicado sin motivo: aunque sobre la plata, comprar un segundo aparato igual
    # al que ya hay no tiene sentido. Solo aplica a dispositivos -- en despensa
    # recomprar es el comportamiento normal (la leche se termina, la consola no).
    if (
        tipo == "dispositivo"
        and veredicto.get("ya_lo_tenemos")
        and not veredicto.get("justifica_tener_otro")
    ):
        return COMPRA_RECHAZADA

    argumento_solido = (
        veredicto.get("fuerza_argumento") == "solido"
        and bool(veredicto.get("argumento_creible", True))
    )

    if porcentaje > PCT_ALTO:
        # Casi toda la plata del hogar: hace falta una razon de peso, pero si la hay
        # se aprueba (los ahorros existen para gastarse).
        return COMPRA_APROBADA if argumento_solido else COMPRA_RECHAZADA

    if porcentaje > PCT_BAJO:
        sin_ninguna_razon = veredicto.get("fuerza_argumento") == "ninguno"
        if situacion == ALCANZA_TOCANDO_AHORRO and sin_ninguna_razon:
            return COMPRA_RECHAZADA
        return COMPRA_APROBADA

    return COMPRA_APROBADA


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
            "es_gusto": bool(extraccion.get("es_gusto")),
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
            "dispositivos_del_hogar": contexto["dispositivos_del_hogar"],
        },
        modelo=MODELO_VEREDICTO,
    )

    resultado = _decidir_resultado(
        situacion, porcentaje_margen, veredicto, extraccion.get("tipo")
    )

    return {
        "resultado": resultado,
        "producto": extraccion.get("producto"),
        "precio": precio,
        "tipo": extraccion.get("tipo"),
        "es_gusto": bool(extraccion.get("es_gusto")),
        "situacion": situacion,
        "justificacion_tecnica": veredicto.get("justificacion_tecnica"),
        # Parametros operativos estimados por el LLM, ya saneados. Solo se usan si
        # la compra se aprueba y hay que crear la fila (ver _dar_de_alta_compra).
        "parametros": _sanear_parametros(extraccion),
    }
