import os

def build_endpoint():
    host = os.environ.get("API_HOST")
    port = os.environ.get("API_PORT")
    return f"https://{host.strip()}:{int(port)}/v1"
