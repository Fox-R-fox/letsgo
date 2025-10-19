from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    # Relationships
    bot_sessions = db.relationship('BotSession', backref='user', lazy=True)
    trades = db.relationship('Trade', backref='user', lazy=True)
    logs = db.relationship('Log', backref='user', lazy=True)
    settings = db.relationship('UserSettings', backref='user', uselist=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class BotSession(db.Model):
    __tablename__ = 'bot_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    instrument_type = db.Column(db.String(50), nullable=False, default='stocks')
    strategy_name = db.Column(db.String(100), nullable=False)
    trading_mode = db.Column(db.String(20), nullable=False, default='paper')  # paper or live
    initial_capital = db.Column(db.Float, nullable=False, default=100000.0)
    current_capital = db.Column(db.Float, default=0.0)
    pnl = db.Column(db.Float, default=0.0)  # Added missing pnl field
    status = db.Column(db.String(20), nullable=False, default='stopped')  # running, stopped, error
    started_at = db.Column(db.DateTime)
    stopped_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Strategy parameters stored as JSON
    strategy_params = db.Column(db.Text, default='{}')
    
    # Relationships
    trades = db.relationship('Trade', backref='bot_session', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'instrument_type': self.instrument_type,
            'strategy_name': self.strategy_name,
            'trading_mode': self.trading_mode,
            'initial_capital': float(self.initial_capital),
            'current_capital': float(self.current_capital) if self.current_capital else float(self.initial_capital),
            'pnl': float(self.pnl) if self.pnl else 0.0,
            'status': self.status,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'stopped_at': self.stopped_at.isoformat() if self.stopped_at else None,
            'strategy_params': json.loads(self.strategy_params) if self.strategy_params else {}
        }

class Trade(db.Model):
    __tablename__ = 'trades'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bot_session_id = db.Column(db.Integer, db.ForeignKey('bot_sessions.id'))
    symbol = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(10), nullable=False)  # BUY or SELL
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    order_type = db.Column(db.String(20), default='LIMIT')
    status = db.Column(db.String(20), default='COMPLETED')  # COMPLETED, PENDING, CANCELLED
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    trading_mode = db.Column(db.String(20), default='paper')  # paper or live
    order_id = db.Column(db.String(100))  # For live trading orders
    
    def to_dict(self):
        return {
            'id': self.id,
            'symbol': self.symbol,
            'action': self.action,
            'quantity': self.quantity,
            'price': float(self.price),
            'order_type': self.order_type,
            'status': self.status,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'trading_mode': self.trading_mode,
            'order_id': self.order_id
        }

class Log(db.Model):
    __tablename__ = 'logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    level = db.Column(db.String(20), default='INFO')  # INFO, WARNING, ERROR, DEBUG
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'message': self.message,
            'level': self.level,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }

class UserSettings(db.Model):
    __tablename__ = 'user_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    kite_api_key = db.Column(db.String(100))
    kite_api_secret = db.Column(db.String(100))
    kite_access_token = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'kite_api_key': self.kite_api_key or '',
            'kite_api_secret': self.kite_api_secret or '',
            'kite_access_token': self.kite_access_token or ''
        }