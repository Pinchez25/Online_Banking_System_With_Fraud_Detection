"""Model training as a Celery DAG:

    prepare_dataset ─► build_frame ─┬─► train_candidate(hgb_balanced) ─┐
                                    ├─► train_candidate(hgb)          ─┼─► select_and_register
                                    └─► train_candidate(logreg)       ─┘
                                          (parallel fan-out)            (chord fan-in)
"""
from celery import chain, chord, shared_task

from fraud.training import pipeline, trainer

# Heavy CPU/memory work: routed to the dedicated "ml" queue (see CELERY_TASK_ROUTES).
_options = {"acks_late": True, "time_limit": 60 * 60}


@shared_task(**_options)
def prepare_dataset(run_id: str) -> str:
    return pipeline.prepare_dataset(run_id)


@shared_task(**_options)
def build_frame(run_id: str) -> str:
    return pipeline.build_frame(run_id)


@shared_task(**_options)
def train_candidate(run_id: str, name: str) -> dict:
    return pipeline.train_candidate(run_id, name)


@shared_task(**_options)
def select_and_register(results: list[dict], run_id: str) -> dict:
    return pipeline.select_and_register(results, run_id)


def training_dag(run_id: str | None = None, candidates=None):
    run_id = run_id or pipeline.new_run_id()
    return chain(
        prepare_dataset.si(run_id),
        build_frame.si(run_id),
        chord(
            [train_candidate.si(run_id, name) for name in candidates or trainer.CANDIDATES],
            select_and_register.s(run_id),
        ),
    )
