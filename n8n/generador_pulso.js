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
const SYSTEM_PROMPT_EVENTO = `Sos un generador de eventos inesperados para la simulacion de un hogar inteligente (SofIA). Se te informa el dia de la semana simulado (0=domingo..6=sabado), la lista de residentes de la casa (nombre, telefono, arquetipo de personalidad), el catalogo de items de despensa conocidos por el sistema con su unidad de medida, y un campo "hay_evento_forzado" (true o false) YA DECIDIDO por el sistema fuera de tu control. Tu unico trabajo es: copiar ese valor tal cual en tu campo "hay_evento" de la respuesta, y si es true, inventar el contenido del evento -- una compra o consumo que no es parte de la rutina normal de nadie, con un motivo breve y creible. No decidas vos si hay evento o no, eso ya viene resuelto. Cuando hay_evento_forzado es true, variá el tipo cada vez (no repitas siempre lo mismo entre llamadas distintas): a veces una visita que sube el consumo de agua o luz, a veces alguien compra algo puntual que no es su habito, a veces un gasto de emergencia; y elegi un residente distinto cada vez que puedas, no siempre el mismo. Si el evento involucra un producto de despensa, usa exactamente uno de los nombres del catalogo (respetando su unidad de medida al elegir la cantidad) para que efectivamente impacte el stock; si es otra cosa (una visita, un gasto de streaming, etc.), consumo_despensa puede quedar vacio y el evento se refleja solo en el motivo y/o en consumo_servicios. Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional: {"hay_evento": true o false, "telefono": "<telefono de un residente de la lista, o null si hay_evento es false>", "consumo_despensa": [{"item": "<nombre exacto del catalogo>", "cantidad": <numero en la unidad de ese item>}], "consumo_servicios": {"AGUA": <numero opcional>, "LUZ": <numero opcional>, "GAS": <numero opcional>}, "motivo": "<1 frase breve en espanol, o null si hay_evento es false>"}. Si hay_evento es false, consumo_despensa tiene que ser un array vacio y consumo_servicios un objeto vacio.`;

// La probabilidad se decide aca (JS), no le pedimos al LLM que "adivine" un
// porcentaje - es mas preciso y mas barato (no gasta tokens de mas intentando
// calibrar una frecuencia via prompt). El LLM solo rellena el contenido cuando
// PROB_EVENTO_INESPERADO dice que si.
const PROB_EVENTO_INESPERADO = 0.2; // ~1 de cada 5 pulsos
const hayEventoForzado = Math.random() < PROB_EVENTO_INESPERADO;

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
        dia_semana: diaSemana,
        residentes: RESIDENTES.map((r) => ({ nombre: r.nombre, telefono: r.telefono, arquetipo: r.arquetipo })),
        catalogo_despensa: Object.entries(CATALOGO_DESPENSA).map(([nombre, info]) => ({
          nombre,
          unidad: info.unidad,
        })),
      }),
    },
  ],
};

return [{ json: { body, openai_request } }];
