from accounts.identity import normalize_email
from accounts.models import Account


def find_account(email):
    return Account.objects.filter(email=normalize_email(email)).first()
