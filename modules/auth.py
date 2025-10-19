from kiteconnect import KiteConnect
from flask import request, redirect, session, url_for
import logging
from modules.database import db, User
import secrets

class KiteAuth:
    def __init__(self, app):
        self.app = app
        self.kite = None
        self.logger = logging.getLogger(__name__)
    
    def init_kite(self, api_key: str):
        """Initialize Kite Connect with API key"""
        self.kite = KiteConnect(api_key=api_key)
    
    def get_login_url(self):
        """Get Kite login URL"""
        if not self.kite:
            raise Exception("Kite Connect not initialized")
        
        # Generate a random request token
        session['kite_request_token'] = secrets.token_urlsafe(32)
        return self.kite.login_url()
    
    def set_access_token(self, request_token: str):
        """Generate access token from request token"""
        if not self.kite:
            raise Exception("Kite Connect not initialized")
        
        try:
            data = self.kite.generate_session(request_token)
            access_token = data['access_token']
            
            # Store access token in user session
            session['kite_access_token'] = access_token
            session['kite_user_data'] = data
            
            self.kite.set_access_token(access_token)
            
            # Store in database for current user
            user = User.query.get(session.get('user_id'))
            if user:
                user.kite_access_token = access_token  # You might want to encrypt this
                db.session.commit()
            
            return True
        except Exception as e:
            self.logger.error(f"Failed to generate access token: {str(e)}")
            return False
    
    def get_user_profile(self):
        """Get user profile from Kite"""
        if not self.kite or 'kite_access_token' not in session:
            return None
        
        try:
            return self.kite.profile()
        except Exception as e:
            self.logger.error(f"Failed to get user profile: {str(e)}")
            return None
    
    def is_authenticated(self):
        """Check if user is authenticated with Kite"""
        return 'kite_access_token' in session and self.kite is not None
    
    def logout(self):
        """Logout from Kite"""
        session.pop('kite_access_token', None)
        session.pop('kite_user_data', None)
        session.pop('kite_request_token', None)
        self.kite = None