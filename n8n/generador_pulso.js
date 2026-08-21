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

// ----- RESIDENTES: el "elenco" del hogar para este escenario -----
// `telefono` es informativo hoy (Django no lo valida en /api/pulso/, solo lo
// usa para identificar quien escribe por WhatsApp en /api/comando/, y ahora
// tambien para matchear ingresos en /api/ingresos/ - ver generador_ingresos.js).
// Si lo usas para eso, tiene que matchear EXACTO el campo `telefono` de la
// tabla `residente` en Supabase.
// `item` en habitos_despensa debe coincidir con el `nombre` real de un
// ItemDespensa en la base para que efectivamente le baje el stock.
const RESIDENTES = [
  {
    nombre: "Carla",
    telefono: "+5491122334455",
    arquetipo: "home_office",
    habitos_despensa: [
      { item: "Leche", rango: [0.1, 0.4], prob: 0.6 },
      { item: "Café", rango: [0.05, 0.15], prob: 0.8 },
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
    habitos_despensa: [
      { item: "Leche", rango: [0.1, 0.3], prob: 0.4 },
      { item: "Pan", rango: [0.5, 1], prob: 0.5 },
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
    habitos_despensa: [{ item: "Papel higienico", rango: [0.2, 0.5], prob: 0.3 }],
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

function generarPulso(residentes, arquetipos, escenario, diaSemana) {
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
      if (tirar(h.prob)) {
        consumo_despensa.push({ item: h.item, cantidad: rand(h.rango[0], h.rango[1]) });
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

const body = generarPulso(RESIDENTES, ARQUETIPOS, ESCENARIO, diaSemana);
return [{ json: body }];
