"""
Common weak passwords — reject these on user creation and password change.
Small built-in list (spec AUTH-05). Can be extended later.
"""
COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwerty", "qwerty123", "admin", "admin123", "administrator",
    "welcome", "welcome1", "letmein", "changeme", "iloveyou", "monkey",
    "dragon", "master", "sunshine", "princess", "football", "baseball",
    "123123123", "12345678910", "11111111", "00000000", "abcd1234",
    "password!", "P@ssw0rd", "Passw0rd!", "companyname", "prfs",
}


def check_password_strength(password, username=""):
    """
    Returns (ok: bool, error_message: str|None).
    Rules (AUTH-05):
      - at least 10 characters
      - not equal to (or containing) the username
      - not in the common-password blacklist
    """
    if not password or len(password) < 10:
        return False, "Password must be at least 10 characters."

    if username and username.lower() in password.lower():
        return False, "Password cannot contain the username."

    if password.lower() in COMMON_PASSWORDS:
        return False, "Password is too common. Choose something stronger."

    return True, None