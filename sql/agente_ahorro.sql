-- ============================================================================
-- Agente de Ahorro -- cambios de schema en Supabase (rama agente_ahorro)
--
-- Estas tablas son `managed = False` en Django (residente, item_despensa,
-- dispositivo, decision_log): Django NO las migra, hay que aplicar los cambios
-- a mano. La unica tabla que Django maneja es ingresos_hogar, y esa va por la
-- migracion core/0005_ingresoshogar_ahorros_and_more.py.
--
-- IMPORTANTE: correr el BLOQUE A solo y aparte, antes del resto. Postgres no
-- permite USAR un valor de enum en la misma transaccion en la que se agrega, y
-- el editor SQL de Supabase puede envolver todo en una sola transaccion.
--
-- Todo el script es idempotente: se puede correr dos veces sin romper nada.
-- ============================================================================


-- ============================================================================
-- BLOQUE A -- correr SOLO esto primero, despues el resto
-- ============================================================================

-- Habilita AGENTE_AHORRO como valor del enum nativo agente_enum, que es el tipo
-- de decision_log.id_agente. Sin esto, cada intento de guardar una decision del
-- agente nuevo falla a nivel base (el TextChoices de Django no alcanza).
ALTER TYPE agente_enum ADD VALUE IF NOT EXISTS 'AGENTE_AHORRO';


-- ============================================================================
-- BLOQUE B -- el resto, se puede correr todo junto
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. residente.telegram_id -- identidad de Telegram
-- ----------------------------------------------------------------------------
-- /api/consulta/ resuelve quien escribe por este campo. Telegram no entrega el
-- telefono: entrega un chat_id numerico y estable (a diferencia del username,
-- que el usuario puede cambiar). Nullable porque un residente puede no tener
-- Telegram vinculado; UNIQUE para que dos residentes no compartan la misma
-- cuenta (el UNIQUE ademas crea el indice que usa la busqueda).
ALTER TABLE residente
  ADD COLUMN IF NOT EXISTS telegram_id bigint;

ALTER TABLE residente
  DROP CONSTRAINT IF EXISTS residente_telegram_id_key;
ALTER TABLE residente
  ADD CONSTRAINT residente_telegram_id_key UNIQUE (telegram_id);


-- ----------------------------------------------------------------------------
-- 2. dispositivo.gustos -- un capricho no entra al ciclo de mantenimiento
-- ----------------------------------------------------------------------------
-- Una Play 5 comprada por gusto no deberia pedir service nunca. Con gustos=true
-- se permite vida_util_estimada NULL, y el Agente de Mantenimiento la ignora
-- (ver la guarda en agents/services/agente_mantenimiento.py).
ALTER TABLE dispositivo
  ADD COLUMN IF NOT EXISTS gustos boolean NOT NULL DEFAULT false;

ALTER TABLE dispositivo
  ALTER COLUMN vida_util_estimada DROP NOT NULL;

-- Postgres no tiene "nullable condicional", pero el CHECK lo expresa exacto:
-- solo una fila marcada como gusto puede tener la vida util en NULL. Asi no se
-- afloja la restriccion para el resto de los dispositivos.
ALTER TABLE dispositivo
  DROP CONSTRAINT IF EXISTS dispositivo_gustos_vida_util_check;
ALTER TABLE dispositivo
  ADD CONSTRAINT dispositivo_gustos_vida_util_check
  CHECK (gustos OR vida_util_estimada IS NOT NULL);


-- ----------------------------------------------------------------------------
-- 3. item_despensa.gustos -- un gusto no se repone automaticamente
-- ----------------------------------------------------------------------------
-- Un pan artesanal es un antojo, no un articulo de primera necesidad: se
-- consume, llega a 0, y ahi queda. Con gustos=true se permite stock_minimo NULL
-- y el Agente de Despensa lo saltea (ver la guarda en agente_despensa.py).
--
-- Nota: el CHECK que ya existe (stock_minimo >= 0) NO estorba. En SQL un CHECK
-- solo falla cuando la expresion da FALSE, y `NULL >= 0` da NULL, que pasa.
-- Por eso no hace falta tocarlo.
--
-- Nota 2: consumo_promedio_diario queda NOT NULL a proposito. avanzar_dia()
-- hace `stock_actual - consumo_promedio_diario` sobre TODOS los items; si fuera
-- NULL, la resta rompe el Pulso entero. Un gusto va con 0 o con su consumo real.
ALTER TABLE item_despensa
  ADD COLUMN IF NOT EXISTS gustos boolean NOT NULL DEFAULT false;

ALTER TABLE item_despensa
  ALTER COLUMN stock_minimo DROP NOT NULL;

ALTER TABLE item_despensa
  DROP CONSTRAINT IF EXISTS item_despensa_gustos_stock_minimo_check;
ALTER TABLE item_despensa
  ADD CONSTRAINT item_despensa_gustos_stock_minimo_check
  CHECK (gustos OR stock_minimo IS NOT NULL);


-- ============================================================================
-- VERIFICACION -- correr despues para confirmar que quedo todo aplicado
-- ============================================================================
-- Esperado: 5 filas, todas con ok = true.

SELECT 'agente_enum tiene AGENTE_AHORRO' AS chequeo,
       EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'agente_enum' AND e.enumlabel = 'AGENTE_AHORRO') AS ok
UNION ALL
SELECT 'residente.telegram_id existe',
       EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'residente' AND column_name = 'telegram_id')
UNION ALL
SELECT 'dispositivo.gustos existe + vida_util nullable',
       EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'dispositivo' AND column_name = 'gustos')
       AND (SELECT is_nullable = 'YES' FROM information_schema.columns
            WHERE table_name = 'dispositivo' AND column_name = 'vida_util_estimada')
UNION ALL
SELECT 'item_despensa.gustos existe + stock_minimo nullable',
       EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'item_despensa' AND column_name = 'gustos')
       AND (SELECT is_nullable = 'YES' FROM information_schema.columns
            WHERE table_name = 'item_despensa' AND column_name = 'stock_minimo')
UNION ALL
SELECT 'los 2 CHECK de gustos estan creados',
       (SELECT count(*) = 2 FROM pg_constraint
        WHERE conname IN ('dispositivo_gustos_vida_util_check',
                          'item_despensa_gustos_stock_minimo_check'));
