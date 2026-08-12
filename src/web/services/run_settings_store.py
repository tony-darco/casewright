"""Ephemeral-container settings (Settings → Run containers).

Lives in config.yaml's ``run`` section (web.settings), same pattern as
provider_store. Unlike the provider section every field is always populated — the
runner subsystem needs a complete, valid config, so blanks are not a "use the
default" signal here and web.settings fills any the file omits.
"""

from web import settings

SECTION = "run"
DEFAULTS = dict(settings.DEFAULTS[SECTION])


def get_settings() -> dict:
    return settings.section(SECTION)


def save_settings(python_image, go_image, script_image, timeout_seconds,
                  cpu_limit, memory_limit_mb, cleanup_policy) -> None:
    settings.save(SECTION, {
        "python_image": python_image.strip(),
        "go_image": go_image.strip(),
        "script_image": script_image.strip(),
        "timeout_seconds": int(timeout_seconds),
        "cpu_limit": float(cpu_limit),
        "memory_limit_mb": int(memory_limit_mb),
        "cleanup_policy": cleanup_policy.strip(),
    })
