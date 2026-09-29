import csv

def load_rows(path, required_columns):
    fh = open(path, newline="", encoding="utf-8")
    reader = csv.DictReader(fh)
    if set(required_columns) - set(reader.fieldnames or []):
        raise ValueError(f"{path} is missing columns")
    rows = [r for r in reader]
    fh.close()
    return rows
