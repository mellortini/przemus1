"""
Migracja istniejącej pamięci z markdown (user.memory) do nowego systemu drzewiastego.

Uruchamiany automatycznie raz — sprawdza flagę w tabeli users.
"""

from database import db, User
from memory_manager import ensure_default_tree, add_fact


def parse_markdown_memory(memory_text: str) -> list[dict]:
    """Parsuje stary format markdown pamięci i zwraca listę faktów."""
    if not memory_text:
        return []
    
    facts = []
    current_date = None
    
    for line in memory_text.split('\n'):
        line = line.strip()
        
        # Nagłówek daty
        if line.startswith('### '):
            current_date = line[4:].strip()
            continue
        
        # Wpis z tagiem
        if line.startswith('['):
            bracket_end = line.find(']')
            if bracket_end > 0:
                tag = line[1:bracket_end].upper()
                text = line[bracket_end+1:].strip()
                
                if not text:
                    continue
                
                # Mapowanie starych tagów na nowe typy
                type_map = {
                    'FACT': 'fact',
                    'PREF': 'pref',
                    'TODO': 'todo',
                    'DECISION': 'decision',
                }
                fact_type = type_map.get(tag, 'fact')
                
                # Mapowanie na target_hint
                target = 'GENERAL'
                if fact_type == 'pref':
                    target = 'PREFERENCES'
                elif fact_type == 'todo':
                    target = 'GENERAL'
                
                facts.append({
                    'text': text,
                    'type': fact_type,
                    'target_hint': target,
                    'date': current_date,
                })
    
    return facts


# Mapowanie target_hint → node_id
HINT_TO_NODE = {
    "PREFERENCES": "ROOT/PREFERENCES",
    "PROJECTS": "ROOT/PROJECTS",
    "PERSONAL": "ROOT/PERSONAL",
    "GENERAL": "ROOT/GENERAL",
}


def migrate_user_memory(user: User) -> int:
    """
    Migruje pamięć jednego użytkownika z markdown do drzewa.
    Zwraca liczbę zmigrowanych faktów.
    """
    if not user.memory or user.memory.strip() == '# Log pamięci Przemusia':
        return 0
    
    # Utwórz domyślne drzewo
    ensure_default_tree(user.id)
    
    # Parsuj stary format
    old_facts = parse_markdown_memory(user.memory)
    
    if not old_facts:
        return 0
    
    # Deduplikacja na poziomie tekstu
    seen = set()
    migrated = 0
    
    for fact_data in old_facts:
        # Prosta deduplikacja
        clean = fact_data['text'].lower().strip()
        if clean in seen:
            continue
        seen.add(clean)
        
        node_id = HINT_TO_NODE.get(fact_data['target_hint'], 'ROOT/GENERAL')
        
        add_fact(
            user_id=user.id,
            node_id=node_id,
            text=fact_data['text'],
            fact_type=fact_data['type'],
            confidence=0.8,
            pinned=(fact_data['type'] == 'pref'),
        )
        migrated += 1
    
    return migrated


def run_migration(app) -> dict:
    """
    Uruchamia migrację dla wszystkich użytkowników.
    Zwraca podsumowanie: {"total_users": int, "migrated_users": int, "total_facts": int}
    """
    with app.app_context():
        users = User.query.all()
        total_facts = 0
        migrated_users = 0
        
        for user in users:
            # Sprawdź czy user ma starą pamięć
            if user.memory and len(user.memory.strip()) > 30:
                count = migrate_user_memory(user)
                if count > 0:
                    migrated_users += 1
                    total_facts += count
                    print(f"  [OK] {user.email}: {count} faktów zmigrowanych")
        
        db.session.commit()
        
        result = {
            "total_users": len(users),
            "migrated_users": migrated_users,
            "total_facts": total_facts
        }
        print(f"\nMigracja zakończona: {result}")
        return result
