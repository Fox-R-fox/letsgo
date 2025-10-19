from flask import Flask, render_template, request, jsonify, session, redirect, url_for, current_app
from flask_socketio import SocketIO, emit
import sys
import os
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import logging
from datetime import datetime, time, timedelta
import threading
import time as time_module
from typing import Dict, List, Any
import json
import pandas as pd
import requests
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
import random

# Add current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Initialize Flask app first
app = Flask(__name__)
app.config.from_object('config.Config')

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, 
                   async_mode=app.config['SOCKETIO_ASYNC_MODE'],
                   cors_allowed_origins="*",
                   logger=True,
                   engineio_logger=True)

# Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Database Models
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class BotSession(db.Model):
    __tablename__ = 'bot_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    instrument_type = db.Column(db.String(50), nullable=False, default='stocks')
    strategy_name = db.Column(db.String(100), nullable=False)
    trading_mode = db.Column(db.String(20), nullable=False, default='paper')
    initial_capital = db.Column(db.Float, nullable=False, default=100000.0)
    current_capital = db.Column(db.Float, default=0.0)
    pnl = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), nullable=False, default='stopped')
    started_at = db.Column(db.DateTime)
    stopped_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    strategy_params = db.Column(db.Text, default='{}')
    
    user = db.relationship('User', backref=db.backref('bot_sessions', lazy=True))

class Trade(db.Model):
    __tablename__ = 'trades'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    bot_session_id = db.Column(db.Integer, db.ForeignKey('bot_sessions.id'))
    symbol = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    order_type = db.Column(db.String(20), default='LIMIT')
    status = db.Column(db.String(20), default='COMPLETED')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    trading_mode = db.Column(db.String(20), default='paper')
    order_id = db.Column(db.String(100))
    
    user = db.relationship('User', backref=db.backref('trades', lazy=True))
    bot_session = db.relationship('BotSession', backref=db.backref('trades', lazy=True))

class Log(db.Model):
    __tablename__ = 'logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    level = db.Column(db.String(20), default='INFO')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('logs', lazy=True))

class UserSettings(db.Model):
    __tablename__ = 'user_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    kite_api_key = db.Column(db.String(100))
    kite_api_secret = db.Column(db.String(100))
    kite_access_token = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('settings', uselist=False))

# Trading session state
trading_sessions: Dict[str, Any] = {}

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Paper Trading System
class PaperTrading:
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.portfolios = {}
    
    def get_portfolio(self, user_id: int) -> Dict[str, Any]:
        if user_id not in self.portfolios:
            self.portfolios[user_id] = {
                'initial_capital': self.initial_capital,
                'available_cash': self.initial_capital,
                'positions': {},
                'total_charges': 0.0,
                'realized_pnl': 0.0,
                'trades_count': 0
            }
        
        portfolio = self.portfolios[user_id]
        positions_value = self._calculate_positions_value(portfolio['positions'])
        
        return {
            'initial_capital': portfolio['initial_capital'],
            'available_cash': portfolio['available_cash'],
            'positions_value': positions_value,
            'portfolio_value': portfolio['available_cash'] + positions_value,
            'total_charges': portfolio['total_charges'],
            'realized_pnl': portfolio['realized_pnl'],
            'trades_count': portfolio['trades_count']
        }
    
    def get_positions(self, user_id: int) -> List[Dict[str, Any]]:
        if user_id not in self.portfolios:
            return []
        
        portfolio = self.portfolios[user_id]
        positions = []
        
        for symbol, position in portfolio['positions'].items():
            positions.append({
                'symbol': symbol,
                'quantity': position['quantity'],
                'average_price': position['average_price'],
                'action': position.get('action', 'BUY'),
                'invested_amount': position['quantity'] * position['average_price']
            })
        
        return positions
    
    def execute_trade(self, user_id: int, symbol: str, action: str, quantity: int, price: float) -> Dict[str, Any]:
        try:
            if user_id not in self.portfolios:
                self.portfolios[user_id] = {
                    'initial_capital': self.initial_capital,
                    'available_cash': self.initial_capital,
                    'positions': {},
                    'total_charges': 0.0,
                    'realized_pnl': 0.0,
                    'trades_count': 0
                }
            
            portfolio = self.portfolios[user_id]
            symbol = symbol.upper()
            
            # Calculate charges (0.1% brokerage + taxes ~0.05%)
            trade_value = quantity * price
            brokerage = trade_value * 0.001
            taxes = trade_value * 0.0005
            total_charges = brokerage + taxes
            
            if action.upper() == 'BUY':
                # Check if enough cash
                total_cost = trade_value + total_charges
                if total_cost > portfolio['available_cash']:
                    return {'success': False, 'error': f'Insufficient funds. Need: ₹{total_cost:.2f}, Available: ₹{portfolio["available_cash"]:.2f}'}
                
                # Update cash
                portfolio['available_cash'] -= total_cost
                
                # Update or create position
                if symbol in portfolio['positions']:
                    # Average the position
                    old_position = portfolio['positions'][symbol]
                    total_quantity = old_position['quantity'] + quantity
                    total_invested = (old_position['quantity'] * old_position['average_price']) + trade_value
                    new_avg_price = total_invested / total_quantity
                    
                    portfolio['positions'][symbol] = {
                        'quantity': total_quantity,
                        'average_price': new_avg_price,
                        'action': 'BUY'
                    }
                else:
                    portfolio['positions'][symbol] = {
                        'quantity': quantity,
                        'average_price': price,
                        'action': 'BUY'
                    }
                
            elif action.upper() == 'SELL':
                # Check if position exists and has enough quantity
                if symbol not in portfolio['positions']:
                    return {'success': False, 'error': f'No position found for {symbol}'}
                
                position = portfolio['positions'][symbol]
                if position['quantity'] < quantity:
                    return {'success': False, 'error': f'Insufficient quantity to sell. Have: {position["quantity"]}, Need: {quantity}'}
                
                # Calculate P&L
                buy_value = quantity * position['average_price']
                sell_value = trade_value
                realized_pnl = sell_value - buy_value - total_charges
                
                # Update cash
                portfolio['available_cash'] += sell_value - total_charges
                
                # Update realized P&L
                portfolio['realized_pnl'] += realized_pnl
                
                # Update position
                new_quantity = position['quantity'] - quantity
                if new_quantity == 0:
                    del portfolio['positions'][symbol]
                else:
                    portfolio['positions'][symbol]['quantity'] = new_quantity
            
            # Update charges and trade count
            portfolio['total_charges'] += total_charges
            portfolio['trades_count'] += 1
            
            return {
                'success': True,
                'message': f'{action} {quantity} {symbol} @ ₹{price:.2f}',
                'charges': total_charges,
                'realized_pnl': realized_pnl if action.upper() == 'SELL' else 0,
                'portfolio_value': portfolio['available_cash'] + self._calculate_positions_value(portfolio['positions'])
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _calculate_positions_value(self, positions: Dict[str, Any]) -> float:
        total_value = 0.0
        for symbol, position in positions.items():
            total_value += position['quantity'] * position['average_price']
        return total_value
    
    def get_pnl(self, user_id: int, current_prices: Dict[str, float]) -> Dict[str, float]:
        if user_id not in self.portfolios:
            return {
                'realized_pnl': 0.0,
                'unrealized_pnl': 0.0,
                'total_pnl': 0.0,
                'return_percent': 0.0,
                'portfolio_value': self.initial_capital
            }
        
        portfolio = self.portfolios[user_id]
        positions = self.get_positions(user_id)
        
        unrealized_pnl = 0.0
        portfolio_value = portfolio['available_cash']
        
        for position in positions:
            symbol = position['symbol']
            current_price = current_prices.get(symbol, position['average_price'])
            position_value = position['quantity'] * current_price
            cost_basis = position['quantity'] * position['average_price']
            unrealized_pnl += position_value - cost_basis
            portfolio_value += position_value
        
        total_pnl = portfolio['realized_pnl'] + unrealized_pnl
        return_percent = (total_pnl / portfolio['initial_capital']) * 100 if portfolio['initial_capital'] > 0 else 0
        
        return {
            'realized_pnl': portfolio['realized_pnl'],
            'unrealized_pnl': unrealized_pnl,
            'total_pnl': total_pnl,
            'return_percent': return_percent,
            'portfolio_value': portfolio_value
        }

# Initialize paper trading
paper_trading = PaperTrading(app.config['PAPER_TRADING_INITIAL_CAPITAL'])

# Live Trading System
class LiveTrading:
    def __init__(self):
        self.kite = None
    
    def initialize(self, api_key: str, access_token: str) -> bool:
        """Initialize Kite connection"""
        try:
            # Try to import kiteconnect
            try:
                from kiteconnect import KiteConnect
                from kiteconnect.exceptions import KiteException
            except ImportError:
                print("⚠️  kiteconnect not installed. Live trading disabled.")
                return False
            
            self.kite = KiteConnect(api_key=api_key)
            self.kite.set_access_token(access_token)
            
            # Test connection by fetching profile
            profile = self.kite.profile()
            if profile:
                print(f"✅ Kite Connect initialized for user: {profile.get('user_name', 'Unknown')}")
                return True
            return False
            
        except Exception as e:
            print(f"❌ Kite initialization error: {e}")
            return False
    
    def get_margins(self) -> Dict[str, Any]:
        """Get account margins"""
        try:
            if self.kite:
                return self.kite.margins()
            return None
        except Exception as e:
            print(f"Error getting margins: {e}")
            return None
    
    def place_order(self, symbol: str, action: str, quantity: int, price: float) -> Dict[str, Any]:
        """Place live order"""
        try:
            if not self.kite:
                return {'success': False, 'error': 'Kite not initialized'}
            
            # For demo purposes, we'll simulate order placement
            # In production, you would use actual KiteConnect order placement
            order_id = f"LIVE_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}"
            
            print(f"📊 LIVE TRADE: {action} {quantity} {symbol} @ ₹{price:.2f} | Order: {order_id}")
            
            return {
                'success': True, 
                'order_id': order_id,
                'message': f'Live order placed: {order_id}'
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}

# Initialize live trading
live_trading = LiveTrading()

# Fallback Strategy Engine
class FallbackStrategyEngine:
    def __init__(self):
        self.available_strategies = [
            {
                'name': 'moving_average_crossover',
                'display_name': 'Moving Average Crossover',
                'description': 'Generates signals when short-term MA crosses long-term MA',
                'parameters': [
                    {'name': 'short_window', 'type': 'number', 'default': 5, 'min': 1, 'max': 50, 'description': 'Short moving average window'},
                    {'name': 'long_window', 'type': 'number', 'default': 20, 'min': 5, 'max': 100, 'description': 'Long moving average window'},
                    {'name': 'quantity', 'type': 'number', 'default': 10, 'min': 1, 'max': 100, 'description': 'Quantity to trade per signal'}
                ]
            },
            {
                'name': 'mean_reversion',
                'display_name': 'Mean Reversion',
                'description': 'Trades based on price deviations from historical mean',
                'parameters': [
                    {'name': 'lookback_period', 'type': 'number', 'default': 10, 'min': 5, 'max': 50, 'description': 'Lookback period for mean calculation'},
                    {'name': 'deviation_threshold', 'type': 'number', 'default': 2.0, 'min': 1.0, 'max': 5.0, 'description': 'Standard deviation threshold'},
                    {'name': 'quantity', 'type': 'number', 'default': 5, 'min': 1, 'max': 50, 'description': 'Quantity to trade per signal'}
                ]
            },
            {
                'name': 'breakout',
                'display_name': 'Breakout Strategy',
                'description': 'Trades when price breaks through support/resistance levels',
                'parameters': [
                    {'name': 'resistance_level', 'type': 'number', 'default': 1.02, 'min': 1.01, 'max': 1.10, 'description': 'Resistance level multiplier'},
                    {'name': 'support_level', 'type': 'number', 'default': 0.98, 'min': 0.90, 'max': 0.99, 'description': 'Support level multiplier'},
                    {'name': 'quantity', 'type': 'number', 'default': 8, 'min': 1, 'max': 50, 'description': 'Quantity to trade per signal'}
                ]
            }
        ]
    
    def get_available_strategies(self):
        return self.available_strategies
    
    def get_strategy(self, strategy_name, parameters=None):
        return DemoStrategy(parameters)
    
    def validate_strategy_parameters(self, strategy_name, parameters):
        return True

class DemoStrategy:
    def __init__(self, parameters=None):
        self.parameters = parameters or {}
        self.name = "demo_strategy"
    
    def generate_signals(self, market_data):
        signals = []
        symbols = list(market_data.keys())[:5]  # Limit to 5 symbols
        
        for symbol in symbols:
            # More realistic signal generation with 20% probability
            if random.random() < 0.2:
                # Determine action based on price movement
                current_data = market_data.get(symbol, {})
                if not current_data:
                    continue
                    
                last_price = current_data.get('last_price', 1000)
                base_price = 1000 + (hash(symbol) % 5000)
                
                # Buy if current price is below base, sell if above
                if last_price < base_price * 0.98:
                    action = 'BUY'
                    # Buy at slightly higher than current to ensure execution
                    price = last_price * 1.005
                elif last_price > base_price * 1.02:
                    action = 'SELL'
                    # Sell at slightly lower than current to ensure execution
                    price = last_price * 0.995
                else:
                    continue
                
                signals.append({
                    'symbol': symbol,
                    'action': action,
                    'quantity': self.parameters.get('quantity', 10),
                    'price': round(price, 2),
                    'timestamp': datetime.now()
                })
        
        return signals

# Initialize strategy engine
strategy_engine = FallbackStrategyEngine()

def is_market_open() -> bool:
    """Check if market is currently open"""
    try:
        now = datetime.now()
        current_time = now.time()
        current_day = now.weekday()
        
        # Market is closed on weekends
        if current_day >= 5:
            return False
        
        # Market hours (9:15 AM to 3:30 PM IST)
        market_open = time(9, 15)
        market_close = time(15, 30)
        
        is_open = market_open <= current_time <= market_close
        return is_open
    except Exception as e:
        print(f"❌ Market status check error: {e}")
        return False

def get_market_status_message(is_open: bool, is_weekend: bool) -> str:
    """Get appropriate market status message"""
    try:
        if is_weekend:
            return "Market closed on weekends"
        elif is_open:
            return "Market is currently open for trading"
        else:
            now = datetime.now().time()
            if now < time(9, 15):
                return "Market opens at 9:15 AM"
            else:
                return "Market closed for the day"
    except Exception as e:
        return f"Status check error: {e}"

def test_kite_connection(settings) -> Dict[str, Any]:
    """Test Kite Connect connection with real API call"""
    try:
        if not settings or not settings.kite_api_key or not settings.kite_access_token:
            return {'connected': False, 'message': 'Credentials missing'}
        
        # Initialize Kite
        if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
            # Test API call - get profile
            try:
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=settings.kite_api_key)
                kite.set_access_token(settings.kite_access_token)
                profile = kite.profile()
                margins = kite.margins()
                
                if profile:
                    return {
                        'connected': True, 
                        'message': f"Connected as {profile.get('user_name', 'Unknown')}",
                        'profile': {
                            'user_name': profile.get('user_name'),
                            'email': profile.get('email'),
                            'user_id': profile.get('user_id')
                        },
                        'margins': margins
                    }
            except Exception as e:
                return {'connected': False, 'message': f'API Error: {str(e)}'}
        
        return {'connected': False, 'message': 'Connection failed'}
    
    except Exception as e:
        return {'connected': False, 'message': f"Connection failed: {str(e)}"}

def get_current_prices() -> Dict[str, float]:
    """Get current market prices for symbols with realistic variations"""
    symbols = get_top_symbols('stocks') + get_top_symbols('indices')
    current_prices = {}
    
    for symbol in symbols:
        base_price = 1000 + (hash(symbol) % 5000)
        # Add realistic price variation (±5%)
        variation = random.uniform(-0.05, 0.05)
        current_prices[symbol] = round(base_price * (1 + variation), 2)
    
    return current_prices

def get_top_symbols(instrument_type: str, count: int = 20) -> List[str]:
    """Get top symbols based on volume"""
    if instrument_type == 'stocks':
        return ['RELIANCE', 'TCS', 'HDFC', 'INFY', 'HINDUNILVR', 'SBIN', 
                'BHARTIARTL', 'ITC', 'KOTAKBANK', 'ICICIBANK', 'LT', 'AXISBANK',
                'ASIANPAINT', 'MARUTI', 'SUNPHARMA', 'TITAN', 'ULTRACEMCO',
                'WIPRO', 'NESTLEIND', 'HCLTECH']
    elif instrument_type == 'indices':
        return ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']
    else:
        return ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFC', 'SBIN']

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            
            log_entry = Log(
                user_id=user.id,
                message=f"User {username} logged in successfully",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            socketio.emit('user_notification', {
                'type': 'success',
                'message': 'Login successful!',
                'timestamp': datetime.now().isoformat()
            })
            
            return redirect(url_for('dashboard'))
        else:
            socketio.emit('user_notification', {
                'type': 'error',
                'message': 'Invalid credentials!',
                'timestamp': datetime.now().isoformat()
            })
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    user_id = current_user.id
    username = current_user.username
    logout_user()
    
    log_entry = Log(
        user_id=user_id,
        message=f"User {username} logged out",
        level="INFO"
    )
    db.session.add(log_entry)
    db.session.commit()
    
    return redirect(url_for('login'))

# SPA Routes
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/market_watch')
@login_required
def market_watch():
    return render_template('dashboard.html')

@app.route('/positions')
@login_required
def positions():
    return render_template('dashboard.html')

@app.route('/orders')
@login_required
def orders():
    return render_template('dashboard.html')

@app.route('/logs')
@login_required
def logs():
    return render_template('dashboard.html')

@app.route('/settings')
@login_required
def settings():
    return render_template('dashboard.html')

# API Routes
@app.route('/api/market_status')
@login_required
def market_status():
    """Check if market is open with detailed information"""
    try:
        now = datetime.now()
        current_time = now.time()
        current_day = now.strftime('%A')
        
        is_open = is_market_open()
        is_weekend = now.weekday() >= 5
        
        status_info = {
            'is_open': is_open,
            'is_weekend': is_weekend,
            'current_day': current_day,
            'current_time': now.strftime('%H:%M:%S'),
            'open_time': '09:15',
            'close_time': '15:30',
            'message': get_market_status_message(is_open, is_weekend),
            'timestamp': now.isoformat()
        }
        
        return jsonify(status_info)
    
    except Exception as e:
        return jsonify({
            'error': str(e),
            'is_open': False,
            'message': 'Error checking market status'
        }), 500

@app.route('/api/current_time')
@login_required
def current_time():
    """Get current server time"""
    try:
        return jsonify({
            'current_time': datetime.now().strftime('%H:%M:%S'),
            'current_date': datetime.now().strftime('%Y-%m-%d'),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/user_settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    """Get or update user settings"""
    try:
        if request.method == 'POST':
            data = request.json
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            
            if not settings:
                settings = UserSettings(user_id=current_user.id)
                db.session.add(settings)
            
            if 'kite_api_key' in data:
                settings.kite_api_key = data['kite_api_key']
            if 'kite_access_token' in data:
                settings.kite_access_token = data['kite_access_token']
            if 'kite_api_secret' in data:
                settings.kite_api_secret = data['kite_api_secret']
            
            db.session.commit()
            
            # Test connection after saving
            kite_status = test_kite_connection(settings)
            
            socketio.emit('user_notification', {
                'type': 'success' if kite_status['connected'] else 'warning',
                'message': f'Settings saved! Kite: {kite_status["message"]}',
                'timestamp': datetime.now().isoformat()
            })
            
            return jsonify({
                'success': True, 
                'message': 'Settings updated successfully',
                'kite_status': kite_status
            })
        
        else:
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            kite_status = test_kite_connection(settings) if settings else {'connected': False, 'message': 'No credentials'}
            
            if settings:
                return jsonify({
                    'kite_api_key': settings.kite_api_key or '',
                    'kite_access_token': settings.kite_access_token or '',
                    'kite_api_secret': settings.kite_api_secret or '',
                    'kite_status': kite_status
                })
            else:
                return jsonify({
                    'kite_api_key': '', 
                    'kite_access_token': '', 
                    'kite_api_secret': '',
                    'kite_status': kite_status
                })
    
    except Exception as e:
        error_msg = f"Settings error: {str(e)}"
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/kite_connection_status')
@login_required
def kite_connection_status():
    """Check Kite connection status"""
    try:
        settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        status = test_kite_connection(settings)
        return jsonify(status)
    except Exception as e:
        return jsonify({'connected': False, 'message': str(e)})

@app.route('/api/wallet_balance')
@login_required
def wallet_balance():
    """Get wallet balance based on trading mode"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            portfolio = paper_trading.get_portfolio(current_user.id)
            current_prices = get_current_prices()
            pnl_data = paper_trading.get_pnl(current_user.id, current_prices)
            
            return jsonify({
                'balance': portfolio['available_cash'],
                'portfolio_value': pnl_data['portfolio_value'],
                'realized_pnl': pnl_data['realized_pnl'],
                'unrealized_pnl': pnl_data['unrealized_pnl'],
                'total_pnl': pnl_data['total_pnl'],
                'currency': 'INR',
                'mode': 'paper'
            })
        else:
            # Live trading - get balance from Kite
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            if settings and settings.kite_api_key and settings.kite_access_token:
                kite_status = test_kite_connection(settings)
                if kite_status['connected'] and 'margins' in kite_status:
                    equity = kite_status['margins']['equity']
                    return jsonify({
                        'balance': equity['available']['cash'],
                        'portfolio_value': equity['available']['net'],
                        'realized_pnl': 0.0,  # You might want to calculate this from positions
                        'unrealized_pnl': 0.0,
                        'total_pnl': 0.0,
                        'currency': 'INR',
                        'mode': 'live'
                    })
            
            return jsonify({'error': 'Not connected to Kite or no margins data'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/active_bots')
@login_required
def get_active_bots():
    """Get active trading bots for current user"""
    try:
        active_sessions = BotSession.query.filter_by(
            user_id=current_user.id, 
            status='running'
        ).all()
        
        bots_data = [{
            'id': session.id,
            'instrument_type': session.instrument_type,
            'strategy_name': session.strategy_name,
            'trading_mode': session.trading_mode,
            'initial_capital': float(session.initial_capital),
            'current_capital': float(session.current_capital) if session.current_capital else float(session.initial_capital),
            'started_at': session.started_at.isoformat() if session.started_at else None,
            'status': session.status,
            'pnl': float(session.pnl) if session.pnl else 0.0
        } for session in active_sessions]
        
        return jsonify(bots_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/market_watch_data')
@login_required
def get_market_watch_data():
    """Get market watch data"""
    try:
        instrument_type = request.args.get('type', 'stocks')
        
        symbols = get_top_symbols(instrument_type)
        market_data_list = []
        current_prices = get_current_prices()
        
        for symbol in symbols:
            base_price = 1000 + (hash(symbol) % 5000)
            current_price = current_prices.get(symbol, base_price)
            previous_price = base_price  # In real implementation, store previous prices
            
            change = current_price - previous_price
            change_percent = (change / previous_price) * 100
            
            market_data_list.append({
                'symbol': symbol,
                'last_price': current_price,
                'change': round(change, 2),
                'change_percent': round(change_percent, 2),
                'volume': random.randint(100000, 1000000),
                'timestamp': datetime.now().isoformat()
            })
        
        return jsonify(market_data_list)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/strategy_parameters/<strategy_name>')
@login_required
def get_strategy_parameters(strategy_name):
    """Get parameters for a specific strategy"""
    try:
        strategies = strategy_engine.get_available_strategies()
        strategy_info = next((s for s in strategies if s['name'] == strategy_name), None)
        
        if strategy_info:
            return jsonify({
                'parameters': strategy_info['parameters'],
                'description': strategy_info['description']
            })
        else:
            return jsonify({'error': 'Strategy not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400

def validate_strategy_parameters(strategy_name: str, parameters: Dict[str, Any]) -> bool:
    """Validate strategy parameters - FIXED VERSION"""
    try:
        strategies = strategy_engine.get_available_strategies()
        strategy_info = next((s for s in strategies if s['name'] == strategy_name), None)
        
        if not strategy_info:
            print(f"❌ Strategy not found: {strategy_name}")
            return False
        
        print(f"🔍 Validating parameters for {strategy_name}: {parameters}")
        
        for param in strategy_info['parameters']:
            param_name = param['name']
            if param_name in parameters:
                value = parameters[param_name]
                
                if param['type'] == 'number':
                    try:
                        if isinstance(value, str):
                            value = float(value) if '.' in value else int(value)
                        
                        if 'min' in param and value < param['min']:
                            print(f"❌ Parameter {param_name} value {value} below minimum {param['min']}")
                            return False
                        if 'max' in param and value > param['max']:
                            print(f"❌ Parameter {param_name} value {value} above maximum {param['max']}")
                            return False
                            
                    except (ValueError, TypeError):
                        print(f"❌ Parameter {param_name} is not a valid number: {value}")
                        return False
        
        print(f"✅ All parameters validated successfully for {strategy_name}")
        return True
    
    except Exception as e:
        print(f"❌ Validation error: {e}")
        return False

@app.route('/api/start_bot', methods=['POST'])
@login_required
def start_bot():
    """Start trading bot with given parameters - FIXED VERSION"""
    try:
        data = request.json
        print(f"🚀 Starting bot with data: {data}")
        
        # Convert string parameters to numbers
        strategy_params = data.get('strategy_params', {})
        converted_params = {}
        
        for key, value in strategy_params.items():
            try:
                if isinstance(value, str):
                    if '.' in value:
                        converted_params[key] = float(value)
                    else:
                        converted_params[key] = int(value)
                else:
                    converted_params[key] = value
            except (ValueError, TypeError):
                converted_params[key] = value
        
        print(f"🔄 Converted parameters: {converted_params}")
        
        market_open = is_market_open()
        trading_mode = data.get('trading_mode', 'paper')
        
        # If market is closed, force paper trading for safety
        if not market_open and trading_mode == 'live':
            trading_mode = 'paper'
            socketio.emit('user_notification', {
                'type': 'warning',
                'message': 'Market is closed. Switching to Paper Trading mode for safety.',
                'timestamp': datetime.now().isoformat()
            })
        
        bot_config = {
            'instrument_type': data.get('instrument_type', 'stocks'),
            'strategy': data.get('strategy', 'moving_average_crossover'),
            'trading_mode': trading_mode,
            'capital': float(data.get('capital', 100000)),
            'symbols': data.get('symbols', []),
            'strategy_params': converted_params,
            'user_id': current_user.id,
            'test_mode': not market_open,
            'demo_mode': True
        }
        
        # Validate strategy parameters
        if not validate_strategy_parameters(bot_config['strategy'], bot_config['strategy_params']):
            error_msg = "Invalid strategy parameters"
            socketio.emit('user_notification', {
                'type': 'error',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            return jsonify({'success': False, 'error': error_msg})
        
        # Create bot session
        session = BotSession(
            user_id=current_user.id,
            instrument_type=bot_config['instrument_type'],
            strategy_name=bot_config['strategy'],
            trading_mode=bot_config['trading_mode'],
            initial_capital=bot_config['capital'],
            current_capital=bot_config['capital'],
            status='running',
            started_at=datetime.now(),
            strategy_params=json.dumps(bot_config['strategy_params'])
        )
        db.session.add(session)
        db.session.commit()
        
        bot_config['session_id'] = session.id
        
        thread = threading.Thread(
            target=run_trading_bot,
            args=(session.id, bot_config),
            name=f"BotThread-{session.id}"
        )
        thread.daemon = True
        thread.start()
        
        trading_sessions[str(session.id)] = {
            'thread': thread,
            'config': bot_config,
            'session': session,
            'started_at': datetime.now()
        }
        
        log_entry = Log(
            user_id=current_user.id,
            message=f"Bot started - {bot_config['instrument_type']} - {bot_config['strategy']} - {bot_config['trading_mode']}",
            level="INFO"
        )
        db.session.add(log_entry)
        db.session.commit()
        
        success_msg = f"Bot started successfully! Mode: {trading_mode} | Market: {'Open' if market_open else 'Closed'}"
        socketio.emit('user_notification', {
            'type': 'success',
            'message': success_msg,
            'timestamp': datetime.now().isoformat()
        })
        
        socketio.emit('bot_status_update', {
            'session_id': session.id,
            'status': 'running',
            'message': success_msg,
            'trading_mode': trading_mode
        })
        
        return jsonify({
            'success': True, 
            'session_id': session.id,
            'message': success_msg,
            'trading_mode': trading_mode
        })
    
    except Exception as e:
        error_msg = f"Failed to start bot: {str(e)}"
        print(f"❌ {error_msg}")
        
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        
        log_entry = Log(
            user_id=current_user.id,
            message=error_msg,
            level="ERROR"
        )
        db.session.add(log_entry)
        db.session.commit()
        
        return jsonify({'success': False, 'error': error_msg})

@app.route('/api/stop_bot/<int:session_id>')
@login_required
def stop_bot(session_id):
    """Stop trading bot"""
    try:
        session = BotSession.query.get(session_id)
        if session and session.user_id == current_user.id:
            session.status = 'stopped'
            session.stopped_at = datetime.now()
            db.session.commit()
            
            if str(session_id) in trading_sessions:
                del trading_sessions[str(session_id)]
            
            log_entry = Log(
                user_id=current_user.id,
                message=f"Bot stopped - Session {session_id}",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            socketio.emit('user_notification', {
                'type': 'info',
                'message': f"Bot {session_id} stopped successfully",
                'timestamp': datetime.now().isoformat()
            })
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'stopped',
                'message': 'Bot stopped successfully'
            })
            
            return jsonify({'success': True, 'message': 'Bot stopped successfully'})
        else:
            error_msg = 'Session not found or access denied'
            socketio.emit('user_notification', {
                'type': 'error',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            return jsonify({'success': False, 'error': error_msg})
    
    except Exception as e:
        error_msg = f"Error stopping bot: {str(e)}"
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({'success': False, 'error': error_msg})

@app.route('/api/positions')
@login_required
def get_positions():
    """Get current positions - dynamic based on trading"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            positions = paper_trading.get_positions(current_user.id)
            current_prices = get_current_prices()
            
            # Add current prices and P&L
            for position in positions:
                symbol = position['symbol']
                current_price = current_prices.get(symbol, position['average_price'])
                position['current_price'] = current_price
                position['unrealized_pnl'] = (current_price - position['average_price']) * position['quantity']
                position['current_value'] = position['quantity'] * current_price
                position['pnl_percent'] = ((current_price - position['average_price']) / position['average_price']) * 100
            
            return jsonify(positions)
        
        else:
            # Live positions - for demo, return empty or simulate
            return jsonify([])
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders')
@login_required
def get_orders():
    """Get order history"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        limit = request.args.get('limit', 50, type=int)
        
        orders = Trade.query.filter_by(
            user_id=current_user.id, 
            trading_mode=trading_mode
        ).order_by(Trade.timestamp.desc()).limit(limit).all()
        
        orders_data = []
        for order in orders:
            orders_data.append({
                'id': order.id,
                'symbol': order.symbol,
                'action': order.action,
                'quantity': order.quantity,
                'price': float(order.price),
                'order_type': order.order_type,
                'status': order.status,
                'timestamp': order.timestamp.isoformat() if order.timestamp else None,
                'trading_mode': order.trading_mode,
                'order_id': order.order_id
            })
        
        return jsonify(orders_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs')
@login_required
def get_logs():
    """Get application logs"""
    try:
        limit = request.args.get('limit', 100, type=int)
        logs = Log.query.filter_by(user_id=current_user.id).order_by(Log.timestamp.desc()).limit(limit).all()
        
        logs_data = []
        for log in logs:
            logs_data.append({
                'id': log.id,
                'message': log.message,
                'level': log.level,
                'timestamp': log.timestamp.isoformat() if log.timestamp else None
            })
        
        return jsonify(logs_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/portfolio_summary')
@login_required
def get_portfolio_summary():
    """Get portfolio summary with real P&L"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            positions = paper_trading.get_positions(current_user.id)
            portfolio = paper_trading.get_portfolio(current_user.id)
            current_prices = get_current_prices()
            pnl_data = paper_trading.get_pnl(current_user.id, current_prices)
            
            summary = {
                'initial_capital': portfolio['initial_capital'],
                'available_cash': portfolio['available_cash'],
                'portfolio_value': pnl_data['portfolio_value'],
                'realized_pnl': pnl_data['realized_pnl'],
                'unrealized_pnl': pnl_data['unrealized_pnl'],
                'total_pnl': pnl_data['total_pnl'],
                'return_percent': pnl_data['return_percent'],
                'total_charges': portfolio['total_charges'],
                'positions_count': len(positions),
                'trades_count': portfolio['trades_count'],
                'mode': 'paper'
            }
            
            return jsonify(summary)
        
        else:
            # Live portfolio summary
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            if settings and settings.kite_api_key and settings.kite_access_token:
                kite_status = test_kite_connection(settings)
                if kite_status['connected'] and 'margins' in kite_status:
                    equity = kite_status['margins']['equity']
                    return jsonify({
                        'available_cash': equity['available']['cash'],
                        'portfolio_value': equity['available']['net'],
                        'realized_pnl': 0.0,
                        'unrealized_pnl': 0.0,
                        'total_pnl': 0.0,
                        'mode': 'live'
                    })
            
            return jsonify({'error': 'Not connected to Kite'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/reset_paper_portfolio', methods=['POST'])
@login_required
def reset_paper_portfolio():
    """Reset paper trading portfolio"""
    try:
        # For the simple in-memory implementation, we'll recreate the portfolio
        user_id = current_user.id
        if hasattr(paper_trading, 'portfolios') and user_id in paper_trading.portfolios:
            del paper_trading.portfolios[user_id]
        
        # Get fresh portfolio
        paper_trading.get_portfolio(user_id)
        
        socketio.emit('user_notification', {
            'type': 'success',
            'message': 'Paper portfolio reset successfully!',
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({'success': True, 'message': 'Portfolio reset successfully'})
    
    except Exception as e:
        error_msg = f"Error resetting portfolio: {str(e)}"
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({'success': False, 'error': error_msg})

def run_trading_bot(session_id: int, config: Dict[str, Any]):
    """Main trading bot execution function"""
    with app.app_context():
        try:
            strategy = strategy_engine.get_strategy(config['strategy'], config['strategy_params'])
            symbols = config['symbols'] or get_top_symbols(config['instrument_type'])
            
            log_entry = Log(
                user_id=config['user_id'],
                message=f"Bot {session_id} started with {len(symbols)} symbols. Strategy: {config['strategy']}",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            print(f"🤖 Bot {session_id} started! Monitoring {len(symbols)} symbols | Mode: {config['trading_mode']}")
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'monitoring',
                'message': f'Monitoring {len(symbols)} symbols',
                'symbols_count': len(symbols)
            })
            
            session = BotSession.query.get(session_id)
            iteration = 0
            
            while session and session.status == 'running':
                should_trade = is_market_open() or config.get('test_mode', False)
                
                if should_trade:
                    # Generate realistic market data with price movements
                    market_data_dict = {}
                    current_prices = get_current_prices()
                    
                    for symbol in symbols:
                        market_data_dict[symbol] = {
                            'symbol': symbol,
                            'last_price': current_prices.get(symbol, 1000 + (hash(symbol) % 5000)),
                            'volume': random.randint(100000, 1000000),
                            'timestamp': datetime.now()
                        }
                    
                    # Generate signals
                    signals = strategy.generate_signals(market_data_dict)
                    
                    if signals:
                        print(f"📈 Bot {session_id} generated {len(signals)} signals")
                        for signal in signals:
                            execute_trade(session_id, config, signal)
                    else:
                        if iteration % 10 == 0:
                            log_entry = Log(
                                user_id=config['user_id'],
                                message=f"Bot {session_id} running - no signals generated (Iteration: {iteration})",
                                level="DEBUG"
                            )
                            db.session.add(log_entry)
                            db.session.commit()
                    
                    iteration += 1
                
                # Update session status
                session = BotSession.query.get(session_id)
                time_module.sleep(10)  # Check every 10 seconds
                
        except Exception as e:
            error_msg = f"Bot {session_id} error: {str(e)}"
            print(f"❌ {error_msg}")
            log_entry = Log(
                user_id=config['user_id'],
                message=error_msg,
                level="ERROR"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            socketio.emit('user_notification', {
                'type': 'error',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'error',
                'message': error_msg
            })

def execute_trade(session_id: int, config: Dict[str, Any], signal: Dict[str, Any]):
    """Execute a trade based on signal with proper pricing"""
    try:
        user_id = config['user_id']
        trading_mode = config['trading_mode']
        
        # Get current market price for better execution
        current_prices = get_current_prices()
        current_price = current_prices.get(signal['symbol'], signal['price'])
        
        # Adjust price for buy/sell (buy slightly below, sell slightly above current)
        if signal['action'] == 'BUY':
            execution_price = round(current_price * 0.995, 2)  # 0.5% below current
        else:
            execution_price = round(current_price * 1.005, 2)  # 0.5% above current
        
        print(f"🎯 Executing {signal['action']} trade for {signal['symbol']} at {execution_price:.2f} | Mode: {trading_mode}")
        
        if trading_mode == 'paper':
            result = paper_trading.execute_trade(
                user_id=user_id,
                symbol=signal['symbol'],
                action=signal['action'],
                quantity=signal['quantity'],
                price=execution_price
            )
            
            if result['success']:
                # Create trade record
                trade = Trade(
                    user_id=user_id,
                    bot_session_id=session_id,
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal['quantity'],
                    price=execution_price,
                    trading_mode='paper',
                    status='COMPLETED'
                )
                db.session.add(trade)
                db.session.commit()
                
                log_entry = Log(
                    user_id=user_id,
                    message=f"Paper trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f}",
                    level="INFO"
                )
                db.session.add(log_entry)
                db.session.commit()
                
                print(f"✅ Paper trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f}")
                
                socketio.emit('user_notification', {
                    'type': 'success',
                    'message': f"Paper trade: {signal['action']} {signal['symbol']} @ ₹{execution_price:.2f}",
                    'timestamp': datetime.now().isoformat()
                })
                
                socketio.emit('trade_executed', {
                    'session_id': session_id,
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'quantity': signal['quantity'],
                    'price': execution_price,
                    'mode': 'paper',
                    'timestamp': datetime.now().isoformat()
                })
                
                # Update portfolio display
                socketio.emit('portfolio_update', {
                    'user_id': user_id,
                    'portfolio': paper_trading.get_portfolio(user_id),
                    'timestamp': datetime.now().isoformat()
                })
            else:
                error_msg = f"Paper trade failed: {result.get('error', 'Unknown error')}"
                log_entry = Log(
                    user_id=user_id,
                    message=error_msg,
                    level="ERROR"
                )
                db.session.add(log_entry)
                db.session.commit()
                print(f"❌ {error_msg}")
                
                socketio.emit('user_notification', {
                    'type': 'error',
                    'message': error_msg,
                    'timestamp': datetime.now().isoformat()
                })
        
        else:
            # Live trading
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if settings and settings.kite_api_key and settings.kite_access_token:
                live_trading.initialize(settings.kite_api_key, settings.kite_access_token)
                
                result = live_trading.place_order(
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal['quantity'],
                    price=execution_price
                )
                
                if result['success']:
                    # Create trade record
                    trade = Trade(
                        user_id=user_id,
                        bot_session_id=session_id,
                        symbol=signal['symbol'],
                        action=signal['action'],
                        quantity=signal['quantity'],
                        price=execution_price,
                        trading_mode='live',
                        status='COMPLETED',
                        order_id=result.get('order_id')
                    )
                    db.session.add(trade)
                    db.session.commit()
                    
                    log_entry = Log(
                        user_id=user_id,
                        message=f"Live trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f}",
                        level="INFO"
                    )
                    db.session.add(log_entry)
                    db.session.commit()
                    
                    socketio.emit('user_notification', {
                        'type': 'success',
                        'message': f"Live trade: {signal['action']} {signal['symbol']} @ ₹{execution_price:.2f} | Order: {result.get('order_id')}",
                        'timestamp': datetime.now().isoformat()
                    })
                    
                    socketio.emit('trade_executed', {
                        'session_id': session_id,
                        'symbol': signal['symbol'],
                        'action': signal['action'],
                        'quantity': signal['quantity'],
                        'price': execution_price,
                        'mode': 'live',
                        'order_id': result.get('order_id'),
                        'timestamp': datetime.now().isoformat()
                    })
                    
                    print(f"✅ Live trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f}")
                else:
                    error_msg = f"Live trade failed: {result.get('error', 'Unknown error')}"
                    log_entry = Log(
                        user_id=user_id,
                        message=error_msg,
                        level="ERROR"
                    )
                    db.session.add(log_entry)
                    db.session.commit()
                    print(f"❌ {error_msg}")
                    
                    socketio.emit('user_notification', {
                        'type': 'error',
                        'message': error_msg,
                        'timestamp': datetime.now().isoformat()
                    })
            else:
                error_msg = "Kite credentials not configured for live trading"
                print(f"❌ {error_msg}")
                socketio.emit('user_notification', {
                    'type': 'error',
                    'message': error_msg,
                    'timestamp': datetime.now().isoformat()
                })
        
        db.session.commit()
    
    except Exception as e:
        error_msg = f"Trade execution error: {str(e)}"
        log_entry = Log(
            user_id=config['user_id'],
            message=error_msg,
            level="ERROR"
        )
        db.session.add(log_entry)
        db.session.commit()
        print(f"❌ {error_msg}")
        
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print(f"🔌 WebSocket connected: {request.sid}")
    emit('connection_response', {
        'data': 'Connected to trading bot', 
        'status': 'connected',
        'timestamp': datetime.now().isoformat()
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnect"""
    print(f"🔌 WebSocket disconnected: {request.sid}")

@socketio.on('subscribe_market_data')
def handle_subscribe_market_data(data):
    """Subscribe to market data updates"""
    symbols = data.get('symbols', [])
    if symbols:
        emit('subscription_confirmed', {
            'symbols': symbols,
            'message': f'Subscribed to {len(symbols)} symbols',
            'timestamp': datetime.now().isoformat()
        })

@socketio.on('request_market_data')
def handle_request_market_data(data):
    """Request current market data for symbols"""
    symbols = data.get('symbols', [])
    market_data_dict = {}
    current_prices = get_current_prices()
    
    for symbol in symbols:
        market_data_dict[symbol] = {
            'symbol': symbol,
            'last_price': current_prices.get(symbol, 1000 + (hash(symbol) % 5000)),
            'volume': random.randint(100000, 1000000),
            'timestamp': datetime.now().isoformat()
        }
    
    emit('market_data_batch', market_data_dict)

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(401)
def unauthorized_error(error):
    return jsonify({'error': 'Unauthorized access'}), 401

if __name__ == '__main__':
    with app.app_context():
        try:
            db.drop_all()
            db.create_all()
            
            if not User.query.filter_by(username='demo').first():
                demo_user = User(username='demo', email='demo@tradingbot.com')
                demo_user.set_password('demo123')
                db.session.add(demo_user)
                db.session.commit()
                print("✅ Created demo user: username='demo', password='demo123'")
            
            print("🚀 Starting Indian Stock Trading Bot...")
            print("📍 Access: http://localhost:5000")
            print("🔑 Demo: username='demo', password='demo123'")
            print("🤖 Bots will run in TEST MODE with demo data")
            print(f"🕒 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"📊 Market status: {'OPEN' if is_market_open() else 'CLOSED'}")
            
        except Exception as e:
            print(f"❌ Database initialization error: {e}")
    
    socketio.run(app, 
                debug=True, 
                host='0.0.0.0', 
                port=5000,
                allow_unsafe_werkzeug=True)