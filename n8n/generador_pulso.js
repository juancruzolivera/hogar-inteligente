// ============================================================================
// Nodo "Code" de n8n (lenguaje: JavaScript) para el workflow "El Pulso".
// Genera el body de POST /api/pulso/ simulando que distintos convivientes,
// cada uno con su propia personalidad/rutina, consumieron productos y
// servicios ese dia.
//
// Para simular OTRO escenario (otra familia, otro dia, forzar una anomalia):
// tocá solo las secciones RESIDENTES y ESCENARIO. No hace falta tocar el motor.
// ============================================================================

// ----- ARQUETIPOS: personalidades reutilizables -----
// prob_en_casa: probabilidad de estar en casa cada dia de la semana simulada,
// [domingo, lunes, martes, miercoles, jueves, viernes, sabado].
// factor_actividad: multiplica el consumo de servicios cuando esta en casa
// (>1 = consume mas de lo normal, <1 = menos).
const ARQUETIPOS = {
  home_office: {
    prob_en_casa: [0.9, 0.85, 0.85, 0.85, 0.85, 0.85, 0.9],
    factor_actividad: 1.3,
  },
  oficina_9_18: {
    prob_en_casa: [0.9, 0.35, 0.35, 0.35, 0.35, 0.35, 0.7],
    factor_actividad: 1.0,
  },
  estudiante: {
    prob_en_casa: [0.8, 0.5, 0.5, 0.5, 0.5, 0.6, 0.85],
    factor_actividad: 0.8,
  },
  viajante_frecuente: {
    prob_en_casa: [0.4, 0.2, 0.2, 0.2, 0.2, 0.3, 0.5],
    factor_actividad: 0.9,
  },
  derrochador: {
    // Util para forzar CU-02 (pico de consumo > 20% de la media movil) a proposito.
    prob_en_casa: [0.95, 0.9, 0.9, 0.9, 0.9, 0.95, 0.95],
    factor_actividad: 2.2,
  },
};

// ----- CATALOGO_DESPENSA: fuente unica de los productos reales de la base -----
// `nombre` tiene que matchear EXACTO (Django compara en minusculas, sin espacios
// sobrantes) el campo `nombre` de un ItemDespensa en Supabase. `unidad` es solo
// informativa (se la mandamos a la IA para que use unidades correctas), Django
// no la valida. `rango_tipico` es el consumo diario por defecto para un
// residente que no especifica su propio rango.
//
// PARA AGREGAR UN PRODUCTO NUEVO: 1) crearlo en Supabase (tabla item_despensa)
// con ese nombre exacto; 2) agregar una entrada aca; 3) referenciarlo desde el
// habitos_despensa del/los residente(s) que lo consuman. Si un residente
// referencia un nombre que no esta aca, el motor tira un error explicito en
// vez de fallar en silencio (que es lo que pasaba antes con "Leche"/"Pan"/etc,
// que no matcheaban ningun item real).
const CATALOGO_DESPENSA = {
  "Leche Entera 1L": { unidad: "L", rango_tipico: [0.3, 0.6] },
  "Arroz 1kg": { unidad: "kg", rango_tipico: [0.1, 0.25] },
  "Café Molido 500g": { unidad: "kg", rango_tipico: [0.03, 0.07] },
  "Jabón para Ropa 3L": { unidad: "L", rango_tipico: [0.03, 0.07] },
  "Aceite de Girasol 1.5L": { unidad: "L", rango_tipico: [0.01, 0.03] },
  "Papel Higiénico 6u": { unidad: "unidades", rango_tipico: [0.2, 0.4] },
  "Yerba Mate 1kg": { unidad: "kg", rango_tipico: [0.05, 0.12] },
  "Pan Lactal": { unidad: "unidades", rango_tipico: [0.1, 0.2] },
  "Huevos (docena)": { unidad: "docenas", rango_tipico: [0.05, 0.15] },
  "Detergente para Platos": { unidad: "L", rango_tipico: [0.02, 0.05] },
  Shampoo: { unidad: "L", rango_tipico: [0.01, 0.02] },
  "Azúcar 1kg": { unidad: "kg", rango_tipico: [0.04, 0.08] },
};

// ----- RESIDENTES: el "elenco" del hogar para este escenario -----
// `telefono` es informativo hoy (Django no lo valida en /api/pulso/, solo lo
// usa para identificar quien escribe por WhatsApp en /api/comando/, y ahora
// tambien para matchear ingresos en /api/ingresos/ - ver generador_ingresos.js).
// Si lo usas para eso, tiene que matchear EXACTO el campo `telefono` de la
// tabla `residente` en Supabase.
// `item` en habitos_despensa tiene que ser una clave de CATALOGO_DESPENSA.
// `rango` es opcional: si no se especifica, se usa el rango_tipico del catalogo.
// Los 3 residentes pueden consumir los 12 productos del catalogo -- lo que
// cambia entre ellos es la probabilidad de cada uno (en mayor o menor medida
// segun su personalidad), no la lista de productos disponibles.
const RESIDENTES = [
  {
    nombre: "Carla",
    telefono: "+5491122334455",
    arquetipo: "home_office",
    // Pasa mucho tiempo en casa: consume de todo, alto en cafe/mate/te.
    habitos_despensa: [
      { item: "Leche Entera 1L", prob: 0.6 },
      { item: "Arroz 1kg", prob: 0.2 },
      { item: "Café Molido 500g", prob: 0.8 },
      { item: "Jabón para Ropa 3L", prob: 0.15 },
      { item: "Aceite de Girasol 1.5L", prob: 0.2 },
      { item: "Papel Higiénico 6u", prob: 0.25 },
      { item: "Yerba Mate 1kg", prob: 0.5 },
      { item: "Pan Lactal", prob: 0.3 },
      { item: "Huevos (docena)", prob: 0.15 },
      { item: "Detergente para Platos", prob: 0.15 },
      { item: "Shampoo", prob: 0.1 },
      { item: "Azúcar 1kg", prob: 0.3 },
    ],
    habitos_servicios: {
      AGUA: [2, 5],
      LUZ: [1.5, 3.5],
      GAS: [0.5, 1.5],
    },
  },
  {
    nombre: "Julian",
    telefono: "+5491133445566",
    arquetipo: "oficina_9_18",
    // Menos tiempo en casa: come mas simple (pan, huevos), limpieza moderada.
    habitos_despensa: [
      { item: "Leche Entera 1L", prob: 0.4 },
      { item: "Arroz 1kg", prob: 0.25 },
      { item: "Café Molido 500g", prob: 0.3 },
      { item: "Jabón para Ropa 3L", prob: 0.2 },
      { item: "Aceite de Girasol 1.5L", prob: 0.15 },
      { item: "Papel Higiénico 6u", prob: 0.15 },
      { item: "Yerba Mate 1kg", prob: 0.2 },
      { item: "Pan Lactal", prob: 0.5 },
      { item: "Huevos (docena)", prob: 0.3 },
      { item: "Detergente para Platos", prob: 0.2 },
      { item: "Shampoo", prob: 0.15 },
      { item: "Azúcar 1kg", prob: 0.15 },
    ],
    habitos_servicios: {
      AGUA: [1.5, 3],
      LUZ: [0.8, 2],
      GAS: [0.3, 1],
    },
  },
  {
    nombre: "Sofia (invitada)",
    telefono: "+5491144556677",
    arquetipo: "estudiante",
    // Invitada: consume un poco de todo, en general menos que los otros dos.
    habitos_despensa: [
      { item: "Leche Entera 1L", prob: 0.2 },
      { item: "Arroz 1kg", prob: 0.2 },
      { item: "Café Molido 500g", prob: 0.15 },
      { item: "Jabón para Ropa 3L", prob: 0.05 },
      { item: "Aceite de Girasol 1.5L", prob: 0.1 },
      { item: "Papel Higiénico 6u", prob: 0.3 },
      { item: "Yerba Mate 1kg", prob: 0.15 },
      { item: "Pan Lactal", prob: 0.15 },
      { item: "Huevos (docena)", prob: 0.1 },
      { item: "Detergente para Platos", prob: 0.05 },
      { item: "Shampoo", prob: 0.2 },
      { item: "Azúcar 1kg", prob: 0.1 },
    ],
    habitos_servicios: {
      AGUA: [1, 2.5],
      LUZ: [0.5, 1.5],
    },
  },
];

// ----- ESCENARIO: override manual opcional para forzar un caso puntual -----
// Dejar en `null` para simulacion 100% organica (solo arquetipos + azar).
const ESCENARIO = null;
// const ESCENARIO = { forzar_servicios: { AGUA: 25 } };            // fuga de agua -> dispara CU-02
// const ESCENARIO = { excluir_residentes: ["+5491122334455"] };    // Carla de viaje esta semana
// const ESCENARIO = { multiplicador_global: 3 };                   // "junta en casa": todos consumen mas

// ============================================================================
// MOTOR — no hace falta tocar esto para armar un escenario nuevo.
// ============================================================================
function rand(min, max) {
  return +(min + Math.random() * (max - min)).toFixed(2);
}

function tirar(prob) {
  return Math.random() < prob;
}

function generarPulso(residentes, arquetipos, catalogo, escenario, diaSemana) {
  const excluidos = new Set(escenario?.excluir_residentes ?? []);
  const multGlobal = escenario?.multiplicador_global ?? 1;

  const residentes_en_casa = [];

  for (const r of residentes) {
    if (excluidos.has(r.telefono)) continue;

    const perfil = arquetipos[r.arquetipo];
    if (!tirar(perfil.prob_en_casa[diaSemana])) continue;

    const factor = (perfil.factor_actividad ?? 1) * multGlobal;

    const consumo_despensa = [];
    for (const h of r.habitos_despensa ?? []) {
      const catalogado = catalogo[h.item];
      if (!catalogado) {
        throw new Error(
          `"${h.item}" (habito de ${r.nombre}) no esta en CATALOGO_DESPENSA. ` +
          `Agregalo al catalogo o revisa que el nombre matchee exacto un ItemDespensa real.`
        );
      }
      if (tirar(h.prob)) {
        const [min, max] = h.rango ?? catalogado.rango_tipico;
        consumo_despensa.push({ item: h.item, cantidad: rand(min, max) });
      }
    }

    const consumo_servicios = {};
    for (const [tipo, rango] of Object.entries(r.habitos_servicios ?? {})) {
      consumo_servicios[tipo] = +(rand(rango[0], rango[1]) * factor).toFixed(2);
    }

    residentes_en_casa.push({ telefono: r.telefono, consumo_despensa, consumo_servicios });
  }

  if (escenario?.forzar_servicios) {
    if (residentes_en_casa.length === 0) {
      residentes_en_casa.push({
        telefono: residentes[0]?.telefono,
        consumo_despensa: [],
        consumo_servicios: {},
      });
    }
    Object.assign(residentes_en_casa[0].consumo_servicios, escenario.forzar_servicios);
  }

  return { residentes_en_casa };
}

// El contador vive en el static data del workflow: sobrevive entre
// ejecuciones del Schedule Trigger sin depender del reloj real, y cicla una
// semana simulada cada 7 pulsos (7 minutos reales = 7 dias simulados).
const staticData = $getWorkflowStaticData('global');
staticData.pulso_count = (staticData.pulso_count ?? 0) + 1;
const diaSemana = staticData.pulso_count % 7;

const body = generarPulso(RESIDENTES, ARQUETIPOS, CATALOGO_DESPENSA, ESCENARIO, diaSemana);

// ----- openai_request: el body completo para el nodo HTTP Request "Evento
// inesperado (IA)" que sigue en el workflow. Se arma aca (no en el nodo HTTP)
// para no tener que escapar un prompt largo dentro del JSON del workflow.
const SYSTEM_PROMPT_EVENTO = `Sos un generador de eventos inesperados para la simulacion de un hogar inteligente (SofIA). Se te informa "hay_evento_forzado" (true o false, siempre uno de los dos, nunca null) y, cuando es true, tambien "categoria_forzada", "residente_forzado", "escenario_forzado", y segun la categoria "item_forzado" o "rango_monto_ocio" -- TODO YA DECIDIDO por el sistema fuera de tu control, no lo cambies ni inventes un escenario distinto. Tu unico trabajo es copiar hay_evento_forzado tal cual en "hay_evento" (boolean exacto, nunca null ni string), y si es true, redactar el motivo especifico y elegir cantidades/montos concretos y creibles PARA ESE escenario ya decidido -- no elijas vos el residente, la categoria, el escenario, ni (cuando viene dado) el producto.

Que completar segun categoria_forzada:
- "sociales": escenario_forzado ya describe el tipo de junta. Completa consumo_servicios (agua y/o luz, un poco por encima de lo normal) y, si item_forzado viene dado, agregalo a consumo_despensa con una cantidad razonable para ese escenario.
- "compras_puntuales" / "imprevistos": item_forzado ya es el producto exacto a usar (viene del catalogo). Completa consumo_despensa con ESE item y una cantidad mayor a lo habitual, coherente con escenario_forzado.
- "ocio": rango_monto_ocio ya es el rango de precio [minimo, maximo] para escenario_forzado. Elegi un monto_ocio DENTRO de ese rango. consumo_despensa y consumo_servicios quedan vacios.

El motivo tiene que mencionar el escenario_forzado con tus propias palabras (no lo copies literal), sonando natural. Nunca repitas la misma redaccion exacta entre llamadas.

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional: {"hay_evento": true o false, "telefono": "<telefono de residente_forzado si hay_evento es true, o null si es false>", "consumo_despensa": [{"item": "<nombre exacto del catalogo>", "cantidad": <numero en la unidad de ese item>}], "consumo_servicios": {"AGUA": <numero opcional>, "LUZ": <numero opcional>, "GAS": <numero opcional>}, "monto_ocio": <numero en pesos dentro de rango_monto_ocio si categoria_forzada es "ocio", o null en cualquier otro caso>, "motivo": "<1 frase breve en espanol, o null si hay_evento es false>"}. Si hay_evento es false, consumo_despensa tiene que ser un array vacio, consumo_servicios un objeto vacio, y monto_ocio null.`;

// La probabilidad, la categoria, el residente, el escenario concreto y (cuando
// aplica) el producto/rango de monto se deciden ACA (JS) -- no le pedimos al
// LLM que "elija" nada de eso. Se probo dejarselo a la IA con solo una lista de
// categorias sugeridas y quedo sesgada siempre a la misma respuesta ("reunion
// con amigos y compra de cafe", 0 eventos de Ocio en 10 intentos). Forzando
// hasta el escenario especifico, el LLM solo redacta texto y numeros dentro de
// un molde ya armado -- mucho mas confiable.
const PROB_EVENTO_INESPERADO = 0.2; // ~1 de cada 5 pulsos
const DETALLE_EVENTO = {
  sociales: ["visita sorpresa de un amigo", "junta de amigos en casa", "asado familiar", "alguien se quedo a dormir", "festejo de cumpleanos"],
  compras_puntuales: ["antojo fuera de la rutina", "se termino antes de lo esperado y hay que reponerlo urgente", "aprovecharon una oferta y compraron de mas"],
  imprevistos: ["se rompio o derramo un producto y hay que reponerlo", "una visita medica genero gasto en articulos del hogar", "un electrodomestico personal fallo y hubo que reponer algo"],
  ocio: [
    { escenario: "salida al cine", rango_monto: [2000, 4500] },
    { escenario: "salida a comer afuera (bar o restaurante)", rango_monto: [5000, 15000] },
    { escenario: "suscripcion nueva de streaming/entretenimiento", rango_monto: [1500, 3500] },
    { escenario: "evento o salida de fin de semana", rango_monto: [8000, 20000] },
  ],
};

const hayEventoForzado = Math.random() < PROB_EVENTO_INESPERADO;
const categoriaForzada = hayEventoForzado
  ? Object.keys(DETALLE_EVENTO)[Math.floor(Math.random() * Object.keys(DETALLE_EVENTO).length)]
  : null;
const residenteForzado = hayEventoForzado
  ? RESIDENTES[Math.floor(Math.random() * RESIDENTES.length)]
  : null;

let escenarioForzado = null;
let itemForzado = null;
let rangoMontoOcio = null;
if (hayEventoForzado) {
  if (categoriaForzada === "ocio") {
    const opcion = DETALLE_EVENTO.ocio[Math.floor(Math.random() * DETALLE_EVENTO.ocio.length)];
    escenarioForzado = opcion.escenario;
    rangoMontoOcio = opcion.rango_monto;
  } else {
    const opciones = DETALLE_EVENTO[categoriaForzada];
    escenarioForzado = opciones[Math.floor(Math.random() * opciones.length)];
  }
  if (categoriaForzada === "compras_puntuales" || categoriaForzada === "imprevistos") {
    const nombresCatalogo = Object.keys(CATALOGO_DESPENSA);
    itemForzado = nombresCatalogo[Math.floor(Math.random() * nombresCatalogo.length)];
  } else if (categoriaForzada === "sociales" && Math.random() < 0.5) {
    // La mitad de las juntas sociales tambien involucran comprar algo puntual.
    const nombresCatalogo = Object.keys(CATALOGO_DESPENSA);
    itemForzado = nombresCatalogo[Math.floor(Math.random() * nombresCatalogo.length)];
  }
}

const openai_request = {
  model: "gpt-4o-mini",
  temperature: 1.0,
  response_format: { type: "json_object" },
  messages: [
    { role: "system", content: SYSTEM_PROMPT_EVENTO },
    {
      role: "user",
      content: JSON.stringify({
        hay_evento_forzado: hayEventoForzado,
        categoria_forzada: categoriaForzada,
        residente_forzado: residenteForzado
          ? { nombre: residenteForzado.nombre, telefono: residenteForzado.telefono, arquetipo: residenteForzado.arquetipo }
          : null,
        escenario_forzado: escenarioForzado,
        item_forzado: itemForzado,
        rango_monto_ocio: rangoMontoOcio,
        dia_semana: diaSemana,
        catalogo_despensa: Object.entries(CATALOGO_DESPENSA).map(([nombre, info]) => ({
          nombre,
          unidad: info.unidad,
        })),
      }),
    },
  ],
};

return [{ json: { body, openai_request } }];
