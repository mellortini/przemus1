from pathlib import Path
import datetime as dt

# Ścieżki do plików danych
BASE = Path(__file__).resolve().parent.parent / "data"

# Stare ścieżki (kompatybilność)
PROFILE_PATH = BASE / "profile.md"
PROJECTS_PATH = BASE / "projects.md"
MEMORY_PATH = BASE / "memory.md"

# Nowe ścieżki - state
STATE_DIR = BASE / "state"
CURRENT_PATH = STATE_DIR / "current.md"
GOALS_PATH = STATE_DIR / "goals.md"
TODOS_PATH = STATE_DIR / "todos.md"

# Sessions
SESSIONS_DIR = BASE / "sessions"

# Skills
SKILLS_DIR = BASE / "skills"


def read_file(path: Path) -> str:
    """Odczytuje plik jeśli istnieje, w przeciwnym razie zwraca pusty string."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_file(path: Path, content: str) -> None:
    """Zapisuje zawartość do pliku, tworząc katalogi jeśli potrzeba."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def get_today() -> str:
    """Zwraca dzisiejszą datę w formacie YYYY-MM-DD."""
    return dt.datetime.now().strftime("%Y-%m-%d")


def get_timestamp() -> str:
    """Zwraca aktualny timestamp."""
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# === STATE FUNCTIONS ===

def load_current() -> str:
    """Ładuje aktualny stan (priorytety, otwarte wątki)."""
    return read_file(CURRENT_PATH)


def load_goals() -> str:
    """Ładuje cele."""
    return read_file(GOALS_PATH)


def load_todos() -> str:
    """Ładuje listę zadań."""
    return read_file(TODOS_PATH)


def save_current(content: str) -> None:
    """Zapisuje aktualny stan."""
    write_file(CURRENT_PATH, content)


def save_todos(content: str) -> None:
    """Zapisuje listę zadań."""
    write_file(TODOS_PATH, content)


def update_state_timestamp(path: Path) -> None:
    """Aktualizuje timestamp w pliku stanu."""
    content = read_file(path)
    timestamp = get_timestamp()
    updated = content.replace("Ostatnia aktualizacja: ---", f"Ostatnia aktualizacja: {timestamp}")
    # Jeśli już był timestamp, zaktualizuj go
    import re
    updated = re.sub(
        r"Ostatnia aktualizacja: \d{4}-\d{2}-\d{2} \d{2}:\d{2}",
        f"Ostatnia aktualizacja: {timestamp}",
        updated
    )
    write_file(path, updated)


# === SESSION FUNCTIONS ===

def get_session_path(date: str = None) -> Path:
    """Zwraca ścieżkę do pliku sesji dla danej daty."""
    if date is None:
        date = get_today()
    return SESSIONS_DIR / f"{date}.md"


def load_session(date: str = None) -> str:
    """Ładuje log sesji dla danej daty."""
    return read_file(get_session_path(date))


def load_today_session() -> str:
    """Ładuje dzisiejszy log sesji."""
    return load_session(get_today())


def load_yesterday_session() -> str:
    """Ładuje wczorajszy log sesji."""
    yesterday = (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    return load_session(yesterday)


def append_to_session(entry: str, date: str = None) -> None:
    """Dopisuje wpis do logu sesji."""
    path = get_session_path(date)
    
    # Jeśli plik nie istnieje, utwórz z nagłówkiem
    if not path.exists():
        header = f"# Log Sesji: {date or get_today()}\n\n"
        write_file(path, header)
    
    timestamp = dt.datetime.now().strftime("%H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {timestamp}\n{entry}\n")


def create_session_summary(topics: list, decisions: list, todos: list, open_threads: list) -> str:
    """Tworzy podsumowanie sesji."""
    summary = []
    
    if topics:
        summary.append("### Tematy\n" + "\n".join(f"- {t}" for t in topics))
    
    if decisions:
        summary.append("### Decyzje\n" + "\n".join(f"- {d}" for d in decisions))
    
    if todos:
        summary.append("### Nowe zadania\n" + "\n".join(f"- {t}" for t in todos))
    
    if open_threads:
        summary.append("### Otwarte wątki\n" + "\n".join(f"- {t}" for t in open_threads))
    
    return "\n\n".join(summary)


# === MEMORY FUNCTIONS (rolling summary - kompatybilność) ===

def append_memory(entry: str) -> None:
    """Dopisuje wpis do logu pamięci z datą."""
    stamp = get_timestamp()
    with MEMORY_PATH.open("a", encoding="utf-8") as f:
        f.write(f"\n### {stamp}\n{entry}\n")


def load_memory() -> str:
    """Ładuje log pamięci."""
    return read_file(MEMORY_PATH)


def get_memory_size() -> int:
    """Zwraca rozmiar pliku pamięci w znakach."""
    return len(load_memory())


def get_memory_entries() -> list[dict]:
    """Parsuje memory.md i zwraca listę wpisów."""
    content = load_memory()
    entries = []
    
    current_date = None
    for line in content.split('\n'):
        line = line.strip()
        
        # Nagłówek daty
        if line.startswith('### '):
            current_date = line[4:]
            continue
        
        # Wpis z tagiem
        if line.startswith('['):
            bracket_end = line.find(']')
            if bracket_end > 0:
                tag = line[1:bracket_end]
                text = line[bracket_end+1:].strip()
                entries.append({
                    'id': len(entries),
                    'date': current_date,
                    'tag': tag,
                    'text': text
                })
    
    return entries


def delete_memory_entry(entry_text: str) -> bool:
    """Usuwa wpis z pamięci zawierający podany tekst."""
    content = load_memory()
    lines = content.split('\n')
    new_lines = []
    deleted = False
    
    for line in lines:
        # Sprawdź czy linia zawiera tekst do usunięcia (case insensitive)
        if entry_text.lower() in line.lower() and line.strip().startswith('['):
            deleted = True
            continue  # Pomiń tę linię
        new_lines.append(line)
    
    if deleted:
        write_file(MEMORY_PATH, '\n'.join(new_lines))
    
    return deleted


def delete_memory_entries_by_keyword(keyword: str) -> int:
    """Usuwa wszystkie wpisy zawierające słowo kluczowe. Zwraca liczbę usuniętych."""
    content = load_memory()
    lines = content.split('\n')
    new_lines = []
    deleted_count = 0
    
    for line in lines:
        if keyword.lower() in line.lower() and line.strip().startswith('['):
            deleted_count += 1
            continue
        new_lines.append(line)
    
    if deleted_count > 0:
        write_file(MEMORY_PATH, '\n'.join(new_lines))
    
    return deleted_count


def clear_all_memory() -> None:
    """Czyści całą pamięć."""
    write_file(
        MEMORY_PATH,
        "# Log pamięci Przemusia\n\n"
        "Wpisy: [FACT], [DECISION], [TODO], [PREF]\n\n---\n"
    )


# === SKILLS FUNCTIONS ===

def load_all_skills() -> str:
    """Ładuje wszystkie skills z katalogu skills/."""
    if not SKILLS_DIR.exists():
        return ""
    
    skills_content = []
    for skill_dir in SKILLS_DIR.iterdir():
        if skill_dir.is_dir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                content = read_file(skill_file)
                skills_content.append(f"### Skill: {skill_dir.name}\n{content}")
    
    return "\n\n---\n\n".join(skills_content)


def get_skill(name: str) -> str:
    """Ładuje konkretny skill po nazwie."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    return read_file(skill_path)


# === LEGACY FUNCTIONS ===

def load_profile() -> str:
    """Ładuje profil użytkownika."""
    return read_file(PROFILE_PATH)


def load_projects() -> str:
    """Ładuje listę aktywnych projektów."""
    return read_file(PROJECTS_PATH)


# === ENSURE STRUCTURE ===

def ensure_files() -> None:
    """Tworzy strukturę plików jeśli nie istnieje."""
    # Katalogi
    for dir_path in [BASE, STATE_DIR, SESSIONS_DIR, SKILLS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Profile
    if not PROFILE_PATH.exists():
        write_file(
            PROFILE_PATH,
            "# Profil użytkownika\n\n"
            "## Styl komunikacji\n"
            "- Konkretny i zwięzły\n"
            "- Bez emotikon\n"
            "- Bez lania wody\n\n"
            "## Preferencje\n"
            "- Język: polski\n"
        )

    # Projects
    if not PROJECTS_PATH.exists():
        write_file(
            PROJECTS_PATH,
            "# Aktywne projekty\n\n"
            "## Projekt 1\n"
            "- Status: w trakcie\n"
            "- Opis: \n"
        )

    # Memory (legacy)
    if not MEMORY_PATH.exists():
        write_file(
            MEMORY_PATH,
            "# Log pamięci agenta\n\n"
            "Wpisy: [FACT], [DECISION], [TODO], [PREF]\n\n---\n"
        )

    # State files
    if not CURRENT_PATH.exists():
        write_file(
            CURRENT_PATH,
            "# Aktualny Stan\n\n"
            "Ostatnia aktualizacja: ---\n\n"
            "## Aktywne Priorytety\n\n1. \n2. \n3. \n\n"
            "## Otwarte Wątki\n\n- \n\n"
            "## Ostatni Kontekst\n\n- Brak\n"
        )

    if not GOALS_PATH.exists():
        write_file(
            GOALS_PATH,
            "# Cele\n\n"
            "Ostatnia aktualizacja: ---\n\n"
            "## Cele Zawodowe\n\n- \n\n"
            "## Cele Osobiste\n\n- \n"
        )

    if not TODOS_PATH.exists():
        write_file(
            TODOS_PATH,
            "# Lista Zadań\n\n"
            "Ostatnia aktualizacja: ---\n\n"
            "## Aktywne\n\n- \n\n"
            "## Ukończone\n\n- \n"
        )
