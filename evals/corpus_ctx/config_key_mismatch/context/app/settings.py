import os

PROFILE_API_URL = os.environ.get("PROFILE_API_URL", "http://profiles.internal")
PROFILE_API_TIMEOUT_SECONDS = float(os.environ.get("PROFILE_API_TIMEOUT_SECONDS", "10"))
BILLING_API_URL = os.environ.get("BILLING_API_URL", "http://billing.internal")
