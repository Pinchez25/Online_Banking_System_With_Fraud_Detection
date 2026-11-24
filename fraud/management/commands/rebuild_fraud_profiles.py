from django.core.management.base import BaseCommand

from fraud.services import rebuild_profiles


class Command(BaseCommand):
    help = "Rebuild the Redis behavioural profiles from the transaction ledger."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Replayed {rebuild_profiles()} transactions into fraud profiles."))
