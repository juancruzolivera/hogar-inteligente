// ============================================================================
// Nodo "Code" de n8n: combina el body base (de generador_ingresos.js) con el
// evento inesperado que devolvio el nodo de IA anterior, y arma el body final
// que se manda a POST /api/ingresos/.
//
// Va DESPUES del nodo HTTP Request "Ingreso inesperado (IA)" en el workflow.
// ============================================================================

const base = $('Generar ingresos del mes').item.json.body;

let evento = { hay_evento: false };
try {
  evento = JSON.parse($json.choices[0].message.content);
} catch (e) {
  // IA caida o respuesta invalida: seguimos sin evento, no se rompe el cierre de mes.
}

if (evento.hay_evento && evento.telefono && evento.ajuste_monto) {
  const residente = base.ingresos.find((r) => r.telefono === evento.telefono);
  if (residente) {
    residente.monto = Math.max(0, residente.monto + evento.ajuste_monto);
  } else if (evento.ajuste_monto > 0) {
    // Alguien con ingreso_base 0 (ej. un invitado) tuvo un ingreso puntual este mes.
    base.ingresos.push({ telefono: evento.telefono, monto: evento.ajuste_monto });
  }
}

return [{ json: base }];
