from django.core.management.base import BaseCommand

from events import topics
from events.consumer import ensure_topics


class Command(BaseCommand):
    help = "Create all application Kafka topics (and their dead-letter topics) if missing."

    def handle(self, *args, **options):
        ensure_topics([*topics.ALL, *map(topics.dead_letter, topics.ALL)])
        self.stdout.write(self.style.SUCCESS("Kafka topics are in place."))
