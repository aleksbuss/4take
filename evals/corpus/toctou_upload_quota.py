def accept_upload(store, user, payload):
    used = store.bytes_used(user)
    if used + len(payload) > store.quota(user):
        raise QuotaExceeded(user)
    key = store.reserve_key(user)
    store.write(key, payload)
    store.bump_usage(user, len(payload))
    return key
