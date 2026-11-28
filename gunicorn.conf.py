"""Gunicorn settings for every environment. Uvicorn workers serve the ASGI app, so
HTTP and WebSockets share one server. Each value can be overridden from the environment."""
import multiprocessing
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

debug = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}

wsgi_app = "Online_Banking_System.asgi:application"
worker_class = "uvicorn_worker.UvicornWorker"
bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8000")
workers = int(os.getenv("GUNICORN_WORKERS", 2 if debug else multiprocessing.cpu_count() * 2 + 1))

reload = debug
timeout = int(os.getenv("GUNICORN_TIMEOUT", 60))
graceful_timeout = 30
keepalive = 5

# Recycle workers in production to cap slow memory growth; restarts would only get in the way in development.
max_requests = 0 if debug else 1000
max_requests_jitter = 0 if debug else 100

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "debug" if debug else "info")
