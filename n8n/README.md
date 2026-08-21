# Workflows de n8n

Dos workflows independientes, cada uno con su propio Schedule Trigger:

- **El Pulso** — cada 1 minuto = 1 día simulado. Simula consumo diario de despensa/servicios.
- **Cierre de Mes** — cada 30 minutos = 30 días simulados = 1 mes. Simula los ingresos de cada
  residente y renueva el presupuesto (resetea `monto_gastado` en todas las categorías).

Van por separado a propósito: el trigger de ingresos no depende del `dia_numero` real de la
simulación, así que puede desincronizarse si el Pulso se pausa o se reintenta — tenerlo en
cuenta si algún día el reset de presupuesto no coincide con el día 30/60/90 exacto.

## Importar

1. En n8n: Workflows > Import from File > `workflow_pulso.json` y `workflow_ingresos.json`.
2. En cada uno, abrir el nodo Code y reemplazar el `jsCode` de ejemplo por el contenido
   completo de [`generador_pulso.js`](generador_pulso.js) / [`generador_ingresos.js`](generador_ingresos.js) según corresponda.
3. Configurar variables de entorno de la instancia n8n (o hardcodear en el nodo HTTP Request):
   - `DJANGO_BASE_URL` — donde corre Django (ej. la URL del túnel de cloudflared).
   - `N8N_WEBHOOK_SECRET` — mismo valor que `N8N_WEBHOOK_SECRET` en el `.env` de Django.
4. En el nodo HTTP Request de cada workflow: `authentication: none` + header manual
   `X-Webhook-Secret`. No usar Basic Auth ni "Generic Credential Type" sin haber creado antes
   la credencial desde la UI de n8n (si no, el nodo tira error al ejecutar sin llegar a mandar
   el request).
5. Activar ambos workflows.

## El Pulso — cómo personalizar el escenario

Todo lo que hace falta tocar está en las primeras secciones de `generador_pulso.js`, el motor
de generación no se toca:

- **`ARQUETIPOS`** — personalidades reutilizables (cuánto tiende a estar en casa cada día de
  la semana simulada, cuánto consume cuando está). Agregar uno nuevo o ajustar los existentes.
- **`RESIDENTES`** — el elenco actual del hogar. Cada uno = nombre + teléfono + un arquetipo
  base + sus hábitos puntuales de despensa/servicios.
  - `item` en `habitos_despensa` tiene que matchear el `nombre` real de un `ItemDespensa` en
    la base para que le baje el stock.
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
      "telefono": "+5491122334455",
      "consumo_despensa": [{ "item": "Leche", "cantidad": 0.5 }],
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
    { "telefono": "+5491122334455", "monto": 420000 }
  ]
}
```

Suma los montos de todos los residentes presentes en el body (más el fallback `ingreso_mensual`
para los que no aparecen), y resetea `monto_gastado` a 0 en todas las categorías de presupuesto.
Ver `core/services/ingresos.py`.

Ambos endpoints requieren el header `X-Webhook-Secret` (401 si falta o no matchea).
