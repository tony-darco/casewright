"""Per-user model-provider settings (Settings → Model provider).

One row per user in SQLite (db.provider_settings). Blank fields mean "use the
backend default" — the .env / ProviderConfig defaults (rag.provider) stay
authoritative, the UI only layers overrides on top. ``overrides()`` maps just
the fields the user actually set onto ProviderConfig attribute names, so an
empty settings row leaves the pipeline exactly on its defaults.
"""

from web import db

DEFAULTS = {"provider": "ollama", "ollama_url": "", "chat_model": "", "embed_model": "", "temperature": None}


def get_settings(user_id: int) -> dict:
    with db.cursor() as conn:
        r = conn.execute(
            "SELECT provider, ollama_url, chat_model, embed_model, temperature "
            "FROM provider_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(r) if r else dict(DEFAULTS)


def save_settings(user_id: int, provider: str, ollama_url: str, chat_model: str,
                  embed_model: str, temperature) -> None:
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO provider_settings (user_id, provider, ollama_url, chat_model, embed_model, temperature) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET provider = excluded.provider, "
            "ollama_url = excluded.ollama_url, chat_model = excluded.chat_model, "
            "embed_model = excluded.embed_model, temperature = excluded.temperature",
            (user_id, provider.strip(), ollama_url.strip(), chat_model.strip(),
             embed_model.strip(), temperature),
        )


def overrides(user_id: int) -> dict:
    """The fields this user set, as ProviderConfig attribute overrides.
    ``{}`` when nothing is set (i.e. run entirely on backend defaults)."""
    s = get_settings(user_id)
    out = {}
    if s["ollama_url"]:
        out["provider"] = s["provider"] or "ollama"
        out["base_url"] = s["ollama_url"]
    if s["chat_model"]:
        out["chat_model"] = s["chat_model"]
    if s["embed_model"]:
        out["embed_model"] = s["embed_model"]
    if s["temperature"] is not None:
        out["temperature"] = float(s["temperature"])
    return out
