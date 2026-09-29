import os

def archive_directory(src, dest_name):
    cmd = f"tar czf /var/backups/{dest_name}.tar.gz {src}"
    return os.system(cmd)
