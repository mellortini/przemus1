"""
Database models for Przemuś web app.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import json

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User model - stores Google OAuth users."""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    name = db.Column(db.String(200))
    avatar_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User settings as JSON
    settings_json = db.Column(db.Text, default='{}')
    
    # User's memory as markdown
    memory = db.Column(db.Text, default='# Log pamięci Przemusia\n\n')
    
    # User's profile
    profile = db.Column(db.Text, default='# Profil użytkownika\n\n')
    
    # Relationships
    conversations = db.relationship('Conversation', backref='user', lazy=True, cascade='all, delete-orphan')
    
    @property
    def settings(self):
        return json.loads(self.settings_json or '{}')
    
    @settings.setter
    def settings(self, value):
        self.settings_json = json.dumps(value, ensure_ascii=False)
    
    def __repr__(self):
        return f'<User {self.email}>'


class Conversation(db.Model):
    """Conversation model - stores chat history per user."""
    __tablename__ = 'conversations'
    
    id = db.Column(db.String(50), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), default='Nowa rozmowa')
    messages_json = db.Column(db.Text, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def messages(self):
        return json.loads(self.messages_json or '[]')
    
    @messages.setter
    def messages(self, value):
        self.messages_json = json.dumps(value, ensure_ascii=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'messages': self.messages,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
    
    def __repr__(self):
        return f'<Conversation {self.id}>'


def init_db(app):
    """Initialize database with app."""
    db.init_app(app)
    with app.app_context():
        db.create_all()
