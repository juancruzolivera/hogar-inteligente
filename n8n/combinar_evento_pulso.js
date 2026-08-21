// ============================================================================
// Nodo "Code" de n8n: combina el body base (de generador_pulso.js) con el
// evento inesperado que devolvio el nodo de IA anterior, y arma el body final
// que se manda a POST /api/pulso/.
//
// Va DESPUES del nodo HTTP Request "Evento inesperado (IA)" en el workflow.
// Referencia el output del nodo Code anterior por nombre porque el nodo de IA
// que esta en el medio pisa el $json actual con la respuesta cruda de OpenAI.
// ============================================================================

const base = $('Generar consumo simulado').item.json.body;

let evento = { hay_evento: false };
try {
  evento = JSON.parse($json.choices[0].message.content);
} catch (e) {
  // Si la IA devuelve algo invalido o la llamada fallo, seguimos sin evento:
  // una IA caida no puede tumbar el Pulso (mismo criterio que las notificaciones
  // salientes de Django hacia n8n).
}

if (evento.hay_evento && evento.telefono) {
  let residente = base.residentes_en_casa.find((r) => r.telefono === evento.telefono);
  if (!residente) {
    residente = { telefono: evento.telefono, consumo_despensa: [], consumo_servicios: {} };
    base.residentes_en_casa.push(residente);
  }

  for (const item of evento.consumo_despensa ?? []) {
    residente.consumo_despensa.push(item);
  }
  for (const [tipo, cantidad] of Object.entries(evento.consumo_servicios ?? {})) {
    residente.consumo_servicios[tipo] = (residente.consumo_servicios[tipo] ?? 0) + cantidad;
  }
}

return [{ json: base }];
