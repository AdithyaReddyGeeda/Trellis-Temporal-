import asyncio
import os
import uuid

# db/__init__.py reads DATABASE_URL at import time
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://trellis:trellis@localhost:5432/trellis",
)
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

os.environ.setdefault("ORDER_TASK_QUEUE", "order-tq")
os.environ.setdefault("SHIPPING_TASK_QUEUE", "shipping-tq")

import business_logic.functions as bl
from workflows.order_workflow import OrderWorkflow
from workflows.shipping_workflow import ShippingWorkflow

ORDER_TASK_QUEUE = os.environ["ORDER_TASK_QUEUE"]
SHIPPING_TASK_QUEUE = os.environ["SHIPPING_TASK_QUEUE"]


def _default_order_mocks():
    @activity.defn(name="receive_order")
    async def mock_receive_order(order_id: str) -> dict:
        return {"order_id": order_id, "items": [{"sku": "ABC", "qty": 1}]}

    @activity.defn(name="validate_order")
    async def mock_validate_order(order: dict) -> bool:
        return True

    @activity.defn(name="charge_payment")
    async def mock_charge_payment(order: dict, payment_id: str) -> dict:
        amount = sum(i.get("qty", 1) for i in order.get("items", []))
        return {"status": "charged", "amount": amount}

    @activity.defn(name="ship_order")
    async def mock_ship_order(order: dict) -> str:
        return "Shipped"

    return (
        mock_receive_order,
        mock_validate_order,
        mock_charge_payment,
        mock_ship_order,
    )


def _default_shipping_mocks():
    @activity.defn(name="prepare_package")
    async def mock_prepare_package(order: dict) -> str:
        return "Package ready"

    @activity.defn(name="dispatch_carrier")
    async def mock_dispatch_carrier(order: dict) -> str:
        return "Dispatched"

    return mock_prepare_package, mock_dispatch_carrier


@pytest.mark.asyncio
async def test_order_happy_path():
    (
        mock_receive_order,
        mock_validate_order,
        mock_charge_payment,
        mock_ship_order,
    ) = _default_order_mocks()

    @activity.defn(name="update_order_address")
    async def mock_update_order_address(order_id: str, new_address: dict) -> None:
        return None

    mock_prepare, mock_dispatch = _default_shipping_mocks()

    order_id = f"order-{uuid.uuid4().hex}"
    payment_id = "pay-1"

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        order_worker = Worker(
            client,
            task_queue=ORDER_TASK_QUEUE,
            workflows=[OrderWorkflow],
            activities=[
                mock_receive_order,
                mock_validate_order,
                mock_charge_payment,
                mock_ship_order,
                mock_update_order_address,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        shipping_worker = Worker(
            client,
            task_queue=SHIPPING_TASK_QUEUE,
            workflows=[ShippingWorkflow],
            activities=[mock_prepare, mock_dispatch],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with order_worker:
            async with shipping_worker:
                task = asyncio.create_task(
                    asyncio.wait_for(
                        client.execute_workflow(
                            OrderWorkflow.run,
                            args=[order_id, payment_id],
                            id=order_id,
                            task_queue=ORDER_TASK_QUEUE,
                            execution_timeout=timedelta(seconds=10),
                        ),
                        timeout=10.0,
                    )
                )
                await asyncio.sleep(0.1)
                handle = client.get_workflow_handle(order_id)
                await handle.signal(OrderWorkflow.approve_order)
                result = await task

    assert result == "completed"


@pytest.mark.asyncio
async def test_order_cancelled_before_approval():
    (
        mock_receive_order,
        mock_validate_order,
        mock_charge_payment,
        mock_ship_order,
    ) = _default_order_mocks()

    @activity.defn(name="update_order_address")
    async def mock_update_order_address(order_id: str, new_address: dict) -> None:
        return None

    mock_prepare, mock_dispatch = _default_shipping_mocks()

    order_id = f"order-{uuid.uuid4().hex}"
    payment_id = "pay-1"

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        order_worker = Worker(
            client,
            task_queue=ORDER_TASK_QUEUE,
            workflows=[OrderWorkflow],
            activities=[
                mock_receive_order,
                mock_validate_order,
                mock_charge_payment,
                mock_ship_order,
                mock_update_order_address,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        shipping_worker = Worker(
            client,
            task_queue=SHIPPING_TASK_QUEUE,
            workflows=[ShippingWorkflow],
            activities=[mock_prepare, mock_dispatch],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with order_worker:
            async with shipping_worker:
                handle = await client.start_workflow(
                    OrderWorkflow.run,
                    args=[order_id, payment_id],
                    id=order_id,
                    task_queue=ORDER_TASK_QUEUE,
                    execution_timeout=timedelta(seconds=10),
                )
                await handle.signal(OrderWorkflow.cancel_order)
                result = await asyncio.wait_for(
                    handle.result(),
                    timeout=10.0,
                )

    assert result in ("cancelled", "cancelled_at_review")


@pytest.mark.asyncio
async def test_order_address_update():
    captured: List[Dict[str, Any]] = []

    (
        mock_receive_order,
        mock_validate_order,
        mock_charge_payment,
        mock_ship_order,
    ) = _default_order_mocks()

    @activity.defn(name="update_order_address")
    async def mock_update_order_address(order_id: str, new_address: dict) -> None:
        captured.append({"order_id": order_id, "address": dict(new_address)})

    mock_prepare, mock_dispatch = _default_shipping_mocks()

    order_id = f"order-{uuid.uuid4().hex}"
    payment_id = "pay-1"
    addr = {"street": "123 Test St"}

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        order_worker = Worker(
            client,
            task_queue=ORDER_TASK_QUEUE,
            workflows=[OrderWorkflow],
            activities=[
                mock_receive_order,
                mock_validate_order,
                mock_charge_payment,
                mock_ship_order,
                mock_update_order_address,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        shipping_worker = Worker(
            client,
            task_queue=SHIPPING_TASK_QUEUE,
            workflows=[ShippingWorkflow],
            activities=[mock_prepare, mock_dispatch],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with order_worker:
            async with shipping_worker:
                handle = await client.start_workflow(
                    OrderWorkflow.run,
                    args=[order_id, payment_id],
                    id=order_id,
                    task_queue=ORDER_TASK_QUEUE,
                    execution_timeout=timedelta(seconds=10),
                )
                await handle.signal(OrderWorkflow.update_address, addr)
                await asyncio.sleep(0.1)
                await handle.signal(OrderWorkflow.approve_order)
                await asyncio.wait_for(handle.result(), timeout=10.0)

    assert len(captured) == 1
    assert captured[0]["order_id"] == order_id
    assert captured[0]["address"] == addr


@pytest.mark.asyncio
async def test_payment_idempotency():
    @asynccontextmanager
    async def mock_get_db():
        session = MagicMock()
        session.execute = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        try:
            yield session
            await session.commit()
        except Exception:
            raise

    async def noop_flaky() -> None:
        return None

    order = {"order_id": "oid-1", "items": [{"sku": "ABC", "qty": 1}]}
    payment_id = "pay-same"

    with patch.object(bl, "log", MagicMock()):
        with patch.object(bl, "flaky_call", noop_flaky):
            with patch.object(bl, "get_db", mock_get_db):
                r1 = await bl.payment_charged(order, payment_id, db=None)
                r2 = await bl.payment_charged(order, payment_id, db=None)

    assert r1 == r2
    assert r1["status"] == "charged"
    assert r1["amount"] == 1


@pytest.mark.asyncio
async def test_shipping_dispatch_failure_retries_parent():
    (
        mock_receive_order,
        mock_validate_order,
        mock_charge_payment,
        mock_ship_order,
    ) = _default_order_mocks()

    @activity.defn(name="update_order_address")
    async def mock_update_order_address(order_id: str, new_address: dict) -> None:
        return None

    @activity.defn(name="prepare_package")
    async def mock_prepare_package(order: dict) -> str:
        return "Package ready"

    @activity.defn(name="dispatch_carrier")
    async def mock_dispatch_carrier_fail(order: dict) -> str:
        # non_retryable=True so activity does not retry 5x (fits 10s test budget)
        raise ApplicationError("carrier down", non_retryable=True)

    order_id = f"order-{uuid.uuid4().hex}"
    payment_id = "pay-1"

    async with await WorkflowEnvironment.start_local() as env:
        client = env.client
        order_worker = Worker(
            client,
            task_queue=ORDER_TASK_QUEUE,
            workflows=[OrderWorkflow],
            activities=[
                mock_receive_order,
                mock_validate_order,
                mock_charge_payment,
                mock_ship_order,
                mock_update_order_address,
            ],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        shipping_worker = Worker(
            client,
            task_queue=SHIPPING_TASK_QUEUE,
            workflows=[ShippingWorkflow],
            activities=[mock_prepare_package, mock_dispatch_carrier_fail],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        async with order_worker:
            async with shipping_worker:
                task = asyncio.create_task(
                    asyncio.wait_for(
                        client.execute_workflow(
                            OrderWorkflow.run,
                            args=[order_id, payment_id],
                            id=order_id,
                            task_queue=ORDER_TASK_QUEUE,
                            execution_timeout=timedelta(seconds=10),
                        ),
                        timeout=10.0,
                    )
                )
                await asyncio.sleep(0.1)
                handle = client.get_workflow_handle(order_id)
                await handle.signal(OrderWorkflow.approve_order)
                result = await task

    assert result == "dispatch_failed"
