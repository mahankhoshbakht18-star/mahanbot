import asyncio
import json
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from fastapi import WebSocket

from database import DBHandler

EVENT_BUFFER_SIZE = 500


class EventBroadcaster:
    def __init__(self, max_events: int = EVENT_BUFFER_SIZE):
        self.active_connections: List[WebSocket] = []
        self.event_history: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        await self._send_history(websocket)
        await self.broadcast_event(
            build_event("metric", name="ws_clients", value=len(self.active_connections))
        )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            self.emit_event(build_event("metric", name="ws_clients", value=len(self.active_connections)))

    async def broadcast_event(self, event: Dict[str, Any]) -> None:
        self.event_history.append(event)
        if not self.active_connections:
            return
        stale_connections = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(json.dumps(event, ensure_ascii=False))
            except Exception:
                stale_connections.append(connection)
        for connection in stale_connections:
            self.disconnect(connection)

    def emit_event(self, event: Dict[str, Any]) -> None:
        if not self._loop or not self._loop.is_running():
            self.event_history.append(event)
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop and running_loop is self._loop:
            asyncio.create_task(self.broadcast_event(event))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast_event(event), self._loop)

    async def _send_history(self, websocket: WebSocket) -> None:
        for event in list(self.event_history):
            try:
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            except Exception:
                break


EVENT_BROADCASTER = EventBroadcaster()


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    EVENT_BROADCASTER.set_loop(loop)


def create_job_id() -> str:
    return uuid.uuid4().hex


def build_event(event_type: str, nid: Optional[str] = None, job_id: Optional[str] = None, **payload: Any) -> Dict[str, Any]:
    event = {
        "type": event_type,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ts": time.time(),
    }
    if nid is not None:
        event["nid"] = nid
    if job_id is not None:
        event["job_id"] = job_id
    event.update(payload)
    return event


def log_event(nid: str, job_id: Optional[str], message: str, level: str = "info") -> None:
    DBHandler.update_status(nid, level.title(), message)
    EVENT_BROADCASTER.emit_event(
        build_event("log", nid=nid, job_id=job_id, level=level, message=message)
    )


def job_status_event(nid: str, job_id: Optional[str], status: str, detail: Optional[str] = None) -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("job_status", nid=nid, job_id=job_id, status=status, detail=detail)
    )


def metric_event(name: str, value: Any, nid: Optional[str] = None, job_id: Optional[str] = None) -> None:
    EVENT_BROADCASTER.emit_event(
        build_event("metric", nid=nid, job_id=job_id, name=name, value=value)
    )


def build_log_callback(job_id: Optional[str]):
    def _callback(nid: str, message: str, level: str = "info") -> None:
        log_event(nid, job_id, message, level)

    return _callback
