# Workflows de n8n

no Tres workflows independientes. Los dos primeros **simulan el paso del tiempo** (Schedule
Trigger, 4 nodos en cadena: **Code (generador determinístico) → HTTP Request (evento
inesperado, IA) → Code (combinar) → HTTP Request (POST a Django)**). El tercero es
distinto: **reacciona a una persona**, no al reloj.

- **El Pulso** — cada 1 minuto = 1 día simulado. Simula consumo diario de despensa/servicios.
- **Cierre de Mes** — cada 30 minutos = 30 días simulados = 1 mes. Simula los ingresos de cada
  residente y renueva el presupuesto (resetea `monto_gastado` en todas las categorías).
- **Consulta de Compra** — Telegram Trigger. Un residente pregunta si conviene una compra y
  el Agente de Ahorro le responde en el mismo chat. Solo 3 nodos y **sin nodo Code**: no hace
  falta generar nada ni llamar a OpenAI desde n8n, porque todo el razonamiento (y las dos
  llamadas al LLM) vive en Django.

Van por separado a propósito: el trigger de ingresos no depende del `dia_numero` real de la
simulación, así que puede desincronizarse si el Pulso se pausa o se reintenta — tenerlo en
cuenta si algún día el reset de presupuesto no coincide con el día 30/60/90 exacto.

## Los 3 residentes reales (Supabase) vs. los nombres "de fantasía" en n8n

`RESIDENTES` en `generador_pulso.js` y `generador_ingresos.js` usa nombres amigables (Carla,
Julian, Sofia) para que los motivos que redacta la IA suenen naturales, pero el campo
`telefono` de cada uno tiene que ser EXACTO al de la tabla `residente` en Supabase — si no
matchea, `/api/ingresos/` no le suma el monto a nadie (da `total_ingresos: 0` sin avisar) y
`/api/comando/` no reconoce al residente. El mapeo actual:

| n8n (nombre ficticio) | Supabase (`residente.nombre`) | `telefono` |
|---|---|---|
| Carla | Administrador Hogar | `+5491112345678` |
| Julian | Co-residente | `+5491122223333` |
| Sofia (invitada) | Visita Frecuente | `+5491144445555` |

Si en algún momento cambian los residentes reales en Supabase (otros teléfonos, más o menos
gente), hay que actualizar el `telefono` en los dos archivos para que sigan matcheando.

## Importar

1. En n8n: Workflows > Import from File > `workflow_pulso.json`, `workflow_ingresos.json` y
   `workflow_consulta.json`.
2. En cada uno, abrir el primer nodo Code ("Generar consumo simulado" / "Generar ingresos del
   mes") y reemplazar el `jsCode` de ejemplo por el contenido completo de
   [`generador_pulso.js`](generador_pulso.js) / [`generador_ingresos.js`](generador_ingresos.js)
   según corresponda.
3. Abrir el segundo nodo Code ("Combinar evento con el body base" / "Combinar ingreso
   inesperado") y pegar [`combinar_evento_pulso.js`](combinar_evento_pulso.js) /
   [`combinar_evento_ingresos.js`](combinar_evento_ingresos.js).
4. Configurar variables de entorno de la instancia n8n (o hardcodear en los nodos HTTP Request):
   - `DJANGO_BASE_URL` — donde corre Django (ej. la URL del túnel de cloudflared).
   - `N8N_WEBHOOK_SECRET` — mismo valor que `N8N_WEBHOOK_SECRET` en el `.env` de Django.
   - `OPENAI_API_KEY` — tu API key de OpenAI (puede ser la misma que usa Django, o una aparte).
5. En los 4 nodos HTTP Request: `authentication: none` + header manual (`X-Webhook-Secret` para
   los que van a Django, `Authorization: Bearer ...` para los que van a OpenAI). No usar Basic
   Auth ni "Generic Credential Type" sin haber creado antes la credencial desde la UI de n8n (si
   no, el nodo tira error al ejecutar sin llegar a mandar el request).
6. Solo para **Consulta de Compra**: abrir los nodos `Mensaje de Telegram` y `Responder en el
   chat` y elegir la credencial de Telegram (la misma en los dos). Los pasos 2 y 3 no aplican
   a este workflow, porque no tiene nodos Code.
7. Activar los tres workflows.

## Consulta de Compra — el único que responde a una persona

```
Mensaje de Telegram ──► POST /api/consulta/ ──► Responder en el chat
```

El residente escribe libre (*"puedo comprar una play 5 que sale 900000?"*), Django decide, y la
respuesta vuelve **en el body del POST** (campo `respuesta`), que el tercer nodo manda de
vuelta al chat. Por eso este flujo no usa un webhook saliente: el residente lee la respuesta
en el mismo hilo donde preguntó.

Cosas a tener en cuenta al armarlo:

- **Hace falta una credencial de Telegram** (un bot creado con @BotFather). Es lo único que no
  se puede dejar resuelto en el JSON: la credencial se elige desde la UI de n8n.
- **El `telegram_id` de cada residente tiene que estar cargado** en la tabla `residente` de
  Supabase. Si no, Django responde `RESIDENTE_DESCONOCIDO` y no evalúa nada. El id lo da
  @userinfobot en Telegram.
- **El precio es obligatorio en el mensaje.** Si no lo trae, Django devuelve
  `CONSULTA_INCOMPLETA` y la respuesta le pide al residente que lo aclare. El LLM tiene
  prohibido estimar precios: ese número se le descuenta de verdad a la billetera del hogar.
- **El trigger se dispara con cualquier mensaje al bot**, no solo con consultas de compra. Un
  "hola" termina contestado con el pedido de que aclare el precio. Si molesta, se resuelve
  agregando un nodo IF antes del HTTP Request. Un mensaje sin texto (una foto, un sticker) se
  manda como `(sin texto)` y cae por el mismo camino, así que no rompe la ejecución.

## El paso de IA — "Evento inesperado"

Entre el generador determinístico y el POST a Django se agregó un paso que hace que la
simulación no sea siempre la misma rutina mecánica: cada corrida, hay una probabilidad chica
(configurable) de que pase algo fuera de lo común — una visita que sube el consumo, un gasto
puntual, un bono, un imprevisto — y un modelo de OpenAI lo redacta con un motivo creíble.

Un detalle de diseño importante: **la probabilidad la decide el JS, no la IA**. Pedirle a un
LLM que "solo el 20% de las veces" diga que sí es poco confiable en la práctica (se probó y el
modelo termina disparando eventos con una frecuencia bastante distinta a la pedida). Entonces el
generador tira su propio dado (`PROB_EVENTO_INESPERADO` en `generador_pulso.js` /
`generador_ingresos.js`) y le manda a la IA el resultado ya decidido (`hay_evento_forzado`); el
único trabajo de la IA es respetar ese valor y, si es `true`, inventar el contenido (a quién le
pasa, qué pasó, cuánto). Esto también ahorra tokens: se prueba la probabilidad gratis en JS antes
de llamar a la API. Coincide además con el mismo patrón que ya usan los agentes de Django
(`agents/services/*.py`): trigger determinístico primero, LLM solo para la parte creativa.

Para ajustar qué tan seguido pasan cosas: `PROB_EVENTO_INESPERADO` (0.2 en Pulso, 0.25 en
Cierre de Mes). Para ajustar qué tan creativo/variado es el resultado: `temperature` dentro de
`openai_request` (ya en 1.0, el máximo recomendado para no perder coherencia). Si la IA no
responde o devuelve algo inválido, el nodo Combinar lo ignora silenciosamente y sigue con el
body base — una IA caída no tiene que tumbar el Pulso.

## El Pulso — cómo personalizar el escenario

Todo lo que hace falta tocar está en las primeras secciones de `generador_pulso.js`, el motor
de generación no se toca:

- **`ARQUETIPOS`** — personalidades reutilizables (cuánto tiende a estar en casa cada día de
  la semana simulada, cuánto consume cuando está). Agregar uno nuevo o ajustar los existentes.
- **`CATALOGO_DESPENSA`** — la fuente única de productos reales. Cada clave tiene que matchear
  EXACTO (Django compara en minúsculas) el `nombre` de un `ItemDespensa` en Supabase, más su
  `unidad` (informativa, se la mandamos también a la IA) y un `rango_tipico` de consumo diario
  por defecto. **Para agregar un producto nuevo:** 1) crearlo en Supabase con ese nombre exacto
  (y su `unidad_medida`); 2) agregar una entrada acá; 3) referenciarlo desde `habitos_despensa`
  del/los residente(s) que lo consuman. Si un residente referencia un ítem que no está en el
  catálogo, el script tira un error explícito al ejecutar en vez de fallar en silencio — así
  se detectó y corrigió que "Leche"/"Café"/"Pan" no matcheaban ningún nombre real y la
  personalización de despensa nunca había estado funcionando.
- **`RESIDENTES`** — el elenco actual del hogar. Cada uno = nombre + teléfono + un arquetipo
  base + sus hábitos puntuales. `habitos_despensa` es una lista de `{ item, prob, rango? }`
  donde `item` es una clave de `CATALOGO_DESPENSA`; `rango` es opcional, si no se especifica
  usa el `rango_tipico` del catálogo.
  - `telefono` identifica al residente en `/api/comando/` y en `/api/ingresos/` (ver abajo) —
    tiene que matchear exacto el campo `telefono` de la tabla `residente` en Supabase.
- **`ESCENARIO`** — override puntual para forzar un caso a demanda (fuga de agua para gatillar
  CU-02, alguien de viaje, una "junta" que dispara consumo alto). Ejemplos comentados en el
  propio archivo.

### Por qué el día de la semana no usa la fecha real

1 minuto real = 1 día simulado, así que la fecha real prácticamente no avanza durante una demo
corta. El script lleva su propio contador de pulsos (`$getWorkflowStaticData`) y deriva el día
de semana simulado de ahí (`contador % 7`), para que las rutinas semanales de cada residente sí
se noten en una corrida de minutos.

## Cierre de Mes — cómo personalizar los ingresos

`generador_ingresos.js` tiene su propio array `RESIDENTES` (mismos teléfonos que en
`generador_pulso.js` — n8n no comparte estado entre Code nodes de workflows distintos, así que
esta lista vive duplicada a propósito). Cada uno define `ingreso_base` y `variacion_pct` (una
variación aleatoria mes a mes para que no sea siempre el mismo número exacto). Un residente con
`ingreso_base: 0` no aporta nada (ej. un invitado) y directamente no entra en el body.

Un residente que **no aparece en el body** (porque no está en este array, o porque
`ingreso_base` es 0) cae al fallback `ingreso_mensual` cargado en su fila de la tabla
`residente` en Supabase — hoy en `0` para los tres residentes reales, así que si este nodo no
manda nada para alguien, ese mes no entra plata para esa persona.

## Contratos de Django

```json
POST /api/pulso/
{
  "residentes_en_casa": [
    {
      "telefono": "+5491112345678",
      "consumo_despensa": [{ "item": "Leche Entera 1L", "cantidad": 0.5 }],
      "consumo_servicios": { "AGUA": 3.2, "LUZ": 1.1 }
    }
  ]
}
```

Un ítem o servicio que no aparece en el body cae al comportamiento baseline de siempre
(consumo_promedio_diario / valor random). Ver `core/services/simulacion.py`.

```json
POST /api/ingresos/
{
  "ingresos": [
    { "telefono": "+5491112345678", "monto": 420000 }
  ]
}
```

Suma los montos de todos los residentes presentes en el body (más el fallback `ingreso_mensual`
para los que no aparecen), y resetea `monto_gastado` a 0 en todas las categorías de presupuesto.
Ver `core/services/ingresos.py`.

```json
POST /api/consulta/
{
  "telegram_id": 6079531003,
  "mensaje": "puedo comprar una play 5 que sale 900000?"
}
```

Responde `{"procesado": bool, "resultado": "...", "respuesta": "<texto para el chat>", ...}`,
donde `resultado` es `COMPRA_APROBADA`, `COMPRA_RECHAZADA`, `CONSULTA_INCOMPLETA` o
`RESIDENTE_DESCONOCIDO`. Ver `agents/services/agente_ahorro.py`.

Los tres endpoints requieren el header `X-Webhook-Secret` (401 si falta o no matchea).
