import os

ROOT = "/srv/reports"

def read_report(name):
    full = os.path.join(ROOT, name)
    if not os.path.isfile(full):
        raise FileNotFoundError(name)
    with open(full, "rb") as fh:
        return fh.read()
