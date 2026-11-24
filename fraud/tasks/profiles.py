from celery import shared_task

from fraud.services import rebuild_profiles


@shared_task(ignore_result=True)
def rebuild_profiles_task() -> int:
    return rebuild_profiles()
