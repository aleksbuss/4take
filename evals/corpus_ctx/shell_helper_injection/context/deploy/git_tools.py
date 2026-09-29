import subprocess

REPO = "/srv/app"


def run_git(*args):
    command = "git -C " + REPO + " " + " ".join(args)
    return subprocess.run(command, shell=True, capture_output=True, text=True, check=True).stdout
