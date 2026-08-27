-- ============================================================================
-- Agente de Mantenimiento -- service rutinario vs. reemplazo por fin de vida util
--
-- `dispositivo` es managed=False en Django: Django NO la migra, hay que aplicar
-- los cambios a mano (mismo criterio que sql/agente_ahorro.sql).
--
-- Dos relojes independientes por dispositivo:
-- - fecha_instalacion + vida_util_estimada (fecha_instalacion es el rename de
--   fecha_ultimo_service -- nunca fue "fecha de la ultima visita de service",
--   es la fecha de alta o de ultimo reemplazo) -> cuando toca REEMPLAZO (se
--   cumplio toda la vida util).
-- - fecha_ultima_revision + dias_entre_service (nuevos) -> cuando toca un
--   SERVICE RUTINARIO (mantenimiento periodico, no reinicia el reloj de
--   reemplazo).
--
-- Todo el script es idempotente: se puede correr dos veces sin romper nada.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0. Rename -- fecha_ultimo_service nunca significo "ultimo service", siempre
--    fue "fecha de alta/reemplazo" (nada mas la tocaba). Se renombra para que
--    el nombre no confunda con fecha_ultima_revision, que si es de service.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'dispositivo' AND column_name = 'fecha_ultimo_service'
  ) THEN
    ALTER TABLE dispositivo RENAME COLUMN fecha_ultimo_service TO fecha_instalacion;
  END IF;
END $$;


-- ----------------------------------------------------------------------------
-- 1. Columnas nuevas
-- ----------------------------------------------------------------------------
ALTER TABLE dispositivo
  ADD COLUMN IF NOT EXISTS dias_entre_service integer,
  ADD COLUMN IF NOT EXISTS costo_service numeric(12, 2),
  ADD COLUMN IF NOT EXISTS costo_reemplazo numeric(12, 2),
  ADD COLUMN IF NOT EXISTS fecha_ultima_revision date;

-- Backfill: fecha_ultima_revision arranca en fecha_instalacion para que el
-- primer Pulso con esta logica no dispare un service rutinario espurio en
-- todos los dispositivos el mismo dia.
UPDATE dispositivo
SET fecha_ultima_revision = fecha_instalacion
WHERE fecha_ultima_revision IS NULL
  AND NOT gustos;


-- ----------------------------------------------------------------------------
-- 2. Valores para los dispositivos precargados (tiene que correr ANTES del
--    CHECK del paso 3: hasta aca las filas no-gusto todavia tienen NULL)
-- ----------------------------------------------------------------------------
-- Placeholders calibrados contra la escala del sistema (limite mensual de la
-- categoria Mantenimiento: $80.000; el service plano viejo era $5.000). Se
-- pueden ajustar despues desde el admin de Django, esto no es plata real.
UPDATE dispositivo SET dias_entre_service = 90,  costo_service = 4000,  costo_reemplazo = 25000 WHERE nombre = 'Aire Acondicionado Living';
UPDATE dispositivo SET dias_entre_service = 120, costo_service = 3500,  costo_reemplazo = 22000 WHERE nombre = 'Termotanque Eléctrico';
UPDATE dispositivo SET dias_entre_service = 45,  costo_service = 2500,  costo_reemplazo = 18000 WHERE nombre = 'Robot Aspiradora';
UPDATE dispositivo SET dias_entre_service = 180, costo_service = 3000,  costo_reemplazo = 20000 WHERE nombre = 'Bomba de Agua';
UPDATE dispositivo SET dias_entre_service = 90,  costo_service = 4500,  costo_reemplazo = 35000 WHERE nombre = 'Heladera Inverter';
UPDATE dispositivo SET dias_entre_service = 300, costo_service = 3000,  costo_reemplazo = 30000 WHERE nombre = 'pc de última generación';
UPDATE dispositivo SET dias_entre_service = 365, costo_service = 1500,  costo_reemplazo = 12000 WHERE nombre = 'microfono';
UPDATE dispositivo SET dias_entre_service = 60,  costo_service = 3500,  costo_reemplazo = 20000 WHERE nombre = 'Lavarropas';


-- ----------------------------------------------------------------------------
-- 3. CHECK -- recien aca, con todas las filas no-gusto ya rellenas
-- ----------------------------------------------------------------------------
-- Mismo patron que dispositivo_gustos_vida_util_check: un gusto no tiene
-- ciclo de mantenimiento, asi que estos 3 campos van en NULL solo para el.
ALTER TABLE dispositivo
  DROP CONSTRAINT IF EXISTS dispositivo_gustos_mantenimiento_check;
ALTER TABLE dispositivo
  ADD CONSTRAINT dispositivo_gustos_mantenimiento_check
  CHECK (
    gustos
    OR (dias_entre_service IS NOT NULL
        AND costo_service IS NOT NULL
        AND costo_reemplazo IS NOT NULL)
  );


-- ----------------------------------------------------------------------------
-- 4. Limpieza de estados colgados del sistema viejo
-- ----------------------------------------------------------------------------
-- Con el diseno anterior, REQUIERE_SERVICE/EN_MANTENIMIENTO/
-- WAITING_HUMAN_APPROVAL quedaban pegados para siempre (nada los resolvia).
-- Con el nuevo diseno todo se resuelve solo dentro del mismo Pulso, asi que
-- estos 3 estados dejan de tener sentido como "estado de espera": se
-- normalizan a OPERATIVO para que el primer Pulso los evalue limpio.
UPDATE dispositivo
SET estado_actual = 'OPERATIVO'
WHERE estado_actual IN ('REQUIERE_SERVICE', 'EN_MANTENIMIENTO', 'WAITING_HUMAN_APPROVAL')
  AND NOT gustos;


-- ============================================================================
-- VERIFICACION -- correr despues para confirmar que quedo todo aplicado
-- ============================================================================
-- Esperado: 2 filas, ambas con ok = true.

SELECT 'columnas nuevas existen + CHECK creado' AS chequeo,
       (SELECT count(*) = 4 FROM information_schema.columns
        WHERE table_name = 'dispositivo'
          AND column_name IN ('dias_entre_service', 'costo_service', 'costo_reemplazo', 'fecha_ultima_revision'))
       AND (SELECT count(*) = 1 FROM pg_constraint
            WHERE conname = 'dispositivo_gustos_mantenimiento_check') AS ok
UNION ALL
SELECT 'ningun dispositivo (no-gusto) quedo sin los 3 campos',
       NOT EXISTS (
         SELECT 1 FROM dispositivo
         WHERE NOT gustos
           AND (dias_entre_service IS NULL OR costo_service IS NULL OR costo_reemplazo IS NULL)
       );
