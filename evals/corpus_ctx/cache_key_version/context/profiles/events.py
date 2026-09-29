from profiles.store import redis


def on_profile_updated(user_id):
    """Drop the cached profile so the next read sees the change."""
    redis.delete(f"profile:{user_id}")
