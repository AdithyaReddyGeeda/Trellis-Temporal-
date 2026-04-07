# trellis-temporal

## Overview

**trellis-temporal** is a Temporal-based order lifecycle sample in Python. An `OrderWorkflow` runs receive, validate, manual approval, payment, and post-payment shipping steps, then starts a child `ShippingWorkflow` on a dedicated task queue. A FastAPI layer starts workflows and sends signals; PostgreSQL persists orders, payments, and an append-only event log.

The stack is Python 3.11+, Temporal, FastAPI, SQLAlchemy (async) with asyncpg, PostgreSQL, Docker Compose, Alembic, and structlog for structured logging.

## Prerequisites

Docker and Docker Compose, Python 3.11 or newer, and pip are required. The app expects a `.env` file (copy from `.env.example`) with `DATABASE_URL`, `TEMPORAL_HOST`, and task queue names aligned with your workers.

## Quickstart

```bash
docker compose up -d
cp .env.example .env   # adjust if needed
pip install -r requirements.txt
alembic upgrade head
```

Start infrastructure first, then run workers in **two** separate terminals and the API in a third:

```bash
python -m workers.order_worker
python -m workers.shipping_worker
uvicorn api.main:app --reload --port 8000
```

Temporal UI is available at [http://localhost:8080](http://localhost:8080).

## API usage

Workflows are keyed by `order_id` in the URL. Start a run with a client-supplied `payment_id`, then approve after validation to pass the manual review gate. Use `GET /orders/{order_id}/status` for a live snapshot from the workflow query handler.

**Start workflow**

```bash
curl -X POST http://localhost:8000/orders/order-001/start \
  -H "Content-Type: application/json" \
  -d '{"payment_id": "pay-abc-001"}'
```

**Approve (manual review)**

```bash
curl -X POST http://localhost:8000/orders/order-001/signals/approve
```

**Cancel**

```bash
curl -X POST http://localhost:8000/orders/order-001/signals/cancel
```

**Update shipping address**

```bash
curl -X POST http://localhost:8000/orders/order-001/signals/update_address \
  -H "Content-Type: application/json" \
  -d '{"address": {"street": "456 New St", "city": "Austin", "zip": "78701"}}'
```

**Inspect state**

```bash
curl http://localhost:8000/orders/order-001/status
```

Order records in the database follow a progression such as **received → validated → payment_charged → shipped → completed**, driven by business logic and workflow completion.

## Architecture

Order processing and shipping are intentionally split across **two task queues**. The order worker handles `OrderWorkflow` and order-side activities; the shipping worker runs `ShippingWorkflow` and shipping activities. That boundary mirrors how separate services or teams can scale and deploy without sharing a single queue.

Payment idempotency is enforced in the database: **`payment_id` is the primary key** on `payments`. Inserts use **`INSERT … ON CONFLICT DO NOTHING`**, followed by status updates, so Temporal activity retries cannot create duplicate charges for the same payment key.

Order-side activities share a retry policy tuned to the overall workflow budget: **`start_to_close_timeout` of five seconds**, **`maximum_attempts` of three**, and **`maximum_interval` of two seconds** on backoff. Business functions call **`flaky_call()`**, which randomly raises or sleeps long enough (**300 seconds**) that the activity times out—exercising real retry and timeout behavior under Temporal.

Workflow code avoids reading **`os.environ` at import time** (a Temporal sandbox constraint). Shipping’s task queue name is a workflow-safe literal; workers still bind to queues from environment variables. Both workers use **`UnsandboxedWorkflowRunner`** so importing activity modules (and transitive dependencies such as structlog) does not trigger sandbox violations during workflow validation.

## Database schema

PostgreSQL holds three main concepts. The **`orders`** table stores `id` (primary key), `state`, optional `address_json` and `payment_id`, and timestamps. The **`payments`** table uses **`payment_id` as primary key**, with `order_id` as a foreign key, plus `status`, `amount`, and `created_at`. The **`events`** table is an append-only audit trail: `order_id`, event `type`, optional `payload_json`, and `ts`. Migrations live under `db/migrations` and are applied with Alembic.

## Tests

```bash
pytest tests/ -v
```

There are **seven** tests. They use Temporal’s in-memory workflow test environment and an unsandboxed workflow runner, so **Docker is not required** to execute the suite. Coverage includes the happy-path order lifecycle, cancellation before approval, address updates via signal, payment idempotency, shipping dispatch failure and parent retry behavior, shipping happy path, and carrier retry logic.
