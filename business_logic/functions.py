import asyncio
import json
import random
from typing import Any, Dict, Optional

import structlog
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_db
from db.models import Event, Order, Payment

log = structlog.get_logger()


async def flaky_call() -> None:
    rand_num = random.random()
    if rand_num < 0.33:
        raise RuntimeError("Forced failure for testing")
    if rand_num < 0.67:
        await asyncio.sleep(300)  # Expect the activity layer to time out before this completes


async def _log_event(
    session,
    *,
    order_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    session.add(
        Event(
            order_id=order_id,
            type_=event_type,
            payload_json=json.dumps(payload) if payload is not None else None,
        )
    )


async def order_received(order_id: str, db) -> Dict[str, Any]:
    await flaky_call()
    result: Dict[str, Any] = {
        "order_id": order_id,
        "items": [{"sku": "ABC", "qty": 1}],
    }
    async with get_db() as session:
        session.add(Order(id=order_id, state="received"))
        await session.flush()  # ensure Order row exists before FK-constrained Event insert
        await _log_event(session, order_id=order_id, event_type="order_received")
        await session.commit()
    log.info(
        "business_logic",
        event="order_received",
        order_id=order_id,
        result=result,
    )
    return result


async def order_validated(order: Dict[str, Any], db) -> bool:
    await flaky_call()
    if not order.get("items"):
        raise ValueError("No items to validate")
    async with get_db() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order["order_id"])
            .values(state="validated")
        )
        await _log_event(
            session,
            order_id=order["order_id"],
            event_type="order_validated",
        )
        await session.commit()
    log.info(
        "business_logic",
        event="order_validated",
        order_id=order["order_id"],
        result=True,
    )
    return True


async def payment_charged(
    order: Dict[str, Any], payment_id: str, db
) -> Dict[str, Any]:
    await flaky_call()
    amount = sum(i.get("qty", 1) for i in order.get("items", []))
    out: Dict[str, Any] = {"status": "charged", "amount": amount}
    async with get_db() as session:
        await session.execute(
            pg_insert(Payment).values(
                payment_id=payment_id,
                order_id=order["order_id"],
                status="pending",
            ).on_conflict_do_nothing(index_elements=["payment_id"])
        )
        await session.execute(
            update(Payment)
            .where(Payment.payment_id == payment_id)
            .values(status="charged", amount=amount)
        )
        await session.execute(
            update(Order)
            .where(Order.id == order["order_id"])
            .values(state="payment_charged")
        )
        await _log_event(
            session,
            order_id=order["order_id"],
            event_type="payment_charged",
            payload={"payment_id": payment_id, "amount": amount},
        )
        await session.commit()
    log.info(
        "business_logic",
        event="payment_charged",
        order_id=order["order_id"],
        result=out,
    )
    return out


async def order_shipped(order: Dict[str, Any], db) -> str:
    await flaky_call()
    out = "Shipped"
    async with get_db() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order["order_id"])
            .values(state="shipped")
        )
        await _log_event(
            session,
            order_id=order["order_id"],
            event_type="order_shipped",
        )
        await session.commit()
    log.info(
        "business_logic",
        event="order_shipped",
        order_id=order["order_id"],
        result=out,
    )
    return out


async def package_prepared(order: Dict[str, Any], db) -> str:
    await flaky_call()
    out = "Package ready"
    async with get_db() as session:
        await _log_event(
            session,
            order_id=order["order_id"],
            event_type="package_prepared",
        )
        await session.commit()
    log.info(
        "business_logic",
        event="package_prepared",
        order_id=order["order_id"],
        result=out,
    )
    return out


async def carrier_dispatched(order: Dict[str, Any], db) -> str:
    await flaky_call()
    out = "Dispatched"
    async with get_db() as session:
        await _log_event(
            session,
            order_id=order["order_id"],
            event_type="carrier_dispatched",
        )
        await session.execute(
            update(Order)
            .where(Order.id == order["order_id"])
            .values(state="dispatched")
        )
        await session.commit()
    log.info(
        "business_logic",
        event="carrier_dispatched",
        order_id=order["order_id"],
        result=out,
    )
    return out
