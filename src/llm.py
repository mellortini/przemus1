from config import get_api_key, get_current_provider, get_current_model

# Cache dla klientów
_clients = {}


def get_client(provider: str, api_key: str = None):
    """Tworzy lub zwraca klienta dla danego providera."""
    global _clients
    
    # Jeśli nie podano api_key, pobierz z config
    if not api_key:
        api_key = get_api_key(provider)
    
    if not api_key:
        raise ValueError(f"Brak klucza API dla {provider}. Dodaj go w ustawieniach.")
    
    # Utwórz nowego klienta jeśli nie istnieje lub zmienił się klucz
    cache_key = f"{provider}:{api_key[:10]}"
    
    if cache_key not in _clients:
        if provider == "groq":
            from groq import Groq
            _clients[cache_key] = Groq(api_key=api_key)
        elif provider == "openai":
            from openai import OpenAI
            _clients[cache_key] = OpenAI(api_key=api_key)
        elif provider == "anthropic":
            from anthropic import Anthropic
            _clients[cache_key] = Anthropic(api_key=api_key)
    
    return _clients[cache_key]


def ask_llm(messages: list[dict], provider: str = None, model: str = None, api_key: str = None) -> str:
    """
    Wywołuje LLM API i zwraca odpowiedź.
    
    Args:
        messages: Lista wiadomości w formacie ChatML
        provider: Provider LLM (opcjonalnie, domyślnie z config)
        model: Model do użycia (opcjonalnie, domyślnie z config)
        api_key: Klucz API (opcjonalnie, domyślnie z config)
    """
    if not provider:
        provider = get_current_provider()
    if not model:
        model = get_current_model()
    
    client = get_client(provider, api_key)
    
    if provider == "anthropic":
        # Anthropic ma inny format API
        system_msg = ""
        user_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_msg += msg["content"] + "\n"
            else:
                user_messages.append(msg)
        
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_msg.strip(),
            messages=user_messages
        )
        return response.content[0].text.strip()
    else:
        # OpenAI i Groq mają ten sam format
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()


def test_connection(provider: str, api_key: str) -> tuple[bool, str]:
    """Testuje połączenie z API."""
    try:
        if provider == "groq":
            from groq import Groq
            client = Groq(api_key=api_key)
            client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            )
        elif provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            )
        elif provider == "anthropic":
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
            client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=5,
                messages=[{"role": "user", "content": "test"}]
            )
        return True, "Połączenie OK!"
    except Exception as e:
        return False, str(e)
