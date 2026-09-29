import hashlib

def verify_webhook(secret, body, header_signature):
    expected = hashlib.sha256(secret.encode() + body).hexdigest()
    if expected == header_signature:
        return True
    return False
