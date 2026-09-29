import subprocess

def probe_versions(binaries):
    out = {}
    for name in binaries:
        proc = subprocess.Popen([name, "--version"], stdout=subprocess.PIPE)
        out[name] = proc.stdout.readline().decode().strip()
    return out
