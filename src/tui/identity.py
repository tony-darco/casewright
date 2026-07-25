"""Single local user.

The web front-end identified users by JWT session cookie; the TUI is a local,
single-user tool, so there is no login. We resolve (and, on first run, create) one
fixed user row in the same SQLite DB and hand every service call its ``id``. The
password hash is a deliberately-unusable placeholder — nothing ever authenticates
against it.
"""

from web import db

LOCAL_USERNAME = "local"
# Not a valid bcrypt hash: no login path exists, and this guarantees nothing can
# ever verify against it if one is added later.
_UNUSABLE_HASH = "!"

_cached = None


def local_user() -> dict:
    """The local user dict (``{id, username, ...}``), created once on first run."""
    global _cached
    if _cached is not None:
        return _cached
    user = db.get_user_by_username(LOCAL_USERNAME)
    if user is None:
        user = db.create_user(LOCAL_USERNAME, _UNUSABLE_HASH,
                              first_name="Local", last_name="User")
    _cached = user
    return user
