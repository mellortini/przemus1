"""
Test suite for the new memory system (Faza 1 MVP).

Tests: memory models, context packer, memory manager, migration.
Does NOT test memory extractor (requires LLM calls).

Usage:
    cd d:\\projekty\\Billy\\ai-os-agent\\src
    python -m pytest test_memory_system.py -v
"""

import pytest
import sys
import os
import json

# Ensure src is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def app():
    """Create a test Flask app with in-memory SQLite."""
    from flask import Flask
    from database import db
    
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['TESTING'] = True
    
    db.init_app(app)
    
    with app.app_context():
        import memory_models  # noqa: F401
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def test_user(app):
    """Create a test user."""
    from database import db, User
    
    with app.app_context():
        user = User(
            google_id='test123',
            email='test@example.com',
            name='Test User',
            memory='# Log pamięci Przemusia\n\n### 2025-01-01\n[FACT] Ma na imię Mateusz\n[PREF] Preferuje język polski\n[TODO] Nauczyć się niemieckiego\n[DECISION] Wybrał Groq jako provider\n'
        )
        db.session.add(user)
        db.session.commit()
        yield user


# ============================================
# Test: Memory Models
# ============================================

class TestMemoryModels:
    
    def test_memory_node_creation(self, app, test_user):
        from memory_models import MemoryNode
        from database import db
        
        with app.app_context():
            node = MemoryNode(
                user_id=test_user.id,
                node_id='ROOT',
                title='Root'
            )
            db.session.add(node)
            db.session.commit()
            
            assert node.id is not None
            assert node.node_id == 'ROOT'
            assert node.title == 'Root'
    
    def test_memory_fact_creation(self, app, test_user):
        from memory_models import MemoryNode, MemoryFact
        from database import db
        
        with app.app_context():
            node = MemoryNode(user_id=test_user.id, node_id='ROOT', title='Root')
            db.session.add(node)
            db.session.flush()
            
            fact = MemoryFact(
                node_id=node.id,
                text='Ma na imię Mateusz',
                type='fact',
                confidence=0.9
            )
            db.session.add(fact)
            db.session.commit()
            
            assert fact.id is not None
            assert fact.text == 'Ma na imię Mateusz'
            assert fact.type == 'fact'
            assert not fact.is_expired
    
    def test_working_state_prompt(self, app, test_user):
        from memory_models import WorkingState
        from database import db
        
        with app.app_context():
            ws = WorkingState(
                conversation_id='conv123',
                user_id=test_user.id,
                task='Refaktor systemu pamięci',
            )
            ws.issues = ['Brak testów']
            ws.tried = ['Flat markdown']
            db.session.add(ws)
            db.session.commit()
            
            prompt = ws.to_prompt_string()
            assert 'TASK: Refaktor systemu pamięci' in prompt
            assert 'ISSUES:' in prompt
            assert '- Brak testów' in prompt
            assert 'TRIED:' in prompt


# ============================================
# Test: Memory Manager
# ============================================

class TestMemoryManager:
    
    def test_ensure_default_tree(self, app, test_user):
        import memory_manager as mm
        from memory_models import MemoryNode
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            
            root = MemoryNode.query.filter_by(
                user_id=test_user.id, node_id='ROOT'
            ).first()
            assert root is not None
            
            prefs = MemoryNode.query.filter_by(
                user_id=test_user.id, node_id='ROOT/PREFERENCES'
            ).first()
            assert prefs is not None
            assert prefs.parent_id == root.id
    
    def test_ensure_default_tree_idempotent(self, app, test_user):
        import memory_manager as mm
        from memory_models import MemoryNode
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            mm.ensure_default_tree(test_user.id)
            
            roots = MemoryNode.query.filter_by(
                user_id=test_user.id, node_id='ROOT'
            ).all()
            assert len(roots) == 1
    
    def test_add_fact(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            
            fact = mm.add_fact(
                user_id=test_user.id,
                node_id='ROOT/GENERAL',
                text='Test fact',
                fact_type='fact',
                confidence=0.9
            )
            assert fact.id is not None
            assert fact.text == 'Test fact'
    
    def test_get_preferences(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            
            mm.add_fact(test_user.id, 'ROOT/PREFERENCES', 'lang=pl', 'pref', 0.9, pinned=True)
            mm.add_fact(test_user.id, 'ROOT/PREFERENCES', 'dark_mode=true', 'pref', 0.8, pinned=True)
            mm.add_fact(test_user.id, 'ROOT/GENERAL', 'Some fact', 'fact', 0.7)
            
            prefs = mm.get_preferences(test_user.id)
            assert 'lang=pl' in prefs
            assert 'dark_mode=true' in prefs
    
    def test_get_relevant_facts_keyword(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            
            mm.add_fact(test_user.id, 'ROOT/PROJECTS', 'Trading bot w Pythonie', 'fact', 0.9)
            mm.add_fact(test_user.id, 'ROOT/PERSONAL', 'Ma na imię Mateusz', 'fact', 0.9)
            mm.add_fact(test_user.id, 'ROOT/GENERAL', 'Nie powiązany fakt', 'fact', 0.5)
            
            # Query o trading
            results = mm.get_relevant_facts(test_user.id, 'trading bot')
            assert any('Trading' in r or 'trading' in r.lower() for r in results)
    
    def test_working_state_crud(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            ws = mm.get_working_state('conv123', test_user.id)
            assert ws is not None
            assert ws.task == ''
            
            ws2 = mm.update_working_state('conv123', test_user.id, 
                                           task='Budowa nowego systemu')
            assert ws2.task == 'Budowa nowego systemu'
    
    def test_candidate_staging_and_commit(self, app, test_user):
        import memory_manager as mm
        from memory_models import MemoryCandidate
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            
            candidates = mm.create_candidates(test_user.id, [
                {'text': 'High confidence fact', 'type': 'fact', 
                 'target_hint': 'GENERAL', 'confidence': 0.95},
                {'text': 'Low confidence fact', 'type': 'fact', 
                 'target_hint': 'GENERAL', 'confidence': 0.3},
                {'text': 'Preference always commits', 'type': 'pref',
                 'target_hint': 'PREFERENCES', 'confidence': 0.5},
            ])
            
            assert len(candidates) == 3
            
            # Auto-commit 
            committed = mm.auto_commit_candidates(test_user.id)
            # High confidence (>0.8) + pref = 2 committed
            assert committed == 2
            
            # Check low confidence is still pending
            pending = MemoryCandidate.query.filter_by(
                user_id=test_user.id, status='pending'
            ).all()
            assert len(pending) == 1
            assert pending[0].text == 'Low confidence fact'
    
    def test_duplicate_candidate_increments_occurrences(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            
            mm.create_candidates(test_user.id, [
                {'text': 'Same fact about Python', 'type': 'fact',
                 'target_hint': 'GENERAL', 'confidence': 0.4}
            ])
            
            # Add similar candidate again
            mm.create_candidates(test_user.id, [
                {'text': 'Same fact about Python', 'type': 'fact',
                 'target_hint': 'GENERAL', 'confidence': 0.4}
            ])
            
            from memory_models import MemoryCandidate
            candidate = MemoryCandidate.query.filter_by(
                user_id=test_user.id, status='pending'
            ).first()
            assert candidate.occurrences >= 2
    
    def test_memory_entries_api(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            mm.add_fact(test_user.id, 'ROOT/GENERAL', 'API test fact', 'fact', 0.9)
            
            entries = mm.get_memory_entries(test_user.id)
            assert len(entries) >= 1
            assert entries[0]['text'] == 'API test fact'
            assert entries[0]['type'] == 'fact'
            assert 'id' in entries[0]
    
    def test_delete_fact(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            fact = mm.add_fact(test_user.id, 'ROOT/GENERAL', 'To delete', 'fact')
            
            success = mm.delete_fact(fact.id, test_user.id)
            assert success is True
            
            entries = mm.get_memory_entries(test_user.id)
            assert not any(e['text'] == 'To delete' for e in entries)
    
    def test_delete_facts_by_keyword(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            mm.add_fact(test_user.id, 'ROOT/PROJECTS', 'Trading bot w Pythonie', 'fact')
            mm.add_fact(test_user.id, 'ROOT/GENERAL', 'Lubi kawę', 'fact')
            
            deleted = mm.delete_facts_by_keyword(test_user.id, 'trading')
            assert deleted == 1
    
    def test_clear_all_memory(self, app, test_user):
        import memory_manager as mm
        
        with app.app_context():
            mm.ensure_default_tree(test_user.id)
            mm.add_fact(test_user.id, 'ROOT/GENERAL', 'Fact 1', 'fact')
            mm.add_fact(test_user.id, 'ROOT/PREFERENCES', 'Pref 1', 'pref')
            
            mm.clear_all_memory(test_user.id)
            
            entries = mm.get_memory_entries(test_user.id)
            assert len(entries) == 0


# ============================================
# Test: Context Packer
# ============================================

class TestContextPacker:
    
    def test_basic_packing(self, app):
        from context_packer import pack_context
        
        messages = pack_context(
            system_prompt='You are an assistant',
            preferences=['lang=pl'],
            working_state_str='',
            relevant_facts=['Ma na imię Mateusz'],
            conversation_messages=[],
            user_message='Cześć!',
            budget=3000
        )
        
        # At minimum: system prompt + user message
        assert len(messages) >= 2
        assert messages[0]['role'] == 'system'
        assert messages[-1]['role'] == 'user'
        assert messages[-1]['content'] == 'Cześć!'
    
    def test_packing_with_preferences(self, app):
        from context_packer import pack_context
        
        messages = pack_context(
            system_prompt='System',
            preferences=['dark_mode', 'lang=pl'],
            working_state_str='',
            relevant_facts=[],
            conversation_messages=[],
            user_message='Hi',
            budget=3000
        )
        
        pref_msg = [m for m in messages if m['content'].startswith('PREF:')]
        assert len(pref_msg) == 1
        assert 'dark_mode' in pref_msg[0]['content']
    
    def test_budget_limit(self, app):
        from context_packer import pack_context, estimate_tokens
        
        # Create many long facts
        long_facts = [f'This is a long fact number {i} with extra words to use tokens' for i in range(100)]
        
        messages = pack_context(
            system_prompt='System',
            preferences=[],
            working_state_str='',
            relevant_facts=long_facts,
            conversation_messages=[],
            user_message='Hi',
            budget=200  # Very tight budget
        )
        
        # Should not exceed budget (excluding system prompt and user message)
        context_tokens = 0
        for m in messages:
            if m['role'] == 'system' and m['content'] != 'System':
                context_tokens += estimate_tokens(m['content'])
        
        assert context_tokens <= 200
    
    def test_code_block_stripping(self, app):
        from context_packer import strip_code_blocks
        
        text = """Here is some code:
```python
def foo():
    bar = 1
    baz = 2
    qux = 3
    quux = 4
    quuz = 5
    corge = 6
    grault = 7
```
And here is text after."""
        
        result = strip_code_blocks(text)
        assert '[...kod pominięty...]' in result
        assert 'Here is some code:' in result
        assert 'And here is text after.' in result


# ============================================
# Test: Migration
# ============================================

class TestMigration:
    
    def test_parse_markdown(self, app):
        from migrate_memory import parse_markdown_memory
        
        markdown = """# Log pamięci
### 2025-01-01
[FACT] Ma na imię Mateusz
[PREF] Preferuje język polski
[TODO] Nauczyć się niemieckiego
[DECISION] Wybrał Groq jako provider"""
        
        facts = parse_markdown_memory(markdown)
        assert len(facts) == 4
        assert facts[0]['text'] == 'Ma na imię Mateusz'
        assert facts[0]['type'] == 'fact'
        assert facts[1]['type'] == 'pref'
        assert facts[1]['target_hint'] == 'PREFERENCES'
        assert facts[2]['type'] == 'todo'
        assert facts[3]['type'] == 'decision'
    
    def test_migration_empty_memory(self, app, test_user):
        from migrate_memory import migrate_user_memory
        from database import db
        
        with app.app_context():
            test_user.memory = '# Log pamięci Przemusia'
            db.session.commit()
            
            count = migrate_user_memory(test_user)
            assert count == 0
    
    def test_migration_with_facts(self, app, test_user):
        from migrate_memory import migrate_user_memory
        import memory_manager as mm
        
        with app.app_context():
            count = migrate_user_memory(test_user)
            assert count >= 3  # At least FACT, PREF, TODO, DECISION
            
            entries = mm.get_memory_entries(test_user.id)
            texts = [e['text'] for e in entries]
            assert 'Ma na imię Mateusz' in texts


# ============================================
# Test: Memory Extractor (parse only, no LLM)
# ============================================

class TestMemoryExtractor:
    
    def test_parse_candidates_valid_json(self, app):
        from memory_extractor import _parse_candidates
        
        raw = '[{"type":"fact","text":"likes Python","target_hint":"GENERAL","confidence":0.9}]'
        result = _parse_candidates(raw)
        assert len(result) == 1
        assert result[0]['text'] == 'likes Python'
        assert result[0]['type'] == 'fact'
    
    def test_parse_candidates_with_code_block(self, app):
        from memory_extractor import _parse_candidates
        
        raw = '```json\n[{"type":"pref","text":"dark mode","target_hint":"PREFERENCES","confidence":0.95}]\n```'
        result = _parse_candidates(raw)
        assert len(result) == 1
        assert result[0]['type'] == 'pref'
    
    def test_parse_candidates_empty(self, app):
        from memory_extractor import _parse_candidates
        
        result = _parse_candidates('[]')
        assert result == []
    
    def test_parse_candidates_invalid_json(self, app):
        from memory_extractor import _parse_candidates
        
        result = _parse_candidates('not json at all')
        assert result == []
    
    def test_parse_candidates_normalizes_types(self, app):
        from memory_extractor import _parse_candidates
        
        raw = '[{"type":"invalid_type","text":"test","target_hint":"INVALID","confidence":2.0}]'
        result = _parse_candidates(raw)
        assert len(result) == 1
        assert result[0]['type'] == 'fact'  # normalized to 'fact'
        assert result[0]['target_hint'] == 'GENERAL'  # normalized
        assert result[0]['confidence'] == 1.0  # clamped
