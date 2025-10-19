import os
from datetime import timedelta

class Config:
    # Basic Flask Config
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # Database Config
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///alphatrader.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Session Config
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    
    # SocketIO Config
    SOCKETIO_ASYNC_MODE = 'threading'
    
    # Trading Config
    PAPER_TRADING_INITIAL_CAPITAL = 100000.0
    
    # Kite Connect Config
    KITE_API_KEY = os.environ.get('KITE_API_KEY', '')
    KITE_API_SECRET = os.environ.get('KITE_API_SECRET', '')
    
    # Logging Config
    LOG_LEVEL = 'INFO'
    
    # Market Hours (IST)
    MARKET_OPEN_TIME = '09:15:00'
    MARKET_CLOSE_TIME = '15:30:00'