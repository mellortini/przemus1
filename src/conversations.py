"""
Moduł do zarządzania historią rozmów.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime

# Ścieżka do katalogu z rozmowami
CONVERSATIONS_DIR = Path(__file__).resolve().parent.parent / "data" / "conversations"


def ensure_dir():
    """Tworzy katalog rozmów jeśli nie istnieje."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def generate_id() -> str:
    """Generuje unikalne ID rozmowy."""
    return str(uuid.uuid4())[:8]


def get_conversation_path(conv_id: str) -> Path:
    """Zwraca ścieżkę do pliku rozmowy."""
    return CONVERSATIONS_DIR / f"{conv_id}.json"


def create_conversation(first_message: str = None) -> dict:
    """Tworzy nową rozmowę."""
    ensure_dir()
    
    conv_id = generate_id()
    now = datetime.now()
    
    # Generuj tytuł z pierwszej wiadomości lub domyślny
    if first_message:
        title = first_message[:50] + ("..." if len(first_message) > 50 else "")
    else:
        title = f"Nowa rozmowa"
    
    conversation = {
        "id": conv_id,
        "title": title,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "messages": []
    }
    
    save_conversation(conversation)
    return conversation


def save_conversation(conversation: dict) -> None:
    """Zapisuje rozmowę do pliku."""
    ensure_dir()
    
    conversation["updated_at"] = datetime.now().isoformat()
    path = get_conversation_path(conversation["id"])
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conversation, f, indent=2, ensure_ascii=False)


def load_conversation(conv_id: str) -> dict:
    """Ładuje rozmowę z pliku."""
    path = get_conversation_path(conv_id)
    
    if not path.exists():
        return None
    
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_conversation(conv_id: str) -> bool:
    """Usuwa rozmowę."""
    path = get_conversation_path(conv_id)
    
    if path.exists():
        path.unlink()
        return True
    return False


def list_conversations() -> list[dict]:
    """Zwraca listę wszystkich rozmów (posortowane od najnowszej)."""
    ensure_dir()
    
    conversations = []
    
    for path in CONVERSATIONS_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                conv = json.load(f)
                conversations.append({
                    "id": conv["id"],
                    "title": conv["title"],
                    "created_at": conv["created_at"],
                    "updated_at": conv["updated_at"],
                    "message_count": len(conv.get("messages", []))
                })
        except:
            continue
    
    # Sortuj od najnowszej
    conversations.sort(key=lambda x: x["updated_at"], reverse=True)
    return conversations


def update_title(conv_id: str, title: str) -> bool:
    """Aktualizuje tytuł rozmowy."""
    conv = load_conversation(conv_id)
    if conv:
        conv["title"] = title
        save_conversation(conv)
        return True
    return False


def add_message(conv_id: str, role: str, content: str) -> bool:
    """Dodaje wiadomość do rozmowy."""
    conv = load_conversation(conv_id)
    if conv:
        conv["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        
        # Aktualizuj tytuł jeśli to pierwsza wiadomość użytkownika
        if role == "user" and len([m for m in conv["messages"] if m["role"] == "user"]) == 1:
            conv["title"] = content[:50] + ("..." if len(content) > 50 else "")
        
        save_conversation(conv)
        return True
    return False
