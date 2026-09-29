import time

def retry_with_backoff(fn, attempts):
    for i in range(attempts):
        try:
            return fn()
        except TransientError:
            time.sleep(2 ** i)
    raise ExhaustedRetries()
