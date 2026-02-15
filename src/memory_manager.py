"""
Memory Manager — serce nowego systemu pamięci Przemusia.

Odpowiedzialności:
- CRUD na drzewie pamięci (MemoryNode + MemoryFact)
- Routing: przypisywanie faktów do węzłów
- Retrieval: wyszukiwanie relevantnych faktów (keyword matching, Faza 1)
- Process turn: chunk + staging + auto-commit
- Working state management (STM)
"""

import uuid
import os
from datetime import datetime
from database import db
from memory_models import (
    MemoryNode, MemoryFact, ConversationChunk,
    MemoryCandidate, WorkingState
)
from memory_extractor import extract_candidates


# === DOMYŚLNA STRUKTURA DRZEWA ===

DEFAULT_TREE = [
    {"node_id": "ROOT", "title": "Root", "parent": None},
    {"node_id": "ROOT/PREFERENCES", "title": "Preferencje", "parent": "ROOT"},
    {"node_id": "ROOT/PROJECTS", "title": "Projekty", "parent": "ROOT"},
    {"node_id": "ROOT/PERSONAL", "title": "Osobiste", "parent": "ROOT"},
    {"node_id": "ROOT/GENERAL", "title": "Ogólne", "parent": "ROOT"},
]

# Mapowanie target_hint → node_id
HINT_TO_NODE = {
    "PREFERENCES": "ROOT/PREFERENCES",
    "PROJECTS": "ROOT/PROJECTS",
    "PERSONAL": "ROOT/PERSONAL",
    "GENERAL": "ROOT/GENERAL",
    "ROOT": "ROOT",
}

# Progi auto-commitu
AUTO_COMMIT_CONFIDENCE = 0.8  # commit od razu jeśli confidence >= 0.8
COMMIT_ON_OCCURRENCES = 2      # commit jeśli info pojawiła się N razy


# === ENSURE TREE ===

def ensure_default_tree(user_id: int) -> None:
    """Tworzy domyślne drzewo pamięci dla użytkownika jeśli nie istnieje."""
    existing = MemoryNode.query.filter_by(user_id=user_id, node_id="ROOT").first()
    if existing:
        return
    
    node_map = {}
    for spec in DEFAULT_TREE:
        parent_db_id = None
        if spec["parent"] and spec["parent"] in node_map:
            parent_db_id = node_map[spec["parent"]].id
        
        node = MemoryNode(
            user_id=user_id,
            node_id=spec["node_id"],
            title=spec["title"],
            parent_id=parent_db_id
        )
        db.session.add(node)
        db.session.flush()  # żeby dostać id
        node_map[spec["node_id"]] = node
    
    db.session.commit()


# === NODE CRUD ===

def get_node(user_id: int, node_id: str) -> MemoryNode:
    """Pobiera węzeł po node_id."""
    return MemoryNode.query.filter_by(user_id=user_id, node_id=node_id).first()


def get_or_create_node(user_id: int, node_id: str, title: str = None) -> MemoryNode:
    """Pobiera lub tworzy węzeł."""
    node = get_node(user_id, node_id)
    if node:
        return node
    
    # Znajdź lub stwórz rodzica
    parts = node_id.rsplit("/", 1)
    parent_id = None
    if len(parts) > 1:
        parent_node = get_or_create_node(user_id, parts[0], parts[0].split("/")[-1])
        parent_id = parent_node.id
    
    node = MemoryNode(
        user_id=user_id,
        node_id=node_id,
        title=title or node_id.split("/")[-1],
        parent_id=parent_id
    )
    db.session.add(node)
    db.session.flush()
    return node


# === FACT CRUD ===

def add_fact(user_id: int, node_id: str, text: str, fact_type: str = 'fact',
             confidence: float = 0.8, pinned: bool = False,
             evidence: list = None) -> MemoryFact:
    """Dodaje atomowy fakt do węzła."""
    node = get_or_create_node(user_id, node_id)
    
    fact = MemoryFact(
        node_id=node.id,
        text=text,
        type=fact_type,
        confidence=confidence,
        pinned=pinned,
    )
    if evidence:
        fact.evidence = evidence
    
    db.session.add(fact)
    db.session.commit()
    return fact


def get_all_facts(user_id: int) -> list[MemoryFact]:
    """Zwraca wszystkie aktywne fakty użytkownika (nie wygasłe, nie zastąpione)."""
    nodes = MemoryNode.query.filter_by(user_id=user_id).all()
    node_ids = [n.id for n in nodes]
    
    facts = MemoryFact.query.filter(
        MemoryFact.node_id.in_(node_ids),
        MemoryFact.superseded_by.is_(None)
    ).all()
    
    # Filtruj wygasłe
    return [f for f in facts if not f.is_expired]


def get_facts_by_type(user_id: int, fact_type: str) -> list[MemoryFact]:
    """Zwraca fakty danego typu."""
    return [f for f in get_all_facts(user_id) if f.type == fact_type]


# === RETRIEVAL (Faza 1: keyword matching) ===

def get_preferences(user_id: int) -> list[str]:
    """Zwraca preferencje użytkownika jako listę stringów do promptu."""
    prefs = get_facts_by_type(user_id, 'pref')
    pinned = [f for f in get_all_facts(user_id) if f.pinned and f.type != 'pref']
    
    result = [f.text for f in prefs]
    result.extend([f"[pinned] {f.text}" for f in pinned])
    return result


def get_relevant_facts(user_id: int, query: str, budget_tokens: int = 600) -> list[str]:
    """
    Wyszukuje relevantne fakty za pomocą keyword matching.
    
    Scoring:
    - pinned/critical: +3
    - keyword match w tekście faktu: +2
    - keyword match w tytule węzła: +1
    - recency bonus: +0.5 (ostatnie 7 dni)
    """
    all_facts = get_all_facts(user_id)
    if not all_facts:
        return []
    
    # Przygotuj słowa kluczowe z query
    query_words = set(query.lower().split())
    # Usuń krótkie słowa (stopwords prosty)
    query_words = {w for w in query_words if len(w) > 2}
    
    if not query_words:
        # Jeśli brak słów kluczowych, zwróć pinned + najnowsze
        pinned = [f for f in all_facts if f.pinned]
        non_pinned = sorted(all_facts, key=lambda f: f.created_at, reverse=True)[:5]
        result = [f.text for f in pinned]
        result.extend([f.text for f in non_pinned if f.text not in result])
        return _trim_to_budget(result, budget_tokens)
    
    scored = []
    for fact in all_facts:
        score = 0.0
        fact_words = set(fact.text.lower().split())
        
        # Keyword match w tekście faktu
        overlap = query_words & fact_words
        if overlap:
            score += 2.0 * len(overlap) / len(query_words)
        
        # Keyword match w tytule węzła
        if fact.node:
            node_words = set(fact.node.title.lower().split())
            node_overlap = query_words & node_words
            if node_overlap:
                score += 1.0
        
        # Pinned bonus
        if fact.pinned:
            score += 3.0
        
        # Recency bonus (ostatnie 7 dni)
        days_old = (datetime.utcnow() - fact.created_at).days
        if days_old <= 7:
            score += 0.5
        
        # Confidence bonus
        score += fact.confidence * 0.3
        
        if score > 0:
            scored.append((fact, score))
    
    # Sortuj po score malejąco
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # Deduplikacja (MMR-light: pomijaj bardzo podobne fakty)
    result = []
    seen_words = set()
    for fact, score in scored:
        fact_words = set(fact.text.lower().split())
        # Jeśli >80% słów już widziane, pomijaj
        if seen_words and len(fact_words) > 0:
            overlap_ratio = len(fact_words & seen_words) / len(fact_words)
            if overlap_ratio > 0.8:
                continue
        
        result.append(fact.text)
        seen_words.update(fact_words)
    
    return _trim_to_budget(result, budget_tokens)


def _trim_to_budget(facts: list[str], budget_tokens: int) -> list[str]:
    """Przycina listę faktów do budżetu tokenów."""
    result = []
    used = 0
    for fact in facts:
        tokens = max(1, len(fact) // 4)
        if used + tokens <= budget_tokens:
            result.append(fact)
            used += tokens
        else:
            break
    return result


# === WORKING STATE (STM) ===

def get_working_state(conversation_id: str, user_id: int) -> WorkingState:
    """Pobiera lub tworzy working state dla rozmowy."""
    ws = WorkingState.query.filter_by(conversation_id=conversation_id).first()
    if not ws:
        ws = WorkingState(
            conversation_id=conversation_id,
            user_id=user_id
        )
        db.session.add(ws)
        db.session.commit()
    return ws


def update_working_state(conversation_id: str, user_id: int,
                         task: str = None, file_ref: str = None,
                         issues: list = None, tried: list = None,
                         constraints: list = None) -> WorkingState:
    """Aktualizuje working state (delta update)."""
    ws = get_working_state(conversation_id, user_id)
    
    if task is not None:
        ws.task = task
    if file_ref is not None:
        ws.file_ref = file_ref
    if issues is not None:
        ws.issues = issues
    if tried is not None:
        ws.tried = tried
    if constraints is not None:
        ws.constraints = constraints
    
    ws.last_updated = datetime.utcnow()
    db.session.commit()
    return ws


# === CONVERSATION CHUNKS ===

def save_chunk(user_id: int, conversation_id: str,
               user_msg: str, assistant_msg: str,
               tags: list[str] = None) -> ConversationChunk:
    """Zapisuje chunk logu rozmowy."""
    chunk = ConversationChunk(
        chunk_id=f"chunk_{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        conversation_id=conversation_id,
        tags=",".join(tags) if tags else ""
    )
    chunk.messages = [
        {"role": "user", "text": user_msg},
        {"role": "assistant", "text": assistant_msg}
    ]
    db.session.add(chunk)
    db.session.commit()
    return chunk


# === STAGING / COMMIT ===

def create_candidates(user_id: int, candidates_data: list[dict],
                      chunk_id: str = None) -> list[MemoryCandidate]:
    """Tworzy kandydatów w staging area."""
    created = []
    for c in candidates_data:
        # Sprawdź czy podobny kandydat już istnieje (pending)
        existing = _find_similar_candidate(user_id, c['text'])
        
        if existing:
            # Zwiększ occurrences
            existing.occurrences += 1
            existing.confidence = min(1.0, existing.confidence + 0.1)
            if existing.occurrences >= COMMIT_ON_OCCURRENCES:
                existing.stability = 'high'
            db.session.commit()
            created.append(existing)
        else:
            candidate = MemoryCandidate(
                user_id=user_id,
                text=c['text'],
                type=c.get('type', 'fact'),
                target_hint=c.get('target_hint', 'GENERAL'),
                confidence=c.get('confidence', 0.5),
                stability='low',
                status='pending'
            )
            if chunk_id:
                candidate.evidence = [chunk_id]
            db.session.add(candidate)
            created.append(candidate)
    
    db.session.commit()
    return created


def _find_similar_candidate(user_id: int, text: str) -> MemoryCandidate:
    """Szuka podobnego kandydata w staging (word overlap > 70%)."""
    pending = MemoryCandidate.query.filter_by(
        user_id=user_id, status='pending'
    ).all()
    
    new_words = set(text.lower().split())
    if not new_words:
        return None
    
    for candidate in pending:
        exist_words = set(candidate.text.lower().split())
        if not exist_words:
            continue
        overlap = len(new_words & exist_words) / max(len(new_words), len(exist_words))
        if overlap > 0.7:
            return candidate
    
    return None


def _is_duplicate_fact(user_id: int, text: str) -> bool:
    """Sprawdza czy podobny fakt już istnieje w LTM."""
    existing_facts = get_all_facts(user_id)
    new_words = set(text.lower().split())
    if not new_words:
        return False
    
    for fact in existing_facts:
        exist_words = set(fact.text.lower().split())
        if not exist_words:
            continue
        overlap = len(new_words & exist_words) / max(len(new_words), len(exist_words))
        if overlap > 0.7:
            return True
    
    return False


def auto_commit_candidates(user_id: int) -> int:
    """Auto-commituje kandydatów spełniających kryteria. Zwraca liczbę commitów."""
    pending = MemoryCandidate.query.filter_by(
        user_id=user_id, status='pending'
    ).all()
    
    committed = 0
    for candidate in pending:
        should_commit = (
            candidate.confidence >= AUTO_COMMIT_CONFIDENCE or
            candidate.occurrences >= COMMIT_ON_OCCURRENCES or
            candidate.type == 'pref'  # preferencje zawsze commitujemy
        )
        
        if should_commit:
            # Sprawdź duplikat w LTM
            if _is_duplicate_fact(user_id, candidate.text):
                candidate.status = 'rejected'
                db.session.commit()
                continue
            
            # Commit: przenieś do LTM
            node_id = HINT_TO_NODE.get(candidate.target_hint, "ROOT/GENERAL")
            
            add_fact(
                user_id=user_id,
                node_id=node_id,
                text=candidate.text,
                fact_type=candidate.type,
                confidence=candidate.confidence,
                pinned=(candidate.type == 'pref'),
                evidence=candidate.evidence
            )
            
            candidate.status = 'committed'
            candidate.committed_at = datetime.utcnow()
            committed += 1
    
    db.session.commit()
    return committed


def commit_candidates(user_id: int, candidate_ids: list[int] = None) -> int:
    """Ręczny commit wybranych kandydatów."""
    if candidate_ids:
        pending = MemoryCandidate.query.filter(
            MemoryCandidate.id.in_(candidate_ids),
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status == 'pending'
        ).all()
    else:
        pending = MemoryCandidate.query.filter_by(
            user_id=user_id, status='pending'
        ).all()
    
    committed = 0
    for candidate in pending:
        if _is_duplicate_fact(user_id, candidate.text):
            candidate.status = 'rejected'
            continue
        
        node_id = HINT_TO_NODE.get(candidate.target_hint, "ROOT/GENERAL")
        add_fact(
            user_id=user_id,
            node_id=node_id,
            text=candidate.text,
            fact_type=candidate.type,
            confidence=candidate.confidence,
            evidence=candidate.evidence
        )
        candidate.status = 'committed'
        candidate.committed_at = datetime.utcnow()
        committed += 1
    
    db.session.commit()
    return committed


# === PROCESS TURN (główny pipeline) ===

def process_turn(user_id: int, conversation_id: str,
                 user_msg: str, assistant_msg: str,
                 provider: str = 'groq', api_key: str = None) -> dict:
    """
    Pełny pipeline po każdej parze user/assistant:
    1. Zapisz chunk logu
    2. Ekstraktor → kandydaci (staging)
    3. Auto-commit wysokiej pewności
    
    Returns:
        {"chunk_id": str, "candidates": int, "committed": int}
    """
    # Upewnij się, że drzewo istnieje
    ensure_default_tree(user_id)
    
    # 1. Zapisz chunk
    chunk = save_chunk(user_id, conversation_id, user_msg, assistant_msg)
    
    # 2. Wyciągnij kandydatów
    existing_facts = [f.text for f in get_all_facts(user_id)]
    candidates_data = extract_candidates(
        user_msg, assistant_msg,
        existing_facts=existing_facts,
        provider=provider,
        api_key=api_key
    )
    
    candidates_count = 0
    if candidates_data:
        created = create_candidates(user_id, candidates_data, chunk.chunk_id)
        candidates_count = len(created)
    
    # 3. Auto-commit
    committed = auto_commit_candidates(user_id)
    
    return {
        "chunk_id": chunk.chunk_id,
        "candidates": candidates_count,
        "committed": committed
    }


# === MEMORY API (dla web.py) ===

def get_memory_entries(user_id: int) -> list[dict]:
    """Zwraca wszystkie fakty jako listę dict (dla API /api/memory)."""
    facts = get_all_facts(user_id)
    return [
        {
            'id': f.id,
            'type': f.type,
            'text': f.text,
            'confidence': f.confidence,
            'pinned': f.pinned,
            'node_path': f.node.node_id if f.node else 'unknown',
            'created_at': f.created_at.isoformat()
        }
        for f in facts
    ]


def delete_fact(fact_id: int, user_id: int) -> bool:
    """Usuwa fakt po ID (z weryfikacją usera)."""
    fact = db.session.get(MemoryFact, fact_id)
    if not fact or not fact.node:
        return False
    
    node = db.session.get(MemoryNode, fact.node_id)
    if not node or node.user_id != user_id:
        return False
    
    db.session.delete(fact)
    db.session.commit()
    return True


def update_fact(fact_id: int, user_id: int, new_text: str = None,
                new_pinned: bool = None) -> bool:
    """Aktualizuje fakt po ID."""
    fact = db.session.get(MemoryFact, fact_id)
    if not fact or not fact.node:
        return False
    
    node = db.session.get(MemoryNode, fact.node_id)
    if not node or node.user_id != user_id:
        return False
    
    if new_text is not None:
        fact.text = new_text
    if new_pinned is not None:
        fact.pinned = new_pinned
    
    fact.last_verified = datetime.utcnow()
    db.session.commit()
    return True


def clear_all_memory(user_id: int) -> None:
    """Czyści całą pamięć użytkownika (fakty + kandydaci + chunki + working states)."""
    # Pobierz węzły usera
    nodes = MemoryNode.query.filter_by(user_id=user_id).all()
    for node in nodes:
        # Usuń fakty węzła
        MemoryFact.query.filter_by(node_id=node.id).delete()
    
    # Usuń kandydatów
    MemoryCandidate.query.filter_by(user_id=user_id).delete()
    
    # Usuń chunki
    ConversationChunk.query.filter_by(user_id=user_id).delete()
    
    # Usuń working states
    WorkingState.query.filter_by(user_id=user_id).delete()
    
    # Usuń węzły
    # Najpierw dzieci, potem parents (żeby FK nie protestowały)
    for node in sorted(nodes, key=lambda n: n.node_id.count('/'), reverse=True):
        db.session.delete(node)
    
    db.session.commit()


def delete_facts_by_keyword(user_id: int, keyword: str) -> int:
    """Usuwa fakty zawierające słowo kluczowe. Zwraca liczbę usuniętych."""
    facts = get_all_facts(user_id)
    deleted = 0
    for fact in facts:
        if keyword.lower() in fact.text.lower():
            db.session.delete(fact)
            deleted += 1
    
    if deleted > 0:
        db.session.commit()
    return deleted
