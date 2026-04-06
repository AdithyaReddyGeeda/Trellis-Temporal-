import os
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from workflows.order_workflow import OrderWorkflow

load_dotenv()

ORDER_TASK_QUEUE = os.environ["ORDER_TASK_QUEUE"]


class StartOrderBody(BaseModel):
    payment_id: str


class UpdateAddressBody(BaseModel):
    address: Dict[str, Any]


def _temporal_http_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND:
        return JSONResponse(
            status_code=404,
            content={"error": "workflow not found"},
        )
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = await Client.connect(os.environ["TEMPORAL_HOST"])
    app.state.temporal_client = client
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/orders/{order_id}/start")
async def start_order(order_id: str, body: StartOrderBody):
    client: Client = app.state.temporal_client
    try:
        await client.start_workflow(
            OrderWorkflow.run,
            args=[order_id, body.payment_id],
            id=order_id,
            task_queue=ORDER_TASK_QUEUE,
            execution_timeout=timedelta(seconds=15),
        )
    except WorkflowAlreadyStartedError:
        return JSONResponse(
            status_code=409,
            content={"error": "workflow already running"},
        )
    except Exception as exc:
        return _temporal_http_error(exc)
    return {"workflow_id": order_id, "status": "started"}


@app.post("/orders/{order_id}/signals/cancel")
async def signal_cancel(order_id: str):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        await handle.signal(OrderWorkflow.cancel_order)
    except Exception as exc:
        return _temporal_http_error(exc)
    return {"signal": "cancel_order", "sent": True}


@app.post("/orders/{order_id}/signals/approve")
async def signal_approve(order_id: str):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        await handle.signal(OrderWorkflow.approve_order)
    except Exception as exc:
        return _temporal_http_error(exc)
    return {"signal": "approve_order", "sent": True}


@app.post("/orders/{order_id}/signals/update_address")
async def signal_update_address(order_id: str, body: UpdateAddressBody):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        await handle.signal(OrderWorkflow.update_address, body.address)
    except Exception as exc:
        return _temporal_http_error(exc)
    return {"signal": "update_address", "sent": True}


@app.get("/orders/{order_id}/status")
async def get_status(order_id: str):
    client: Client = app.state.temporal_client
    try:
        handle = client.get_workflow_handle(order_id)
        result = await handle.query(OrderWorkflow.status)
    except Exception as exc:
        return _temporal_http_error(exc)
    return result
