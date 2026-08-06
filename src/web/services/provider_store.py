"""Model-provider settings (Settings → Model provider).

Lives in config.yaml's ``provider`` section (web.settings). Blank fields mean "use
the backend default" — the ProviderConfig defaults (rag.provider) stay authoritative,
the file only layers overrides on top. ``overrides()`` maps just the fields actually
set onto ProviderConfig attribute names, so an untouched section leaves the pipeline
exactly on its defaults.
"""

from web import settings

SECTION = "provider"
DEFAULTS = dict(settings.DEFAULTS[SECTION])


def get_settings() -> dict:
    return settings.section(SECTION)


def save_settings(provider: str, ollama_url: str, chat_model: str, embed_model: str,
                  temperature, reasoning=None) -> None:
    settings.save(SECTION, {
        "provider": provider.strip(),
        "ollama_url": ollama_url.strip(),
        "chat_model": chat_model.strip(),
        "embed_model": embed_model.strip(),
        "temperature": None if temperature is None else float(temperature),
        "reasoning": None if reasoning is None else bool(reasoning),
    })


def overrides() -> dict:
    """The fields that are set, as ProviderConfig attribute overrides.
    ``{}`` when nothing is set (i.e. run entirely on backend defaults)."""
    s = get_settings()
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
    if s["reasoning"] is not None:
        out["reasoning"] = bool(s["reasoning"])
    return out
