import os
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from activities.shipping_activities import dispatch_carrier, prepare_package

SHIPPING_TASK_QUEUE = os.environ.get("SHIPPING_TASK_QUEUE", "shipping-tq")


@workflow.defn
class ShippingWorkflow:
    @workflow.run
    async def run(self, order: dict) -> str:
        activity_retry = RetryPolicy(
            maximum_attempts=5,
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2.0,
        )
        await workflow.execute_activity(
            prepare_package,
            order,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=activity_retry,
        )
        try:
            await workflow.execute_activity(
                dispatch_carrier,
                order,
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=activity_retry,
            )
        except ActivityError as e:
            cause = e.cause
            if isinstance(cause, ApplicationError):
                parent = workflow.info().parent
                if parent is not None:
                    handle = workflow.get_external_workflow_handle(
                        parent.workflow_id,
                        run_id=parent.run_id,
                    )
                    reason = cause.message or str(cause)
                    await handle.signal("dispatch_failed", reason)
            raise
        return "shipped"
