// ============================================================================
// Nodo "Code" de n8n (lenguaje: JavaScript) para el workflow "Cierre de Mes".
// Segundo Schedule Trigger, cada 30 minutos = 30 dias simulados = 1 mes.
// Genera el body de POST /api/ingresos/.
//
// Los `telefono` tienen que ser los MISMOS que en generador_pulso.js. n8n no
// comparte estado entre Code nodes de workflows distintos, asi que esta lista
// vive duplicada a proposito - si agregas/sacas un residente, actualizar los
// dos archivos.
// ============================================================================

// ----- RESIDENTES: ingreso base de cada uno + variacion mes a mes -----
// ingreso_base en 0 = ese residente no aporta (ej. un invitado). Si no aparece
// en el body que arma este nodo, Django usa el fallback `ingreso_mensual` de
// su fila en la base (hoy 0 para todos: sin body, no entra plata).
const RESIDENTES = [
  { nombre: "Carla", telefono: "+5491112345678", ingreso_base: 420000, variacion_pct: 0.1 },
  { nombre: "Julian", telefono: "+5491122223333", ingreso_base: 350000, variacion_pct: 0.1 },
  { nombre: "Sofia (invitada)", telefono: "+5491144445555", ingreso_base: 0, variacion_pct: 0 },
];

// ============================================================================
// MOTOR — no hace falta tocar esto para ajustar montos, eso se edita arriba.
// ============================================================================
function rand(min, max) {
  return min + Math.random() * (max - min);
}

function generarIngresos(residentes) {
  const ingresos = residentes
    .filter((r) => r.ingreso_base > 0)
    .map((r) => {
      const jitter = 1 + rand(-r.variacion_pct, r.variacion_pct);
      return { telefono: r.telefono, monto: Math.round(r.ingreso_base * jitter) };
    });
  return { ingresos };
}

// ----- openai_request: el body completo para el nodo HTTP Request "Ingreso
// inesperado (IA)" que sigue en el workflow. Armado aca por lo mismo que en
// generador_pulso.js: evitar escapar un prompt largo dentro del JSON del workflow.
const SYSTEM_PROMPT_EVENTO = `Sos un generador de eventos inesperados para el ciclo de ingresos mensuales de un hogar inteligente (SofIA). Se te informa la lista de residentes que aportan ingresos (nombre, telefono), un numero de mes, y un campo "hay_evento_forzado" (true o false) YA DECIDIDO por el sistema fuera de tu control. Tu unico trabajo es: copiar ese valor tal cual en tu campo "hay_evento" de la respuesta, y si es true, inventar el contenido del evento. No decidas vos si hay evento o no, eso ya viene resuelto.

Cuando hay_evento_forzado es true: elegi un residente al azar entre los de la lista (no siempre el mismo entre llamadas distintas). Elegi el tipo de evento SORTEANDO entre estas categorias variadas, sin repetir la misma categoria en llamadas consecutivas -- no caigas siempre en "reparacion del auto":
Ingresos extra (ajuste_monto positivo): bono laboral, changa freelance de fin de semana, venta de algo usado, regalo de un familiar, reembolso de impuestos, premio o sorteo.
Imprevistos (ajuste_monto negativo): gasto medico u odontologico, multa de transito, perdida de un cliente o changa fija, rotura de un electrodomestico personal (no de la casa), gasto de tramite o documentacion, prestamo a un amigo.
Alterná tambien entre positivo y negativo, no generes siempre el mismo signo. Variá el monto ampliamente segun que tan grande es el evento (de unos pocos miles a varias decenas de miles, en valor absoluto).

Respondes SIEMPRE con un JSON de esta forma exacta, sin texto adicional: {"hay_evento": true o false, "telefono": "<telefono de un residente de la lista, o null si hay_evento es false>", "ajuste_monto": <numero, positivo si es un ingreso extra, negativo si es un imprevisto que resta, o null si hay_evento es false>, "motivo": "<1 frase breve en espanol, o null si hay_evento es false>"}.`;

// La probabilidad se decide aca (JS), no le pedimos al LLM que "adivine" un
// porcentaje. El LLM solo rellena el contenido cuando PROB_EVENTO_INESPERADO
// dice que si.
const PROB_EVENTO_INESPERADO = 0.25; // ~1 de cada 4 meses
const hayEventoForzado = Math.random() < PROB_EVENTO_INESPERADO;

// Contador propio (independiente del de generador_pulso.js) solo de referencia
// para el prompt.
const staticData = $getWorkflowStaticData('global');
staticData.mes_numero = (staticData.mes_numero ?? 0) + 1;

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
        mes_numero: staticData.mes_numero,
        residentes: RESIDENTES.filter((r) => r.ingreso_base > 0).map((r) => ({ nombre: r.nombre, telefono: r.telefono })),
      }),
    },
  ],
};

return [{ json: { body: generarIngresos(RESIDENTES), openai_request } }];
