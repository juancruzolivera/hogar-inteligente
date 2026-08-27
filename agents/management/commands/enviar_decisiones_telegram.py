from django.core.management.base import BaseCommand, CommandError

from agents.models import DecisionLog
from agents.services import notificaciones


class Command(BaseCommand):
    help = (
        "Manda por Telegram decisiones del Agente de Despensa / Mantenimiento que ya estan "
        "guardadas en decision_log. Sirve para probar el envio sin correr un pulso (no llama "
        "al LLM ni mueve plata) y para reenviar algo que se perdio porque el webhook estaba caido."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cantidad",
            type=int,
            default=1,
            help="Cuantas decisiones mandar, de la mas reciente hacia atras (default: 1).",
        )
        parser.add_argument(
            "--id",
            type=int,
            dest="id_decision",
            help="id_decision puntual a mandar. Si se pasa, ignora --cantidad.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra el mensaje que se mandaria, sin pegarle al webhook de n8n.",
        )

    def handle(self, *args, **options):
        decisiones = DecisionLog.objects.filter(
            id_agente__in=notificaciones.AGENTES_NOTIFICABLES
        )

        if options["id_decision"]:
            decisiones = decisiones.filter(id_decision=options["id_decision"])
            if not decisiones.exists():
                raise CommandError(
                    f"No hay ninguna decision de Despensa/Mantenimiento con id "
                    f"{options['id_decision']}."
                )
        else:
            # DecisionLog.Meta ya ordena por -timestamp: las mas recientes primero.
            decisiones = decisiones[: max(options["cantidad"], 0)]

        decisiones = list(decisiones)
        if not decisiones:
            self.stdout.write(
                self.style.WARNING("No hay decisiones de Despensa/Mantenimiento en la base.")
            )
            return

        if options["dry_run"]:
            destinatarios = notificaciones.destinatarios()
            self.stdout.write(
                f"Destinatarios (telegram_id): {destinatarios or 'ninguno -> chat por defecto de n8n'}"
            )
            for decision in decisiones:
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n--- #{decision.id_decision} ---"))
                self.stdout.write(notificaciones.formatear_decision(decision))
            return

        for decision in decisiones:
            enviados = notificaciones.notificar_decision(decision)
            estilo = self.style.SUCCESS if enviados else self.style.ERROR
            self.stdout.write(
                estilo(
                    f"#{decision.id_decision} [{decision.id_agente}] "
                    f"{decision.accion_tomada} -> {enviados} envio(s)"
                )
            )
