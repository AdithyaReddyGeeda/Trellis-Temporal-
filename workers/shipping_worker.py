import asyncio
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from activities.shipping_activities import dispatch_carrier, prepare_package
from workflows.shipping_workflow import ShippingWorkflow

load_dotenv()

TEMPORAL_HOST = os.environ["TEMPORAL_HOST"]
SHIPPING_TASK_QUEUE = os.environ["SHIPPING_TASK_QUEUE"]


async def main() -> None:
    client = await Client.connect(TEMPORAL_HOST)
    worker = Worker(
        client,
        task_queue=SHIPPING_TASK_QUEUE,
        workflows=[ShippingWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activities=[
            prepare_package,
            dispatch_carrier,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
