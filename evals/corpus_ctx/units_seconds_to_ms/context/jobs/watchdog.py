from jobs.timing import elapsed

TIMEOUT_SECONDS = 30


def check_stalled(jobs):
    """Cancel every job that has been running for longer than TIMEOUT_SECONDS."""
    for job in jobs:
        if job.running and elapsed(job.started_at) > TIMEOUT_SECONDS:
            job.cancel(reason="timeout")
