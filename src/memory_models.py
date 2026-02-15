"""
Modele danych nowego systemu pamięci Przemusia.

Drzewiasty system z atomowymi faktami, staging/commit, working state.
"""

import json
from datetime import datetime, timedelta
from database import db


class MemoryNode(db.Model):
    """Węzeł drzewa pamięci długoterminowej (LTM).
    
    Przykładowe node_id: 'ROOT', 'ROOT/PREFERENCES', 'ROOT/PROJECTS/TRADING_BOTS'
    """
    __tablename__ = 'memory_nodes'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    node_id = db.Column(db.String(300), nullable=False)  # path, np. 'ROOT/PROJECTS'
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, default='')
    parent_id = db.Column(db.Integer, db.ForeignKey('memory_nodes.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacje
    facts = db.relationship('MemoryFact', backref='node', lazy=True, cascade='all, delete-orphan')
    children = db.relationship('MemoryNode', backref=db.backref('parent', remote_side=[id]), lazy=True)
    
    # Unikalny per user
    __table_args__ = (db.UniqueConstraint('user_id', 'node_id', name='uq_user_node'),)
    
    def __repr__(self):
        return f'<MemoryNode {self.node_id}>'


class MemoryFact(db.Model):
    """Atomowy fakt przypisany do węzła drzewa LTM.
    
    Typy: fact, pref, decision, procedure, todo, definition, project_state
    """
    __tablename__ = 'memory_facts'
    
    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.Integer, db.ForeignKey('memory_nodes.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(30), nullable=False, default='fact')  # fact/pref/decision/procedure/todo
    confidence = db.Column(db.Float, default=0.8)
    pinned = db.Column(db.Boolean, default=False)
    ttl_days = db.Column(db.Integer, default=3650)  # domyślnie ~10 lat
    evidence_chunks = db.Column(db.Text, default='[]')  # JSON lista chunk_id
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_verified = db.Column(db.DateTime, default=datetime.utcnow)
    superseded_by = db.Column(db.Integer, db.ForeignKey('memory_facts.id'), nullable=True)
    
    @property
    def evidence(self):
        return json.loads(self.evidence_chunks or '[]')
    
    @evidence.setter
    def evidence(self, value):
        self.evidence_chunks = json.dumps(value)
    
    @property
    def is_expired(self):
        if self.pinned:
            return False
        expiry = self.created_at + timedelta(days=self.ttl_days)
        return datetime.utcnow() > expiry
    
    def __repr__(self):
        return f'<MemoryFact [{self.type}] {self.text[:40]}>'


class ConversationChunk(db.Model):
    """Chunk logu epizodycznego — źródło prawdy dla faktów."""
    __tablename__ = 'conversation_chunks'
    
    id = db.Column(db.Integer, primary_key=True)
    chunk_id = db.Column(db.String(50), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    conversation_id = db.Column(db.String(50), db.ForeignKey('conversations.id'), nullable=True)
    messages_json = db.Column(db.Text, nullable=False, default='[]')
    tags = db.Column(db.String(500), default='')  # comma-separated
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @property
    def messages(self):
        return json.loads(self.messages_json or '[]')
    
    @messages.setter
    def messages(self, value):
        self.messages_json = json.dumps(value, ensure_ascii=False)
    
    def __repr__(self):
        return f'<ConversationChunk {self.chunk_id}>'


class MemoryCandidate(db.Model):
    """Kandydat do pamięci — staging area przed commitem do LTM."""
    __tablename__ = 'memory_candidates'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(30), nullable=False, default='fact')
    target_hint = db.Column(db.String(300), default='ROOT')  # sugerowany node_id
    confidence = db.Column(db.Float, default=0.5)
    stability = db.Column(db.String(20), default='low')  # low/medium/high
    evidence_chunks = db.Column(db.Text, default='[]')  # JSON lista chunk_id
    status = db.Column(db.String(20), default='pending')  # pending/committed/rejected
    occurrences = db.Column(db.Integer, default=1)  # ile razy pojawiła się ta info
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    committed_at = db.Column(db.DateTime, nullable=True)
    
    @property
    def evidence(self):
        return json.loads(self.evidence_chunks or '[]')
    
    @evidence.setter
    def evidence(self, value):
        self.evidence_chunks = json.dumps(value)
    
    def __repr__(self):
        return f'<MemoryCandidate [{self.status}] {self.text[:40]}>'


class WorkingState(db.Model):
    """Stan roboczy sesji (STM) — per conversation.
    
    Minimal, zawsze w promptcie. Zastępuje trzymanie historii.
    """
    __tablename__ = 'working_states'
    
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.String(50), db.ForeignKey('conversations.id'), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    task = db.Column(db.String(200), default='')
    file_ref = db.Column(db.String(200), default='')  # referencja do pliku/artefaktu
    issues_json = db.Column(db.Text, default='[]')
    tried_json = db.Column(db.Text, default='[]')
    constraints_json = db.Column(db.Text, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def issues(self):
        return json.loads(self.issues_json or '[]')
    
    @issues.setter
    def issues(self, value):
        self.issues_json = json.dumps(value, ensure_ascii=False)
    
    @property
    def tried(self):
        return json.loads(self.tried_json or '[]')
    
    @tried.setter
    def tried(self, value):
        self.tried_json = json.dumps(value, ensure_ascii=False)
    
    @property
    def constraints(self):
        return json.loads(self.constraints_json or '[]')
    
    @constraints.setter
    def constraints(self, value):
        self.constraints_json = json.dumps(value, ensure_ascii=False)
    
    def to_prompt_string(self):
        """Zwraca zwięzły format do promptu (token-efficient)."""
        parts = ["WORK"]
        if self.task:
            parts.append(f"TASK: {self.task}")
        if self.file_ref:
            parts.append(f"FILE: {self.file_ref}")
        if self.issues:
            parts.append("ISSUES:")
            for issue in self.issues:
                parts.append(f"- {issue}")
        if self.tried:
            parts.append("TRIED:")
            for t in self.tried:
                parts.append(f"- {t}")
        if self.constraints:
            parts.append("CONSTRAINTS:")
            for c in self.constraints:
                parts.append(f"- {c}")
        parts.append("END")
        return "\n".join(parts)
    
    def __repr__(self):
        return f'<WorkingState conv={self.conversation_id}>'
