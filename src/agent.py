from llm import ask_llm
from memory import (
    load_profile, load_projects, load_memory, append_memory,
    load_current, load_goals, load_todos,
    load_today_session, load_yesterday_session, append_to_session,
    load_all_skills, get_today, get_timestamp,
    update_state_timestamp, CURRENT_PATH, TODOS_PATH,
    get_memory_entries, delete_memory_entries_by_keyword, clear_all_memory
)

# Maksymalna liczba wiadomości w oknie sesji
SESSION_WINDOW_SIZE = 10

SYSTEM_RULES = """Masz na imię Przemuś i jesteś osobistym asystentem użytkownika.

Twoje zasady:
- Bądź konkretny, logiczny i bez lania wody
- Odpowiadaj zwięźle i na temat
- Pamiętaj kontekst z poprzednich rozmów
- Pomagaj w realizacji projektów użytkownika
- Bądź proaktywny - przypominaj o deadlinach i celach
- Bądź pomocny i przyjazny

Masz dostęp do:
- Profilu użytkownika
- Aktualnego stanu (priorytety, otwarte wątki)
- Celów (zawodowych i osobistych)
- Listy zadań
- Aktywnych projektów
- Historii rozmów
"""


def build_context(session_window: list[dict]) -> list[dict]:
    """
    Buduje pełny kontekst dla LLM.
    
    Kolejność:
    1. System rules
    2. Profil użytkownika
    3. Aktualny stan (priorytety, otwarte wątki)
    4. Cele
    5. Lista zadań
    6. Aktywne projekty
    7. Skills (jeśli są)
    8. Pamięć (rolling summary)
    9. Ostatnie wiadomości z sesji
    """
    profile = load_profile()
    current = load_current()
    goals = load_goals()
    todos = load_todos()
    projects = load_projects()
    skills = load_all_skills()
    memory = load_memory()

    messages = [
        {"role": "system", "content": SYSTEM_RULES},
        {"role": "system", "content": f"PROFIL UŻYTKOWNIKA:\n{profile}"},
        {"role": "system", "content": f"AKTUALNY STAN:\n{current}"},
        {"role": "system", "content": f"CELE:\n{goals}"},
        {"role": "system", "content": f"ZADANIA:\n{todos}"},
        {"role": "system", "content": f"PROJEKTY:\n{projects}"},
    ]
    
    if skills:
        messages.append({"role": "system", "content": f"SKILLS:\n{skills}"})
    
    if memory:
        messages.append({"role": "system", "content": f"PAMIĘĆ:\n{memory}"})

    # Dodaj ostatnie wiadomości z bieżącej sesji
    messages.extend(session_window[-SESSION_WINDOW_SIZE:])
    
    return messages


def update_memory(user_msg: str, assistant_msg: str) -> None:
    """
    Generuje rolling summary rozmowy i zapisuje do pamięci.
    """
    prompt = f"""Przeanalizuj poniższą wymianę zdań i stwórz maksymalnie 6 krótkich punktów podsumowania.
Zapisuj TYLKO istotne informacje, które warto zapamiętać na przyszłość.
Jeśli rozmowa jest trywialna (powitanie, small talk), zwróć: "[BRAK]"

Typy wpisów:
[FACT] - fakty o użytkowniku lub świecie
[DECISION] - podjęte decyzje
[TODO] - nowe zadania do zrobienia
[PREF] - preferencje użytkownika

Format: jeden wpis na linię.

ROZMOWA:
USER: {user_msg}
ASSISTANT: {assistant_msg}

PODSUMOWANIE:"""

    messages = [
        {"role": "system", "content": "Jesteś modułem kompresji pamięci. Wyciągasz kluczowe informacje."},
        {"role": "user", "content": prompt}
    ]

    summary = ask_llm(messages)
    
    if "[BRAK]" not in summary:
        append_memory(summary)


# === KOMENDY ===

def cmd_plan() -> str:
    """
    Komenda /plan - Briefing na start dnia.
    Pokazuje priorytety, cele, deadliny, alerty.
    """
    today = get_today()
    current = load_current()
    goals = load_goals()
    todos = load_todos()
    projects = load_projects()
    
    # Sprawdź wczorajszą sesję dla kontekstu
    yesterday_session = load_yesterday_session()
    today_session = load_today_session()
    
    prompt = f"""Przygotuj zwięzły briefing na dzień {today}.

AKTUALNY STAN:
{current}

CELE:
{goals}

ZADANIA:
{todos}

PROJEKTY:
{projects}

WCZORAJSZA SESJA:
{yesterday_session if yesterday_session else "Brak"}

DZISIEJSZA SESJA DO TEJ PORY:
{today_session if today_session else "Nowa sesja"}

Stwórz briefing w formacie:
---
## Dzień: [data, dzień tygodnia]

**Priorytety na dziś:**
1. [najważniejsze]
2. [drugie]
3. [trzecie]

**Alerty:**
- [deadline'y, zaległości, rzeczy wymagające uwagi]

**Postęp celów:**
- [status kluczowych celów]

**Pytanie:** Nad czym chcesz dziś pracować?
---"""

    messages = [
        {"role": "system", "content": "Jesteś asystentem planowania dnia. Bądź konkretny i zwięzły."},
        {"role": "user", "content": prompt}
    ]

    return ask_llm(messages)


def cmd_update(session_window: list[dict]) -> str:
    """
    Komenda /update - Szybki checkpoint.
    Zapisuje postęp bez kończenia sesji.
    """
    if len(session_window) < 2:
        return "Brak rozmowy do zapisania."
    
    # Weź ostatnie wiadomości
    recent = session_window[-6:]  # max 3 pary user/assistant
    conversation = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in recent])
    
    prompt = f"""Stwórz krótkie podsumowanie ostatniej rozmowy (2-3 punkty).

ROZMOWA:
{conversation}

Format:
- [co zostało omówione/zrobione]"""

    messages = [
        {"role": "system", "content": "Twórz ultra-krótkie checkpointy. Max 3 punkty."},
        {"role": "user", "content": prompt}
    ]

    summary = ask_llm(messages)
    
    # Zapisz do sesji
    append_to_session(f"### Checkpoint\n{summary}")
    
    return f"Zapisano checkpoint:\n{summary}"


def cmd_end(session_window: list[dict]) -> str:
    """
    Komenda /end - Zakończenie sesji.
    Pełne podsumowanie, aktualizacja stanu, zapis.
    """
    if len(session_window) < 2:
        return "Brak rozmowy do podsumowania. Sesja zakończona."
    
    conversation = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in session_window])
    
    prompt = f"""Przygotuj podsumowanie sesji.

CAŁA ROZMOWA:
{conversation}

Stwórz podsumowanie w formacie:
---
## Podsumowanie Sesji

### Tematy
- [temat 1]
- [temat 2]

### Decyzje
- [decyzja 1] (jeśli były)

### Nowe zadania
- [TODO] (jeśli są)

### Otwarte wątki
- [rzeczy do kontynuacji]

### Następne kroki
- [co dalej]
---"""

    messages = [
        {"role": "system", "content": "Tworzysz podsumowania sesji. Bądź kompletny ale zwięzły."},
        {"role": "user", "content": prompt}
    ]

    summary = ask_llm(messages)
    
    # Zapisz do sesji
    append_to_session(f"### Koniec Sesji\n{summary}")
    
    # Aktualizuj timestampy w plikach stanu
    update_state_timestamp(CURRENT_PATH)
    update_state_timestamp(TODOS_PATH)
    
    return f"Sesja zakończona.\n\n{summary}"


# === KOMENDY PAMIĘCI ===

def cmd_memory() -> str:
    """
    Komenda /memory - pokazuje co Przemuś pamięta.
    """
    entries = get_memory_entries()
    
    if not entries:
        return "Nie mam jeszcze żadnych zapisanych informacji o Tobie."
    
    # Grupuj po tagach
    facts = [e for e in entries if e['tag'] == 'FACT']
    prefs = [e for e in entries if e['tag'] == 'PREF']
    todos = [e for e in entries if e['tag'] == 'TODO']
    decisions = [e for e in entries if e['tag'] == 'DECISION']
    
    result = ["**Co o Tobie pamiętam:**\n"]
    
    if facts:
        result.append("**Fakty:**")
        for e in facts:
            result.append(f"• {e['text']}")
        result.append("")
    
    if prefs:
        result.append("**Preferencje:**")
        for e in prefs:
            result.append(f"• {e['text']}")
        result.append("")
    
    if todos:
        result.append("**Zadania:**")
        for e in todos:
            result.append(f"• {e['text']}")
        result.append("")
    
    if decisions:
        result.append("**Decyzje:**")
        for e in decisions:
            result.append(f"• {e['text']}")
        result.append("")
    
    result.append("---")
    result.append("*Powiedz mi jeśli chcesz, żebym coś zapomniał (np. \"zapomnij o tradingu\")*")
    
    return "\n".join(result)


def cmd_forget(keyword: str) -> str:
    """
    Komenda /forget [słowo] - usuwa wpisy zawierające słowo.
    """
    if not keyword:
        return "Podaj co mam zapomnieć, np: /forget trading"
    
    count = delete_memory_entries_by_keyword(keyword)
    
    if count > 0:
        return f"Zapomniałem {count} rzeczy związanych z \"{keyword}\"."
    else:
        return f"Nie znalazłem nic związanego z \"{keyword}\" w mojej pamięci."


def cmd_forget_all() -> str:
    """
    Komenda /forget-all - czyści całą pamięć.
    """
    clear_all_memory()
    return "Wyczyściłem całą pamięć. Zaczynamy od nowa!"


# === LISTA KOMEND ===

COMMANDS = {
    "/plan": ("Briefing na start dnia", cmd_plan),
    "/update": ("Szybki checkpoint", cmd_update),
    "/end": ("Zakończ sesję z podsumowaniem", cmd_end),
    "/memory": ("Co o Tobie pamiętam", cmd_memory),
    "/forget": ("Zapomnij coś (np. /forget trading)", None),
    "/forget-all": ("Wyczyść całą pamięć", cmd_forget_all),
}


def get_commands_help() -> str:
    """Zwraca listę dostępnych komend."""
    lines = ["**Dostępne komendy:**"]
    lines.append("")
    lines.append("**Sesja:**")
    lines.append("  /plan - Briefing na start dnia")
    lines.append("  /update - Szybki checkpoint")
    lines.append("  /end - Zakończ sesję")
    lines.append("")
    lines.append("**Pamięć:**")
    lines.append("  /memory - Co o Tobie pamiętam")
    lines.append("  /forget [słowo] - Zapomnij rzeczy (np. /forget trading)")
    lines.append("  /forget-all - Wyczyść całą pamięć")
    lines.append("")
    lines.append("**Inne:**")
    lines.append("  /help - Ta lista")
    return "\n".join(lines)
