import json

from django.core.management.base import BaseCommand

from fraud.tasks.training import training_dag
from fraud.training import pipeline, trainer


class Command(BaseCommand):
    help = "Train, evaluate and register a fraud model on PaySim (Celery DAG, or --inline)."

    def add_arguments(self, parser):
        parser.add_argument("--inline", action="store_true", help="Run every step in this process (no broker).")
        parser.add_argument("--wait", action="store_true", help="With Celery: block until the DAG finishes.")
        parser.add_argument("--candidates", nargs="+", choices=list(trainer.CANDIDATES))

    def handle(self, *args, inline, wait, candidates, **options):
        run_id = pipeline.new_run_id()
        if inline:
            summary = pipeline.run_inline(run_id, candidates)
            self.stdout.write(json.dumps(summary, indent=2))
            return

        result = training_dag(run_id, candidates).apply_async()
        self.stdout.write(self.style.SUCCESS(f"Training DAG {run_id} dispatched (task {result.id}) to queue 'ml'."))
        if wait:
            self.stdout.write(json.dumps(result.get(), indent=2))
