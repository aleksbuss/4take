from app.mail import smtp
from app.users import find_user


def send_welcome_email(user_id):
    user = find_user(user_id)
    smtp.send(
        to=user.email,
        subject="Welcome aboard",
        body=f"Hi {user.name}, thanks for signing up.",
    )
