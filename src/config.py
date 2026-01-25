import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Ścieżki
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
SETTINGS_PATH = BASE_DIR / "settings.json"

# Ładuj .env
load_dotenv(ENV_PATH)

# Domyślne ustawienia
DEFAULT_SETTINGS = {
    "provider": "groq",
    "model": "llama-3.3-70b-versatile",
    "api_keys": {
        "openai": "",
        "groq": "",
        "anthropic": ""
    }
}

# Dostępni providerzy i ich modele
PROVIDERS = {
    "groq": {
        "name": "Groq (Darmowy)",
        "models": [
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B (polecany)"},
            {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B"},
            {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B (szybki)"},
            {"id": "gemma2-9b-it", "name": "Gemma 2 9B"},
            {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B"},
        ],
        "api_url": "https://console.groq.com/keys",
        "key_prefix": "gsk_"
    },
    "openai": {
        "name": "OpenAI (Płatny)",
        "models": [
            {"id": "gpt-4.1", "name": "GPT-4.1 (najnowszy)"},
            {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini (tańszy)"},
            {"id": "gpt-4.1-nano", "name": "GPT-4.1 Nano (najtańszy)"},
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "o3-mini", "name": "o3-mini (reasoning)"},
            {"id": "o1", "name": "o1 (reasoning, drogi)"},
        ],
        "api_url": "https://platform.openai.com/api-keys",
        "key_prefix": "sk-"
    },
    "anthropic": {
        "name": "Anthropic Claude (Płatny)",
        "models": [
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4 (najnowszy)"},
            {"id": "claude-opus-4-20250514", "name": "Claude Opus 4 (najmocniejszy)"},
            {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
            {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku (tańszy)"},
        ],
        "api_url": "https://console.anthropic.com/settings/keys",
        "key_prefix": "sk-ant-"
    }
}


def load_settings() -> dict:
    """Ładuje ustawienia z pliku JSON."""
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
                # Uzupełnij brakujące klucze
                for key, value in DEFAULT_SETTINGS.items():
                    if key not in settings:
                        settings[key] = value
                return settings
        except:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    """Zapisuje ustawienia do pliku JSON."""
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_api_key(provider: str) -> str:
    """Pobiera klucz API dla danego providera."""
    settings = load_settings()
    
    # Najpierw sprawdź ustawienia
    key = settings.get("api_keys", {}).get(provider, "")
    if key:
        return key
    
    # Potem zmienne środowiskowe
    env_vars = {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY"
    }
    return os.getenv(env_vars.get(provider, ""), "")


def get_current_provider() -> str:
    """Zwraca aktualnego providera."""
    return load_settings().get("provider", "groq")


def get_current_model() -> str:
    """Zwraca aktualny model."""
    return load_settings().get("model", "llama-3.3-70b-versatile")
