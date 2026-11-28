"""The `bank_manage` command: Django's management utility for this project."""
import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Online_Banking_System.settings")
    execute_from_command_line(sys.argv)
