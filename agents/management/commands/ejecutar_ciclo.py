from django.core.management.base import BaseCommand

from agents.services.orquestador import ejecutar_ciclo


class Command(BaseCommand):
    help = "Corre un 'pulso' de la simulacion: dispara los agentes y registra sus decisiones."

    def handle(self, *args, **options):
        logs = ejecutar_ciclo()
        if not logs:
            self.stdout.write(self.style.WARNING("Ningun agente disparo una accion en este pulso."))
            return
        for log in logs:
            self.stdout.write(self.style.SUCCESS(f"[{log.id_agente}] {log.accion_tomada}"))
            self.stdout.write(f"  -> {log.justificacion_tecnica}")
