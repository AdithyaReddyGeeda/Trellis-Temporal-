import asyncio
import os
import uuid
from datetime import timedelta

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://trellis:trellis@localhost:5432/trellis",
)

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

os.environ.setdefault("SHIPPING_TASK_QUEUE", "shipping-tq")

from workflows.shipping_workflow import ShippingWorkflow

SHIPPING_TASK_QUEUE = os.environ["SHIPPING_TASK_QUEUE"]


@pytest.mark.asyncio
async def test_shipping_happy_path():
    @activity.defn(name="prepare_package")
    async def mock_prepare_package(order: dict) -> str:
        return "Package ready"

    @activity.defn(name="dispatch_carrier")
    async def mock_dispatch_carrier(order: dict) -> str:
        return "Dispatched"

    order = {"order_id": f"o-{uuid.uuid4().hex}", "items": [{"sku": "ABC", "qty": 1}]}
    wid = f"ship-{uuid.uuid4().hex}"

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        worker = Worker(
            client,
            task_queue=SHIPPING_TASK_QUEUE,
            workflows=[ShippingWorkflow],
            activities=[mock_prepare_package, mock_dispatch_carrier],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with worker:
            result = await asyncio.wait_for(
                client.execute_workflow(
                    ShippingWorkflow.run,
                    order,
                    id=wid,
                    task_queue=SHIPPING_TASK_QUEUE,
                    execution_timeout=timedelta(seconds=10),
                ),
                timeout=10.0,
            )

    assert result == "shipped"


@pytest.mark.asyncio
async def test_dispatch_carrier_retries():
    call_count = {"n": 0}

    @activity.defn(name="prepare_package")
    async def mock_prepare_package(order: dict) -> str:
        return "Package ready"

    @activity.defn(name="dispatch_carrier")
    async def mock_dispatch_carrier_retry(order: dict) -> str:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ApplicationError("fail", non_retryable=False)
        return "Dispatched"

    order = {"order_id": f"o-{uuid.uuid4().hex}", "items": [{"sku": "ABC", "qty": 1}]}
    wid = f"ship-{uuid.uuid4().hex}"

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        worker = Worker(
            client,
            task_queue=SHIPPING_TASK_QUEUE,
            workflows=[ShippingWorkflow],
            activities=[mock_prepare_package, mock_dispatch_carrier_retry],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with worker:
            result = await asyncio.wait_for(
                client.execute_workflow(
                    ShippingWorkflow.run,
                    order,
                    id=wid,
                    task_queue=SHIPPING_TASK_QUEUE,
                    execution_timeout=timedelta(seconds=10),
                ),
                timeout=10.0,
            )

    assert result == "shipped"
    assert call_count["n"] == 3
