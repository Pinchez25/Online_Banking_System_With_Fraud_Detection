from django.core.management.base import BaseCommand, CommandError

from events import registry
from events.consumer import GroupConsumer


class Command(BaseCommand):
    help = "Run the Kafka consumer for one consumer group (e.g. fraud-scorer, bank-screening)."

    def add_arguments(self, parser):
        parser.add_argument("group", help=f"One of: {', '.join(sorted(registry.groups())) or '(none registered)'}")

    def handle(self, *args, group, **options):
        if group not in registry.groups():
            raise CommandError(f"Unknown group {group!r}. Known: {sorted(registry.groups())}")
        GroupConsumer(group).run()
