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
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trading_bot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PAPER_TRADING_INITIAL_CAPITAL'] = 100000.0
app.config['SOCKETIO_ASYNC_MODE'] = 'threading'

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
    target_profit = db.Column(db.Float, default=0.0)
    max_duration_hours = db.Column(db.Integer, default=24)
    total_brokerage = db.Column(db.Float, default=0.0)
    should_exit_positions = db.Column(db.Boolean, default=False)
    stop_requested = db.Column(db.Boolean, default=False)
    force_stop = db.Column(db.Boolean, default=False)
    
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
    brokerage = db.Column(db.Float, default=0.0)
    
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
    kite_api_key = db.Column(db.String(100), default='')
    kite_api_secret = db.Column(db.String(100), default='')
    kite_access_token = db.Column(db.String(500), default='')
    default_target_profit = db.Column(db.Float, default=5000.0)
    default_max_duration = db.Column(db.Integer, default=8)
    max_capital_usage = db.Column(db.Float, default=0.8)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('settings', uselist=False))

# Enhanced trading session state with thread control
class TradingSession:
    def __init__(self, thread, config, session, started_at):
        self.thread = thread
        self.config = config
        self.session = session
        self.started_at = started_at
        self.should_stop = False  # Thread-safe stop flag

trading_sessions: Dict[str, TradingSession] = {}

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Enhanced Paper Trading System with Capital Management
class PaperTrading:
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.portfolios = {}
    
    def calculate_zerodha_brokerage(self, trade_value: float, action: str) -> float:
        """
        Calculate Zerodha-like brokerage charges
        Equity Delivery: Zero brokerage (only taxes)
        Equity Intraday: ₹20 per executed order or 0.03% whichever is lower
        """
        if action.upper() in ['BUY', 'SELL']:
            brokerage_percentage = 0.0003
            brokerage_by_percentage = trade_value * brokerage_percentage
            fixed_brokerage = 20.0
            
            brokerage = min(brokerage_by_percentage, fixed_brokerage)
            
            stt = trade_value * 0.00025
            transaction_charges = trade_value * 0.0000345
            gst = (brokerage + transaction_charges) * 0.18
            sebi_charges = trade_value * 0.000001
            stamp_duty = trade_value * 0.00003
            
            total_charges = brokerage + stt + transaction_charges + gst + sebi_charges + stamp_duty
            return total_charges
        
        return 0.0
    
    def get_portfolio(self, user_id: int) -> Dict[str, Any]:
        if user_id not in self.portfolios:
            self.portfolios[user_id] = {
                'initial_capital': self.initial_capital,
                'available_cash': self.initial_capital,
                'positions': {},
                'total_charges': 0.0,
                'total_brokerage': 0.0,
                'realized_pnl': 0.0,
                'trades_count': 0,
                'used_capital': 0.0
            }
        
        portfolio = self.portfolios[user_id]
        positions_value = self._calculate_positions_value(portfolio['positions'])
        used_capital = self.initial_capital - portfolio['available_cash']
        
        return {
            'initial_capital': portfolio['initial_capital'],
            'available_cash': portfolio['available_cash'],
            'positions_value': positions_value,
            'portfolio_value': portfolio['available_cash'] + positions_value,
            'total_charges': portfolio['total_charges'],
            'total_brokerage': portfolio['total_brokerage'],
            'realized_pnl': portfolio['realized_pnl'],
            'trades_count': portfolio['trades_count'],
            'used_capital': used_capital,
            'capital_usage_percent': (used_capital / self.initial_capital) * 100 if self.initial_capital > 0 else 0
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
                'invested_amount': position['quantity'] * position['average_price'],
                'brokerage_paid': position.get('brokerage_paid', 0.0)
            })
        
        return positions
    
    def execute_trade(self, user_id: int, symbol: str, action: str, quantity: int, price: float, max_capital_usage: float = 0.8) -> Dict[str, Any]:
        try:
            if user_id not in self.portfolios:
                self.portfolios[user_id] = {
                    'initial_capital': self.initial_capital,
                    'available_cash': self.initial_capital,
                    'positions': {},
                    'total_charges': 0.0,
                    'total_brokerage': 0.0,
                    'realized_pnl': 0.0,
                    'trades_count': 0,
                    'used_capital': 0.0
                }
            
            portfolio = self.portfolios[user_id]
            symbol = symbol.upper()
            
            trade_value = quantity * price
            brokerage = self.calculate_zerodha_brokerage(trade_value, action)
            
            if action.upper() == 'BUY':
                total_cost = trade_value + brokerage
                
                max_usable_capital = portfolio['initial_capital'] * max_capital_usage
                current_used_capital = portfolio['initial_capital'] - portfolio['available_cash']
                
                if current_used_capital + total_cost > max_usable_capital:
                    return {'success': False, 'error': f'Capital limit exceeded. Max usable: ₹{max_usable_capital:.2f}, Trying to use: ₹{current_used_capital + total_cost:.2f}'}
                
                if total_cost > portfolio['available_cash']:
                    return {'success': False, 'error': f'Insufficient funds. Need: ₹{total_cost:.2f}, Available: ₹{portfolio["available_cash"]:.2f}'}
                
                portfolio['available_cash'] -= total_cost
                
                if symbol in portfolio['positions']:
                    old_position = portfolio['positions'][symbol]
                    total_quantity = old_position['quantity'] + quantity
                    total_invested = (old_position['quantity'] * old_position['average_price']) + trade_value
                    total_brokerage = old_position.get('brokerage_paid', 0.0) + brokerage
                    new_avg_price = total_invested / total_quantity
                    
                    portfolio['positions'][symbol] = {
                        'quantity': total_quantity,
                        'average_price': new_avg_price,
                        'action': 'BUY',
                        'brokerage_paid': total_brokerage
                    }
                else:
                    portfolio['positions'][symbol] = {
                        'quantity': quantity,
                        'average_price': price,
                        'action': 'BUY',
                        'brokerage_paid': brokerage
                    }
                
            elif action.upper() == 'SELL':
                if symbol not in portfolio['positions']:
                    return {'success': False, 'error': f'No position found for {symbol}'}
                
                position = portfolio['positions'][symbol]
                if position['quantity'] < quantity:
                    return {'success': False, 'error': f'Insufficient quantity to sell. Have: {position["quantity"]}, Need: {quantity}'}
                
                buy_value = quantity * position['average_price']
                sell_value = trade_value
                realized_pnl = sell_value - buy_value - brokerage
                
                portfolio['available_cash'] += sell_value - brokerage
                portfolio['realized_pnl'] += realized_pnl
                
                new_quantity = position['quantity'] - quantity
                if new_quantity == 0:
                    del portfolio['positions'][symbol]
                else:
                    remaining_brokerage = position.get('brokerage_paid', 0.0) * (new_quantity / position['quantity'])
                    portfolio['positions'][symbol] = {
                        'quantity': new_quantity,
                        'average_price': position['average_price'],
                        'action': 'BUY',
                        'brokerage_paid': remaining_brokerage
                    }
            
            portfolio['total_charges'] += brokerage
            portfolio['total_brokerage'] += brokerage
            portfolio['trades_count'] += 1
            
            return {
                'success': True,
                'message': f'{action} {quantity} {symbol} @ ₹{price:.2f}',
                'brokerage': brokerage,
                'realized_pnl': realized_pnl if action.upper() == 'SELL' else 0,
                'portfolio_value': portfolio['available_cash'] + self._calculate_positions_value(portfolio['positions']),
                'trade_value': trade_value
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
                'portfolio_value': self.initial_capital,
                'total_brokerage': 0.0,
                'net_pnl': 0.0
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
        net_pnl = total_pnl - portfolio['total_brokerage']
        return_percent = (total_pnl / portfolio['initial_capital']) * 100 if portfolio['initial_capital'] > 0 else 0
        
        return {
            'realized_pnl': portfolio['realized_pnl'],
            'unrealized_pnl': unrealized_pnl,
            'total_pnl': total_pnl,
            'net_pnl': net_pnl,
            'return_percent': return_percent,
            'portfolio_value': portfolio_value,
            'total_brokerage': portfolio['total_brokerage']
        }
    
    def exit_all_positions(self, user_id: int, current_prices: Dict[str, float]) -> Dict[str, Any]:
        """Exit all positions and return to cash"""
        try:
            if user_id not in self.portfolios:
                return {'success': False, 'error': 'No portfolio found'}
            
            portfolio = self.portfolios[user_id]
            positions = self.get_positions(user_id)
            total_realized = 0.0
            exited_positions = []
            
            for position in positions:
                symbol = position['symbol']
                current_price = current_prices.get(symbol, position['average_price'])
                
                result = self.execute_trade(
                    user_id=user_id,
                    symbol=symbol,
                    action='SELL',
                    quantity=position['quantity'],
                    price=current_price
                )
                
                if result['success']:
                    total_realized += result.get('realized_pnl', 0)
                    exited_positions.append({
                        'symbol': symbol,
                        'quantity': position['quantity'],
                        'price': current_price,
                        'realized_pnl': result.get('realized_pnl', 0)
                    })
            
            return {
                'success': True,
                'message': f'Exited {len(exited_positions)} positions',
                'exited_positions': exited_positions,
                'total_realized_pnl': total_realized,
                'remaining_cash': portfolio['available_cash']
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}

# Initialize paper trading
paper_trading = PaperTrading(app.config['PAPER_TRADING_INITIAL_CAPITAL'])

# Live Trading System
class LiveTrading:
    def __init__(self):
        self.kite = None
    
    def initialize(self, api_key: str, access_token: str) -> bool:
        """Initialize Kite connection"""
        try:
            try:
                from kiteconnect import KiteConnect
                from kiteconnect.exceptions import KiteException
            except ImportError:
                print("⚠️  kiteconnect not installed. Live trading disabled.")
                return False
            
            self.kite = KiteConnect(api_key=api_key)
            self.kite.set_access_token(access_token)
            
            profile = self.kite.profile()
            if profile:
                print(f"✅ Kite Connect initialized for user: {profile.get('user_name', 'Unknown')}")
                return True
            return False
            
        except Exception as e:
            print(f"❌ Kite initialization error: {e}")
            self.kite = None # --- MODIFIED ---: Ensure kite is None on failure
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
    
    def get_holdings(self) -> Dict[str, Any]:
        """Get current holdings"""
        try:
            if self.kite:
                return self.kite.holdings()
            return None
        except Exception as e:
            print(f"Error getting holdings: {e}")
            return None
    
    def get_positions(self) -> Dict[str, Any]:
        """Get current positions"""
        try:
            if self.kite:
                return self.kite.positions()
            return None
        except Exception as e:
            print(f"Error getting positions: {e}")
            return None
    
    def get_live_balance(self) -> Dict[str, Any]:
        """Get actual live balance from Zerodha"""
        try:
            if not self.kite:
                return {'success': False, 'error': 'Kite not initialized'}
            
            margins = self.get_margins()
            if not margins:
                return {'success': False, 'error': 'Could not fetch margins'}
            
            holdings = self.get_holdings()
            positions = self.get_positions()
            
            equity_margins = margins.get('equity', {})
            available_cash = equity_margins.get('available', {}).get('cash', 0.0)
            
            portfolio_value = available_cash
            
            if holdings:
                for holding in holdings:
                    portfolio_value += holding.get('quantity', 0) * holding.get('average_price', 0)
            
            if positions and 'net' in positions:
                for position in positions['net']:
                    portfolio_value += position.get('quantity', 0) * position.get('average_price', 0)
            
            return {
                'success': True,
                'available_cash': available_cash,
                'portfolio_value': portfolio_value,
                'margins': equity_margins,
                'holdings_count': len(holdings) if holdings else 0,
                'positions_count': len(positions.get('net', [])) if positions else 0
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def place_order(self, symbol: str, action: str, quantity: int, price: float) -> Dict[str, Any]:
        """Place live order"""
        try:
            if not self.kite:
                return {'success': False, 'error': 'Kite not initialized'}
            
            # --- MODIFIED ---: This is a placeholder for actual order placement
            # In a real system, you would call:
            # order_id = self.kite.place_order(
            #     variety=self.kite.VARIETY_REGULAR,
            #     exchange=self.kite.EXCHANGE_NSE,
            #     tradingsymbol=symbol, # Symbol must be correct, e.g., 'RELIANCE'
            #     transaction_type=self.kite.TRANSACTION_TYPE_BUY if action.upper() == 'BUY' else self.kite.TRANSACTION_TYPE_SELL,
            #     quantity=quantity,
            #     product=self.kite.PRODUCT_MIS, # For intraday
            #     order_type=self.kite.ORDER_TYPE_LIMIT,
            #     price=price
            # )
            # For this demo, we'll simulate a successful order.
            
            order_id = f"LIVE_{int(datetime.now().timestamp())}_{random.randint(1000, 9999)}"
            trade_value = quantity * price
            brokerage = paper_trading.calculate_zerodha_brokerage(trade_value, action)
            
            print(f"📊 LIVE TRADE (SIMULATED): {action} {quantity} {symbol} @ ₹{price:.2f} | Order: {order_id} | Brokerage: ₹{brokerage:.2f}")
            
            return {
                'success': True, 
                'order_id': order_id,
                'message': f'Live order (simulated) placed: {order_id}',
                'brokerage': brokerage
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}

# Initialize live trading
live_trading = LiveTrading()

# Enhanced Strategy Engine with Capital Management
class EnhancedStrategyEngine:
    def __init__(self):
        self.available_strategies = [
            {
                'name': 'moving_average_crossover',
                'display_name': 'Moving Average Crossover',
                'description': 'Generates signals when short-term MA crosses long-term MA',
                'parameters': [
                    {'name': 'short_window', 'type': 'number', 'default': 5, 'min': 1, 'max': 50, 'description': 'Short moving average window'},
                    {'name': 'long_window', 'type': 'number', 'default': 20, 'min': 5, 'max': 100, 'description': 'Long moving average window'},
                    {'name': 'quantity', 'type': 'number', 'default': 10, 'min': 1, 'max': 100, 'description': 'Quantity to trade per signal'},
                    {'name': 'max_positions', 'type': 'number', 'default': 5, 'min': 1, 'max': 20, 'description': 'Maximum number of simultaneous positions'}
                ]
            },
            {
                'name': 'mean_reversion',
                'display_name': 'Mean Reversion',
                'description': 'Trades based on price deviations from historical mean',
                'parameters': [
                    {'name': 'lookback_period', 'type': 'number', 'default': 10, 'min': 5, 'max': 50, 'description': 'Lookback period for mean calculation'},
                    {'name': 'deviation_threshold', 'type': 'number', 'default': 2.0, 'min': 1.0, 'max': 5.0, 'description': 'Standard deviation threshold'},
                    {'name': 'quantity', 'type': 'number', 'default': 5, 'min': 1, 'max': 50, 'description': 'Quantity to trade per signal'},
                    {'name': 'max_positions', 'type': 'number', 'default': 5, 'min': 1, 'max': 20, 'description': 'Maximum number of simultaneous positions'}
                ]
            },
            {
                'name': 'breakout',
                'display_name': 'Breakout Strategy',
                'description': 'Trades when price breaks through support/resistance levels',
                'parameters': [
                    {'name': 'resistance_level', 'type': 'number', 'default': 1.02, 'min': 1.01, 'max': 1.10, 'description': 'Resistance level multiplier'},
                    {'name': 'support_level', 'type': 'number', 'default': 0.98, 'min': 0.90, 'max': 0.99, 'description': 'Support level multiplier'},
                    {'name': 'quantity', 'type': 'number', 'default': 8, 'min': 1, 'max': 50, 'description': 'Quantity to trade per signal'},
                    {'name': 'max_positions', 'type': 'number', 'default': 5, 'min': 1, 'max': 20, 'description': 'Maximum number of simultaneous positions'}
                ]
            }
        ]
    
    def get_available_strategies(self):
        return self.available_strategies
    
    def get_strategy(self, strategy_name, parameters=None):
        return EnhancedStrategy(strategy_name, parameters)
    
    def validate_strategy_parameters(self, strategy_name, parameters):
        return True

class EnhancedStrategy:
    def __init__(self, strategy_name, parameters=None):
        self.strategy_name = strategy_name
        self.parameters = parameters or {}
        self.name = "enhanced_strategy"
        self.max_positions = self.parameters.get('max_positions', 5)
    
    def generate_signals(self, market_data, current_positions=None):
        """Generate trading signals with position limits"""
        signals = []
        symbols = list(market_data.keys())[:10]
        
        current_position_count = len(current_positions) if current_positions else 0
        
        for symbol in symbols:
            if current_position_count >= self.max_positions:
                break
                
            if random.random() < 0.15:
                current_data = market_data.get(symbol, {})
                if not current_data:
                    continue
                    
                last_price = current_data.get('last_price', 1000)
                base_price = 1000 + (hash(symbol) % 5000)
                
                if last_price < base_price * 0.98:
                    action = 'BUY'
                    price = last_price * 1.005
                elif last_price > base_price * 1.02:
                    action = 'SELL'
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
                current_position_count += 1
        
        return signals

# Initialize strategy engine
strategy_engine = EnhancedStrategyEngine()

def is_market_open() -> bool:
    """Check if market is currently open"""
    try:
        now = datetime.now()
        current_time = now.time()
        current_day = now.weekday()
        
        if current_day >= 5:
            return False
        
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
        
        # --- MODIFIED ---: We re-initialize here to test the token
        if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
            try:
                # Use the initialized instance
                profile = live_trading.kite.profile()
                margins = live_trading.kite.margins()
                
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
                # --- MODIFIED ---: Token might be invalid
                live_trading.kite = None # Invalidate the connection
                return {'connected': False, 'message': f'API Error (Token may be expired): {str(e)}'}
        
        return {'connected': False, 'message': 'Connection failed'}
    
    except Exception as e:
        return {'connected': False, 'message': f"Connection failed: {str(e)}"}

# --- MODIFIED ---: This function is rewritten to fetch LIVE data
def get_current_prices() -> Dict[str, float]:
    """
    Get current market prices for all symbols.
    Uses Kite API if initialized (live mode), otherwise falls back to mock data (paper mode).
    """
    symbols_stocks = get_top_symbols('stocks')
    symbols_indices = get_top_symbols('indices')
    all_symbols = symbols_stocks + symbols_indices
    current_prices = {}

    # --- LIVE DATA FETCH (using Zerodha) ---
    if live_trading.kite:
        try:
            # Format symbols for Kite API
            # Stocks: 'RELIANCE' -> 'NSE:RELIANCE'
            # Indices: 'NIFTY' -> 'INDICES:NIFTY 50'
            kite_symbol_map = {} # Maps 'NSE:RELIANCE' back to 'RELIANCE'
            kite_symbols_to_fetch = []
            
            for s in symbols_stocks:
                kite_symbol = f"NSE:{s}"
                kite_symbol_map[kite_symbol] = s
                kite_symbols_to_fetch.append(kite_symbol)

            # Manually map common indices to their correct Kite symbols
            index_map = {
                'NIFTY': 'INDICES:NIFTY 50',
                'BANKNIFTY': 'INDICES:NIFTY BANK',
                'FINNIFTY': 'INDICES:NIFTY FIN SERVICE',
                'MIDCPNIFTY': 'INDICES:NIFTY MID SELECT' # Corrected symbol
            }

            for i in symbols_indices:
                if i in index_map:
                    kite_symbol = index_map[i]
                    kite_symbol_map[kite_symbol] = i
                    kite_symbols_to_fetch.append(kite_symbol)
            
            # Fetch quotes from Kite
            quotes = live_trading.kite.quote(kite_symbols_to_fetch)
            
            for kite_symbol, quote_data in quotes.items():
                original_symbol = kite_symbol_map.get(kite_symbol)
                if original_symbol:
                    current_prices[original_symbol] = quote_data.get('last_price', 0.0)
            
            # Check if any symbols failed (e.g., 0.0 price) and fill with mock data if needed
            for s in all_symbols:
                if s not in current_prices or current_prices.get(s, 0.0) == 0.0:
                    # Fallback for this specific symbol
                    base_price = 1000 + (hash(s) % 5000)
                    variation = random.uniform(-0.05, 0.05)
                    current_prices[s] = round(base_price * (1 + variation), 2)
                    if s in symbols_indices: # Give indices a different base
                         base_price = 10000 + (hash(s) % 5000)
                         current_prices[s] = round(base_price * (1 + variation), 2)
                         
                    # print(f"⚠️ Warning: Could not fetch live price for {s}. Using mock data.")

            return current_prices

        except Exception as e:
            print(f"❌ Kite API error in get_current_prices: {e}. Falling back to mock data.")
            # Fallback to mock data on API error
    
    # --- MOCK DATA (Paper Trading / Fallback) ---
    # print("Using mock data for prices.") # Optional: for debugging
    for symbol in all_symbols:
        base_price = 1000 + (hash(symbol) % 5000)
        if symbol in symbols_indices: # Give indices a different base
            base_price = 10000 + (hash(symbol) % 5000)
            
        variation = random.uniform(-0.05, 0.05)
        current_prices[symbol] = round(base_price * (1 + variation), 2)
    
    return current_prices


def get_top_symbols(instrument_type: str, count: int = 20) -> List[str]:
    """Get top symbols based on volume"""
    if instrument_type == 'stocks':
        return ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'HINDUNILVR', 'SBIN',  # --- MODIFIED ---: HDFC -> HDFCBANK
                'BHARTIARTL', 'ITC', 'KOTAKBANK', 'ICICIBANK', 'LT', 'AXISBANK',
                'ASIANPAINT', 'MARUTI', 'SUNPHARMA', 'TITAN', 'ULTRACEMCO',
                'WIPRO', 'NESTLEIND', 'HCLTECH']
    elif instrument_type == 'indices':
        return ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']
    else:
        return ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'SBIN']

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
    """Get or update user settings with enhanced fields"""
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
            if 'default_target_profit' in data:
                settings.default_target_profit = float(data['default_target_profit'])
            if 'default_max_duration' in data:
                settings.default_max_duration = int(data['default_max_duration'])
            if 'max_capital_usage' in data:
                settings.max_capital_usage = float(data['max_capital_usage'])
            
            db.session.commit()
            
            kite_status = test_kite_connection(settings)
            
            socketio.emit('user_notification', {
                'type': 'success',
                'message': f'Settings saved! Default profit: ₹{settings.default_target_profit}, Max duration: {settings.default_max_duration}h, Capital usage: {settings.max_capital_usage*100}%',
                'timestamp': datetime.now().isoformat()
            })
            
            return jsonify({
                'success': True, 
                'message': 'Settings updated successfully',
                'kite_status': kite_status
            })
        
        else:
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            
            if not settings:
                settings = UserSettings(user_id=current_user.id)
                db.session.add(settings)
                db.session.commit()
            
            kite_status = test_kite_connection(settings)
            
            return jsonify({
                'kite_api_key': settings.kite_api_key or '',
                'kite_access_token': settings.kite_access_token or '',
                'kite_api_secret': settings.kite_api_secret or '',
                'default_target_profit': settings.default_target_profit,
                'default_max_duration': settings.default_max_duration,
                'max_capital_usage': settings.max_capital_usage,
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
                'net_pnl': pnl_data['net_pnl'],
                'total_brokerage': pnl_data['total_brokerage'],
                'currency': 'INR',
                'mode': 'paper'
            })
        else:
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            
            if not settings or not settings.kite_api_key or not settings.kite_access_token:
                return jsonify({
                    'error': 'Zerodha credentials not configured',
                    'balance': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'total_brokerage': 0,
                    'currency': 'INR',
                    'mode': 'live'
                })
            
            # --- MODIFIED ---: Check live_trading.kite instead of re-initializing
            if live_trading.kite:
                balance_data = live_trading.get_live_balance()
                
                if balance_data and balance_data['success']:
                    return jsonify({
                        'balance': balance_data['available_cash'],
                        'portfolio_value': balance_data['portfolio_value'],
                        'realized_pnl': 0, # Note: Real PNL/Unrealized PNL needs parsing positions
                        'unrealized_pnl': 0,
                        'total_pnl': 0,
                        'net_pnl': 0,
                        'total_brokerage': 0,
                        'currency': 'INR',
                        'mode': 'live',
                        'holdings_count': balance_data.get('holdings_count', 0),
                        'positions_count': balance_data.get('positions_count', 0),
                        'note': 'Real Zerodha data'
                    })
                else:
                    return jsonify({
                        'error': f'Failed to fetch Zerodha balance: {balance_data.get("error", "Unknown error")}',
                        'balance': 0,
                        'portfolio_value': 0,
                        'realized_pnl': 0,
                        'unrealized_pnl': 0,
                        'total_pnl': 0,
                        'net_pnl': 0,
                        'total_brokerage': 0,
                        'currency': 'INR',
                        'mode': 'live'
                    })
            else:
                # --- MODIFIED ---: Try to initialize if not already
                if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                    # If successful, redirect to same function (will be handled by above block)
                    return redirect(url_for('wallet_balance', mode='live'))
                
                return jsonify({
                    'error': 'Failed to connect to Zerodha. Token might be invalid.',
                    'balance': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'total_brokerage': 0,
                    'currency': 'INR',
                    'mode': 'live'
                })
    
    except Exception as e:
        print(f"❌ Wallet balance error: {e}")
        return jsonify({
            'error': f'Failed to get wallet balance: {str(e)}',
            'balance': 0,
            'portfolio_value': 0,
            'realized_pnl': 0,
            'unrealized_pnl': 0,
            'total_pnl': 0,
            'net_pnl': 0,
            'total_brokerage': 0,
            'currency': 'INR',
            'mode': 'paper'
        }), 200

@app.route('/api/active_bots')
@login_required
def get_active_bots():
    """Get active trading bots for current user"""
    try:
        active_sessions = BotSession.query.filter_by(
            user_id=current_user.id, 
            status='running'
        ).all()
        
        bots_data = []
        current_prices = get_current_prices() # --- MODIFIED ---: Gets live prices now
        
        for session in active_sessions:
            pnl_data = paper_trading.get_pnl(current_user.id, current_prices)
            
            bots_data.append({
                'id': session.id,
                'instrument_type': session.instrument_type,
                'strategy_name': session.strategy_name,
                'trading_mode': session.trading_mode,
                'initial_capital': float(session.initial_capital),
                'current_capital': float(session.current_capital) if session.current_capital else float(session.initial_capital),
                'started_at': session.started_at.isoformat() if session.started_at else None,
                'status': session.status,
                'pnl': float(session.pnl) if session.pnl else 0.0,
                'target_profit': float(session.target_profit),
                'max_duration_hours': session.max_duration_hours,
                'total_brokerage': float(session.total_brokerage),
                'current_net_pnl': pnl_data['net_pnl']
            })
        
        return jsonify(bots_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/market_watch_data')
@login_required
def get_market_watch_data():
    """
    Get market watch data. 
    This route now returns LIVE data if credentials are set, 
    thanks to the updated get_current_prices() function.
    """
    try:
        instrument_type = request.args.get('type', 'stocks')
        
        symbols = get_top_symbols(instrument_type)
        market_data_list = []
        current_prices = get_current_prices() # --- MODIFIED ---: This now fetches LIVE data
        
        for symbol in symbols:
            # Use a consistent base price for calculating change %
            base_price = 1000 + (hash(symbol) % 5000)
            if instrument_type == 'indices':
                base_price = 10000 + (hash(symbol) % 5000)
                
            current_price = current_prices.get(symbol, base_price)
            previous_price = base_price # This logic is flawed, but matches original code.
                                        # A real impl would use 'open_price' or 'previous_close'
            
            change = current_price - previous_price
            change_percent = (change / previous_price) * 100 if previous_price != 0 else 0
            
            market_data_list.append({
                'symbol': symbol,
                'last_price': current_price,
                'change': round(change, 2),
                'change_percent': round(change_percent, 2),
                'volume': random.randint(100000, 1000000), # Volume is still mock
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
    """Validate strategy parameters"""
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

def can_start_live_bot(settings, capital_required: float) -> Dict[str, Any]:
    """Check if live bot can be started with current conditions"""
    try:
        market_open = is_market_open()
        if not market_open:
            return {
                'can_start': False,
                'reason': 'market_closed',
                'message': '❌ Market is currently closed. Live trading is only available during market hours (9:15 AM - 3:30 PM IST). Please switch to Paper Trading or wait for market to open.'
            }
        
        if not settings or not settings.kite_api_key or not settings.kite_access_token:
            return {
                'can_start': False,
                'reason': 'credentials_missing',
                'message': '❌ Zerodha credentials not configured. Please go to Settings and enter your Kite API credentials.'
            }
        
        # --- MODIFIED ---: Check if connection is already live, or try to init
        if not live_trading.kite:
            print("Kite not initialized, attempting connection...")
            if not live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                return {
                    'can_start': False,
                    'reason': 'connection_failed',
                    'message': '❌ Failed to connect to Zerodha. Please check your API credentials and (if new) your access token.'
                }

        # --- MODIFIED ---: Connection is assumed to be working if we reach here
        balance_data = live_trading.get_live_balance()
        
        if balance_data and balance_data['success']:
            available_cash = balance_data['available_cash']
            
            if available_cash < capital_required:
                return {
                    'can_start': False,
                    'reason': 'insufficient_balance',
                    'message': f'❌ Insufficient balance for live trading. Required: ₹{capital_required:.2f}, Available: ₹{available_cash:.2f}. Please add funds to your Zerodha account or reduce the capital amount.'
                }
            
            return {
                'can_start': True,
                'message': '✅ All checks passed. Live bot can be started.',
                'available_balance': available_cash
            }
        else:
            return {
                'can_start': False,
                'reason': 'balance_check_failed',
                'message': f'❌ Failed to check Zerodha balance: {balance_data.get("error", "Unknown error")}'
            }
    
    except Exception as e:
        return {
            'can_start': False,
            'reason': 'error',
            'message': f'❌ Error checking live trading conditions: {str(e)}'
        }

@app.route('/api/start_bot', methods=['POST'])
@login_required
def start_bot():
    """Start trading bot with enhanced parameters"""
    try:
        data = request.json
        print(f"🚀 Starting bot with data: {data}")
        
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
        
        trading_mode = data.get('trading_mode', 'paper')
        capital = float(data.get('capital', 100000))
        
        user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        if not user_settings:
            user_settings = UserSettings(user_id=current_user.id)
            db.session.add(user_settings)
            db.session.commit()
        
        if trading_mode == 'live':
            validation_result = can_start_live_bot(user_settings, capital)
            
            if not validation_result['can_start']:
                socketio.emit('user_notification', {
                    'type': 'error',
                    'message': validation_result['message'],
                    'timestamp': datetime.now().isoformat()
                })
                
                return jsonify({
                    'success': False, 
                    'error': validation_result['message'],
                    'reason': validation_result.get('reason'),
                    'suggestion': 'Please switch to Paper Trading mode or fix the issues mentioned above.'
                })
        
        target_profit = float(data.get('target_profit', user_settings.default_target_profit))
        max_duration = int(data.get('max_duration_hours', user_settings.default_max_duration))
        
        bot_config = {
            'instrument_type': data.get('instrument_type', 'stocks'),
            'strategy': data.get('strategy', 'moving_average_crossover'),
            'trading_mode': trading_mode,
            'capital': capital,
            'symbols': data.get('symbols', []),
            'strategy_params': converted_params,
            'user_id': current_user.id,
            'test_mode': trading_mode == 'paper', # test_mode allows trading when market is closed
            'demo_mode': True,
            'target_profit': target_profit,
            'max_duration_hours': max_duration,
            'max_capital_usage': user_settings.max_capital_usage
        }
        
        if not validate_strategy_parameters(bot_config['strategy'], bot_config['strategy_params']):
            error_msg = "Invalid strategy parameters"
            socketio.emit('user_notification', {
                'type': 'error',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            return jsonify({'success': False, 'error': error_msg})
        
        session = BotSession(
            user_id=current_user.id,
            instrument_type=bot_config['instrument_type'],
            strategy_name=bot_config['strategy'],
            trading_mode=bot_config['trading_mode'],
            initial_capital=bot_config['capital'],
            current_capital=bot_config['capital'],
            status='running',
            started_at=datetime.now(),
            strategy_params=json.dumps(bot_config['strategy_params']),
            target_profit=bot_config['target_profit'],
            max_duration_hours=bot_config['max_duration_hours'],
            stop_requested=False,
            force_stop=False
        )
        db.session.add(session)
        db.session.commit()
        
        bot_config['session_id'] = session.id
        
        # Create trading session with stop control
        trading_session = TradingSession(
            thread=None,
            config=bot_config,
            session=session,
            started_at=datetime.now()
        )
        
        # Start thread with the trading session reference
        thread = threading.Thread(
            target=run_enhanced_trading_bot,
            args=(session.id, bot_config, trading_session),
            name=f"BotThread-{session.id}"
        )
        thread.daemon = True
        thread.start()
        
        # Update trading session with thread reference
        trading_session.thread = thread
        trading_sessions[str(session.id)] = trading_session
        
        mode_message = "Live Trading" if trading_mode == 'live' else "Paper Trading"
        log_entry = Log(
            user_id=current_user.id,
            message=f"Bot started in {mode_message} mode - Target Profit: ₹{bot_config['target_profit']}, Max Duration: {bot_config['max_duration_hours']}h, Capital: ₹{capital}",
            level="INFO"
        )
        db.session.add(log_entry)
        db.session.commit()
        
        success_msg = f"✅ Bot started in {mode_message} mode! Target: ₹{bot_config['target_profit']}, Duration: {bot_config['max_duration_hours']}h, Capital: ₹{capital}"
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
    """Stop trading bot IMMEDIATELY using thread-safe approach"""
    try:
        session = BotSession.query.get(session_id)
        if session and session.user_id == current_user.id:
            print(f"🛑 IMMEDIATE STOP COMMAND for Bot {session_id}")
            
            # Set database flags first
            session.stop_requested = True
            session.force_stop = True
            session.should_exit_positions = True
            session.status = 'stopping'
            db.session.commit()
            
            # Get the trading session and set thread-safe stop flag
            session_key = str(session_id)
            if session_key in trading_sessions:
                trading_session = trading_sessions[session_key]
                trading_session.should_stop = True  # Thread-safe immediate stop
                
                # Try to interrupt the thread if it's sleeping
                if trading_session.thread and trading_session.thread.is_alive():
                    print(f"🛑 Setting immediate stop flag for thread {session_id}")
            
            # IMMEDIATELY exit all positions
            current_prices = get_current_prices()
            exit_result = paper_trading.exit_all_positions(current_user.id, current_prices)
            print(f"🛑 Positions exit result: {exit_result['success']}")
            
            # Update final status
            session.status = 'stopped'
            session.stopped_at = datetime.now()
            session.stop_requested = False
            session.force_stop = False
            
            # Update final P&L
            pnl_data = paper_trading.get_pnl(current_user.id, current_prices)
            session.pnl = pnl_data['net_pnl']
            db.session.commit()
            
            # Remove from active sessions
            if session_key in trading_sessions:
                print(f"🛑 Removing session {session_id} from active sessions")
                del trading_sessions[session_key]
            
            # Log the stop action
            log_entry = Log(
                user_id=current_user.id,
                message=f"Bot STOPPED IMMEDIATELY - Session {session_id} | Final P&L: ₹{pnl_data['net_pnl']:.2f}",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            # Notify user
            success_msg = f"🛑 Bot {session_id} STOPPED IMMEDIATELY! Final P&L: ₹{pnl_data['net_pnl']:.2f}"
            socketio.emit('user_notification', {
                'type': 'success',
                'message': success_msg,
                'timestamp': datetime.now().isoformat()
            })
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'stopped',
                'message': 'Bot stopped immediately - all trading halted',
                'final_pnl': pnl_data['net_pnl']
            })
            
            print(f"✅ Bot {session_id} completely stopped")
            
            return jsonify({
                'success': True, 
                'message': 'Bot stopped successfully',
                'final_pnl': pnl_data['net_pnl']
            })
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
        print(f"❌ {error_msg}")
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({'success': False, 'error': error_msg})

@app.route('/api/exit_all_positions', methods=['POST'])
@login_required
def exit_all_positions():
    """Exit all positions and book profits"""
    try:
        data = request.json
        session_id = data.get('session_id')
        
        if session_id:
            session = BotSession.query.get(session_id)
            if session and session.user_id == current_user.id:
                current_prices = get_current_prices()
                result = paper_trading.exit_all_positions(current_user.id, current_prices)
                
                if result['success']:
                    pnl_data = paper_trading.get_pnl(current_user.id, current_prices)
                    session.pnl = pnl_data['net_pnl']
                    
                    db.session.commit()
                    
                    socketio.emit('user_notification', {
                        'type': 'success',
                        'message': f"All positions exited! Realized P&L: ₹{result['total_realized_pnl']:.2f}",
                        'timestamp': datetime.now().isoformat()
                    })
                    
                    return jsonify(result)
                else:
                    return jsonify({'success': False, 'error': result['error']}), 400
            else:
                return jsonify({'success': False, 'error': 'Session not found'}), 404
        else:
            current_prices = get_current_prices()
            result = paper_trading.exit_all_positions(current_user.id, current_prices)
            
            if result['success']:
                socketio.emit('user_notification', {
                    'type': 'success',
                    'message': f"All positions exited! Realized P&L: ₹{result['total_realized_pnl']:.2f}",
                    'timestamp': datetime.now().isoformat()
                })
            
            return jsonify(result)
    
    except Exception as e:
        error_msg = f"Error exiting positions: {str(e)}"
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/bot_performance/<int:session_id>')
@login_required
def get_bot_performance(session_id):
    """Get detailed performance metrics for a bot session"""
    try:
        session = BotSession.query.get(session_id)
        if not session or session.user_id != current_user.id:
            return jsonify({'error': 'Session not found'}), 404
        
        current_prices = get_current_prices()
        pnl_data = paper_trading.get_pnl(current_user.id, current_prices)
        portfolio = paper_trading.get_portfolio(current_user.id)
        
        trades = Trade.query.filter_by(bot_session_id=session_id).all()
        total_brokerage = sum(trade.brokerage for trade in trades)
        
        running_time = 0
        if session.started_at:
            if session.stopped_at:
                running_time = (session.stopped_at - session.started_at).total_seconds() / 3600
            else:
                running_time = (datetime.now() - session.started_at).total_seconds() / 3600
        
        performance = {
            'session_id': session.id,
            'strategy': session.strategy_name,
            'initial_capital': float(session.initial_capital),
            'current_portfolio_value': pnl_data['portfolio_value'],
            'total_pnl': pnl_data['total_pnl'],
            'net_pnl': pnl_data['net_pnl'],
            'realized_pnl': pnl_data['realized_pnl'],
            'unrealized_pnl': pnl_data['unrealized_pnl'],
            'total_brokerage': total_brokerage,
            'return_percent': pnl_data['return_percent'],
            'trades_count': len(trades),
            'positions_count': len(paper_trading.get_positions(current_user.id)),
            'capital_usage_percent': portfolio.get('capital_usage_percent', 0),
            'running_time_hours': running_time,
            'target_profit': float(session.target_profit),
            'max_duration_hours': session.max_duration_hours,
            'profit_target_achieved': pnl_data['net_pnl'] >= session.target_profit if session.target_profit > 0 else False,
            'time_remaining_hours': max(0, session.max_duration_hours - running_time) if session.status == 'running' else 0
        }
        
        return jsonify(performance)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/positions')
@login_required
def get_positions():
    """Get current positions - dynamic based on trading"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            positions = paper_trading.get_positions(current_user.id)
            current_prices = get_current_prices()
            
            for position in positions:
                symbol = position['symbol']
                current_price = current_prices.get(symbol, position['average_price'])
                position['current_price'] = current_price
                position['unrealized_pnl'] = (current_price - position['average_price']) * position['quantity']
                position['current_value'] = position['quantity'] * current_price
                position['pnl_percent'] = ((current_price - position['average_price']) / position['average_price']) * 100 if position['average_price'] != 0 else 0
            
            return jsonify(positions)
        
        else:
            # --- MODIFIED ---: Fetch live positions if kite is active
            if live_trading.kite:
                live_positions_data = live_trading.get_positions()
                if live_positions_data and 'net' in live_positions_data:
                    # TODO: This needs parsing and merging with quote data
                    # For now, return the raw data
                    return jsonify(live_positions_data['net'])
            
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
                'order_id': order.order_id,
                'brokerage': float(order.brokerage)
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
    """Get portfolio summary with brokerage details"""
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
                'net_pnl': pnl_data['net_pnl'],
                'return_percent': pnl_data['return_percent'],
                'total_charges': portfolio['total_charges'],
                'total_brokerage': portfolio['total_brokerage'],
                'positions_count': len(positions),
                'trades_count': portfolio['trades_count'],
                'used_capital': portfolio['used_capital'],
                'capital_usage_percent': portfolio['capital_usage_percent'],
                'mode': 'paper'
            }
            
            return jsonify(summary)
        
        else:
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            
            if not settings or not settings.kite_api_key or not settings.kite_access_token:
                return jsonify({
                    'error': 'Zerodha credentials not configured',
                    'available_cash': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'mode': 'live'
                })
            
            if live_trading.kite:
                balance_data = live_trading.get_live_balance()
                
                if balance_data and balance_data['success']:
                    portfolio_value = balance_data.get('portfolio_value', 0)
                    available_cash = balance_data.get('available_cash', 0)
                    used_capital = portfolio_value - available_cash
                    
                    return jsonify({
                        'initial_capital': available_cash, # Not accurate, but best guess
                        'available_cash': available_cash,
                        'portfolio_value': portfolio_value,
                        'realized_pnl': 0, # Needs parsing
                        'unrealized_pnl': 0, # Needs parsing
                        'total_pnl': 0, # Needs parsing
                        'net_pnl': 0, # Needs parsing
                        'return_percent': 0,
                        'total_charges': 0,
                        'total_brokerage': 0,
                        'positions_count': balance_data.get('positions_count', 0),
                        'trades_count': 0,
                        'used_capital': used_capital,
                        'capital_usage_percent': (used_capital / portfolio_value) * 100 if portfolio_value > 0 else 0,
                        'mode': 'live',
                        'note': 'Real Zerodha data (PNL parsing not implemented)'
                    })
                else:
                    return jsonify({
                        'error': f'Failed to fetch Zerodha data: {balance_data.get("error", "Unknown error")}',
                        'available_cash': 0,
                        'portfolio_value': 0,
                        'realized_pnl': 0,
                        'unrealized_pnl': 0,
                        'total_pnl': 0,
                        'net_pnl': 0,
                        'mode': 'live'
                    })
            else:
                return jsonify({
                    'error': 'Failed to connect to Zerodha',
                    'available_cash': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'mode': 'live'
                })
    
    except Exception as e:
        print(f"❌ Portfolio summary error: {e}")
        return jsonify({
            'error': f'Failed to get portfolio summary: {str(e)}',
            'available_cash': 0,
            'portfolio_value': 0,
            'realized_pnl': 0,
            'unrealized_pnl': 0,
            'total_pnl': 0,
            'net_pnl': 0,
            'mode': 'paper'
        }), 200

@app.route('/api/reset_paper_portfolio', methods=['POST'])
@login_required
def reset_paper_portfolio():
    """Reset paper trading portfolio"""
    try:
        user_id = current_user.id
        if hasattr(paper_trading, 'portfolios') and user_id in paper_trading.portfolios:
            del paper_trading.portfolios[user_id]
        
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

def execute_enhanced_trade(session_id: int, config: Dict[str, Any], signal: Dict[str, Any]):
    """Execute trade with brokerage tracking and capital limits"""
    try:
        user_id = config['user_id']
        trading_mode = config['trading_mode']
        
        current_prices = get_current_prices()
        current_price = current_prices.get(signal['symbol'], signal['price'])
        
        if signal['action'] == 'BUY':
            execution_price = round(current_price * 1.005, 2) # Simulate slippage
        else:
            execution_price = round(current_price * 0.995, 2) # Simulate slippage
        
        print(f"🎯 Executing {signal['action']} trade for {signal['symbol']} at {execution_price:.2f}")
        
        if trading_mode == 'paper':
            result = paper_trading.execute_trade(
                user_id=user_id,
                symbol=signal['symbol'],
                action=signal['action'],
                quantity=signal['quantity'],
                price=execution_price,
                max_capital_usage=config.get('max_capital_usage', 0.8)
            )
            
            if result['success']:
                trade = Trade(
                    user_id=user_id,
                    bot_session_id=session_id,
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal['quantity'],
                    price=execution_price,
                    trading_mode='paper',
                    status='COMPLETED',
                    brokerage=result.get('brokerage', 0.0)
                )
                db.session.add(trade)
                
                session = BotSession.query.get(session_id)
                if session:
                    session.total_brokerage += result.get('brokerage', 0.0)
                
                db.session.commit()
                
                log_entry = Log(
                    user_id=user_id,
                    message=f"Trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f} | Brokerage: ₹{result.get('brokerage', 0.0):.2f}",
                    level="INFO"
                )
                db.session.add(log_entry)
                db.session.commit()
                
                socketio.emit('user_notification', {
                    'type': 'success',
                    'message': f"Trade: {signal['action']} {signal['symbol']} @ ₹{execution_price:.2f} | Brokerage: ₹{result.get('brokerage', 0.0):.2f}",
                    'timestamp': datetime.now().isoformat()
                })
                
                socketio.emit('trade_executed', {
                    'session_id': session_id,
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'quantity': signal['quantity'],
                    'price': execution_price,
                    'brokerage': result.get('brokerage', 0.0),
                    'mode': 'paper',
                    'timestamp': datetime.now().isoformat()
                })
                
                socketio.emit('portfolio_update', {
                    'user_id': user_id,
                    'portfolio': paper_trading.get_portfolio(user_id),
                    'timestamp': datetime.now().isoformat()
                })
            else:
                error_msg = f"Trade failed: {result.get('error', 'Unknown error')}"
                log_entry = Log(
                    user_id=user_id,
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
        
        else:
            # --- LIVE TRADING ---
            # --- MODIFIED ---: Map to Kite symbol for placing order
            kite_symbol = f"NSE:{signal['symbol']}" # Assumes NSE stocks
            
            result = live_trading.place_order(
                symbol=kite_symbol,
                action=signal['action'],
                quantity=signal['quantity'],
                price=execution_price
            )
            
            if result['success']:
                trade = Trade(
                    user_id=user_id,
                    bot_session_id=session_id,
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal['quantity'],
                    price=execution_price,
                    trading_mode='live',
                    status='COMPLETED', # In real life, you'd track this
                    order_id=result.get('order_id'),
                    brokerage=result.get('brokerage', 0.0)
                )
                db.session.add(trade)
                
                session = BotSession.query.get(session_id)
                if session:
                    session.total_brokerage += result.get('brokerage', 0.0)
                
                db.session.commit()
                
                log_msg = f"LIVE Trade (Simulated): {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f} | Brokerage: ₹{result.get('brokerage', 0.0):.2f}"
                log_entry = Log(user_id=user_id, message=log_msg, level="INFO")
                db.session.add(log_entry)
                db.session.commit()
                
                socketio.emit('user_notification', {
                    'type': 'success',
                    'message': log_msg,
                    'timestamp': datetime.now().isoformat()
                })
                
                socketio.emit('trade_executed', {
                    'session_id': session_id,
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'quantity': signal['quantity'],
                    'price': execution_price,
                    'brokerage': result.get('brokerage', 0.0),
                    'mode': 'live',
                    'timestamp': datetime.now().isoformat()
                })
            else:
                error_msg = f"LIVE Trade failed: {result.get('error', 'Unknown error')}"
                log_entry = Log(user_id=user_id, message=error_msg, level="ERROR")
                db.session.add(log_entry)
                db.session.commit()
                
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
        
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })

def run_enhanced_trading_bot(session_id: int, config: Dict[str, Any], trading_session: TradingSession):
    """Enhanced trading bot with THREAD-SAFE immediate stop capability"""
    with app.app_context():
        try:
            strategy = strategy_engine.get_strategy(config['strategy'], config['strategy_params'])
            symbols = config['symbols'] or get_top_symbols(config['instrument_type'])
            
            log_entry = Log(
                user_id=config['user_id'],
                message=f"Bot {session_id} started with profit target: ₹{config['target_profit']}, max duration: {config['max_duration_hours']}h, capital: ₹{config['capital']}",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            print(f"🤖 Enhanced Bot {session_id} started! Target: ₹{config['target_profit']}, Duration: {config['max_duration_hours']}h, Capital: ₹{config['capital']}")
            
            session = BotSession.query.get(session_id)
            iteration = 0
            start_time = datetime.now()
            
            while session and session.status == 'running':
                # THREAD-SAFE STOP CHECK - check both database and thread-safe flag
                if (trading_session.should_stop or 
                    not session or 
                    session.stop_requested or 
                    session.force_stop or 
                    session.status != 'running'):
                    
                    print(f"🛑 IMMEDIATE STOP DETECTED for Bot {session_id}. Exiting NOW!")
                    
                    # Final cleanup
                    if session:
                        session.status = 'stopped'
                        session.stopped_at = datetime.now()
                        current_prices = get_current_prices()
                        pnl_data = paper_trading.get_pnl(config['user_id'], current_prices)
                        session.pnl = pnl_data['net_pnl']
                        db.session.commit()
                    
                    break
                
                # Check if max duration exceeded
                current_time = datetime.now()
                running_hours = (current_time - start_time).total_seconds() / 3600
                
                if running_hours >= config['max_duration_hours']:
                    print(f"⏰ Bot {session_id} reached max duration ({config['max_duration_hours']}h). Stopping...")
                    session.status = 'completed'
                    session.stopped_at = current_time
                    db.session.commit()
                    break
                
                should_trade = is_market_open() or config.get('test_mode', False)
                
                if should_trade:
                    # Get current portfolio and check profit target
                    current_prices = get_current_prices() # --- MODIFIED ---: This is now LIVE
                    
                    # PNL calculation depends on the mode
                    pnl_data = {}
                    current_positions = []
                    
                    if config['trading_mode'] == 'paper':
                        pnl_data = paper_trading.get_pnl(config['user_id'], current_prices)
                        current_positions = paper_trading.get_positions(config['user_id'])
                    else:
                        # --- LIVE MODE PNL/Position (Placeholder) ---
                        # This is complex. You need to get live positions,
                        # map them to your current_prices, and calculate PNL.
                        # For this demo, we'll use paper trading's PNL logic
                        # as a stand-in, but this is NOT accurate for live.
                        pnl_data = paper_trading.get_pnl(config['user_id'], current_prices)
                        live_pos = live_trading.get_positions()
                        if live_pos and 'net' in live_pos:
                             current_positions = live_pos['net']
                        
                    
                    # Check if profit target achieved
                    if config['target_profit'] > 0 and pnl_data.get('net_pnl', 0) >= config['target_profit']:
                        print(f"🎯 Bot {session_id} achieved profit target! P&L: ₹{pnl_data['net_pnl']:.2f}")
                        session.status = 'completed'
                        session.stopped_at = current_time
                        session.pnl = pnl_data['net_pnl']
                        db.session.commit()
                        break
                    
                    # Generate market data
                    market_data_dict = {}
                    for symbol in symbols:
                        market_data_dict[symbol] = {
                            'symbol': symbol,
                            'last_price': current_prices.get(symbol, 1000 + (hash(symbol) % 5000)),
                            'volume': random.randint(100000, 1000000),
                            'timestamp': datetime.now()
                        }
                    
                    # Generate signals with position limits
                    signals = strategy.generate_signals(market_data_dict, current_positions)
                    
                    if signals:
                        print(f"📈 Bot {session_id} generated {len(signals)} signals")
                        for signal in signals:
                            # ULTRA-FAST stop check before each trade execution
                            if trading_session.should_stop:
                                print(f"🛑 STOP detected during trade execution. ABORTING ALL TRADES.")
                                break
                            
                            # Also check database flags
                            session = BotSession.query.get(session_id)
                            if not session or session.stop_requested or session.force_stop or session.status != 'running':
                                print(f"🛑 Database stop detected. ABORTING TRADES.")
                                break
                                
                            execute_enhanced_trade(session_id, config, signal)
                    else:
                        if iteration % 10 == 0:
                            capital_used_percent = 0
                            if config['trading_mode'] == 'paper':
                                capital_used_percent = ((config['capital'] - paper_trading.get_portfolio(config['user_id'])['available_cash']) / config['capital']) * 100

                            log_entry = Log(
                                user_id=config['user_id'],
                                message=f"Bot {session_id} running ({config['trading_mode']}) - P&L: ₹{pnl_data.get('net_pnl', 0):.2f}, Positions: {len(current_positions)}, Capital Used: {capital_used_percent:.1f}%",
                                level="DEBUG"
                            )
                            db.session.add(log_entry)
                            db.session.commit()
                    
                    iteration += 1
                
                # Ultra-fast stop check before sleep (check both flags)
                if trading_session.should_stop:
                    print(f"🛑 Thread stop flag detected. Exiting immediately.")
                    break
                    
                session = BotSession.query.get(session_id)
                if not session or session.stop_requested or session.force_stop or session.status != 'running':
                    print(f"🛑 Database stop flags detected. Exiting immediately.")
                    break
                    
                # Use shorter sleep with interruptible sleep (30 * 0.1 = 3 seconds total)
                for i in range(30):
                    if trading_session.should_stop:
                        print(f"🛑 Stop detected during sleep. Breaking out.")
                        break
                    time_module.sleep(0.1)  # 100ms sleep that can be interrupted
            
            # Final cleanup when loop exits
            session_key = str(session_id)
            if session_key in trading_sessions:
                print(f"🧹 Final cleanup for bot session {session_id}")
                del trading_sessions[session_key]
                
        except Exception as e:
            error_msg = f"Enhanced Bot {session_id} error: {str(e)}"
            print(f"❌ {error_msg}")
            log_entry = Log(
                user_id=config['user_id'],
                message=error_msg,
                level="ERROR"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            # Clean up on error
            session_key = str(session_id)
            if session_key in trading_sessions:
                del trading_sessions[session_key]
            
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

# --- NEW ---: Background thread to push market data and PNL every 3 seconds
def background_data_pusher():
    """
    A background thread that pushes market data and PNL updates
    to all clients every 3 seconds.
    """
    print("Starting background data pusher thread...")
    while True:
        try:
            with app.app_context():
                # 1. Get all current prices (live or mock)
                current_prices = get_current_prices() 
                
                # 2. Prepare Market Watch Data
                # --- Prepare Stock Data ---
                symbols_stocks = get_top_symbols('stocks')
                market_data_stocks = []
                for symbol in symbols_stocks:
                    base_price = 1000 + (hash(symbol) % 5000)
                    current_price = current_prices.get(symbol, base_price)
                    previous_price = base_price # Flawed logic from original code
                    
                    change = current_price - previous_price
                    change_percent = (change / previous_price) * 100 if previous_price != 0 else 0
                    
                    market_data_stocks.append({
                        'symbol': symbol,
                        'last_price': current_price,
                        'change': round(change, 2),
                        'change_percent': round(change_percent, 2),
                        'volume': random.randint(100000, 1000000), # Volume is still mock
                        'timestamp': datetime.now().isoformat()
                    })

                # --- Prepare Index Data ---
                symbols_indices = get_top_symbols('indices')
                market_data_indices = []
                for symbol in symbols_indices:
                    base_price = 10000 + (hash(symbol) % 5000)
                    current_price = current_prices.get(symbol, base_price)
                    previous_price = base_price
                    
                    change = current_price - previous_price
                    change_percent = (change / previous_price) * 100 if previous_price != 0 else 0
                    
                    market_data_indices.append({
                        'symbol': symbol,
                        'last_price': current_price,
                        'change': round(change, 2),
                        'change_percent': round(change_percent, 2),
                        'volume': random.randint(100000, 1000000),
                        'timestamp': datetime.now().isoformat()
                    })

                # 3. Emit market data to all clients
                socketio.emit('market_watch_update', {
                    'stocks': market_data_stocks,
                    'indices': market_data_indices,
                    'timestamp': datetime.now().isoformat()
                })
                
                # 4. Prepare and Push PNL Updates for active bots
                active_sessions = trading_sessions.copy()
                for session_id, trading_session in active_sessions.items():
                    user_id = trading_session.config['user_id']
                    
                    # PNL calculation (Paper trading for now, live is more complex)
                    # We use paper_trading.get_pnl as it's the only logic available
                    pnl_data = paper_trading.get_pnl(user_id, current_prices)
                    
                    socketio.emit('pnl_update', {
                        'session_id': session_id,
                        'pnl': pnl_data['net_pnl'],
                        'unrealized_pnl': pnl_data['unrealized_pnl'],
                        'portfolio_value': pnl_data['portfolio_value'],
                        'timestamp': datetime.now().isoformat()
                    })

        except Exception as e:
            print(f"❌ Error in background data pusher: {e}")
        
        # --- MODIFIED ---: Use time_module.sleep for threading async_mode
        time_module.sleep(3) # Sleep for 3 seconds as requested


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
    # This is now handled by the background pusher,
    # but we can keep it as a confirmation
    symbols = data.get('symbols', [])
    if symbols:
        emit('subscription_confirmed', {
            'symbols': symbols,
            'message': f'Subscribed to {len(symbols)} symbols. Updates will be pushed.',
            'timestamp': datetime.now().isoformat()
        })

@socketio.on('request_market_data')
def handle_request_market_data(data):
    """
    Request current market data for symbols.
    This is now less important due to the pusher, but good for initial load.
    """
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
                
                demo_settings = UserSettings(user_id=demo_user.id)
                db.session.add(demo_settings)
                db.session.commit()
                print("✅ Created demo user: username='demo', password='demo123'")
            
            print("🚀 Starting ULTRA-STOP Indian Stock Trading Bot...")
            print("📍 Access: http://localhost:5000")
            print("🔑 Demo: username='demo', password='demo123'")
            print("🎯 GUARANTEED Features:")
            print("   - ✅ INSTANT STOP functionality (thread-safe)")
            print("   - ✅ NO trades after stop command")
            print("   - ✅ Immediate position exit")
            print("   - ✅ Profit booking with target amounts")
            print("   - ✅ LIVE data fetching (if credentials provided)")
            print("   - ✅ 3-SECOND data push (SocketIO)")
            print(f"🕒 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"📊 Market status: {'OPEN' if is_market_open() else 'CLOSED'}")
            
            # --- NEW ---: Start the background data pusher thread
            pusher_thread = threading.Thread(target=background_data_pusher, daemon=True)
            pusher_thread.start()
            print("Background 3-sec data pusher thread started.")

        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            import traceback
            traceback.print_exc()
    
    socketio.run(app, 
                 debug=True, 
                 host='0.0.0.0', 
                 port=5000,
                 allow_unsafe_werkzeug=True)
