<<<<<<< Updated upstream
web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m app.worker
=======
PYTHONPATH=. alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
>>>>>>> Stashed changes
