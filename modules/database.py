from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import json

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    bot_sessions = db.relationship('BotSession', backref='user', lazy=True)
    trades = db.relationship('Trade', backref='user', lazy=True)
    logs = db.relationship('Log', backref='user', lazy=True)
    settings = db.relationship('UserSettings', backref='user', uselist=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    kite_api_key = db.Column(db.String(100))
    kite_access_token = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BotSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    instrument_type = db.Column(db.String(20), nullable=False)
    strategy_name = db.Column(db.String(50), nullable=False)
    trading_mode = db.Column(db.String(20), nullable=False)
    initial_capital = db.Column(db.Float, nullable=False)
    current_capital = db.Column(db.Float)
    status = db.Column(db.String(20), default='stopped')
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    stopped_at = db.Column(db.DateTime)

class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('bot_session.id'))
    symbol = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    order_type = db.Column(db.String(20), default='MARKET')
    product = db.Column(db.String(10), default='MIS')
    status = db.Column(db.String(20), default='COMPLETE')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    pnl = db.Column(db.Float, default=0.0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'symbol': self.symbol,
            'action': self.action,
            'quantity': self.quantity,
            'price': self.price,
            'order_type': self.order_type,
            'status': self.status,
            'timestamp': self.timestamp.isoformat(),
            'pnl': self.pnl
        }

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    level = db.Column(db.String(20), default='INFO')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'message': self.message,
            'level': self.level,
            'timestamp': self.timestamp.isoformat()
        }