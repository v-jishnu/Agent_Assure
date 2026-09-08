"""
WebSocket Event Manager for AgentAssure (Week 2)
"""

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import WebSocket
from agentassure.logging import log_runtime
from agentassure.publisher import default_event_publisher


class WebSocketManager:
    """
    Manages active WebSocket client connections and relays canonical
    AgentEvents published by the AgentAssure runtime in real time.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Auto-subscribe to the in-process publisher
        default_event_publisher.subscribe(self._handle_published_event)

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def _handle_published_event(self, event_data: Dict[str, Any]):
        if not self.active_connections:
            return

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(event_data), loop)

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        log_runtime(
            level="INFO",
            component="websocket",
            message=f"WebSocket client connected. Total clients: {len(self.active_connections)}",
        )

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            log_runtime(
                level="INFO",
                component="websocket",
                message=f"WebSocket client disconnected. Total clients: {len(self.active_connections)}",
            )

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return

        to_remove = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                to_remove.append(connection)
                log_runtime(
                    level="WARN",
                    component="websocket",
                    message=f"WebSocket send error, disconnecting client: {e}",
                )

        for conn in to_remove:
            self.disconnect(conn)


# Global manager instance
ws_manager = WebSocketManager()
