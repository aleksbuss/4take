import asyncio

class TokenCache:
    def __init__(self, refresh):
        self._token = None
        self._refresh = refresh

    async def get(self):
        if self._token is None or self._token.expired:
            token = await self._refresh()
            self._token = token
        return self._token
