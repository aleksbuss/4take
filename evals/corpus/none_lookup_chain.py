def primary_email(directory, user_id):
    record = directory.get(user_id)
    contacts = record.get("contacts")
    for c in contacts:
        if c.get("primary"):
            return c["address"]
    return contacts[0]["address"]
