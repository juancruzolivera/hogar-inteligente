import uuid
from datetime import date, timedelta

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
    nivel_permiso = models.CharField(max_length=16, choices=NivelPermiso.choices)
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
    stock_actual = models.DecimalField(max_digits=12, decimal_places=2)
    stock_minimo = models.DecimalField(max_digits=12, decimal_places=2)
    consumo_promedio_diario = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    precio_estimado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
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
    vida_util_estimada = models.IntegerField()
    estado_actual = models.CharField(max_length=32, choices=EstadoDispositivo.choices)
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
