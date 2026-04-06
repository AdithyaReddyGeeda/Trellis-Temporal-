## Overview

This project is a **Temporal Order Lifecycle** reference implementation: an `OrderWorkflow` orchestrates receive, validate, payment, and shipping steps, then starts a child **`ShippingWorkflow`** on a **separate task queue** so order and shipping workers can scale independently. A **FastAPI** service starts workflows and sends signals (cancel, approve, update address). **PostgreSQL** stores orders, payments, and audit **events**. Payment writes use **`payment_id` as the primary key** and **`INSERT … ON CONFLICT DO NOTHING`** so activity retries never double-charge.

## Prerequisites

- Docker
- Docker Compose
- Python 3.11+
- pip

## Quickstart

```bash
# 1. Start Temporal, Postgres, and Temporal UI
docker compose up -d

# 2. Copy env and install deps
cp .env.example .env
pip install -r requirements.txt

# 3. Run migrations
alembic upgrade head

# 4. Start workers (two terminals)
python -m workers.order_worker
python -m workers.shipping_worker

# 5. Start API
uvicorn api.main:app --reload --port 8000
```

## Trigger a workflow

```bash
curl -X POST http://localhost:8000/orders/order-001/start \
  -H "Content-Type: application/json" \
  -d '{"payment_id": "pay-abc-001"}'
```

## Approve the order (manual review gate)

```bash
curl -X POST http://localhost:8000/orders/order-001/signals/approve
```

## Send signals

```bash
# Cancel
curl -X POST http://localhost:8000/orders/order-001/signals/cancel

# Update address
curl -X POST http://localhost:8000/orders/order-001/signals/update_address \
  -H "Content-Type: application/json" \
  -d '{"address": {"street": "456 New St", "city": "Austin", "zip": "78701"}}'
```

## Inspect live state

```bash
curl http://localhost:8000/orders/order-001/status
```

## Temporal UI

Open http://localhost:8080 to view workflow history, retries, and event timelines.

## Schema

- **`orders`**: `id` (string PK), `state`, optional `address_json` / `payment_id`, timestamps. Tracks order lifecycle (`received` → `validated` → … → `completed`).
- **`payments`**: `payment_id` (string **primary key**), `order_id` (FK → `orders.id`), `status`, `amount`, `created_at`. One row per payment intent.
- **`events`**: append-only audit log (`order_id`, `type`, optional `payload_json`, `ts`).

**Idempotency:** `payment_id` is unique at the database level. The business logic inserts a row with `ON CONFLICT (payment_id) DO NOTHING`, then updates status/amount. Retries or duplicate activity executions do not create duplicate payment rows or double-charge.

## Tests

```bash
pytest tests/ -v
```
