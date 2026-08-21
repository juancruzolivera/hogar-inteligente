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
  { nombre: "Carla", telefono: "+5491122334455", ingreso_base: 420000, variacion_pct: 0.1 },
  { nombre: "Julian", telefono: "+5491133445566", ingreso_base: 350000, variacion_pct: 0.1 },
  { nombre: "Sofia (invitada)", telefono: "+5491144556677", ingreso_base: 0, variacion_pct: 0 },
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

return [{ json: generarIngresos(RESIDENTES) }];
