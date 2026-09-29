import asyncio

async def fan_out(urls, fetch):
    results = await asyncio.gather(*[fetch(u) for u in urls], return_exceptions=True)
    return [r for r in results if r]
