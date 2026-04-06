import structlog
from temporalio import activity

from business_logic import functions as bl

log = structlog.get_logger()


@activity.defn
async def prepare_package(order: dict) -> str:
    order_id = order["order_id"]
    log.info("activity_start", activity="prepare_package", order_id=order_id)
    result = await bl.package_prepared(order, db=None)
    log.info("activity_complete", activity="prepare_package", order_id=order_id)
    return result


@activity.defn
async def dispatch_carrier(order: dict) -> str:
    order_id = order["order_id"]
    log.info("activity_start", activity="dispatch_carrier", order_id=order_id)
    result = await bl.carrier_dispatched(order, db=None)
    log.info("activity_complete", activity="dispatch_carrier", order_id=order_id)
    return result
