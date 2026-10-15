"""
routes/websocket.py — WebSocket endpoint for real-time updates.
"""
import json
import time
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.state import AUTH_COOKIE
from core.deps import _session_user
from core.helpers import _serialize_applicants
from event_logger import EVENT_BROADCASTER, build_event

import logging

router = APIRouter()
logger = logging.getLogger("mahanbot")


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if not _session_user(websocket.cookies.get(AUTH_COOKIE)):
        await websocket.close(code=4401)
        return
    websocket.state.send_lock = asyncio.Lock()
    await EVENT_BROADCASTER.connect(websocket)
    logger.info("WebSocket connected: %s", websocket.client)

    async def _safe_send(payload: Dict[str, Any]) -> bool:
        try:
            async with websocket.state.send_lock:
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception:
            return False

    try:
        await _safe_send(build_event("applicants_snapshot", applicants=_serialize_applicants()))
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                if not await _safe_send({"type": "ping", "ts": time.time()}):
                    break
                continue
            except WebSocketDisconnect:
                break

            payload: Optional[Dict[str, Any]] = None
            try:
                payload = json.loads(message)
            except Exception:
                payload = None

            if payload and payload.get("type") == "ping":
                await _safe_send({"type": "pong", "ts": time.time()})
                continue
            if payload and payload.get("type") == "pong":
                continue
    except ConnectionResetError:
        pass
    except RuntimeError:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        EVENT_BROADCASTER.disconnect(websocket)
        logger.info("WebSocket disconnected: %s", websocket.client)
