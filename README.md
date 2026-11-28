A Django-based online banking system with user authentication, account management, money transfers, transaction workflows, real-time notifications, and fraud detection powered by a machine learning model

## Overview

This is a full-stack banking web application built with Django. It provides a simple digital banking experience
for customers to:

- register and manage personal accounts
- log in securely and update profile details
- deposit and withdraw funds
- send money to other users
- view dashboard summaries and recent transactions
- receive notifications and alerts

## Tech Stack

- Python 3.14+
- Django 6+
- PostgreSQL
- Redis (cache, Channels, Celery broker, behavioural profiles)
- Apache Kafka (KRaft) with a transactional outbox
- Celery (outbox relay, model-training DAG)
- Django Channels + Daphne
- HTML, CSS, JavaScript
- scikit-learn (HistGradientBoosting), pandas, PyArrow; trained on PaySim

## Key Features

- Account opening: customers apply online (name and national ID required); staff approve or reject in the
  back office. Approval assigns an account number and issues a debit card. Only open accounts can transact.
- Profile management with next-of-kin data
- Dashboard with live balance, monthly change, spending breakdown, open requests and loan status
- Deposits, withdrawals (need an active debit card) and transfers to an account number
- Cards: freeze and unfreeze instantly
- Beneficiaries: saved payees with one-tap "send again"
- Transaction history with filters and pagination; CSV and PDF export of the filtered set
- Monthly PDF statements with opening, running and closing balances
- Loans: apply, staff approval and disbursement, fixed amortised instalments, repayments from balance
- Session security: 5-minute idle timeout with a warning and "stay signed in", 8-hour absolute limit,
  sign-out when the browser closes, one active session per customer
- Real-time fraud screening of every deposit, withdrawal and transfer, with a staff review queue
- Real-time notification center with read/unread tracking
- Back office on Django Unfold (`/admin/`): operations dashboard, approval and manual-review queues with one-click
  approve/reject, model version activation
- Email-based authentication and password reset support
- Docker Compose stack: PostgreSQL, Redis, Kafka, Kafka UI and Mailpit

## Architecture

Money never moves on request. Every deposit, withdrawal and transfer is screened by an
event-driven fraud pipeline first, then waits for staff approval.

```text
 Customer ──► Django view ──► request_transaction()  ─┐ one DB transaction
                                 Transaction(SCREENING)│
                                 OutboxEvent           ┘
                                      │ on_commit + beat sweep (Celery)
                                      ▼
                       Kafka: bank.transactions.requested.v1   (key = account id)
                                      │
                         consumer group "fraud-scorer"
                   Redis behavioural profile ─► features ─► HistGradientBoosting
                                      │            └──────► behavioural rules
                                      ▼   RiskAssessment + OutboxEvent
                       Kafka: fraud.decisions.v1
                                      │
                         consumer group "bank-screening"
                     allow/review ─► PENDING (approval queue)
                     block        ─► REJECTED + account frozen + alert/email
```

- **Transactional outbox** (`events/`): an event is published if and only if its business
  change committed. A Celery relay drains it with `SELECT … FOR UPDATE SKIP LOCKED`.
- **At-least-once with idempotent consumers**: offsets commit after handling; scoring and decision
  application are no-ops on redelivery. Poison messages go to `<topic>.dlq` after retries.
- **Behavioural profiles** (`fraud/profiles.py`): per-account Redis hashes, 24h sliding-window
  sorted sets and payee graphs. One pipelined round trip per score.
- **Behavioural rules** (`fraud/rules.py`): velocity burst, amount spike (z-score against the
  account's own history), account drain, large first payment to a new payee, unusual hour,
  pass-through (mule cash-out) and recipient fan-in. Combined with the model by noisy-OR.
  Rules alone escalate to *manual review*; only the model's high-precision band auto-blocks.
- **One feature contract** (`fraud/features.py`): training (vectorised over 6.3M PaySim rows) and
  serving (one event against Redis) produce the same raw signals and share the same feature code.
  `fraud.tests.test_parity` asserts they agree exactly.
- **Model registry** (`FraudModelVersion`): champion/challenger promotion on test PR-AUC, one active
  model, hot-reloaded by scorers. Admin can roll back with one action.

```text
events/      outbox, Kafka/inline bus, consumer runner, contracts, topics
fraud/       profiles, rules, engine, registry, Kafka handlers, training pipeline + Celery DAG
bank/        ledger models, request/approval/screening services, views, admin
accounts/    users, profiles, auth, blocked-account middleware
ml/data/     PaySim CSV (git-ignored)      ml/artifacts/  parquet cache + model runs (git-ignored)
```

## Dataset

[PaySim](https://www.kaggle.com/datasets/ealaxi/paysim1): 6.3M simulated mobile-money transactions.
Save the CSV as `ml/data/paysim.csv`. PaySim types map onto the bank's: `CASH_IN`→deposit,
`CASH_OUT`/`PAYMENT`/`DEBIT`→withdrawal, `TRANSFER`→transfer.

## Local Development Setup

```bash
uv sync
uv run pre-commit install            # ruff, hygiene, lockfile and migration checks on every commit
cp .env.example .env
docker compose up -d                 # Postgres, Redis (:6380), Kafka KRaft (:9094), Kafka UI (:8089), Mailpit
uv run python manage.py migrate
uv run python manage.py kafka_topics
uv run python manage.py train_fraud_model --inline     # ~3-5 min, peaks around 3-4 GB RAM
uv run honcho start                  # web, Celery workers + beat, both Kafka consumer groups
```

| Process          | What it does                                                 |
|------------------|--------------------------------------------------------------|
| `web`            | `runserver` on Daphne (HTTP, WebSockets, auto-reload), :8000 |
| `worker`         | Celery default queue: outbox relay                           |
| `ml_worker`      | Celery `ml` queue, concurrency 1 (memory-bound training)     |
| `beat`           | Outbox sweep every 10s, published-outbox purge hourly        |
| `fraud_scorer`   | `run_consumer fraud-scorer`                                  |
| `bank_screening` | `run_consumer bank-screening`                                |

No Kafka handy? Set `EVENT_BUS=inline` and handlers run in-process after commit (tests use this).

### Training as a Celery DAG

```text
prepare_dataset ─► build_frame ─┬─► train_candidate(hgb_balanced) ─┐
                                ├─► train_candidate(hgb)          ─┼─► select_and_register
                                └─► train_candidate(logreg)       ─┘
```

```bash
uv run python manage.py train_fraud_model --wait     # dispatch to the `ml` queue
uv run python manage.py rebuild_fraud_profiles       # rebuild Redis profiles from the ledger
```

Thresholds come from validation data: *review* maximises F2 (recall-weighted) and *block* is the
lowest score with at least 97% precision. Metrics are reported on an untouched, out-of-time test split.

## Running Tests

Tests use pytest with factory_boy factories (`tests/factories.py`) and run against PostgreSQL only:
cache, channels, email, fraud profiles, Celery and the event bus are in-memory or eager
(`Online_Banking_System/test_settings.py`).

| Layer | Directory | What it covers |
| ------- | ----------- | ---------------- |
| Unit | `tests/unit` | Pure logic: ledger, fraud rules and features, loan maths, status graph, documents |
| Integration | `tests/integration` | Services, views, admin, WebSockets and the event pipeline against the database |
| End-to-end | `tests/e2e` | Customer journeys in a real browser (Playwright) against a live server |

```bash
uv run pytest                      # everything, with a coverage report
uv run pytest -m unit              # or -m integration / -m e2e
uv run pytest -m "not e2e"         # skip the browser
uv run playwright install chromium # once, before the first e2e run
```

## Useful Commands

Create a superuser:

```bash
uv run python manage.py createsuperuser
```

Collect static files for production:

```bash
uv run python manage.py collectstatic --noinput
```

Run Django's system checks:

```bash
uv run python manage.py check
```

## Bank Configuration

Nothing about the bank's identity is hard-coded.

| What | Where | Changeable at runtime |
| ------ | ------- | ----------------------- |
| Name, legal notice, support email and phone | Back office → **Bank profile** (seeded from `BANK_NAME`, `BANK_LEGAL_NOTICE`, `BANK_SUPPORT_EMAIL`, `BANK_SUPPORT_PHONE` on first use) | Yes |
| Currency code | `BANK_CURRENCY` | No: amounts are stored without a currency |
| Time zone | `TIME_ZONE` | No |
| Transaction limits, loan terms and rate, session timeouts | `Online_Banking_System/settings.py` | No |

Templates read the profile as `{{ institution }}` and format amounts with `{{ amount|money }}`; Python code uses
`institution.services.get_institution()` and `format_money()`.

## Environment Notes

- The app uses `EMAIL_HOST` and `EMAIL_PORT` for password reset messages and notifications.
- Mailpit is useful for local development because it captures outbound emails without sending them externally.
- Redis backs the cache, Channels, Celery and the fraud profiles (separate DB numbers).
- The app uses a custom `Account` model as the user model.

## Production Considerations

This project is configured for local development convenience, not a hardened production deployment. Before deploying to
production, review:

- `SECRET_KEY` management
- secure `DEBUG` settings
- production database credentials and SSL settings
- Redis and cache configuration
- email provider configuration
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
- static and media file serving

## License

This project is provided as a learning/demo application. Please check the repository or project owner for any licensing
details.

## Contributing

Contributions are welcome. For a clean workflow:

1. create a feature branch
2. make focused changes
3. run tests
4. open a pull request with a clear summary

## Troubleshooting

### Database connection errors

Verify PostgreSQL is running and ensure the database credentials in `.env` match your local service configuration.

### Redis errors or WebSocket issues

Check whether Redis is available at the URL configured in `REDIS_URL` and `CHANNEL_REDIS_URL`.

### Emails not appearing

Use Mailpit at `http://localhost:8025` to inspect outbound messages during development.

### Missing environment variables

Ensure your `.env` file includes all values expected by `Online_Banking_System/settings.py` before running the app.

---

For development and onboarding, this project is best run with `uv` and Docker to manage the Python environment and local
services cleanly.
