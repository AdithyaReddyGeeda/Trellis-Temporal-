"""Order workflow worker.

The Temporal Python SDK does not expose a default workflow_execution_timeout on
:class:`temporalio.worker.Worker`; enforce the 15s limit when starting workflows
via ``execution_timeout`` in ``api/main.py`` (``start_workflow``).
"""

import asyncio
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from activities.order_activities import (
    charge_payment,
    receive_order,
    ship_order,
    update_order_address,
    validate_order,
)
from workflows.order_workflow import OrderWorkflow

load_dotenv()

TEMPORAL_HOST = os.environ["TEMPORAL_HOST"]
ORDER_TASK_QUEUE = os.environ["ORDER_TASK_QUEUE"]


async def main() -> None:
    client = await Client.connect(TEMPORAL_HOST)
    worker = Worker(
        client,
        task_queue=ORDER_TASK_QUEUE,
        workflows=[OrderWorkflow],
        activities=[
            receive_order,
            validate_order,
            charge_payment,
            ship_order,
            update_order_address,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
