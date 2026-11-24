web: .venv/bin/python manage.py runserver 127.0.0.1:8000
worker: .venv/bin/celery -A Online_Banking_System worker -Q celery -c 2 -n default@%h --loglevel=INFO
ml_worker: .venv/bin/celery -A Online_Banking_System worker -Q ml -c 1 -n ml@%h --max-memory-per-child=6000000 --loglevel=INFO
beat: .venv/bin/celery -A Online_Banking_System beat --loglevel=INFO --schedule=/tmp/kwetu-celerybeat-schedule
fraud_scorer: .venv/bin/python manage.py run_consumer fraud-scorer
bank_screening: .venv/bin/python manage.py run_consumer bank-screening
