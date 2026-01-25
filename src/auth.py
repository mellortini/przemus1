"""
Google OAuth authentication for Przemuś.
"""

from flask import Blueprint, redirect, url_for, session, request, current_app
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from database import db, User
from datetime import datetime
from dotenv import load_dotenv
import os

# Załaduj zmienne środowiskowe z .env
load_dotenv()

auth_bp = Blueprint('auth', __name__)
oauth = OAuth()
login_manager = LoginManager()


def init_auth(app):
    """Initialize authentication with app."""
    # Config
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'przemus-secret-key-change-in-production')
    
    # Login manager
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Zaloguj się, aby kontynuować.'
    
    # OAuth
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile'
        }
    )


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@auth_bp.route('/login')
def login():
    """Redirect to Google OAuth."""
    # Get the redirect URL for after login
    next_url = request.args.get('next', '/')
    session['next_url'] = next_url
    
    redirect_uri = url_for('auth.callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route('/callback')
def callback():
    """Handle Google OAuth callback."""
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if not user_info:
            return redirect(url_for('auth.login'))
        
        # Find or create user
        user = User.query.filter_by(google_id=user_info['sub']).first()
        
        if not user:
            # Create new user
            user = User(
                google_id=user_info['sub'],
                email=user_info['email'],
                name=user_info.get('name', user_info['email'].split('@')[0]),
                avatar_url=user_info.get('picture'),
                settings_json='{"provider": "groq", "model": "llama-3.3-70b-versatile", "api_keys": {}}'
            )
            db.session.add(user)
        else:
            # Update existing user
            user.name = user_info.get('name', user.name)
            user.avatar_url = user_info.get('picture', user.avatar_url)
            user.last_login = datetime.utcnow()
        
        db.session.commit()
        login_user(user, remember=True)
        
        # Redirect to original destination
        next_url = session.pop('next_url', '/')
        return redirect(next_url)
        
    except Exception as e:
        print(f"OAuth error: {e}")
        return redirect('/')


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user."""
    logout_user()
    return redirect('/')


@auth_bp.route('/me')
def me():
    """Get current user info."""
    if current_user.is_authenticated:
        return {
            'logged_in': True,
            'id': current_user.id,
            'name': current_user.name,
            'email': current_user.email,
            'avatar': current_user.avatar_url
        }
    return {'logged_in': False}
