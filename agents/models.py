from django.db import models

from core.models import ConsumoLog, Dispositivo, ItemDespensa, Presupuesto, Residente


class AgenteEnum(models.TextChoices):
    ORQUESTADOR = "ORQUESTADOR"
    AGENTE_DESPENSA = "AGENTE_DESPENSA"
    AGENTE_CONSUMO = "AGENTE_CONSUMO"
    AGENTE_PRESUPUESTO = "AGENTE_PRESUPUESTO"
    AGENTE_MANTENIMIENTO = "AGENTE_MANTENIMIENTO"


class DecisionLog(models.Model):
    id_decision = models.BigAutoField(primary_key=True)
    timestamp = models.DateTimeField()
    id_agente = models.CharField(max_length=32, choices=AgenteEnum.choices)
    accion_tomada = models.CharField(max_length=255)
    justificacion_tecnica = models.TextField()
    detalles_payload = models.JSONField(null=True, blank=True)
    residente_autorizador = models.ForeignKey(
        Residente,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="id_residente_autorizador",
        related_name="decisiones_autorizadas",
    )
    dispositivo_afectado = models.ForeignKey(
        Dispositivo,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="id_dispositivo_afectado",
        related_name="decisiones",
    )
    presupuesto_afectado = models.ForeignKey(
        Presupuesto,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="id_presupuesto_afectado",
        related_name="decisiones",
    )
    item_afectado = models.ForeignKey(
        ItemDespensa,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="id_item_afectado",
        related_name="decisiones",
    )
    consumo_asociado = models.ForeignKey(
        ConsumoLog,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_column="id_consumo_asociado",
        related_name="decisiones",
    )

    class Meta:
        managed = False
        db_table = "decision_log"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.id_agente}: {self.accion_tomada}"
