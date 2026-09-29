import zipfile

def unpack(archive_path, dest):
    with zipfile.ZipFile(archive_path) as zf:
        for member in zf.namelist():
            zf.extract(member, dest)
