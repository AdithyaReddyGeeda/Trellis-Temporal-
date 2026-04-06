import json

import structlog
from sqlalchemy import update
from temporalio import activity

from business_logic import functions as bl
from db import get_db
from db.models import Event, Order

log = structlog.get_logger()


@activity.defn
async def receive_order(order_id: str) -> dict:
    log.info("activity_start", activity="receive_order", order_id=order_id)
    result = await bl.order_received(order_id, db=None)
    log.info("activity_complete", activity="receive_order", order_id=order_id)
    return result


@activity.defn
async def validate_order(order: dict) -> bool:
    order_id = order["order_id"]
    log.info("activity_start", activity="validate_order", order_id=order_id)
    result = await bl.order_validated(order, db=None)
    log.info("activity_complete", activity="validate_order", order_id=order_id)
    return result


@activity.defn
async def charge_payment(order: dict, payment_id: str) -> dict:
    order_id = order["order_id"]
    log.info("activity_start", activity="charge_payment", order_id=order_id)
    result = await bl.payment_charged(order, payment_id, db=None)
    log.info("activity_complete", activity="charge_payment", order_id=order_id)
    return result


@activity.defn
async def ship_order(order: dict) -> str:
    order_id = order["order_id"]
    log.info("activity_start", activity="ship_order", order_id=order_id)
    result = await bl.order_shipped(order, db=None)
    log.info("activity_complete", activity="ship_order", order_id=order_id)
    return result


@activity.defn
async def update_order_address(order_id: str, new_address: dict) -> None:
    log.info("activity_start", activity="update_order_address", order_id=order_id)
    async with get_db() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(address_json=json.dumps(new_address))
        )
        session.add(
            Event(
                order_id=order_id,
                type_="address_updated",
                payload_json=json.dumps(new_address),
            )
        )
        await session.commit()
    log.info("activity_complete", activity="update_order_address", order_id=order_id)
