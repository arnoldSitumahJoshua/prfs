import os
import uuid
from functools import wraps
from flask import abort, current_app
from flask_login import current_user
from argon2 import PasswordHasher

from models import db, AuditLog


# ---------- Password hashing (Argon2) ----------
_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except Exception:
        return False


# ---------- Role guard decorator ----------
def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------- Site isolation helper ----------
def site_scope(query, model):
    if current_user.role in ("officer", "viewer"):
        return query
    return query.filter(model.site_id == current_user.site_id)


# ---------- Audit trail ----------
def audit(action, entity, entity_id=None, old=None, new=None, ip=None):
    entry = AuditLog(
        actor_id=current_user.id if current_user.is_authenticated else None,
        role=current_user.role if current_user.is_authenticated else None,
        site_id=getattr(current_user, "site_id", None),
        action=action,
        entity=entity,
        entity_id=entity_id,
        old_value=str(old) if old is not None else None,
        new_value=str(new) if new is not None else None,
        ip=ip,
    )
    db.session.add(entry)
    return entry


# ---------- File upload helper ----------
def save_upload(file_storage):
    filename = file_storage.filename or ""
    if "." not in filename:
        raise ValueError("File has no extension")

    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError(f"File type .{ext} not allowed")

    stored_name = f"{uuid.uuid4().hex}.{ext}"
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, stored_name)
    file_storage.save(path)

    size = os.path.getsize(path)
    return stored_name, filename, ext, size


# ---------- Notification preference helper (NOT-11) ----------
def _pref_enabled(user_id, kind):
    """
    Return True if the user has that notification kind enabled.
    Default is ON if no preference row exists.
    """
    from models import SystemSetting
    key = f"notify_pref_{user_id}_{kind}"
    row = SystemSetting.query.filter_by(key=key).first()
    if not row or not row.value:
        return True
    return row.value.lower() == "on"


# ---------- Notifications ----------
def notify(user_id, kind, title, body=None, link=None):
    from models import Notification

    # NOT-11: respect the user's preference
    if not _pref_enabled(user_id, kind):
        return None

    n = Notification(
        user_id=user_id,
        kind=kind,
        title=title[:200],
        body=(body or "")[:500] or None,
        link=(link or "")[:255] or None,
    )
    db.session.add(n)
    return n


def notify_officers(kind, title, body=None, link=None):
    from models import User
    for u in User.query.filter_by(role="officer", active=True).all():
        notify(u.id, kind, title, body, link)


# ---------- Status colours (ADM-05) ----------
DEFAULT_STATUS_COLOURS = {
    "Processing": "#E0A82E",
    "Ordered":    "#7B4B7A",
    "Dispatched": "#C2602F",
}


def status_colours():
    """Return a dict of {status: hex colour} for the 3 officer statuses."""
    from models import SystemSetting
    result = {}
    for k, default in DEFAULT_STATUS_COLOURS.items():
        key = "status_colour_" + k.lower()
        row = SystemSetting.query.filter_by(key=key).first()
        result[k] = row.value if row and row.value else default
    return result


def status_chip_style(status):
    """Return an inline style string for a status chip, or empty if default."""
    colours = status_colours()
    hex_c = colours.get(status)
    if not hex_c:
        return ""
    return f"background:{hex_c}1A;color:{hex_c};border-color:{hex_c}66;"