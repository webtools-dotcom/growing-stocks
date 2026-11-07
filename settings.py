"""User settings. API keys live in the OS credential store (Windows Credential Manager / macOS Keychain /
Secret Service) via `keyring` — never in a plain file, never in the repo. Non-secret choices (provider, model)
go to data/settings.json. Environment variables / .env still work and take priority (handy for servers/CI).
"""
import os
from pathlib import Path

import keyring
from safeio import read_json, save_json

SERVICE = "rising-stocks-terminal"
FILE = Path(__file__).resolve().parent / "data" / "settings.json"

# provider -> (env var litellm reads, default model, where to get a key)
PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", "gemini/gemini-3.5-flash-lite", "https://aistudio.google.com/apikey"),
    "anthropic": ("ANTHROPIC_API_KEY", "anthropic/claude-haiku-5-5", "https://console.anthropic.com/settings/keys"),
    "openai": ("OPENAI_API_KEY", "openai/<model>", "https://platform.openai.com/api-keys"),
    "groq": ("GROQ_API_KEY", "groq/<model>", "https://console.groq.com/keys"),
    "openrouter": ("OPENROUTER_API_KEY", "openrouter/<model>", "https://openrouter.ai/keys"),
}


# what the real environment / .env provided at startup (apply() later writes to os.environ itself)
_ENV = {v: os.getenv(v) for v in [p[0] for p in PROVIDERS.values()] + ["DEFAULT_LLM_MODEL"]}


def load():
    s = read_json(FILE, {})
    s.setdefault("provider", "gemini")
    s.setdefault("model", PROVIDERS[s["provider"]][1])
    return s


def get_key(provider):
    env = PROVIDERS[provider][0]
    if _ENV.get(env):
        return _ENV[env]
    try:
        return keyring.get_password(SERVICE, env)
    except Exception:  # no usable keychain on this machine: settings UI says so
        return None


def save(provider, model, key=None):
    """key=None keeps the stored key; key="" deletes it."""
    FILE.parent.mkdir(exist_ok=True)
    save_json(FILE, {"provider": provider, "model": model.strip()}, indent=2)
    env = PROVIDERS[provider][0]
    if key == "":
        try:
            keyring.delete_password(SERVICE, env)
        except keyring.errors.PasswordDeleteError:
            pass
        os.environ.pop(env, None)
    elif key:
        keyring.set_password(SERVICE, env, key.strip())
    apply()


def apply():
    """Expose the configured key/model to this process (litellm reads env vars). Called at import of config."""
    s = load()
    os.environ["DEFAULT_LLM_MODEL"] = _ENV["DEFAULT_LLM_MODEL"] or s["model"]
    key = get_key(s["provider"])
    if key:
        os.environ[PROVIDERS[s["provider"]][0]] = key


def ai_ready():
    return bool(get_key(load()["provider"]))


def masked(provider):
    k = get_key(provider)
    if not k:
        return "not set"
    return f"····{k[-4:]} ({'from .env — overrides this screen' if _ENV.get(PROVIDERS[provider][0]) else 'in OS keychain'})"


def model_locked():
    """True when .env pins the model (then the settings screen can't change it)."""
    return bool(_ENV["DEFAULT_LLM_MODEL"])


def keychain_ok():
    try:
        keyring.get_password(SERVICE, "__probe__")
        return True
    except Exception:
        return False
