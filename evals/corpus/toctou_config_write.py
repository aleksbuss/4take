import os

def save_user_config(path, blob):
    if os.path.exists(path):
        if not os.access(path, os.W_OK):
            raise PermissionError(path)
        os.remove(path)
    with open(path, "w") as fh:
        fh.write(blob)
    os.chmod(path, 0o600)
