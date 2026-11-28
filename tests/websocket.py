"""A WebSocket test client on asgiref's ApplicationCommunicator, so tests don't need
channels.testing (which imports the Daphne server)."""
import json

from asgiref.testing import ApplicationCommunicator


class WebsocketClient(ApplicationCommunicator):
    def __init__(self, application, path, user):
        super().__init__(application, {"type": "websocket", "path": path, "headers": [], "subprotocols": [],
                                       "user": user})

    async def connect(self, timeout=1):
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output(timeout)
        return response["type"] == "websocket.accept", response.get("code")

    async def receive_json_from(self, timeout=1):
        return json.loads((await self.receive_output(timeout))["text"])

    async def disconnect(self, code=1000, timeout=1):
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait(timeout)
