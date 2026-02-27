web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
# WORKER_CONCURRENCY=3  — process 3 Claude jobs in parallel within one process
#   (Variant B: asyncio.gather, no extra DB connections, safe with PgBouncer)
# For Variant A (multiple OS replicas): scale "worker" to 2-3 in Railway dashboard
#   claim_next uses FOR UPDATE SKIP LOCKED — safe for parallel replicas
worker: WORKER_CONCURRENCY=3 python -m app.worker
