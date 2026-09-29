from app.users import UserNotFound, find_user


def get_profile(request, user_id):
    try:
        user = find_user(user_id)
    except UserNotFound:
        return {"error": "not found"}, 404
    return {"email": user.email, "name": user.name}, 200
