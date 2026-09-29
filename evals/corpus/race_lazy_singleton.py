_client = None

def get_client(config):
    global _client
    if _client is None:
        _client = HttpClient(config)
        _client.connect()
    return _client
