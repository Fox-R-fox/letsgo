import os
from datetime import time

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///trading_bot.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Kite Connect
    KITE_API_KEY = os.environ.get('KITE_API_KEY') or 'your_kite_api_key'
    KITE_API_SECRET = os.environ.get('KITE_API_SECRET') or 'your_kite_api_secret'
    
    # Trading Hours (IST)
    MARKET_OPEN_TIME = time(9, 15)  # 9:15 AM
    MARKET_CLOSE_TIME = time(15, 30)  # 3:30 PM
    
    # Risk Management
    MAX_POSITION_SIZE = 100000  # ₹1 Lakh per position
    DAILY_LOSS_LIMIT = 5000  # ₹5000 daily loss limit
    MAX_OPEN_POSITIONS = 5
    
    # Paper Trading
    PAPER_TRADING_INITIAL_CAPITAL = 1000000  # ₹10 Lakhs
    
    # WebSocket
    SOCKETIO_ASYNC_MODE = 'threading'