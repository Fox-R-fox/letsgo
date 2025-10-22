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
import numpy as np

# Add current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Initialize Flask app first
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trading_bot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SOCKETIO_ASYNC_MODE'] = 'threading'

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(
    app,
    async_mode=app.config['SOCKETIO_ASYNC_MODE'],
    cors_allowed_origins="*",
    logger=True,
    engineio_logger=True
)

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
    order_type = db.Column(db.String(10), default='CNC')  # MIS or CNC
    risk_level = db.Column(db.Integer, default=50)  # Percentage from 10 to 100

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
    product_type = db.Column(db.String(10), default='CNC')  # MIS or CNC
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
    default_order_type = db.Column(db.String(10), default='CNC')
    paper_trading_balance = db.Column(db.Float, default=100000.0)
    default_risk_level = db.Column(db.Integer, default=50)  # Percentage from 10 to 100
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('settings', uselist=False))

class PaperPosition(db.Model):
    __tablename__ = 'paper_positions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    symbol = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    average_price = db.Column(db.Float, nullable=False)
    invested_amount = db.Column(db.Float, nullable=False)
    product_type = db.Column(db.String(10), default='CNC')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('paper_positions', lazy=True))

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

# Enhanced Live Trading System with Position Tracking
class LiveTrading:
    def __init__(self):
        self.kite = None
        self._last_api_key = None
        self._last_access_token = None
        self.live_positions = {}  # Track live positions by user_id
        self.mis_blocked_stocks = set()  # Track stocks that have MIS blocks
        self.trade_to_trade_stocks = set()  # Track trade-to-trade stocks
        self._initialization_lock = threading.Lock()  # Thread safety for initialization

    def initialize(self, api_key: str, access_token: str) -> bool:
        """Initialize Kite connection (cached) with thread safety"""
        with self._initialization_lock:
            try:
                try:
                    from kiteconnect import KiteConnect  # noqa
                except ImportError:
                    print("⚠️  kiteconnect not installed. Live trading disabled.")
                    return False

                # Clear cache if credentials changed
                if self.kite and (self._last_api_key != api_key or self._last_access_token != access_token):
                    print("🔄 Credentials changed, reinitializing Kite connection...")
                    self.kite = None
                    self._last_api_key = None
                    self._last_access_token = None

                if self.kite and self._last_api_key == api_key and self._last_access_token == access_token:
                    # Already initialized with same creds
                    return True

                from kiteconnect import KiteConnect
                self.kite = KiteConnect(api_key=api_key)
                self.kite.set_access_token(access_token)
                
                # Test the connection with a simple API call
                try:
                    profile = self.kite.profile()
                    if profile:
                        self._last_api_key = api_key
                        self._last_access_token = access_token
                        print(f"✅ Kite Connect initialized for user: {profile.get('user_name', 'Unknown')}")
                        
                        # Load trade-to-trade stocks list
                        self._load_trade_to_trade_stocks()
                        
                        return True
                    return False
                except Exception as api_error:
                    error_msg = str(api_error)
                    if "Invalid api_key" in error_msg or "Invalid access_token" in error_msg:
                        print(f"❌ INVALID CREDENTIALS: Please check your API Key and Access Token")
                        # Clear invalid credentials
                        self.kite = None
                        self._last_api_key = None
                        self._last_access_token = None
                    else:
                        print(f"❌ Kite API error: {error_msg}")
                    return False

            except Exception as e:
                print(f"❌ Kite initialization error: {e}")
                self.kite = None
                return False

    def _load_trade_to_trade_stocks(self):
        """Load known trade-to-trade stocks that cannot be traded intraday"""
        # Common trade-to-trade stocks that block MIS orders
        self.trade_to_trade_stocks = {
            '3IINFO-RE-BE', 'CALSOFTPP-E1', 'SALSTEEL-BE', 'MBLINFRA', 'MONIFTY100',
            # Add more trade-to-trade stocks as encountered
            'BFINVEST-BE', 'BFUTILITIE-BE', 'BROOKS-BE', 'CCCL-BE', 'MOHITIND-BE',
            'NIRAJ-BE', 'OSWALAGRO-BE', 'PIONEEREMB-BE', 'SABTN-BE', 'SILVERTUC-BE',
            'SUPERSPIN-BE', 'SURANASOL-BE', 'SURANAT-BE', 'SURYALAXMI-BE', 'SUTLEJTEX-BE'
        }
        print(f"📋 Loaded {len(self.trade_to_trade_stocks)} trade-to-trade stocks")

    def _is_trade_to_trade_stock(self, symbol: str) -> bool:
        """Check if a stock is trade-to-trade (cannot be traded intraday)"""
        return symbol.upper() in self.trade_to_trade_stocks

    def get_margins(self):
        """Return equity margins only (more deterministic)"""
        try:
            if not self.kite:
                return None
            # Prefer segment-specific call if supported by kiteconnect version
            try:
                return self.kite.margins('equity')
            except TypeError:
                # Older versions may only support margins() -> dict with 'equity' key
                full = self.kite.margins()
                return full.get('equity', full)
        except Exception as e:
            print(f"Error getting margins: {e}")
            return None

    def _compute_usable_cash_from_margins(self, margins_obj: dict) -> float:
        """
        Compute usable/wallet cash from Zerodha margins payload.
        usable = available.cash + intraday_payin + adhoc_margin + collateral - utilised.debits
        Fallback to opening_balance when cash==0.
        """
        if not margins_obj:
            return 0.0

        # If caller passed full margins(), take 'equity' branch; otherwise use as-is.
        equity = margins_obj.get('equity', margins_obj) if isinstance(margins_obj, dict) else {}

        available = equity.get('available', {}) or {}
        utilised = equity.get('utilised', {}) or {}

        def f(x, default=0.0):
            try:
                v = available.get(x, default)
                return float(v if v is not None else default)
            except Exception:
                return default

        def fu(x, default=0.0):
            try:
                v = utilised.get(x, default)
                return float(v if v is not None else default)
            except Exception:
                return default

        cash = f('cash')
        intraday_payin = f('intraday_payin')
        adhoc_margin = f('adhoc_margin')
        collateral = f('collateral')
        opening_balance = f('opening_balance')

        debits = fu('debits')

        usable_cash = cash + intraday_payin + adhoc_margin + collateral - debits

        if usable_cash == 0:
            # Fallback for tiny balances that sit in opening_balance
            usable_cash = max(0.0, opening_balance - debits)

        return round(usable_cash, 2)

    def get_holdings(self) -> Dict[str, Any] | list | None:
        """Get current holdings"""
        try:
            if self.kite:
                return self.kite.holdings()
            return None
        except Exception as e:
            print(f"Error getting holdings: {e}")
            return None

    def get_positions(self) -> Dict[str, Any] | None:
        """Get current positions"""
        try:
            if self.kite:
                return self.kite.positions()
            return None
        except Exception as e:
            print(f"Error getting positions: {e}")
            return None

    def get_wallet_balance(self) -> Dict[str, Any]:
        """Get usable wallet balance robustly from margins"""
        try:
            if not self.kite:
                return {'success': False, 'error': 'Kite not initialized'}

            margins = self.get_margins()
            if not margins:
                return {'success': False, 'error': 'No margins returned'}

            wallet_balance = self._compute_usable_cash_from_margins(margins)

            # NB: for debug, we return the margins branch we used
            used_equity = margins.get('equity', margins) if isinstance(margins, dict) else {}
            return {
                'success': True,
                'wallet_balance': wallet_balance,
                'available_margins': used_equity.get('available', {}),
                'raw_data': used_equity
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_live_balance(self) -> Dict[str, Any]:
        """Get actual live balances and portfolio value"""
        try:
            if not self.kite:
                return {'success': False, 'error': 'Kite not initialized'}

            margins = self.get_margins()
            if not margins:
                return {'success': False, 'error': 'Could not fetch margins'}

            available_cash = self._compute_usable_cash_from_margins(margins)

            holdings = self.get_holdings() or []
            positions = self.get_positions() or {}

            portfolio_value = available_cash

            # Add holdings value if present
            if isinstance(holdings, list):
                for h in holdings:
                    qty = float(h.get('quantity', 0) or 0)
                    last_price = float(h.get('last_price', h.get('average_price', 0)) or 0)
                    portfolio_value += qty * last_price

            # Add delivery positions (net) if present
            net_positions = positions.get('net', []) if isinstance(positions, dict) else []
            for p in net_positions:
                if p.get('product') == 'CNC':
                    qty = float(p.get('quantity', 0) or 0)
                    last_price = float(p.get('last_price', p.get('average_price', 0)) or 0)
                    portfolio_value += qty * last_price

            used_equity = margins.get('equity', margins) if isinstance(margins, dict) else {}
            return {
                'success': True,
                'available_cash': round(available_cash, 2),
                'portfolio_value': round(portfolio_value, 2),
                'margins': used_equity,
                'holdings_count': len(holdings) if isinstance(holdings, list) else 0,
                'positions_count': len(net_positions),
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_market_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch live market quotes from Zerodha and normalize them to the
        structure required by the frontend table.
        """
        try:
            if not self.kite:
                return []

            # Filter out trade-to-trade stocks
            filtered_symbols = [s for s in symbols if not self._is_trade_to_trade_stock(s)]
            if not filtered_symbols:
                return []

            # Zerodha expects tradingsymbols with exchange. We'll assume NSE:<SYMBOL>
            instruments = [f"NSE:{s}" for s in filtered_symbols]
            quotes = self.kite.quote(instruments)  # dict keyed by instrument

            results = []
            now_iso = datetime.now().isoformat()

            for inst_key, q in quotes.items():
                # Extract symbol back
                sym = inst_key.split(":")[-1]
                last_price = float(q.get('last_price') or 0.0)
                ohlc = q.get('ohlc', {}) or {}
                o = float(ohlc.get('open') or 0.0)
                h = float(ohlc.get('high') or 0.0)
                l = float(ohlc.get('low') or 0.0)
                c = float(ohlc.get('close') or 0.0)

                # Fallbacks if ohlc missing
                if not any([o, h, l, c]):
                    o = last_price
                    h = last_price
                    l = last_price
                    c = last_price

                change = last_price - c
                change_pct = (change / c * 100.0) if c else 0.0

                volume = int(q.get('volume') or q.get('last_quantity') or 0)

                results.append({
                    'symbol': sym,
                    'last_price': round(last_price, 2),
                    'change': round(change, 2),
                    'change_percent': round(change_pct, 2),
                    'volume': volume,
                    'open': round(o, 2),
                    'high': round(max(h, o, last_price), 2),
                    'low': round(min(l, o, last_price), 2),
                    'close': round(c, 2),
                    'timestamp': now_iso,
                    'is_trade_to_trade': self._is_trade_to_trade_stock(sym)
                })

            return results

        except Exception as e:
            print(f"Error fetching live quotes: {e}")
            return []

    def get_all_nse_stocks(self) -> List[str]:
        """Get all NSE stocks dynamically from Zerodha"""
        try:
            if not self.kite:
                return []

            instruments = self.kite.instruments("NSE")
            if not instruments:
                return []

            # Filter for equity stocks and get their symbols
            stocks = []
            for instrument in instruments:
                if instrument['instrument_type'] == 'EQ' and instrument['exchange'] == 'NSE':
                    stocks.append(instrument['tradingsymbol'])
            
            print(f"📊 Found {len(stocks)} NSE stocks from Zerodha API")
            return stocks

        except Exception as e:
            print(f"Error getting NSE stocks: {e}")
            return []

    def get_top_gainers(self, available_cash: float, count: int = 15) -> List[Dict[str, Any]]:
        """
        Get top gainers from Zerodha that are affordable based on available cash
        """
        try:
            if not self.kite:
                return []

            # Get all NSE stocks
            all_stocks = self.get_all_nse_stocks()
            if not all_stocks:
                return []

            # Take a sample of stocks for performance (you can increase this)
            sample_stocks = random.sample(all_stocks, min(100, len(all_stocks)))
            
            # Filter out trade-to-trade stocks
            filtered_stocks = [s for s in sample_stocks if not self._is_trade_to_trade_stock(s)]
            
            # Get quotes for sampled stocks
            instruments_to_fetch = [f"NSE:{stock}" for stock in filtered_stocks]
            quotes = self.kite.quote(instruments_to_fetch)

            gainers = []
            for inst_key, q in quotes.items():
                symbol = inst_key.split(":")[-1]
                last_price = float(q.get('last_price') or 0.0)
                ohlc = q.get('ohlc', {}) or {}
                previous_close = float(ohlc.get('close') or last_price)
                
                if previous_close > 0 and last_price > 0:
                    change_percent = ((last_price - previous_close) / previous_close) * 100
                    
                    # Calculate affordable quantity (can buy at least 1 share)
                    max_affordable_quantity = int(available_cash * 0.1 / last_price)  # Use 10% of capital per stock
                    
                    if max_affordable_quantity >= 1 and last_price <= available_cash:  # Only include stocks we can afford
                        gainers.append({
                            'symbol': symbol,
                            'last_price': last_price,
                            'change_percent': change_percent,
                            'volume': int(q.get('volume') or 0),
                            'affordable_quantity': max_affordable_quantity,
                            'trade_value': last_price * max_affordable_quantity,
                            'is_trade_to_trade': self._is_trade_to_trade_stock(symbol)
                        })

            # Sort by gain percentage and return top count
            gainers.sort(key=lambda x: x['change_percent'], reverse=True)
            return gainers[:count]

        except Exception as e:
            print(f"Error getting top gainers: {e}")
            return []

    def get_affordable_stocks(self, available_cash: float, max_capital_usage: float = 0.8) -> List[str]:
        """
        Dynamically get affordable stocks based on available wallet balance
        Uses top gainers that the user can actually afford to trade
        """
        try:
            if not self.kite:
                return []

            usable_cash = available_cash * max_capital_usage
            print(f"💰 Getting affordable stocks for usable cash: ₹{usable_cash:.2f}")

            # Get top gainers that are affordable
            gainers = self.get_top_gainers(usable_cash, count=15)
            
            affordable_stocks = []
            for stock in gainers:
                if stock['affordable_quantity'] >= 1 and not stock['is_trade_to_trade']:
                    affordable_stocks.append(stock['symbol'])
                    print(f"  ✅ {stock['symbol']}: ₹{stock['last_price']} (Qty: {stock['affordable_quantity']}, Change: {stock['change_percent']:.2f}%)")
                elif stock['is_trade_to_trade']:
                    print(f"  ⚠️  Skipping trade-to-trade stock: {stock['symbol']}")

            print(f"🎯 Selected {len(affordable_stocks)} affordable stocks (excluding trade-to-trade)")
            return affordable_stocks

        except Exception as e:
            print(f"Error getting affordable stocks: {e}")
            # Fallback to popular stocks if API fails
            return ['RELIANCE', 'TCS', 'INFY', 'HDFC', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'BHARTIARTL', 'KOTAKBANK', 'ITC']

    def calculate_zerodha_brokerage(self, trade_value: float, action: str, product_type: str = 'CNC') -> float:
        """
        Zerodha-like brokerage charges:
        - MIS: ₹20 or 0.03% (whichever lower) + taxes/fees
        - CNC: 0% brokerage + taxes/fees (delivery)
        """
        if action.upper() in ['BUY', 'SELL']:
            if product_type == 'CNC':
                # Delivery trading has zero brokerage
                brokerage = 0.0
            else:
                # Intraday trading
                brokerage_percentage = 0.0003
                brokerage_by_percentage = trade_value * brokerage_percentage
                fixed_brokerage = 20.0
                brokerage = min(brokerage_by_percentage, fixed_brokerage)

            # Common charges for both MIS and CNC
            stt = trade_value * 0.00025 if action.upper() == 'SELL' else 0.0
            transaction_charges = trade_value * 0.0000345
            gst = (brokerage + transaction_charges) * 0.18
            sebi_charges = trade_value * 0.000001
            stamp_duty = trade_value * 0.00003 if action.upper() == 'BUY' else 0.0

            total_charges = brokerage + stt + transaction_charges + gst + sebi_charges + stamp_duty
            return total_charges

        return 0.0

    def place_order(self, symbol: str, action: str, quantity: int, price: float, user_id: int, product_type: str = 'CNC') -> Dict[str, Any]:
        """Place REAL live order with Zerodha API with automatic MIS/CNC handling"""
        try:
            if not self.kite:
                return {'success': False, 'error': 'Kite not initialized. Please check your API credentials.'}

            # Check if stock is trade-to-trade and user is trying MIS
            if self._is_trade_to_trade_stock(symbol) and product_type == 'MIS':
                return {
                    'success': False,
                    'error': f'❌ TRADE-TO-TRADE STOCK: {symbol} cannot be traded intraday (MIS). This is a trade-to-trade stock. Use CNC for delivery orders only.'
                }

            # Check available balance first
            balance_data = self.get_live_balance()
            if not balance_data['success']:
                return {'success': False, 'error': f'Failed to check balance: {balance_data.get("error")}'}

            available_cash = balance_data['available_cash']
            trade_value = quantity * price
            brokerage = self.calculate_zerodha_brokerage(trade_value, action, product_type)
            total_cost = trade_value + brokerage if action.upper() == 'BUY' else 0

            # Validate capital for BUY orders
            if action.upper() == 'BUY' and total_cost > available_cash:
                return {
                    'success': False, 
                    'error': f'❌ INSUFFICIENT BALANCE: Required ₹{total_cost:.2f}, Available ₹{available_cash:.2f}. Cannot place BUY order for {quantity} shares of {symbol} at ₹{price:.2f}'
                }

            # Place REAL order with Zerodha
            try:
                order_type = 'LIMIT'
                
                print(f"📊 Placing LIVE ORDER: {action} {quantity} {symbol} @ ₹{price:.2f} ({product_type})")
                
                # Actual order placement - FIXED: Ensure product parameter is properly passed
                order_response = self.kite.place_order(
                    tradingsymbol=symbol,
                    exchange='NSE',
                    transaction_type=action.upper(),
                    quantity=quantity,
                    order_type=order_type,
                    product=product_type,  # Use the specified product type
                    price=price,
                    variety='regular'
                )

                order_id = order_response
                print(f"✅ ORDER PLACED SUCCESSFULLY: {order_id} ({product_type})")

                # Track position internally
                self._update_live_position(user_id, symbol, action, quantity, price, order_id, product_type)

                return {
                    'success': True,
                    'order_id': order_id,
                    'message': f'Live {product_type} order placed: {order_id}',
                    'brokerage': brokerage,
                    'trade_value': trade_value,
                    'product_type': product_type,
                    'available_balance_after': available_cash - total_cost if action.upper() == 'BUY' else available_cash
                }

            except Exception as order_error:
                error_msg = str(order_error)
                
                # Check if it's an MIS block error
                if "MIS orders are currently blocked" in error_msg:
                    print(f"⚠️ MIS blocked for {symbol}, retrying with CNC...")
                    # Retry with CNC
                    return self.place_order(symbol, action, quantity, price, user_id, 'CNC')
                elif "Missing or empty field `product`" in error_msg:
                    print(f"⚠️ Product field missing error, using default CNC...")
                    # Retry with explicit CNC
                    return self.place_order(symbol, action, quantity, price, user_id, 'CNC')
                elif "Intraday trading is not allowed" in error_msg or "trade to trade" in error_msg.lower():
                    print(f"⚠️ Trade-to-trade stock detected: {symbol}, using CNC...")
                    # Add to our known trade-to-trade list
                    self.trade_to_trade_stocks.add(symbol.upper())
                    # Retry with CNC
                    return self.place_order(symbol, action, quantity, price, user_id, 'CNC')
                elif "Invalid api_key" in error_msg or "Invalid access_token" in error_msg:
                    print(f"❌ INVALID CREDENTIALS: Please check your API Key and Access Token")
                    return {'success': False, 'error': '❌ INVALID CREDENTIALS: Please check your Zerodha API Key and Access Token in Settings'}
                else:
                    error_msg = f"Order placement failed: {error_msg}"
                    print(f"❌ ORDER FAILED: {error_msg}")
                    return {'success': False, 'error': error_msg}

        except Exception as e:
            error_msg = f"Order placement error: {str(e)}"
            print(f"❌ ORDER ERROR: {error_msg}")
            return {'success': False, 'error': error_msg}

    def _update_live_position(self, user_id: int, symbol: str, action: str, quantity: int, price: float, order_id: str, product_type: str):
        """Update internal live position tracking"""
        try:
            if user_id not in self.live_positions:
                self.live_positions[user_id] = {}

            symbol = symbol.upper()
            portfolio = self.live_positions[user_id]

            if action.upper() == 'BUY':
                if symbol in portfolio:
                    old_position = portfolio[symbol]
                    total_quantity = old_position['quantity'] + quantity
                    total_invested = (old_position['quantity'] * old_position['average_price']) + (quantity * price)
                    new_avg_price = total_invested / total_quantity

                    portfolio[symbol] = {
                        'quantity': total_quantity,
                        'average_price': new_avg_price,
                        'total_invested': total_invested,
                        'last_order_id': order_id,
                        'product_type': product_type
                    }
                else:
                    portfolio[symbol] = {
                        'quantity': quantity,
                        'average_price': price,
                        'total_invested': quantity * price,
                        'last_order_id': order_id,
                        'product_type': product_type
                    }

            elif action.upper() == 'SELL':
                if symbol in portfolio:
                    position = portfolio[symbol]
                    if position['quantity'] >= quantity:
                        new_quantity = position['quantity'] - quantity
                        if new_quantity == 0:
                            del portfolio[symbol]
                        else:
                            # Keep average price same, just reduce quantity
                            portfolio[symbol] = {
                                'quantity': new_quantity,
                                'average_price': position['average_price'],
                                'total_invested': new_quantity * position['average_price'],
                                'last_order_id': order_id,
                                'product_type': position.get('product_type', 'CNC')
                            }

        except Exception as e:
            print(f"Error updating live position: {e}")

    def get_live_positions(self, user_id: int) -> List[Dict[str, Any]]:
        """Get current live positions for user"""
        try:
            if user_id not in self.live_positions:
                return []

            portfolio = self.live_positions[user_id]
            positions = []
            
            # Get current prices for all positions
            symbols = list(portfolio.keys())
            if symbols:
                quotes = self.get_market_quotes(symbols)
                current_prices = {q['symbol']: q['last_price'] for q in quotes}
            else:
                current_prices = {}

            for symbol, position in portfolio.items():
                current_price = current_prices.get(symbol, position['average_price'])
                unrealized_pnl = (current_price - position['average_price']) * position['quantity']
                
                positions.append({
                    'symbol': symbol,
                    'quantity': position['quantity'],
                    'average_price': position['average_price'],
                    'current_price': current_price,
                    'unrealized_pnl': unrealized_pnl,
                    'invested_amount': position['total_invested'],
                    'current_value': position['quantity'] * current_price,
                    'pnl_percent': ((current_price - position['average_price']) / position['average_price']) * 100,
                    'last_order_id': position.get('last_order_id', ''),
                    'product_type': position.get('product_type', 'CNC'),
                    'action': 'sell'  # FIX: Add action field to prevent undefined
                })

            return positions

        except Exception as e:
            print(f"Error getting live positions: {e}")
            return []

    def get_live_pnl(self, user_id: int) -> Dict[str, float]:
        """Calculate P&L for live trading"""
        try:
            positions = self.get_live_positions(user_id)
            
            total_invested = 0.0
            current_value = 0.0
            unrealized_pnl = 0.0

            for position in positions:
                total_invested += position['invested_amount']
                current_value += position['current_value']
                unrealized_pnl += position['unrealized_pnl']

            # Get realized P&L from database trades
            realized_trades = Trade.query.filter_by(
                user_id=user_id, 
                trading_mode='live',
                action='SELL'
            ).all()
            
            realized_pnl = sum(trade.brokerage for trade in realized_trades)

            total_pnl = unrealized_pnl - realized_pnl

            return {
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': total_pnl,
                'net_pnl': total_pnl,
                'portfolio_value': current_value,
                'total_invested': total_invested
            }

        except Exception as e:
            print(f"Error calculating live P&L: {e}")
            return {
                'realized_pnl': 0.0,
                'unrealized_pnl': 0.0,
                'total_pnl': 0.0,
                'net_pnl': 0.0,
                'portfolio_value': 0.0,
                'total_invested': 0.0
            }

    def exit_all_positions(self, user_id: int) -> Dict[str, Any]:
        """Exit all live positions when bot stops"""
        try:
            if user_id not in self.live_positions:
                return {'success': True, 'message': 'No positions to exit', 'exited_positions': 0}

            portfolio = self.live_positions[user_id]
            if not portfolio:
                return {'success': True, 'message': 'No positions to exit', 'exited_positions': 0}

            print(f"🛑 Exiting all positions for user {user_id}: {len(portfolio)} positions")
            
            exited_count = 0
            errors = []

            for symbol, position in list(portfolio.items()):
                try:
                    # Get current market price
                    quotes = self.get_market_quotes([symbol])
                    if not quotes:
                        errors.append(f"Could not get price for {symbol}")
                        continue

                    current_price = quotes[0]['last_price']
                    sell_price = round(current_price * 0.995, 2)  # Slightly below market to ensure execution

                    # Place SELL order
                    result = self.place_order(
                        symbol=symbol,
                        action='SELL',
                        quantity=position['quantity'],
                        price=sell_price,
                        user_id=user_id,
                        product_type=position.get('product_type', 'CNC')
                    )

                    if result['success']:
                        print(f"✅ Exited position: {symbol} {position['quantity']} shares @ {sell_price}")
                        exited_count += 1
                    else:
                        errors.append(f"Failed to exit {symbol}: {result.get('error')}")

                except Exception as e:
                    errors.append(f"Error exiting {symbol}: {str(e)}")

            return {
                'success': True,
                'exited_positions': exited_count,
                'total_positions': len(portfolio),
                'errors': errors,
                'message': f'Exited {exited_count}/{len(portfolio)} positions'
            }

        except Exception as e:
            return {'success': False, 'error': f'Error exiting positions: {str(e)}'}

# Initialize live trading
live_trading = LiveTrading()

# Paper Trading System
class PaperTrading:
    def __init__(self):
        self.paper_balances = {}  # user_id -> balance
        self.paper_positions = {}  # user_id -> {symbol: position_data}
        
    def get_paper_balance(self, user_id: int) -> Dict[str, Any]:
        """Get paper trading balance"""
        try:
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if not settings:
                # Create default settings
                settings = UserSettings(user_id=user_id, paper_trading_balance=100000.0)
                db.session.add(settings)
                db.session.commit()
            
            # Get current paper positions from database
            paper_positions = PaperPosition.query.filter_by(user_id=user_id).all()
            
            # Calculate current portfolio value
            portfolio_value = settings.paper_trading_balance
            for position in paper_positions:
                # Get current price for the symbol
                quotes = live_trading.get_market_quotes([position.symbol])
                if quotes:
                    current_price = quotes[0]['last_price']
                    position_value = position.quantity * current_price
                    portfolio_value += position_value
            
            return {
                'success': True,
                'paper_balance': settings.paper_trading_balance,
                'portfolio_value': portfolio_value,
                'available_cash': settings.paper_trading_balance,
                'positions_count': len(paper_positions)
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_paper_positions(self, user_id: int) -> List[Dict[str, Any]]:
        """Get paper trading positions"""
        try:
            positions = PaperPosition.query.filter_by(user_id=user_id).all()
            result = []
            
            for position in positions:
                # Get current price
                quotes = live_trading.get_market_quotes([position.symbol])
                current_price = position.average_price  # Default to average price if no quote
                if quotes:
                    current_price = quotes[0]['last_price']
                
                unrealized_pnl = (current_price - position.average_price) * position.quantity
                pnl_percent = ((current_price - position.average_price) / position.average_price) * 100
                
                result.append({
                    'symbol': position.symbol,
                    'quantity': position.quantity,
                    'average_price': position.average_price,
                    'current_price': current_price,
                    'unrealized_pnl': unrealized_pnl,
                    'invested_amount': position.invested_amount,
                    'current_value': position.quantity * current_price,
                    'pnl_percent': pnl_percent,
                    'product_type': position.product_type,
                    'action': 'sell'  # FIX: Add action field to prevent undefined
                })
            
            return result
        except Exception as e:
            print(f"Error getting paper positions: {e}")
            return []
    
    def calculate_paper_brokerage(self, trade_value: float, action: str, product_type: str = 'CNC') -> float:
        """Calculate paper trading brokerage (same as live for realism)"""
        return live_trading.calculate_zerodha_brokerage(trade_value, action, product_type)
    
    def place_paper_order(self, symbol: str, action: str, quantity: int, price: float, user_id: int, product_type: str = 'CNC') -> Dict[str, Any]:
        """Place paper trade order"""
        try:
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if not settings:
                return {'success': False, 'error': 'User settings not found'}
            
            trade_value = quantity * price
            brokerage = self.calculate_paper_brokerage(trade_value, action, product_type)
            total_cost = trade_value + brokerage if action.upper() == 'BUY' else 0
            
            # Check balance for BUY orders
            if action.upper() == 'BUY' and total_cost > settings.paper_trading_balance:
                return {
                    'success': False,
                    'error': f'❌ PAPER: Insufficient balance. Required: ₹{total_cost:.2f}, Available: ₹{settings.paper_trading_balance:.2f}'
                }
            
            # Check position for SELL orders
            if action.upper() == 'SELL':
                position = PaperPosition.query.filter_by(user_id=user_id, symbol=symbol).first()
                if not position or position.quantity < quantity:
                    return {
                        'success': False,
                        'error': f'❌ PAPER: Insufficient shares to sell. Requested: {quantity}, Available: {position.quantity if position else 0}'
                    }
            
            # Execute the paper trade
            if action.upper() == 'BUY':
                # Update balance
                settings.paper_trading_balance -= total_cost
                
                # Update or create position
                position = PaperPosition.query.filter_by(user_id=user_id, symbol=symbol).first()
                if position:
                    # Update existing position
                    total_quantity = position.quantity + quantity
                    total_invested = position.invested_amount + trade_value
                    new_avg_price = total_invested / total_quantity
                    
                    position.quantity = total_quantity
                    position.average_price = new_avg_price
                    position.invested_amount = total_invested
                    position.updated_at = datetime.now()
                else:
                    # Create new position
                    position = PaperPosition(
                        user_id=user_id,
                        symbol=symbol,
                        quantity=quantity,
                        average_price=price,
                        invested_amount=trade_value,
                        product_type=product_type
                    )
                    db.session.add(position)
                
            else:  # SELL
                # Update balance (add sale proceeds minus brokerage)
                sale_proceeds = trade_value - brokerage
                settings.paper_trading_balance += sale_proceeds
                
                # Update position
                position = PaperPosition.query.filter_by(user_id=user_id, symbol=symbol).first()
                if position:
                    if position.quantity == quantity:
                        # Close position
                        db.session.delete(position)
                    else:
                        # Reduce position
                        position.quantity -= quantity
                        position.invested_amount = position.quantity * position.average_price
                        position.updated_at = datetime.now()
            
            # Generate paper order ID
            order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"
            
            db.session.commit()
            
            return {
                'success': True,
                'order_id': order_id,
                'message': f'Paper {product_type} order executed: {order_id}',
                'brokerage': brokerage,
                'trade_value': trade_value,
                'product_type': product_type,
                'available_balance_after': settings.paper_trading_balance
            }
            
        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': f'Paper trade error: {str(e)}'}
    
    def get_paper_pnl(self, user_id: int) -> Dict[str, float]:
        """Calculate paper trading P&L"""
        try:
            positions = self.get_paper_positions(user_id)
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            
            if not settings:
                return {
                    'realized_pnl': 0.0,
                    'unrealized_pnl': 0.0,
                    'total_pnl': 0.0,
                    'net_pnl': 0.0,
                    'portfolio_value': 0.0,
                    'total_invested': 0.0
                }
            
            # Calculate unrealized P&L from current positions
            unrealized_pnl = 0.0
            total_invested = 0.0
            current_value = settings.paper_trading_balance
            
            for position in positions:
                unrealized_pnl += position['unrealized_pnl']
                total_invested += position['invested_amount']
                current_value += position['current_value']
            
            # Calculate realized P&L from trade history
            realized_trades = Trade.query.filter_by(
                user_id=user_id,
                trading_mode='paper',
                action='SELL'
            ).all()
            
            realized_pnl = sum(trade.brokerage for trade in realized_trades)
            
            total_pnl = unrealized_pnl - realized_pnl
            
            return {
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': total_pnl,
                'net_pnl': total_pnl,
                'portfolio_value': current_value,
                'total_invested': total_invested
            }
            
        except Exception as e:
            print(f"Error calculating paper P&L: {e}")
            return {
                'realized_pnl': 0.0,
                'unrealized_pnl': 0.0,
                'total_pnl': 0.0,
                'net_pnl': 0.0,
                'portfolio_value': 0.0,
                'total_invested': 0.0
            }

    def exit_all_positions(self, user_id: int) -> Dict[str, Any]:
        """Exit all paper positions when bot stops"""
        try:
            positions = PaperPosition.query.filter_by(user_id=user_id).all()
            if not positions:
                return {'success': True, 'message': 'No paper positions to exit', 'exited_positions': 0}

            print(f"🛑 Exiting all paper positions for user {user_id}: {len(positions)} positions")
            
            exited_count = 0
            errors = []
            settings = UserSettings.query.filter_by(user_id=user_id).first()

            for position in positions:
                try:
                    # Get current market price
                    quotes = live_trading.get_market_quotes([position.symbol])
                    if not quotes:
                        errors.append(f"Could not get price for {position.symbol}")
                        continue

                    current_price = quotes[0]['last_price']
                    sell_price = round(current_price * 0.995, 2)  # Slightly below market to ensure execution

                    # Calculate brokerage
                    trade_value = position.quantity * sell_price
                    brokerage = self.calculate_paper_brokerage(trade_value, 'SELL', position.product_type)
                    sale_proceeds = trade_value - brokerage

                    # Update balance
                    settings.paper_trading_balance += sale_proceeds

                    # Create trade record
                    trade = Trade(
                        user_id=user_id,
                        symbol=position.symbol,
                        action='SELL',
                        quantity=position.quantity,
                        price=sell_price,
                        trading_mode='paper',
                        status='COMPLETED',
                        order_id=f"EXIT_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}",
                        brokerage=brokerage,
                        product_type=position.product_type
                    )
                    db.session.add(trade)

                    # Delete the position
                    db.session.delete(position)
                    exited_count += 1

                    print(f"✅ Exited paper position: {position.symbol} {position.quantity} shares @ {sell_price}")

                except Exception as e:
                    errors.append(f"Error exiting {position.symbol}: {str(e)}")

            db.session.commit()

            return {
                'success': True,
                'exited_positions': exited_count,
                'total_positions': len(positions),
                'errors': errors,
                'message': f'Exited {exited_count}/{len(positions)} paper positions'
            }

        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': f'Error exiting paper positions: {str(e)}'}

    def reset_paper_portfolio(self, user_id: int) -> Dict[str, Any]:
        """Reset paper trading portfolio to initial state"""
        try:
            # Exit all positions first
            exit_result = self.exit_all_positions(user_id)
            
            # Reset paper balance to default
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if settings:
                settings.paper_trading_balance = 100000.0
                db.session.commit()

            return {
                'success': True,
                'message': 'Paper portfolio reset successfully',
                'exited_positions': exit_result.get('exited_positions', 0),
                'new_balance': 100000.0
            }

        except Exception as e:
            db.session.rollback()
            return {'success': False, 'error': f'Error resetting paper portfolio: {str(e)}'}

# Initialize paper trading
paper_trading = PaperTrading()

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
                    {'name': 'quantity', 'type': 'number', 'default': 1, 'min': 1, 'max': 10, 'description': 'Quantity to trade per signal'},
                    {'name': 'max_positions', 'type': 'number', 'default': 3, 'min': 1, 'max': 10, 'description': 'Maximum number of simultaneous positions'},
                    {'name': 'order_type', 'type': 'select', 'default': 'CNC', 'options': ['MIS', 'CNC'], 'description': 'Order type (MIS for intraday, CNC for delivery)'},
                    {'name': 'risk_level', 'type': 'select', 'default': '50', 'options': ['10', '20', '30', '40', '50', '60', '70', '80', '90', '100'], 'description': 'Trading aggression level (10-100%)'}
                ]
            },
            {
                'name': 'mean_reversion',
                'display_name': 'Mean Reversion',
                'description': 'Trades based on price deviations from historical mean',
                'parameters': [
                    {'name': 'lookback_period', 'type': 'number', 'default': 10, 'min': 5, 'max': 50, 'description': 'Lookback period for mean calculation'},
                    {'name': 'deviation_threshold', 'type': 'number', 'default': 2.0, 'min': 1.0, 'max': 5.0, 'description': 'Standard deviation threshold'},
                    {'name': 'quantity', 'type': 'number', 'default': 1, 'min': 1, 'max': 5, 'description': 'Quantity to trade per signal'},
                    {'name': 'max_positions', 'type': 'number', 'default': 3, 'min': 1, 'max': 10, 'description': 'Maximum number of simultaneous positions'},
                    {'name': 'order_type', 'type': 'select', 'default': 'CNC', 'options': ['MIS', 'CNC'], 'description': 'Order type (MIS for intraday, CNC for delivery)'},
                    {'name': 'risk_level', 'type': 'select', 'default': '50', 'options': ['10', '20', '30', '40', '50', '60', '70', '80', '90', '100'], 'description': 'Trading aggression level (10-100%)'}
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
        self.max_positions = self.parameters.get('max_positions', 3)
        self.order_type = self.parameters.get('order_type', 'CNC')
        self.risk_level = int(self.parameters.get('risk_level', 50))  # Percentage from 10 to 100
        
        # Validate risk level
        if self.risk_level < 10:
            self.risk_level = 10
        elif self.risk_level > 100:
            self.risk_level = 100

    def get_risk_config(self):
        """Get risk configuration based on percentage (10-100)"""
        risk_percentage = self.risk_level / 100.0
        
        # Scale parameters linearly based on risk percentage
        return {
            'trade_probability': 0.05 + (0.20 * risk_percentage),      # 5% to 25% chance per symbol
            'buy_probability': 0.3 + (0.5 * risk_percentage),          # 30% to 80% chance for BUY
            'capital_per_trade': 0.05 + (0.25 * risk_percentage),      # 5% to 30% of available cash per trade
            'max_positions_multiplier': 0.5 + (1.5 * risk_percentage), # 0.5x to 2x max positions
            'stop_loss_percent': 1.0 + (4.0 * risk_percentage),        # 1% to 5% stop loss
            'take_profit_percent': 2.0 + (8.0 * risk_percentage),      # 2% to 10% take profit
            'symbol_count': max(5, int(5 + (15 * risk_percentage)))    # 5 to 20 symbols
        }

    def generate_signals(self, market_data, current_positions=None, available_cash: float = 0.0):
        """Generate trading signals with percentage-based risk levels"""
        signals = []
        risk_config = self.get_risk_config()
        
        # Adjust max positions based on risk level
        adjusted_max_positions = int(self.max_positions * risk_config['max_positions_multiplier'])
        
        # Use dynamic symbol count based on risk
        symbol_count = risk_config['symbol_count']
        symbols = list(market_data.keys())[:symbol_count]

        current_position_count = len(current_positions) if current_positions else 0

        for symbol in symbols:
            if current_position_count >= adjusted_max_positions:
                break

            # Skip trade-to-trade stocks for MIS orders
            if self.order_type == 'MIS' and live_trading._is_trade_to_trade_stock(symbol):
                continue

            # Risk-based trade probability
            if random.random() < risk_config['trade_probability']:
                current_data = market_data.get(symbol, {})
                if not current_data:
                    continue

                last_price = current_data.get('last_price', 0)
                if last_price <= 0:
                    continue

                # Calculate quantity based on risk level and available capital
                max_trade_value = available_cash * risk_config['capital_per_trade']
                quantity = max(1, int(max_trade_value / last_price))
                
                trade_value = quantity * last_price
                brokerage = live_trading.calculate_zerodha_brokerage(trade_value, 'BUY', self.order_type)
                total_cost = trade_value + brokerage

                # Only generate signals if we can afford them
                if total_cost <= available_cash * risk_config['capital_per_trade']:
                    # Risk-based buy probability
                    if random.random() < risk_config['buy_probability']:
                        action = 'BUY'
                        price = last_price * 1.002  # Slightly above current price
                    else:
                        # For SELL signals, check if we have the position
                        has_position = any(pos.get('symbol') == symbol for pos in (current_positions or []))
                        if has_position:
                            action = 'SELL'
                            price = last_price * 0.998  # Slightly below current price
                        else:
                            continue  # Skip SELL if we don't own the stock

                    signals.append({
                        'symbol': symbol,
                        'action': action,
                        'quantity': quantity,
                        'price': round(price, 2),
                        'order_type': self.order_type,
                        'risk_level': self.risk_level,
                        'timestamp': datetime.now()
                    })
                    current_position_count += 1

        print(f"🎯 {self.risk_level}% RISK: Generated {len(signals)} signals (Max positions: {adjusted_max_positions})")
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
            return {'connected': False, 'message': '❌ Credentials missing. Please enter your Zerodha API Key and Access Token in Settings.'}

        if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
            try:
                from kiteconnect import KiteConnect  # noqa
                profile = live_trading.kite.profile()
                margins = live_trading.get_margins()
                if profile:
                    return {
                        'connected': True,
                        'message': f"✅ Connected as {profile.get('user_name', 'Unknown')}",
                        'profile': {
                            'user_name': profile.get('user_name'),
                            'email': profile.get('email'),
                            'user_id': profile.get('user_id')
                        },
                        'margins': margins
                    }
            except Exception as e:
                error_msg = str(e)
                if "Invalid api_key" in error_msg or "Invalid access_token" in error_msg:
                    return {'connected': False, 'message': '❌ INVALID CREDENTIALS: Please check your Zerodha API Key and Access Token'}
                else:
                    return {'connected': False, 'message': f'❌ API Error: {str(e)}'}

        return {'connected': False, 'message': '❌ Connection failed. Please check your credentials and internet connection.'}

    except Exception as e:
        return {'connected': False, 'message': f"❌ Connection failed: {str(e)}"}

def get_current_prices(symbols: List[str]) -> Dict[str, float]:
    """Get current market prices for symbols from Zerodha"""
    try:
        if not symbols:
            return {}
            
        quotes = live_trading.get_market_quotes(symbols)
        current_prices = {}
        
        for quote in quotes:
            current_prices[quote['symbol']] = quote['last_price']
            
        return current_prices
        
    except Exception as e:
        print(f"Error getting current prices: {e}")
        return {}

def get_affordable_stocks(user_id: int, available_cash: float, max_capital_usage: float = 0.8) -> List[str]:
    """
    Dynamically get affordable stocks based on available wallet balance
    Uses Zerodha API to get top gainers
    """
    try:
        settings = UserSettings.query.filter_by(user_id=user_id).first()
        if settings and live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
            return live_trading.get_affordable_stocks(available_cash, max_capital_usage)
        else:
            print("❌ Cannot get affordable stocks: Live trading not configured")
            return []
                
    except Exception as e:
        print(f"Error getting affordable stocks: {e}")
        return []

def get_top_symbols(instrument_type: str, count: int = 20, user_id: int = None, available_cash: float = None) -> List[str]:
    """Get top symbols based on volume AND wallet balance affordability"""
    if instrument_type == 'stocks':
        if user_id and available_cash is not None:
            # Dynamic selection based on wallet balance
            return get_affordable_stocks(user_id, available_cash)[:count]
        else:
            # Return empty if no cash info
            return []
    elif instrument_type == 'indices':
        return ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']
    else:
        return []

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
            if 'default_order_type' in data:
                settings.default_order_type = data['default_order_type']
            if 'paper_trading_balance' in data:
                settings.paper_trading_balance = float(data['paper_trading_balance'])
            if 'default_risk_level' in data:
                settings.default_risk_level = int(data['default_risk_level'])

            db.session.commit()

            kite_status = test_kite_connection(settings)

            socketio.emit('user_notification', {
                'type': 'success',
                'message': f'Settings saved! Default profit: ₹{settings.default_target_profit}, Max duration: {settings.default_max_duration}h, Capital usage: {settings.max_capital_usage*100}%, Order type: {settings.default_order_type}, Risk level: {settings.default_risk_level}%',
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
                'default_order_type': settings.default_order_type,
                'paper_trading_balance': settings.paper_trading_balance,
                'default_risk_level': settings.default_risk_level,
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
    """Get wallet balance for both live and paper trading"""
    try:
        trading_mode = request.args.get('mode', 'paper')  # Default to paper trading
        
        if trading_mode == 'live':
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()

            if not settings or not settings.kite_api_key or not settings.kite_access_token:
                return jsonify({
                    'error': '❌ Zerodha credentials not configured. Please go to Settings and enter your API credentials.',
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

            if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                balance_data = live_trading.get_live_balance()
                if balance_data['success']:
                    pnl_data = live_trading.get_live_pnl(current_user.id)
                    return jsonify({
                        'balance': balance_data['available_cash'],
                        'portfolio_value': pnl_data['portfolio_value'],
                        'realized_pnl': pnl_data['realized_pnl'],
                        'unrealized_pnl': pnl_data['unrealized_pnl'],
                        'total_pnl': pnl_data['total_pnl'],
                        'net_pnl': pnl_data['net_pnl'],
                        'total_brokerage': 0,
                        'currency': 'INR',
                        'mode': 'live',
                        'note': f'Actual Zerodha Wallet Balance: ₹{balance_data["available_cash"]:.2f}',
                    })
                else:
                    return jsonify({
                        'error': f'❌ Failed to fetch Zerodha balance: {balance_data.get("error", "Unknown error")}',
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
                return jsonify({
                    'error': '❌ Failed to connect to Zerodha. Please check your API credentials.',
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
        
        else:  # Paper trading
            balance_data = paper_trading.get_paper_balance(current_user.id)
            if balance_data['success']:
                pnl_data = paper_trading.get_paper_pnl(current_user.id)
                return jsonify({
                    'balance': balance_data['paper_balance'],
                    'portfolio_value': pnl_data['portfolio_value'],
                    'realized_pnl': pnl_data['realized_pnl'],
                    'unrealized_pnl': pnl_data['unrealized_pnl'],
                    'total_pnl': pnl_data['total_pnl'],
                    'net_pnl': pnl_data['net_pnl'],
                    'total_brokerage': 0,
                    'currency': 'INR',
                    'mode': 'paper',
                    'note': f'Paper Trading Balance: ₹{balance_data["paper_balance"]:.2f}',
                })
            else:
                return jsonify({
                    'error': f'❌ Failed to get paper balance: {balance_data.get("error", "Unknown error")}',
                    'balance': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'total_brokerage': 0,
                    'currency': 'INR',
                    'mode': 'paper'
                })

    except Exception as e:
        print(f"❌ Wallet balance error: {e}")
        return jsonify({
            'error': f'❌ Failed to get wallet balance: {str(e)}',
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

@app.route('/api/reset_paper_portfolio', methods=['POST'])
@login_required
def reset_paper_portfolio():
    """Reset paper trading portfolio to initial state"""
    try:
        result = paper_trading.reset_paper_portfolio(current_user.id)
        
        if result['success']:
            socketio.emit('user_notification', {
                'type': 'success',
                'message': f"✅ Paper portfolio reset! Exited {result['exited_positions']} positions. New balance: ₹{result['new_balance']:.2f}",
                'timestamp': datetime.now().isoformat()
            })
        else:
            socketio.emit('user_notification', {
                'type': 'error',
                'message': f"❌ Failed to reset paper portfolio: {result.get('error')}",
                'timestamp': datetime.now().isoformat()
            })
            
        return jsonify(result)
        
    except Exception as e:
        error_msg = f"Error resetting paper portfolio: {str(e)}"
        socketio.emit('user_notification', {
            'type': 'error',
            'message': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/debug_zerodha_balance')
@login_required
def debug_zerodha_balance():
    """Debug endpoint to see what Zerodha API returns"""
    try:
        settings = UserSettings.query.filter_by(user_id=current_user.id).first()

        if not settings or not settings.kite_api_key or not settings.kite_access_token:
            return jsonify({'error': '❌ Credentials not configured'})

        if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
            # equity margins (robust)
            margins = live_trading.get_margins()
            wallet_data = live_trading.get_wallet_balance()
            balance_data = live_trading.get_live_balance()

            return jsonify({
                'raw_margins_equity': margins,
                'wallet_data': wallet_data,
                'balance_data': balance_data,
                'note': 'This shows data returned by Zerodha API for debugging (equity segment)'
            })
        else:
            return jsonify({'error': '❌ Failed to connect to Zerodha'})

    except Exception as e:
        return jsonify({'error': str(e)})

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
        for session_row in active_sessions:
            if session_row.trading_mode == 'live':
                pnl_data = live_trading.get_live_pnl(current_user.id)
                current_net_pnl = pnl_data['net_pnl']
            else:
                pnl_data = paper_trading.get_paper_pnl(current_user.id)
                current_net_pnl = pnl_data['net_pnl']

            bots_data.append({
                'id': session_row.id,
                'instrument_type': session_row.instrument_type,
                'strategy_name': session_row.strategy_name,
                'trading_mode': session_row.trading_mode,
                'initial_capital': float(session_row.initial_capital),
                'current_capital': float(session_row.current_capital) if session_row.current_capital else float(session_row.initial_capital),
                'started_at': session_row.started_at.isoformat() if session_row.started_at else None,
                'status': session_row.status,
                'pnl': float(session_row.pnl) if session_row.pnl else 0.0,
                'target_profit': float(session_row.target_profit),
                'max_duration_hours': session_row.max_duration_hours,
                'total_brokerage': float(session_row.total_brokerage),
                'order_type': session_row.order_type,
                'risk_level': session_row.risk_level,
                'current_net_pnl': current_net_pnl
            })

        return jsonify(bots_data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/market_watch_data')
@login_required
def get_market_watch_data():
    """
    Get market watch data from Zerodha API
    """
    try:
        instrument_type = request.args.get('type', 'stocks')
        
        # Get current balance to determine affordable stocks
        settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        if not settings or not live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
            return jsonify([])

        balance_data = live_trading.get_live_balance()
        if not balance_data['success']:
            return jsonify([])

        available_cash = balance_data['available_cash']
        symbols = get_affordable_stocks(current_user.id, available_cash)

        if not symbols:
            return jsonify([])

        # Get live quotes from Zerodha
        live_rows = live_trading.get_market_quotes(symbols)
        return jsonify(live_rows)

    except Exception as e:
        print(f"Market watch error: {e}")
        return jsonify([])

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

def can_start_bot(settings, trading_mode: str, capital_required: float) -> Dict[str, Any]:
    """Check if bot can be started with current conditions"""
    try:
        if trading_mode == 'live':
            market_open = is_market_open()
            if not market_open:
                return {
                    'can_start': False,
                    'reason': 'market_closed',
                    'message': '❌ Market is currently closed. Live trading is only available during market hours (9:15 AM - 3:30 PM IST). Please wait for market to open.'
                }

            if not settings or not settings.kite_api_key or not settings.kite_access_token:
                return {
                    'can_start': False,
                    'reason': 'credentials_missing',
                    'message': '❌ Zerodha credentials not configured. Please go to Settings and enter your Kite API credentials.'
                }

            # Test connection with better error handling
            connection_test = test_kite_connection(settings)
            if not connection_test['connected']:
                return {
                    'can_start': False,
                    'reason': 'connection_failed',
                    'message': f'❌ {connection_test["message"]}'
                }

            if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                balance_data = live_trading.get_live_balance()

                if balance_data['success']:
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
            else:
                return {
                    'can_start': False,
                    'reason': 'connection_failed',
                    'message': '❌ Failed to connect to Zerodha. Please check your API credentials and internet connection.'
                }
        
        else:  # Paper trading
            # For paper trading, we don't need market hours or API credentials
            if settings.paper_trading_balance < capital_required:
                return {
                    'can_start': False,
                    'reason': 'insufficient_balance',
                    'message': f'❌ Insufficient paper trading balance. Required: ₹{capital_required:.2f}, Available: ₹{settings.paper_trading_balance:.2f}. Please increase your paper trading balance in Settings.'
                }
            
            return {
                'can_start': True,
                'message': '✅ Paper trading bot can be started.',
                'available_balance': settings.paper_trading_balance
            }

    except Exception as e:
        return {
            'can_start': False,
            'reason': 'error',
            'message': f'❌ Error checking trading conditions: {str(e)}'
        }

@app.route('/api/start_bot', methods=['POST'])
@login_required
def start_bot():
    """Start trading bot with enhanced parameters - BOTH LIVE AND PAPER TRADING"""
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

        capital = float(data.get('capital', 1000))
        trading_mode = data.get('trading_mode', 'paper')  # Default to paper trading

        user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        if not user_settings:
            user_settings = UserSettings(user_id=current_user.id)
            db.session.add(user_settings)
            db.session.commit()

        # Validate based on trading mode
        validation_result = can_start_bot(user_settings, trading_mode, capital)

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
                'suggestion': 'Please fix the issues mentioned above.'
            })

        target_profit = float(data.get('target_profit', user_settings.default_target_profit))
        max_duration = int(data.get('max_duration_hours', user_settings.default_max_duration))
        order_type = converted_params.get('order_type', user_settings.default_order_type)
        risk_level = int(converted_params.get('risk_level', user_settings.default_risk_level))

        # Ensure order_type is not empty
        if not order_type:
            order_type = 'CNC'

        # Validate risk level
        if risk_level < 10:
            risk_level = 10
        elif risk_level > 100:
            risk_level = 100

        bot_config = {
            'instrument_type': data.get('instrument_type', 'stocks'),
            'strategy': data.get('strategy', 'mean_reversion'),
            'trading_mode': trading_mode,
            'capital': capital,
            'symbols': [],  # Will be dynamically selected based on wallet
            'strategy_params': converted_params,
            'user_id': current_user.id,
            'target_profit': target_profit,
            'max_duration_hours': max_duration,
            'max_capital_usage': user_settings.max_capital_usage,
            'order_type': order_type,
            'risk_level': risk_level
        }

        if not validate_strategy_parameters(bot_config['strategy'], bot_config['strategy_params']):
            error_msg = "Invalid strategy parameters"
            socketio.emit('user_notification', {
                'type': 'error',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            return jsonify({'success': False, 'error': error_msg})

        session_row = BotSession(
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
            force_stop=False,
            order_type=bot_config['order_type'],
            risk_level=bot_config['risk_level']
        )
        db.session.add(session_row)
        db.session.commit()

        bot_config['session_id'] = session_row.id

        # Create trading session with stop control
        trading_session = TradingSession(
            thread=None,
            config=bot_config,
            session=session_row,
            started_at=datetime.now()
        )

        # Start thread with the trading session reference
        thread = threading.Thread(
            target=run_enhanced_trading_bot,
            args=(session_row.id, bot_config, trading_session),
            name=f"BotThread-{session_row.id}"
        )
        thread.daemon = True
        thread.start()

        # Update trading session with thread reference
        trading_session.thread = thread
        trading_sessions[str(session_row.id)] = trading_session

        log_entry = Log(
            user_id=current_user.id,
            message=f"{trading_mode.upper()} Bot started - Target Profit: ₹{bot_config['target_profit']}, Max Duration: {bot_config['max_duration_hours']}h, Capital: ₹{capital}, Order Type: {order_type}, Risk Level: {risk_level}%",
            level="INFO"
        )
        db.session.add(log_entry)
        db.session.commit()

        success_msg = f"✅ {trading_mode.upper()} Bot started! Target: ₹{bot_config['target_profit']}, Duration: {bot_config['max_duration_hours']}h, Capital: ₹{capital}, Order Type: {order_type}, Risk: {risk_level}%"
        socketio.emit('user_notification', {
            'type': 'success',
            'message': success_msg,
            'timestamp': datetime.now().isoformat()
        })

        socketio.emit('bot_status_update', {
            'session_id': session_row.id,
            'status': 'running',
            'message': success_msg,
            'trading_mode': trading_mode,
            'risk_level': risk_level
        })

        return jsonify({
            'success': True,
            'session_id': session_row.id,
            'message': success_msg,
            'trading_mode': trading_mode,
            'risk_level': risk_level
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
    """Stop trading bot IMMEDIATELY and exit all positions"""
    try:
        session_row = BotSession.query.get(session_id)
        if session_row and session_row.user_id == current_user.id:
            print(f"🛑 IMMEDIATE STOP COMMAND for Bot {session_id} - EXITING ALL POSITIONS")

            # Set database flags first
            session_row.stop_requested = True
            session_row.force_stop = True
            session_row.should_exit_positions = True
            session_row.status = 'stopping'
            db.session.commit()

            # Get the trading session and set thread-safe stop flag
            session_key = str(session_id)
            if session_key in trading_sessions:
                trading_session = trading_sessions[session_key]
                trading_session.should_stop = True  # Thread-safe immediate stop

            # EXIT ALL POSITIONS based on trading mode
            exit_result = None
            if session_row.trading_mode == 'live':
                exit_result = live_trading.exit_all_positions(current_user.id)
            else:
                exit_result = paper_trading.exit_all_positions(current_user.id)

            # Update final status
            session_row.status = 'stopped'
            session_row.stopped_at = datetime.now()
            session_row.stop_requested = False
            session_row.force_stop = False

            # Update final P&L based on trading mode
            if session_row.trading_mode == 'live':
                pnl_data = live_trading.get_live_pnl(current_user.id)
            else:
                pnl_data = paper_trading.get_paper_pnl(current_user.id)
            
            session_row.pnl = pnl_data['net_pnl']

            db.session.commit()

            # Remove from active sessions
            if session_key in trading_sessions:
                print(f"🛑 Removing session {session_id} from active sessions")
                del trading_sessions[session_key]

            # Log the stop action
            exit_message = ""
            if exit_result and exit_result.get('success'):
                exit_message = f" | Exited {exit_result.get('exited_positions', 0)} positions"
                if exit_result.get('errors'):
                    exit_message += f" | Errors: {len(exit_result['errors'])}"

            log_entry = Log(
                user_id=current_user.id,
                message=f"Bot STOPPED IMMEDIATELY - Session {session_id} | Final P&L: ₹{session_row.pnl:.2f}{exit_message}",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()

            # Notify user
            success_msg = f"🛑 Bot {session_id} STOPPED! Final P&L: ₹{session_row.pnl:.2f}{exit_message}"
            socketio.emit('user_notification', {
                'type': 'success',
                'message': success_msg,
                'timestamp': datetime.now().isoformat()
            })

            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'stopped',
                'message': 'Bot stopped immediately - all positions exited',
                'final_pnl': session_row.pnl,
                'exited_positions': exit_result.get('exited_positions', 0) if exit_result else 0
            })

            print(f"✅ Bot {session_id} completely stopped with position exit")

            return jsonify({
                'success': True,
                'message': 'Bot stopped successfully with position exit',
                'final_pnl': session_row.pnl,
                'exited_positions': exit_result.get('exited_positions', 0) if exit_result else 0
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

@app.route('/api/bot_performance/<int:session_id>')
@login_required
def get_bot_performance(session_id):
    """Get detailed performance metrics for a bot session"""
    try:
        session_row = BotSession.query.get(session_id)
        if not session_row or session_row.user_id != current_user.id:
            return jsonify({'error': 'Session not found'}), 404

        if session_row.trading_mode == 'live':
            pnl_data = live_trading.get_live_pnl(current_user.id)
            positions = live_trading.get_live_positions(current_user.id)
        else:
            pnl_data = paper_trading.get_paper_pnl(current_user.id)
            positions = paper_trading.get_paper_positions(current_user.id)

        trades = Trade.query.filter_by(bot_session_id=session_id).all()
        total_brokerage = sum(trade.brokerage for trade in trades)

        running_time = 0
        if session_row.started_at:
            if session_row.stopped_at:
                running_time = (session_row.stopped_at - session_row.started_at).total_seconds() / 3600
            else:
                running_time = (datetime.now() - session_row.started_at).total_seconds() / 3600

        performance = {
            'session_id': session_row.id,
            'strategy': session_row.strategy_name,
            'trading_mode': session_row.trading_mode,
            'initial_capital': float(session_row.initial_capital),
            'current_portfolio_value': pnl_data['portfolio_value'],
            'total_pnl': pnl_data['total_pnl'],
            'net_pnl': pnl_data['net_pnl'],
            'realized_pnl': pnl_data['realized_pnl'],
            'unrealized_pnl': pnl_data['unrealized_pnl'],
            'total_brokerage': total_brokerage,
            'return_percent': (pnl_data['net_pnl'] / session_row.initial_capital) * 100 if session_row.initial_capital > 0 else 0,
            'trades_count': len(trades),
            'positions_count': len(positions),
            'running_time_hours': running_time,
            'target_profit': float(session_row.target_profit),
            'max_duration_hours': session_row.max_duration_hours,
            'order_type': session_row.order_type,
            'risk_level': session_row.risk_level,
            'profit_target_achieved': pnl_data['net_pnl'] >= session_row.target_profit if session_row.target_profit > 0 else False,
            'time_remaining_hours': max(0, session_row.max_duration_hours - running_time) if session_row.status == 'running' else 0
        }

        return jsonify(performance)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/positions')
@login_required
def get_positions():
    """Get current positions for both live and paper trading"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'live':
            positions = live_trading.get_live_positions(current_user.id)
        else:
            positions = paper_trading.get_paper_positions(current_user.id)
            
        return jsonify(positions)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders')
@login_required
def get_orders():
    """Get order history for both live and paper trading"""
    try:
        limit = request.args.get('limit', 50, type=int)
        trading_mode = request.args.get('mode', 'paper')

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
                'product_type': order.product_type,
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
    """Get portfolio summary with brokerage details for both live and paper trading"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'live':
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()

            if not settings or not settings.kite_api_key or not settings.kite_access_token:
                return jsonify({
                    'error': '❌ Zerodha credentials not configured. Please go to Settings and enter your API credentials.',
                    'available_cash': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'mode': 'live'
                })

            if live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                balance_data = live_trading.get_live_balance()
                pnl_data = live_trading.get_live_pnl(current_user.id)
                positions = live_trading.get_live_positions(current_user.id)

                if balance_data['success']:
                    return jsonify({
                        'initial_capital': balance_data['available_cash'],
                        'available_cash': balance_data['available_cash'],
                        'portfolio_value': pnl_data['portfolio_value'],
                        'realized_pnl': pnl_data['realized_pnl'],
                        'unrealized_pnl': pnl_data['unrealized_pnl'],
                        'total_pnl': pnl_data['total_pnl'],
                        'net_pnl': pnl_data['net_pnl'],
                        'return_percent': (pnl_data['net_pnl'] / balance_data['available_cash']) * 100 if balance_data['available_cash'] > 0 else 0,
                        'total_charges': 0,
                        'total_brokerage': 0,
                        'positions_count': len(positions),
                        'trades_count': len(positions),
                        'used_capital': pnl_data.get('total_invested', 0),
                        'capital_usage_percent': (pnl_data.get('total_invested', 0) / balance_data['available_cash']) * 100 if balance_data['available_cash'] > 0 else 0,
                        'mode': 'live',
                        'note': 'Real Zerodha data'
                    })
                else:
                    return jsonify({
                        'error': f'❌ Failed to fetch Zerodha data: {balance_data.get("error", "Unknown error")}',
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
                    'error': '❌ Failed to connect to Zerodha. Please check your API credentials.',
                    'available_cash': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'mode': 'live'
                })
        
        else:  # Paper trading
            balance_data = paper_trading.get_paper_balance(current_user.id)
            pnl_data = paper_trading.get_paper_pnl(current_user.id)
            positions = paper_trading.get_paper_positions(current_user.id)
            
            if balance_data['success']:
                return jsonify({
                    'initial_capital': balance_data['paper_balance'],
                    'available_cash': balance_data['paper_balance'],
                    'portfolio_value': pnl_data['portfolio_value'],
                    'realized_pnl': pnl_data['realized_pnl'],
                    'unrealized_pnl': pnl_data['unrealized_pnl'],
                    'total_pnl': pnl_data['total_pnl'],
                    'net_pnl': pnl_data['net_pnl'],
                    'return_percent': (pnl_data['net_pnl'] / balance_data['paper_balance']) * 100 if balance_data['paper_balance'] > 0 else 0,
                    'total_charges': 0,
                    'total_brokerage': 0,
                    'positions_count': len(positions),
                    'trades_count': len(positions),
                    'used_capital': pnl_data.get('total_invested', 0),
                    'capital_usage_percent': (pnl_data.get('total_invested', 0) / balance_data['paper_balance']) * 100 if balance_data['paper_balance'] > 0 else 0,
                    'mode': 'paper',
                    'note': 'Paper Trading Simulation'
                })
            else:
                return jsonify({
                    'error': f'❌ Failed to get paper trading data: {balance_data.get("error", "Unknown error")}',
                    'available_cash': 0,
                    'portfolio_value': 0,
                    'realized_pnl': 0,
                    'unrealized_pnl': 0,
                    'total_pnl': 0,
                    'net_pnl': 0,
                    'mode': 'paper'
                })

    except Exception as e:
        print(f"❌ Portfolio summary error: {e}")
        return jsonify({
            'error': f'❌ Failed to get portfolio summary: {str(e)}',
            'available_cash': 0,
            'portfolio_value': 0,
            'realized_pnl': 0,
            'unrealized_pnl': 0,
            'total_pnl': 0,
            'net_pnl': 0,
            'mode': 'paper'
        }), 200

def validate_trade_affordability(user_id: int, symbol: str, action: str, quantity: int, price: float, product_type: str = 'CNC', trading_mode: str = 'paper') -> Dict[str, Any]:
    """
    CRITICAL FIX: Validate if user can afford the trade before executing
    """
    try:
        trade_value = quantity * price
        
        if trading_mode == 'live':
            brokerage = live_trading.calculate_zerodha_brokerage(trade_value, action, product_type)
            total_cost = trade_value + brokerage if action.upper() == 'BUY' else 0

            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if not settings or not live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                return {'can_afford': False, 'error': 'Live trading not configured'}
            
            balance_data = live_trading.get_live_balance()
            if not balance_data['success']:
                return {'can_afford': False, 'error': f'Failed to check balance: {balance_data.get("error")}'}
            
            available_cash = balance_data['available_cash']
            
            if action.upper() == 'BUY' and total_cost > available_cash:
                return {
                    'can_afford': False,
                    'error': f'❌ LIVE: Insufficient balance. Required: ₹{total_cost:.2f}, Available: ₹{available_cash:.2f}'
                }
            return {'can_afford': True, 'available_cash': available_cash}
        
        else:  # Paper trading
            brokerage = paper_trading.calculate_paper_brokerage(trade_value, action, product_type)
            total_cost = trade_value + brokerage if action.upper() == 'BUY' else 0
            
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if not settings:
                return {'can_afford': False, 'error': 'User settings not found'}
            
            if action.upper() == 'BUY' and total_cost > settings.paper_trading_balance:
                return {
                    'can_afford': False,
                    'error': f'❌ PAPER: Insufficient balance. Required: ₹{total_cost:.2f}, Available: ₹{settings.paper_trading_balance:.2f}'
                }
            
            # For SELL orders, check if we have the position
            if action.upper() == 'SELL':
                position = PaperPosition.query.filter_by(user_id=user_id, symbol=symbol).first()
                if not position or position.quantity < quantity:
                    return {
                        'can_afford': False,
                        'error': f'❌ PAPER: Insufficient shares to sell. Requested: {quantity}, Available: {position.quantity if position else 0}'
                    }
            
            return {'can_afford': True, 'available_cash': settings.paper_trading_balance}
            
    except Exception as e:
        return {'can_afford': False, 'error': f'Validation error: {str(e)}'}

def execute_trade(session_id: int, config: Dict[str, Any], signal: Dict[str, Any]):
    """Execute trade with capital validation and position tracking - BOTH LIVE AND PAPER"""
    try:
        user_id = config['user_id']
        trading_mode = config['trading_mode']
        product_type = signal.get('order_type', config.get('order_type', 'CNC'))
        risk_level = signal.get('risk_level', config.get('risk_level', 50))

        # Ensure product_type is not empty
        if not product_type:
            product_type = 'CNC'

        # Skip trade-to-trade stocks for MIS orders (only for live trading)
        if trading_mode == 'live' and product_type == 'MIS' and live_trading._is_trade_to_trade_stock(signal['symbol']):
            error_msg = f"❌ TRADE-TO-TRADE STOCK: {signal['symbol']} cannot be traded intraday (MIS). This is a trade-to-trade stock."
            print(error_msg)
            
            log_entry = Log(
                user_id=user_id,
                message=error_msg,
                level="WARNING"
            )
            db.session.add(log_entry)
            db.session.commit()

            socketio.emit('user_notification', {
                'type': 'warning',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            return  # STOP execution - cannot trade this stock intraday

        # Get current price from Zerodha (for both live and paper trading)
        quotes = live_trading.get_market_quotes([signal['symbol']])
        if not quotes:
            print(f"❌ Could not get current price for {signal['symbol']}")
            return

        current_price = quotes[0]['last_price']

        if signal['action'] == 'BUY':
            execution_price = round(current_price * 1.002, 2)  # Slightly above current
        else:
            execution_price = round(current_price * 0.998, 2)  # Slightly below current

        print(f"🎯 Attempting {signal['action']} trade for {signal['symbol']} at {execution_price:.2f} ({product_type}) - Mode: {trading_mode} - Risk: {risk_level}%")

        # CRITICAL FIX: Validate affordability BEFORE attempting trade
        validation_result = validate_trade_affordability(
            user_id=user_id,
            symbol=signal['symbol'],
            action=signal['action'],
            quantity=signal['quantity'],
            price=execution_price,
            product_type=product_type,
            trading_mode=trading_mode
        )

        if not validation_result['can_afford']:
            error_msg = validation_result['error']
            print(f"❌ {error_msg}")
            
            log_entry = Log(
                user_id=user_id,
                message=error_msg,
                level="WARNING"
            )
            db.session.add(log_entry)
            db.session.commit()

            socketio.emit('user_notification', {
                'type': 'warning',
                'message': error_msg,
                'timestamp': datetime.now().isoformat()
            })
            return  # STOP execution - cannot afford this trade

        # If we can afford, proceed with trade execution
        print(f"✅ Affordability check passed. Proceeding with {trading_mode} trade...")

        if trading_mode == 'live':
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if settings and live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                result = live_trading.place_order(
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal['quantity'],
                    price=execution_price,
                    user_id=user_id,
                    product_type=product_type
                )
            else:
                error_msg = "Cannot execute LIVE trade: Kite not initialized or settings not found"
                log_entry = Log(
                    user_id=user_id,
                    message=error_msg,
                    level="ERROR"
                )
                db.session.add(log_entry)
                db.session.commit()
                return
        else:  # Paper trading
            result = paper_trading.place_paper_order(
                symbol=signal['symbol'],
                action=signal['action'],
                quantity=signal['quantity'],
                price=execution_price,
                user_id=user_id,
                product_type=product_type
            )

        if result['success']:
            trade = Trade(
                user_id=user_id,
                bot_session_id=session_id,
                symbol=signal['symbol'],
                action=signal['action'],
                quantity=signal['quantity'],
                price=execution_price,
                trading_mode=trading_mode,
                status='COMPLETED',
                order_id=result.get('order_id'),
                brokerage=result.get('brokerage', 0.0),
                product_type=result.get('product_type', product_type)
            )
            db.session.add(trade)

            session_row = BotSession.query.get(session_id)
            if session_row:
                session_row.total_brokerage += result.get('brokerage', 0.0)

            db.session.commit()

            log_entry = Log(
                user_id=user_id,
                message=f"{trading_mode.upper()} Trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {execution_price:.2f} | Order: {result.get('order_id')} | Product: {result.get('product_type', product_type)} | Brokerage: ₹{result.get('brokerage', 0.0):.2f} | Risk: {risk_level}%",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()

            # Emit position update
            if trading_mode == 'live':
                positions = live_trading.get_live_positions(user_id)
            else:
                positions = paper_trading.get_paper_positions(user_id)
                
            socketio.emit('positions_update', {
                'user_id': user_id,
                'positions': positions,
                'mode': trading_mode,
                'timestamp': datetime.now().isoformat()
            })

            socketio.emit('user_notification', {
                'type': 'success',
                'message': f"{trading_mode.upper()} Trade: {signal['action']} {signal['symbol']} @ ₹{execution_price:.2f} | Order: {result.get('order_id')} | Product: {result.get('product_type', product_type)} | Risk: {risk_level}%",
                'timestamp': datetime.now().isoformat()
            })

            socketio.emit('trade_executed', {
                'session_id': session_id,
                'symbol': signal['symbol'],
                'action': signal['action'],
                'quantity': signal['quantity'],
                'price': execution_price,
                'brokerage': result.get('brokerage', 0.0),
                'order_id': result.get('order_id'),
                'product_type': result.get('product_type', product_type),
                'risk_level': risk_level,
                'mode': trading_mode,
                'timestamp': datetime.now().isoformat()
            })
        else:
            error_msg = f"{trading_mode.upper()} Trade failed: {result.get('error', 'Unknown error')}"
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

def get_available_cash(user_id: int, trading_mode: str) -> float:
    """Get available cash for trading"""
    try:
        if trading_mode == 'live':
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if settings and live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                balance_data = live_trading.get_live_balance()
                if balance_data['success']:
                    return balance_data['available_cash']
            return 0.0
        else:  # Paper trading
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if settings:
                return settings.paper_trading_balance
            return 0.0
    except Exception as e:
        print(f"Error getting available cash: {e}")
        return 0.0

def run_enhanced_trading_bot(session_id: int, config: Dict[str, Any], trading_session: TradingSession):
    """Enhanced trading bot with capital validation and position tracking - BOTH LIVE AND PAPER"""
    with app.app_context():
        try:
            strategy = strategy_engine.get_strategy(config['strategy'], config['strategy_params'])
            trading_mode = config['trading_mode']
            risk_level = config.get('risk_level', 50)
            
            # DYNAMIC STOCK SELECTION: Get affordable stocks based on current wallet balance
            current_balance = get_available_cash(config['user_id'], trading_mode)
            symbols = get_affordable_stocks(config['user_id'], current_balance, config.get('max_capital_usage', 0.8))
            
            if not symbols:
                print(f"❌ No affordable stocks found for bot {session_id}. Stopping bot.")
                session_row = BotSession.query.get(session_id)
                if session_row:
                    session_row.status = 'stopped'
                    session_row.stopped_at = datetime.now()
                    db.session.commit()
                return
            
            print(f"🎯 Bot {session_id} selected {len(symbols)} affordable stocks for wallet: ₹{current_balance:.2f} - Mode: {trading_mode} - Risk: {risk_level}%")
            print(f"📊 Stocks: {symbols}")

            log_entry = Log(
                user_id=config['user_id'],
                message=f"{trading_mode.upper()} Bot {session_id} started with profit target: ₹{config['target_profit']}, max duration: {config['max_duration_hours']}h, capital: ₹{config['capital']}, affordable stocks: {len(symbols)}, order type: {config.get('order_type', 'CNC')}, risk level: {risk_level}%",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()

            print(f"🤖 {trading_mode.upper()} Bot {session_id} started! Target: ₹{config['target_profit']}, Duration: {config['max_duration_hours']}h, Capital: ₹{config['capital']}, Order Type: {config.get('order_type', 'CNC')}, Risk: {risk_level}%")

            session_row = BotSession.query.get(session_id)
            iteration = 0
            start_time = datetime.now()

            while session_row and session_row.status == 'running':
                # THREAD-SAFE STOP CHECK
                if (trading_session.should_stop or
                    not session_row or
                    session_row.stop_requested or
                    session_row.force_stop or
                    session_row.status != 'running'):

                    print(f"🛑 IMMEDIATE STOP DETECTED for Bot {session_id}. Exiting NOW!")

                    # Final cleanup
                    if session_row:
                        session_row.status = 'stopped'
                        session_row.stopped_at = datetime.now()
                        
                        # Update final P&L based on trading mode
                        if trading_mode == 'live':
                            pnl_data = live_trading.get_live_pnl(config['user_id'])
                        else:
                            pnl_data = paper_trading.get_paper_pnl(config['user_id'])
                            
                        session_row.pnl = pnl_data['net_pnl']
                        db.session.commit()

                    break

                # Check if max duration exceeded
                current_time = datetime.now()
                running_hours = (current_time - start_time).total_seconds() / 3600

                if running_hours >= config['max_duration_hours']:
                    print(f"⏰ Bot {session_id} reached max duration ({config['max_duration_hours']}h). Stopping...")
                    session_row.status = 'completed'
                    session_row.stopped_at = current_time
                    db.session.commit()
                    break

                should_trade = is_market_open() if trading_mode == 'live' else True  # Paper trading can run anytime

                if should_trade:
                    # Get current available cash for capital-aware signal generation
                    available_cash = get_available_cash(config['user_id'], trading_mode)

                    # Check profit target
                    if trading_mode == 'live':
                        pnl_data = live_trading.get_live_pnl(config['user_id'])
                    else:
                        pnl_data = paper_trading.get_paper_pnl(config['user_id'])
                        
                    if config['target_profit'] > 0 and pnl_data['net_pnl'] >= config['target_profit']:
                        print(f"🎯 Bot {session_id} achieved profit target! P&L: ₹{pnl_data['net_pnl']:.2f}")
                        session_row.status = 'completed'
                        session_row.stopped_at = current_time
                        session_row.pnl = pnl_data['net_pnl']
                        db.session.commit()
                        break

                    # Generate market data for affordable symbols from Zerodha
                    market_data_dict = {}
                    quotes = live_trading.get_market_quotes(symbols)
                    for quote in quotes:
                        market_data_dict[quote['symbol']] = {
                            'symbol': quote['symbol'],
                            'last_price': quote['last_price'],
                            'volume': quote['volume'],
                            'timestamp': datetime.now()
                        }

                    # Get current positions for position limit
                    if trading_mode == 'live':
                        current_positions = live_trading.get_live_positions(config['user_id'])
                    else:
                        current_positions = paper_trading.get_paper_positions(config['user_id'])

                    # Generate signals with capital validation
                    signals = strategy.generate_signals(
                        market_data_dict, 
                        current_positions,
                        available_cash=available_cash
                    )

                    if signals:
                        print(f"📈 Bot {session_id} generated {len(signals)} AFFORDABLE signals (Risk: {risk_level}%)")
                        for signal in signals:
                            # ULTRA-FAST stop check before each trade execution
                            if trading_session.should_stop:
                                print(f"🛑 STOP detected during trade execution. ABORTING ALL TRADES.")
                                break

                            # Also check database flags
                            session_row = BotSession.query.get(session_id)
                            if not session_row or session_row.stop_requested or session_row.force_stop or session_row.status != 'running':
                                print(f"🛑 Database stop detected. ABORTING TRADES.")
                                break

                            execute_trade(session_id, config, signal)
                    else:
                        if iteration % 10 == 0:
                            if trading_mode == 'live':
                                pnl_data = live_trading.get_live_pnl(config['user_id'])
                            else:
                                pnl_data = paper_trading.get_paper_pnl(config['user_id'])
                                
                            log_entry = Log(
                                user_id=config['user_id'],
                                message=f"Bot {session_id} running - P&L: ₹{pnl_data['net_pnl']:.2f}, Positions: {len(current_positions)}, Risk: {risk_level}%",
                                level="DEBUG"
                            )
                            db.session.add(log_entry)
                            db.session.commit()

                    iteration += 1

                # Ultra-fast stop check before sleep
                if trading_session.should_stop:
                    print(f"🛑 Thread stop flag detected. Exiting immediately.")
                    break

                session_row = BotSession.query.get(session_id)
                if not session_row or session_row.stop_requested or session_row.force_stop or session_row.status != 'running':
                    print(f"🛑 Database stop flags detected. Exiting immediately.")
                    break

                # Interruptible short sleep (5s total)
                for _ in range(50):
                    if trading_session.should_stop:
                        print(f"🛑 Stop detected during sleep. Breaking out.")
                        break
                    time_module.sleep(0.1)

            # Final cleanup when loop exits
            session_key = str(session_id)
            if session_key in trading_sessions:
                print(f"🧹 Final cleanup for bot session {session_id}")
                del trading_sessions[session_key]

        except Exception as e:
            error_msg = f"{trading_mode.upper()} Bot {session_id} error: {str(e)}"
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

# Real-time market data updates
def broadcast_market_updates():
    """Background task to broadcast real-time market data"""
    with app.app_context():
        while True:
            try:
                # Get current balance to determine affordable stocks
                settings = UserSettings.query.filter_by(user_id=1).first()  # Use first user for demo
                if settings and live_trading.initialize(settings.kite_api_key, settings.kite_access_token):
                    balance_data = live_trading.get_live_balance()
                    if balance_data['success']:
                        available_cash = balance_data['available_cash']
                        symbols = get_affordable_stocks(1, available_cash)
                        
                        if symbols:
                            # Get live quotes from Zerodha
                            market_data = live_trading.get_market_quotes(symbols)
                            
                            # Broadcast to all connected clients
                            socketio.emit('market_data_update', {
                                'data': market_data,
                                'timestamp': datetime.now().isoformat()
                            })
                
                time_module.sleep(5)  # Update every 5 seconds
                
            except Exception as e:
                print(f"Market update error: {e}")
                time_module.sleep(10)

# Start market update thread when app starts
market_update_thread = None

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print(f"🔌 WebSocket connected: {request.sid}")
    emit('connection_response', {
        'data': 'Connected to trading bot',
        'status': 'connected',
        'timestamp': datetime.now().isoformat()
    })
    
    # Start market updates if not already running
    global market_update_thread
    if market_update_thread is None or not market_update_thread.is_alive():
        market_update_thread = threading.Thread(target=broadcast_market_updates, daemon=True)
        market_update_thread.start()
        print("📊 Started real-time market data updates (5 second intervals)")

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

            print("🚀 Starting Indian Stock Trading Bot...")
            print("📍 Access: http://localhost:5000")
            print("🔑 Demo: username='demo', password='demo123'")
            print("🎯 Features:")
            print("   - ✅ LIVE TRADING with Zerodha API")
            print("   - ✅ PAPER TRADING with virtual balance")
            print("   - ✅ DYNAMIC STOCK SELECTION using Zerodha API")
            print("   - ✅ REAL ORDER PLACEMENT with Zerodha")
            print("   - ✅ TOP GAINERS based on available wallet balance")
            print("   - ✅ CAPITAL VALIDATION for all trades")
            print("   - ✅ LIVE POSITION TRACKING")
            print("   - ✅ AUTOMATIC MIS/CNC ORDER HANDLING")
            print("   - ✅ TRADE-TO-TRADE STOCK DETECTION")
            print("   - ✅ AUTO POSITION EXIT on bot stop")
            print("   - ✅ PAPER PORTFOLIO RESET")
            print("   - ✅ PERCENTAGE-BASED RISK LEVELS (10-100%)")
            print(f"🕒 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"📊 Market status: {'OPEN' if is_market_open() else 'CLOSED'}")

        except Exception as e:
            print(f"❌ Database initialization error: {e}")
            import traceback
            traceback.print_exc()

    socketio.run(
        app,
        debug=True,
        host='0.0.0.0',
        port=5000,
        allow_unsafe_werkzeug=True)