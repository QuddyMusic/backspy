"""Owner-side REST: create and manage bots with a human account's JWT."""

import os

from .http import HTTPClient
from .models import Object


class OwnerClient:
    """Manage bots as the human owner (Bearer JWT).

        async with backspy.OwnerClient(HUMAN_JWT) as owner:
            summary, token = await owner.create_bot("helper")
    """

    def __init__(self, jwt, *, base_url=None, session=None):
        base_url = (base_url or os.environ.get("BACKSPACE_URL") or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must look like https://<instance> "
                             "(argument or BACKSPACE_URL env)")
        self.http = HTTPClient(base_url, jwt, auth_scheme="Bearer", session=session)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        await self.http.close()

    async def _get_list(self, path, key):
        data = await self.http.request("GET", path)
        return (data or {}).get(key, []) if isinstance(data, dict) else []

    async def list_bots(self):
        return await self._get_list("/api/bots", "bots")

    async def create_bot(self, name):
        """Create a bot; returns (summary, token). The token is shown once."""
        data = await self.http.request("POST", "/api/bots", json={"name": name})
        return Object((data or {}).get("bot")), (data or {}).get("token")

    async def edit_bot(self, bot_id, *, display_name=None, avatar=None):
        payload = {}
        if display_name is not None:
            payload["displayName"] = display_name
        if avatar is not None:
            payload["avatar"] = avatar
        return Object(await self.http.request("PATCH", f"/api/bots/{bot_id}", json=payload))

    async def regenerate_token(self, bot_id):
        """New token; every earlier token stops working, sockets drop."""
        return Object(await self.http.request("POST", f"/api/bots/{bot_id}/token"))

    async def delete_bot(self, bot_id):
        return Object(await self.http.request("DELETE", f"/api/bots/{bot_id}"))

    async def search_bots(self, query):
        data = await self.http.request("GET", "/api/bots/search", params={"q": query})
        return (data or {}).get("bots", []) if isinstance(data, dict) else []

    async def bot_spaces(self, bot_id):
        return await self._get_list(f"/api/bots/{bot_id}/spaces", "spaces")

    async def add_bot_to_space(self, bot_id, space_id):
        return Object(await self.http.request("POST", f"/api/bots/{bot_id}/spaces",
                                              json={"spaceId": space_id}))

    async def remove_bot_from_space(self, bot_id, space_id):
        return Object(await self.http.request("DELETE", f"/api/bots/{bot_id}/spaces/{space_id}"))