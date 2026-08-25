import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.db import models


class NivelPermiso(models.TextChoices):
    ADMIN = "ADMIN"
    RESIDENTE = "RESIDENTE"
    INVITADO = "INVITADO"


class TipoServicio(models.TextChoices):
    AGUA = "AGUA"
    LUZ = "LUZ"
    GAS = "GAS"


class EstadoDispositivo(models.TextChoices):
    OPERATIVO = "OPERATIVO"
    REQUIERE_SERVICE = "REQUIERE_SERVICE"
    EN_MANTENIMIENTO = "EN_MANTENIMIENTO"
    WAITING_HUMAN_APPROVAL = "WAITING_HUMAN_APPROVAL"
    FUERA_DE_SERVICIO = "FUERA_DE_SERVICIO"


class Residente(models.Model):
    id_residente = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre = models.CharField(max_length=255)
    telefono = models.CharField(max_length=32, unique=True)
    # Identidad de Telegram: chat_id numerico, que es estable (el username lo puede
    # cambiar el usuario). Lo usa /api/consulta/ para saber quien pregunta.
    telegram_id = models.BigIntegerField(null=True, blank=True, unique=True)
    nivel_permiso = models.CharField(max_length=16, choices=NivelPermiso.choices)
    ingreso_mensual = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "residente"

    def __str__(self):
        return self.nombre


class Presupuesto(models.Model):
    id_presupuesto = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    categoria = models.CharField(max_length=100, unique=True)
    limite_mensual = models.DecimalField(max_digits=12, decimal_places=2)
    monto_gastado = models.DecimalField(max_digits=12, decimal_places=2)
    es_esencial = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "presupuesto"

    def __str__(self):
        return self.categoria

    @property
    def saldo_disponible(self):
        return self.limite_mensual - self.monto_gastado


class ItemDespensa(models.Model):
    id_item = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre = models.CharField(max_length=255)
    unidad_medida = models.CharField(max_length=20, blank=True, default="")
    stock_actual = models.DecimalField(max_digits=12, decimal_places=2)
    # Nullable solo cuando gustos=True (lo garantiza un CHECK en la base, ver
    # sql/agente_ahorro.sql): un antojo no tiene stock minimo que sostener.
    stock_minimo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    consumo_promedio_diario = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    precio_estimado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Un "gusto" es un antojo puntual (un pan artesanal), no un articulo de primera
    # necesidad: se consume, llega a 0 y ahi queda. El Agente de Despensa lo saltea
    # para no reponerlo indefinidamente (ver agente_despensa.detectar_items_criticos).
    gustos = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    presupuesto = models.ForeignKey(
        Presupuesto,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="id_presupuesto",
        related_name="items_despensa",
    )

    class Meta:
        managed = False
        db_table = "item_despensa"

    def __str__(self):
        return self.nombre


class Dispositivo(models.Model):
    id_dispositivo = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre = models.CharField(max_length=255)
    prioridad = models.IntegerField()
    fecha_ultimo_service = models.DateField(null=True, blank=True)
    # Nullable solo cuando gustos=True (lo garantiza un CHECK en la base, ver
    # sql/agente_ahorro.sql): un capricho no tiene vida util que agotarse.
    vida_util_estimada = models.IntegerField(null=True, blank=True)
    estado_actual = models.CharField(max_length=32, choices=EstadoDispositivo.choices)
    # Un "gusto" es un capricho (una consola, por ejemplo) que no entra al ciclo de
    # mantenimiento del hogar: no se degrada ni se le agenda service nunca.
    gustos = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "dispositivo"

    def __str__(self):
        return self.nombre


class ConsumoLog(models.Model):
    id_log = models.BigAutoField(primary_key=True)
    timestamp = models.DateTimeField()
    tipo_servicio = models.CharField(max_length=16, choices=TipoServicio.choices)
    valor_medicion = models.DecimalField(max_digits=12, decimal_places=2)
    etiqueta = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "consumo_log"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.tipo_servicio} @ {self.timestamp}"


class EstadoSimulacion(models.Model):
    """Reloj de la simulacion: 1 Pulso (POST a /api/pulso/) = 1 dia simulado.

    Tabla propia del backend (no viene del modelo de datos original de Supabase):
    la degradacion de dispositivos y el consumo de despensa necesitan una nocion
    de "hoy" que avance con cada Pulso, no con la fecha real del servidor.
    Fila unica (singleton).
    """

    fecha_actual = models.DateField(default=date.today)
    dia_numero = models.PositiveIntegerField(default=1)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "estado_simulacion"

    def __str__(self):
        return f"Dia {self.dia_numero} ({self.fecha_actual.isoformat()})"

    @classmethod
    def actual(cls) -> "EstadoSimulacion":
        estado, _ = cls.objects.get_or_create(pk=1)
        return estado

    def avanzar_un_dia(self) -> "EstadoSimulacion":
        self.fecha_actual += timedelta(days=1)
        self.dia_numero += 1
        self.save(update_fields=["fecha_actual", "dia_numero", "actualizado_en"])
        return self


LIMITE_DEUDA = Decimal("5000000")
PORCENTAJE_AHORRABLE_DEFAULT = Decimal("10")


class IngresosHogar(models.Model):
    """Billetera del hogar: tres bolsillos que se usan en un orden fijo.

    `saldo_disponible`: la plata del mes en curso. Arranca con los ingresos del
    cierre de mes (ver core.services.ingresos.cerrar_mes) y baja con cada gasto
    que los agentes aprueban.

    `ahorros`: lo que sobro de meses anteriores. Se forma en el cierre de mes,
    barriendo el `saldo_disponible` que quedo sin gastar. Es el colchon del hogar:
    cuando el saldo del mes no alcanza, un gasto tira de aca antes de endeudarse,
    y es lo que habilita compras extraordinarias (ver el Agente de Ahorro).

    `deuda`: si un gasto de una categoria esencial (Presupuesto.es_esencial) no se
    cubre ni con el saldo ni con los ahorros, se paga igual y la diferencia se
    acumula aca, hasta un tope de LIMITE_DEUDA ($5.000.000): una vez alcanzado, ni
    siquiera un gasto esencial se financia. Un gasto NO esencial sin fondos se
    rechaza directamente. La deuda se cancela con los ingresos del proximo cierre
    de mes, antes de que el resto pase a formar el saldo del mes nuevo.

    Invariante: `ahorros` y `deuda` nunca son ambos distintos de cero. Se sostiene
    solo por el orden de las operaciones, sin validarlo aparte: la deuda solo crece
    cuando saldo y ahorros ya estan en 0 (ver pagar), y si hay deuda el saldo era 0,
    asi que el barrido del cierre de mes no tiene nada que mandar a ahorros.

    `porcentaje_ahorrable`: NO mueve plata. Es la meta blanda de referencia (10% por
    defecto) que el Agente de Ahorro usa para razonar si conviene aprobar una compra
    o si conviene esperar al mes siguiente.

    Tabla propia del backend (no viene del modelo de datos original de Supabase),
    igual que EstadoSimulacion. Fila unica (singleton).
    """

    saldo_disponible = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ahorros = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deuda = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    porcentaje_ahorrable = models.DecimalField(
        max_digits=5, decimal_places=2, default=PORCENTAJE_AHORRABLE_DEFAULT
    )
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ingresos_hogar"

    def __str__(self):
        return (
            f"Saldo: {self.saldo_disponible} / Ahorros: {self.ahorros} / "
            f"Deuda: {self.deuda}"
        )

    @classmethod
    def actual(cls) -> "IngresosHogar":
        hogar, _ = cls.objects.get_or_create(pk=1)
        return hogar

    def recibir_ingreso(self, monto) -> "IngresosHogar":
        """Cierre de mes. Tres pasos, en este orden:

        1. Lo que sobro del saldo del mes que termina se barre a `ahorros`.
        2. El ingreso nuevo cancela la `deuda` pendiente.
        3. Lo que quede del ingreso arranca el saldo del mes nuevo.

        Consecuencia buscada: el saldo NO se acumula mes a mes -- cada mes arranca
        con la plata de ese mes, y el excedente pasa a ser colchon en `ahorros`.
        """
        monto = Decimal(str(monto))

        if self.saldo_disponible > 0:
            self.ahorros += self.saldo_disponible
            self.saldo_disponible = Decimal("0")

        if self.deuda > 0:
            pago_deuda = min(self.deuda, monto)
            self.deuda -= pago_deuda
            monto -= pago_deuda

        self.saldo_disponible += monto
        self.save(
            update_fields=["saldo_disponible", "ahorros", "deuda", "actualizado_en"]
        )
        return self

    def pagar(self, monto, es_esencial: bool) -> bool:
        """Aplica un gasto tirando de los bolsillos en orden: `saldo_disponible`,
        despues `ahorros`, y solo para categorias esenciales, `deuda`.

        Devuelve True si el gasto se ejecuta y False si se rechaza. Cuando devuelve
        False no toca ningun bolsillo: se calcula todo antes de escribir.

        Se rechaza en dos casos: el gasto NO es esencial y no alcanzan saldo +
        ahorros, o es esencial pero financiar el faltante superaria LIMITE_DEUDA.
        """
        monto = Decimal(str(monto))
        if monto <= 0:
            return True

        desde_saldo = min(monto, self.saldo_disponible)
        desde_ahorros = min(monto - desde_saldo, self.ahorros)
        faltante = monto - desde_saldo - desde_ahorros

        if faltante > 0:
            if not es_esencial:
                return False
            if self.deuda + faltante > LIMITE_DEUDA:
                return False

        self.saldo_disponible -= desde_saldo
        self.ahorros -= desde_ahorros
        self.deuda += faltante
        self.save(
            update_fields=["saldo_disponible", "ahorros", "deuda", "actualizado_en"]
        )
        return True
