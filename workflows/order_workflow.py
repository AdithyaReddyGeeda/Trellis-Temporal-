"""
Register the order worker with workflow_execution_timeout=timedelta(seconds=15)
so the full run stays within the 15s end-to-end budget.
"""

import asyncio
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

from activities.order_activities import (
    charge_payment,
    receive_order,
    ship_order,
    update_order_address,
    validate_order,
)
from workflows.shipping_workflow import SHIPPING_TASK_QUEUE, ShippingWorkflow

_ACTIVITY_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=2),
)


@workflow.defn
class OrderWorkflow:
    def __init__(self) -> None:
        self._cancelled: bool = False
        self._approved: bool = False
        self._new_address: Optional[dict] = None
        self._dispatch_failed: bool = False
        self._dispatch_fail_reason: str = ""
        self._order: dict = {}
        self._current_step: str = "starting"

    @workflow.signal
    def cancel_order(self) -> None:
        self._cancelled = True
        workflow.logger.info("cancel_order signal received")

    @workflow.signal
    def approve_order(self) -> None:
        self._approved = True
        workflow.logger.info("approve_order signal received")

    @workflow.signal
    def update_address(self, address: dict) -> None:
        self._new_address = address
        workflow.logger.info("update_address signal received")

    @workflow.signal
    def dispatch_failed(self, reason: str) -> None:
        self._dispatch_failed = True
        self._dispatch_fail_reason = reason

    @workflow.query
    def status(self) -> dict:
        return {
            "current_step": self._current_step,
            "cancelled": self._cancelled,
            "approved": self._approved,
            "order": dict(self._order),
            "dispatch_failed": self._dispatch_failed,
            "dispatch_fail_reason": self._dispatch_fail_reason,
        }

    @workflow.run
    async def run(self, order_id: str, payment_id: str) -> str:
        # Step 1 — receive_order
        self._current_step = "receiving"
        self._order = await workflow.execute_activity(
            receive_order,
            order_id,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=_ACTIVITY_RETRY,
        )
        if self._cancelled:
            workflow.logger.info("order cancelled during receive")
            return "cancelled"

        # Step 2 — validate_order
        self._current_step = "validating"
        await workflow.execute_activity(
            validate_order,
            self._order,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=_ACTIVITY_RETRY,
        )
        if self._cancelled:
            return "cancelled"

        # Step 3 — update address if pending
        if self._new_address is not None:
            await workflow.execute_activity(
                update_order_address,
                args=[self._order["order_id"], self._new_address],
                start_to_close_timeout=timedelta(seconds=3),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            self._order["address"] = self._new_address

        # Step 4 — manual review timer
        self._current_step = "awaiting_approval"
        try:
            await workflow.wait_condition(
                lambda: self._approved or self._cancelled,
                timeout=timedelta(seconds=8),
            )
        except asyncio.TimeoutError:
            pass
        if self._cancelled or not self._approved:
            return "cancelled_at_review"

        # Step 5 — charge_payment
        self._current_step = "charging_payment"
        await workflow.execute_activity(
            charge_payment,
            args=[self._order, payment_id],
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=_ACTIVITY_RETRY,
        )
        if self._cancelled:
            workflow.logger.info(
                "payment charged but order cancelled — refund needed"
            )
            return "cancelled_after_payment"

        # Step 6 — ShippingWorkflow child
        self._current_step = "shipping"
        for attempt in range(2):
            self._dispatch_failed = False
            self._dispatch_fail_reason = ""
            try:
                await workflow.execute_child_workflow(
                    ShippingWorkflow.run,
                    self._order,
                    id=f"{workflow.info().workflow_id}-shipping-{attempt}",
                    task_queue=SHIPPING_TASK_QUEUE,
                    execution_timeout=timedelta(seconds=8),
                )
                break
            except Exception:
                # Give Temporal time to deliver the dispatch_failed signal before checking the flag.
                try:
                    await workflow.wait_condition(
                        lambda: self._dispatch_failed,
                        timeout=timedelta(seconds=2),
                    )
                except asyncio.TimeoutError:
                    pass
                if not self._dispatch_failed:
                    raise
                workflow.logger.warning(
                    "shipping child failed for dispatch: %s",
                    self._dispatch_fail_reason,
                )
                if attempt == 0:
                    continue
                self._current_step = "dispatch_failed"
                return "dispatch_failed"

        # Step 7 — ship_order activity
        await workflow.execute_activity(
            ship_order,
            self._order,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=_ACTIVITY_RETRY,
        )

        self._current_step = "completed"
        return "completed"
