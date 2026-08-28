# Cambios aplicados — residentes, ingresos y Agente de Mantenimiento

Documentación de lo agregado en la rama `test` sobre el trabajo del Agente de Ahorro:
sincronización de residentes, persistencia de ingresos, y el rediseño completo del Agente
de Mantenimiento (service rutinario vs. reemplazo por fin de vida útil).

Commits: `a458b84` (corrección en agente de mantenimiento) y `42e2fe1` (script SQL).

## 1. `Residente.ingreso_mensual` ahora se actualiza solo

**Problema:** `core/services/ingresos.py::cerrar_mes()` leía `residente.ingreso_mensual`
como fallback, pero nunca lo escribía. El monto real que manda n8n en el body de
`/api/ingresos/` quedaba solo en `DecisionLog.detalles_payload`, así que el campo en la
tabla `residente` estaba siempre en 0.

**Fix:** en cada cierre de mes, si el `telefono` del residente aparece en el body de n8n,
ese monto se guarda en `residente.ingreso_mensual` además de usarse para el cierre — ver
[core/services/ingresos.py](../core/services/ingresos.py).

## 2. Residentes sincronizados entre la base y n8n

La base (Supabase, tabla `residente`) y los scripts de n8n (`n8n/generador_pulso.js`,
`n8n/generador_ingresos.js`) tenían nombres distintos para el mismo teléfono, y un
residente (Estefania Arrieta) sin ningún hábito de consumo definido en n8n.

Quedaron 7 residentes, todos con hábitos de despensa/servicios propios en
`generador_pulso.js` y con `ingreso_base` en `generador_ingresos.js`:

| Nombre | Teléfono | Arquetipo | Nivel |
|---|---|---|---|
| Gonzalo Lopez | `+5491112345678` | home_office | ADMIN |
| Co-residente | `+5491122223333` | oficina_9_18 | RESIDENTE |
| Visita Frecuente | `+5491144445555` | estudiante | INVITADO |
| Estefania Arrieta | `987546321` | home_office | ADMIN |
| Julieta Ruppert | `+5491155556666` | oficina_9_18 | RESIDENTE |
| Carlos Ruminott | `+5491166667777` | estudiante | RESIDENTE |
| Juan Cruz Olivera | `+5491177778888` | viajante_frecuente | RESIDENTE |

Los `ingreso_base` cargados en `generador_ingresos.js` son placeholders — ajustables
directamente en ese archivo, no hace falta tocar Django.

## 3. Agente de Mantenimiento: service rutinario vs. reemplazo

### El problema que resuelve

Antes, degradación ≥90% disparaba siempre la misma acción (`AGENDAR_SERVICE`, costo fijo
global de $5.000) y **nada volvía a poner el dispositivo en `OPERATIVO`** — quedaba
colgado en `REQUIERE_SERVICE` para siempre, a menos que alguien lo reemplazara a mano vía
el Agente de Ahorro.

### Diseño: dos relojes independientes por dispositivo

- **`fecha_instalacion`** + `vida_util_estimada` → cuándo toca **REEMPLAZO** (se cumplió
  toda la vida útil). `fecha_instalacion` es el rename de `fecha_ultimo_service`: nunca
  significó "última vez que le hicieron un service", sino "fecha de alta o de último
  reemplazo" — nada más la tocaba. Se renombró para que el nombre no confunda con el
  siguiente campo.
- **`fecha_ultima_revision`** + **`dias_entre_service`** (campos nuevos) → cuándo toca un
  **SERVICE_RUTINARIO** (mantenimiento periódico). No reinicia el reloj de vida útil.

Si se cumplen los dos plazos el mismo día, gana el reemplazo (resetea las dos fechas).

```
dias desde fecha_instalacion >= vida_util_estimada        → REEMPLAZO
dias desde fecha_ultima_revision >= dias_entre_service     → SERVICE_RUTINARIO
```
Ver [agents/services/agente_mantenimiento.py](../agents/services/agente_mantenimiento.py),
función `determinar_accion()`.

### Todo se resuelve en el mismo Pulso

`_procesar_mantenimiento()` en
[agents/services/orquestador.py](../agents/services/orquestador.py) ya no deja nada
pendiente:

1. Cobra `dispositivo.costo_reemplazo` o `dispositivo.costo_service` (según la acción)
   contra el presupuesto de Mantenimiento, con la misma lógica de saldo → ahorros → deuda
   que el resto del sistema. Sin fondos: se rechaza y se reintenta el próximo Pulso.
2. Si es reemplazo: resetea `fecha_instalacion` y `fecha_ultima_revision` a hoy.
   Si es service rutinario: resetea solo `fecha_ultima_revision`.
3. En los dos casos, el dispositivo vuelve a `estado_actual = OPERATIVO`.
4. Log nuevo: `DISPOSITIVO_REEMPLAZADO` o `SERVICE_RUTINARIO_REALIZADO` (antes solo existía
   `AGENDAR_SERVICE`).

### Campos nuevos en `dispositivo` (Supabase)

Aplicados a mano vía [sql/agente_mantenimiento.sql](../sql/agente_mantenimiento.sql) (igual
criterio que `sql/agente_ahorro.sql`: la tabla es `managed=False`, Django no la migra).

| Campo | Tipo | Nullable |
|---|---|---|
| `dias_entre_service` | integer | Solo si `gustos=True` |
| `costo_service` | numeric(12,2) | Solo si `gustos=True` |
| `costo_reemplazo` | numeric(12,2) | Solo si `gustos=True` |
| `fecha_ultima_revision` | date | Sí |

Mismo CHECK que ya existía para `vida_util_estimada`: un gusto (ej. una consola) no tiene
ciclo de mantenimiento, así que esos 3 campos van en NULL solo para él.

Valores cargados para los 8 dispositivos precargados: ver el script SQL — son
placeholders ajustables desde el admin de Django, no plata real.

### Bug que se encontró y se corrigió de paso

El flujo de compra por Telegram (`_dar_de_alta_compra`, en `orquestador.py`) creaba
dispositivos nuevos sin cargar los 3 campos obligatorios → violaba el CHECK constraint y
rompía la compra. Se resolvió calculándolos en código (mismo criterio que el resto del
archivo: los montos los decide el código, no el LLM), en
`agente_ahorro._sanear_parametros()`:

- `costo_reemplazo` = el precio que se acaba de pagar.
- `costo_service` = 10% de ese precio.
- `dias_entre_service` = vida útil / 4, con un piso de 30 días.

## Cómo resetear la simulación a día 0

No hay un fixture con el estado original — esto define un estado inicial nuevo, prolijo,
no restaura el real:

- `EstadoSimulacion`: `dia_numero=1`, `fecha_actual=hoy`.
- `DecisionLog` y `ConsumoLog`: se borran todos.
- `IngresosHogar`: `saldo_disponible`, `ahorros`, `deuda` en 0.
- `Presupuesto`: `monto_gastado=0` en todas las categorías (los límites no cambian).
- `Dispositivo` (no gustos): `estado_actual=OPERATIVO`, `fecha_instalacion` y
  `fecha_ultima_revision` = hoy.
- `ItemDespensa`: no gustos → `stock_actual = 2 × stock_minimo`; gustos → `stock_actual = 0`.
- `Residente`: no se toca.

Se corrió una vez desde `manage.py shell` en una transacción atómica; no quedó como
comando reutilizable en el repo.
