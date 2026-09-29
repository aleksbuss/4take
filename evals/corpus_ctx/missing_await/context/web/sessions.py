from web.store import store


async def refresh_session(user_id):
    """Rotate the session id at login, so a session id fixed before login cannot be reused."""
    old = await store.current_session_id(user_id)
    await store.revoke(old)
    await store.issue(user_id)
