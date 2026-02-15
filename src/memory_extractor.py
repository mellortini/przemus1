"""
Memory Extractor — wyciąga atomowe fakty z rozmowy za pomocą LLM.

Zastępuje stary extract_memory_from_conversation().
Zwraca kandydatów w formacie strukturalnym (nie markdown).
"""

import json
from llm import ask_llm


EXTRACTOR_SYSTEM = """Jesteś modułem ekstrakcji pamięci. Twoje zadanie:
1. Przeanalizuj wymianę user/assistant
2. Wyciągnij TYLKO NOWE, ATOMOWE fakty (krótkie, 1-liniowe)
3. NIE powtarzaj informacji które już znasz (podane poniżej)
4. Bądź BARDZO selektywny — lepiej pominąć niż dodać coś banalnego

Zwróć JSON (i NIC więcej):
[
  {"type":"fact|pref|decision|todo|procedure","text":"krótki fakt","target_hint":"PREFERENCES|PROJECTS|PERSONAL|GENERAL","confidence":0.5-1.0}
]

Typy:
- fact: obiektywny fakt o użytkowniku (imię, umiejętności, sytuacja)
- pref: preferencja (język, styl, narzędzia)
- decision: podjęta decyzja
- todo: zadanie do zrobienia
- procedure: zasada/procedura do stosowania

target_hint: kategoria (PREFERENCES, PROJECTS, PERSONAL, GENERAL)
confidence: 0.5 (słabe) - 1.0 (pewne, wyraźnie powiedziane)

Jeśli nie ma nic nowego, zwróć: []"""


def extract_candidates(user_msg: str, assistant_msg: str,
                       existing_facts: list[str] = None,
                       provider: str = 'groq', api_key: str = None) -> list[dict]:
    """
    Wyciąga atomowe fakty z pary wiadomości.
    
    Args:
        user_msg: wiadomość użytkownika
        assistant_msg: odpowiedź asystenta
        existing_facts: lista istniejących faktów (do anty-duplikacji)
        provider: provider LLM
        api_key: klucz API
    
    Returns:
        Lista kandydatów: [{"type", "text", "target_hint", "confidence"}, ...]
    """
    # Przygotuj kontekst anty-duplikacji
    known_context = ""
    if existing_facts:
        # Ogranicz do ostatnich 30 faktów
        recent = existing_facts[-30:]
        known_context = f"\nCO JUŻ WIEM (NIE POWTARZAJ):\n" + "\n".join(f"- {f}" for f in recent) + "\n"
    
    prompt = f"""{known_context}
ROZMOWA:
User: {user_msg}
Assistant: {assistant_msg}

Wyciągnij NOWE atomowe fakty (JSON):"""

    try:
        result = ask_llm([
            {"role": "system", "content": EXTRACTOR_SYSTEM},
            {"role": "user", "content": prompt}
        ], provider=provider, api_key=api_key)
        
        # Parse JSON z odpowiedzi LLM
        return _parse_candidates(result)
    except Exception as e:
        print(f"[MemoryExtractor] Error: {e}")
        return []


def _parse_candidates(raw: str) -> list[dict]:
    """Parsuje odpowiedź LLM na listę kandydatów."""
    # Wyczyść odpowiedź — LLM czasem dodaje markdown code blocks
    raw = raw.strip()
    if raw.startswith("```"):
        # Usuń bloki kodu
        lines = raw.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw = "\n".join(lines)
    
    # Znajdź JSON array w odpowiedzi
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start == -1 or end == 0:
        return []
    
    try:
        candidates = json.loads(raw[start:end])
    except json.JSONDecodeError:
        return []
    
    # Walidacja i normalizacja
    valid = []
    valid_types = {'fact', 'pref', 'decision', 'todo', 'procedure', 'definition', 'project_state'}
    valid_hints = {'PREFERENCES', 'PROJECTS', 'PERSONAL', 'GENERAL', 'ROOT'}
    
    for c in candidates:
        if not isinstance(c, dict):
            continue
        if not c.get('text'):
            continue
        
        candidate = {
            'type': c.get('type', 'fact') if c.get('type') in valid_types else 'fact',
            'text': str(c['text']).strip(),
            'target_hint': c.get('target_hint', 'GENERAL') if c.get('target_hint') in valid_hints else 'GENERAL',
            'confidence': min(1.0, max(0.0, float(c.get('confidence', 0.7))))
        }
        valid.append(candidate)
    
    return valid
