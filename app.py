from flask import Flask, render_template, request, jsonify, session, redirect, url_for, current_app
from flask_socketio import SocketIO, emit
import sys
import os
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import logging
from datetime import datetime, time
import threading
import time as time_module
from typing import Dict, List, Any
import json
import pandas as pd

# Add current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import Config
from modules.auth import KiteAuth
from modules.market_data import MarketDataHandler
from modules.strategy_engine import StrategyEngine
from modules.order_manager import OrderManager
from modules.paper_trading import PaperTrading
from modules.database import db, User, BotSession, Trade, Log, UserSettings

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
socketio = SocketIO(app, async_mode=app.config['SOCKETIO_ASYNC_MODE'])

# Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Global instances
market_data = MarketDataHandler(socketio)
strategy_engine = StrategyEngine(market_data)
order_manager = OrderManager()
paper_trading = PaperTrading(app.config['PAPER_TRADING_INITIAL_CAPITAL'])
kite_auth = KiteAuth(app)

# Trading session state
trading_sessions: Dict[str, Any] = {}

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def is_market_open() -> bool:
    """Check if market is currently open (considering weekends and holidays)"""
    now = datetime.now()
    current_time = now.time()
    current_day = now.weekday()  # Monday=0, Sunday=6
    
    # Market is closed on weekends
    if current_day >= 5:  # Saturday or Sunday
        return False
    
    # Market hours (9:15 AM to 3:30 PM IST)
    market_open = time(9, 15)
    market_close = time(15, 30)
    
    # Check if current time is within market hours
    is_open = market_open <= current_time <= market_close
    
    return is_open

def get_market_status_message(is_open: bool, is_weekend: bool) -> str:
    """Get appropriate market status message"""
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
            
            return redirect(url_for('dashboard'))
        else:
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

# All main routes use the same dashboard.html template for SPA
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/market_watch')
@login_required
def market_watch():
    return render_template('dashboard.html')  # Same template, navigation handled by JS

@app.route('/positions')
@login_required
def positions():
    return render_template('dashboard.html')  # Same template, navigation handled by JS

@app.route('/orders')
@login_required
def orders():
    return render_template('dashboard.html')  # Same template, navigation handled by JS

@app.route('/logs')
@login_required
def logs():
    return render_template('dashboard.html')  # Same template, navigation handled by JS

@app.route('/settings')
@login_required
def settings():
    return render_template('dashboard.html')  # Same template, navigation handled by JS

@app.route('/api/market_status')
@login_required
def market_status():
    """Check if market is open with detailed information"""
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
        'message': get_market_status_message(is_open, is_weekend)
    }
    
    return jsonify(status_info)

@app.route('/api/user_settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    """Get or update user settings including Kite API credentials"""
    if request.method == 'POST':
        try:
            data = request.json
            settings = UserSettings.query.filter_by(user_id=current_user.id).first()
            
            if not settings:
                settings = UserSettings(user_id=current_user.id)
                db.session.add(settings)
            
            # Update Kite API credentials
            if 'kite_api_key' in data:
                settings.kite_api_key = data['kite_api_key']
            if 'kite_access_token' in data:
                settings.kite_access_token = data['kite_access_token']
            
            db.session.commit()
            
            # Initialize Kite with new credentials
            if settings.kite_api_key and settings.kite_access_token:
                kite_auth.init_kite(settings.kite_api_key)
                kite_auth.kite.set_access_token(settings.kite_access_token)
            
            return jsonify({'success': True, 'message': 'Settings updated successfully'})
        
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    
    else:
        # GET request - return current settings
        settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        if settings:
            return jsonify({
                'kite_api_key': settings.kite_api_key or '',
                'kite_access_token': settings.kite_access_token or ''
            })
        else:
            return jsonify({'kite_api_key': '', 'kite_access_token': ''})

@app.route('/api/wallet_balance')
@login_required
def wallet_balance():
    """Get wallet balance based on trading mode"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            portfolio = paper_trading.get_portfolio(current_user.id)
            return jsonify({
                'balance': portfolio['available_cash'],
                'portfolio_value': portfolio['available_cash'] + portfolio.get('positions_value', 0),
                'currency': 'INR',
                'mode': 'paper'
            })
        else:
            # Live trading - get balance from Kite
            if kite_auth.kite and kite_auth.is_authenticated():
                margins = kite_auth.kite.margins()
                equity_margins = margins['equity']
                return jsonify({
                    'balance': equity_margins['available']['cash'],
                    'portfolio_value': equity_margins['available']['cash'] + equity_margins['utilised']['value'],
                    'currency': 'INR',
                    'mode': 'live'
                })
            else:
                return jsonify({'error': 'Not connected to Kite'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/active_bots')
@login_required
def get_active_bots():
    """Get active trading bots for current user"""
    active_sessions = BotSession.query.filter_by(
        user_id=current_user.id, 
        status='running'
    ).all()
    
    return jsonify([{
        'id': session.id,
        'instrument_type': session.instrument_type,
        'strategy_name': session.strategy_name,
        'trading_mode': session.trading_mode,
        'initial_capital': session.initial_capital,
        'started_at': session.started_at.isoformat() if session.started_at else None
    } for session in active_sessions])

@app.route('/api/market_watch_data')
@login_required
def get_market_watch_data():
    """Get market watch data"""
    instrument_type = request.args.get('type', 'stocks')
    
    symbols = market_data.get_top_symbols(instrument_type)
    return jsonify(symbols)

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

@app.route('/api/start_bot', methods=['POST'])
@login_required
def start_bot():
    """Start trading bot with given parameters"""
    try:
        data = request.json
        
        bot_config = {
            'instrument_type': data.get('instrument_type', 'stocks'),
            'strategy': data.get('strategy', 'moving_average'),
            'trading_mode': data.get('trading_mode', 'paper'),
            'capital': float(data.get('capital', 100000)),
            'symbols': data.get('symbols', []),
            'strategy_params': data.get('strategy_params', {}),
            'user_id': current_user.id,
            'test_mode': True,  # Allow testing even when market is closed
            'demo_mode': True   # Generate demo signals
        }
        
        # Validate strategy parameters
        if not validate_strategy_parameters(bot_config['strategy'], bot_config['strategy_params']):
            return jsonify({'success': False, 'error': 'Invalid strategy parameters'})
        
        # Create bot session
        session = BotSession(
            user_id=current_user.id,
            instrument_type=bot_config['instrument_type'],
            strategy_name=bot_config['strategy'],
            trading_mode=bot_config['trading_mode'],
            initial_capital=bot_config['capital'],
            status='running'
        )
        db.session.add(session)
        db.session.commit()
        
        # Store bot configuration
        bot_config['session_id'] = session.id
        
        # Start bot in background thread
        thread = threading.Thread(
            target=run_trading_bot,
            args=(session.id, bot_config)
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
        
        socketio.emit('bot_status_update', {
            'session_id': session.id,
            'status': 'running',
            'message': 'Bot started successfully'
        })
        
        return jsonify({'success': True, 'session_id': session.id})
    
    except Exception as e:
        log_entry = Log(
            user_id=current_user.id,
            message=f"Failed to start bot: {str(e)}",
            level="ERROR"
        )
        db.session.add(log_entry)
        db.session.commit()
        
        return jsonify({'success': False, 'error': str(e)})

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
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'stopped',
                'message': 'Bot stopped successfully'
            })
            
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Session not found or access denied'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/positions')
@login_required
def get_positions():
    """Get current positions"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            positions = paper_trading.get_positions(current_user.id)
            
            # Add current market prices
            for position in positions:
                symbol = position['symbol']
                market_info = market_data.get_latest_data(symbol)
                if market_info:
                    position['current_price'] = market_info.get('last_price', position.get('average_price', 0))
                    position['unrealized_pnl'] = (position['current_price'] - position['average_price']) * position['quantity']
                else:
                    position['current_price'] = position.get('average_price', 0)
                    position['unrealized_pnl'] = 0
            
            return jsonify(positions)
        
        else:
            # Live positions from Kite
            if kite_auth.kite and kite_auth.is_authenticated():
                positions = kite_auth.kite.positions()
                return jsonify(positions)
            else:
                return jsonify({'error': 'Not connected to Kite'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders')
@login_required
def get_orders():
    """Get order history"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            limit = request.args.get('limit', 50, type=int)
            orders = Trade.query.filter_by(user_id=current_user.id).order_by(Trade.timestamp.desc()).limit(limit).all()
            return jsonify([order.to_dict() for order in orders])
        
        else:
            # Live orders from Kite
            if kite_auth.kite and kite_auth.is_authenticated():
                orders = kite_auth.kite.orders()
                return jsonify(orders)
            else:
                return jsonify({'error': 'Not connected to Kite'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs')
@login_required
def get_logs():
    """Get application logs"""
    try:
        limit = request.args.get('limit', 100, type=int)
        logs = Log.query.filter_by(user_id=current_user.id).order_by(Log.timestamp.desc()).limit(limit).all()
        return jsonify([log.to_dict() for log in logs])
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/portfolio_summary')
@login_required
def get_portfolio_summary():
    """Get portfolio summary with P&L"""
    try:
        trading_mode = request.args.get('mode', 'paper')
        
        if trading_mode == 'paper':
            positions = paper_trading.get_positions(current_user.id)
            portfolio = paper_trading.get_portfolio(current_user.id)
            
            current_prices = {}
            for symbol in [pos['symbol'] for pos in positions]:
                market_info = market_data.get_latest_data(symbol)
                if market_info:
                    current_prices[symbol] = market_info['last_price']
            
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
                'mode': 'paper'
            }
            
            return jsonify(summary)
        
        else:
            # Live portfolio from Kite
            if kite_auth.kite and kite_auth.is_authenticated():
                portfolio = kite_auth.kite.portfolio()
                margins = kite_auth.kite.margins()
                
                summary = {
                    'available_cash': margins['equity']['available']['cash'],
                    'portfolio_value': margins['equity']['available']['net'],
                    'realized_pnl': portfolio.get('realised_pnl', 0),
                    'unrealized_pnl': portfolio.get('unrealised_pnl', 0),
                    'total_pnl': portfolio.get('total_pnl', 0),
                    'mode': 'live'
                }
                
                return jsonify(summary)
            else:
                return jsonify({'error': 'Not connected to Kite'}), 400
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/kite_login')
@login_required
def kite_login():
    """Initiate Kite Connect login"""
    try:
        settings = UserSettings.query.filter_by(user_id=current_user.id).first()
        if settings and settings.kite_api_key:
            kite_auth.init_kite(settings.kite_api_key)
            login_url = kite_auth.get_login_url()
            return jsonify({
                'success': True, 
                'login_url': login_url,
                'message': 'Redirect to Kite login'
            })
        else:
            return jsonify({'success': False, 'error': 'Kite API key not configured'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/kite_callback')
@login_required
def kite_callback():
    """Handle Kite Connect callback"""
    try:
        request_token = request.args.get('request_token')
        if request_token:
            if kite_auth.set_access_token(request_token):
                # Save access token to user settings
                settings = UserSettings.query.filter_by(user_id=current_user.id).first()
                if not settings:
                    settings = UserSettings(user_id=current_user.id)
                    db.session.add(settings)
                
                settings.kite_access_token = session.get('kite_access_token')
                db.session.commit()
                
                return jsonify({
                    'success': True, 
                    'message': 'Kite Connect authentication successful'
                })
        
        return jsonify({'success': False, 'error': 'Authentication failed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/kite_profile')
@login_required
def kite_profile():
    """Get Kite user profile"""
    try:
        profile = kite_auth.get_user_profile()
        if profile:
            return jsonify({'success': True, 'profile': profile})
        else:
            return jsonify({'success': False, 'error': 'Not authenticated with Kite'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def validate_strategy_parameters(strategy_name: str, parameters: Dict[str, Any]) -> bool:
    """Validate strategy parameters"""
    try:
        strategies = strategy_engine.get_available_strategies()
        strategy_info = next((s for s in strategies if s['name'] == strategy_name), None)
        
        if not strategy_info:
            return False
        
        for param in strategy_info['parameters']:
            param_name = param['name']
            if param_name in parameters:
                value = parameters[param_name]
                if param['type'] == 'number':
                    if not isinstance(value, (int, float)):
                        return False
                    if 'min' in param and value < param['min']:
                        return False
                    if 'max' in param and value > param['max']:
                        return False
        
        return True
    
    except Exception:
        return False

def run_trading_bot(session_id: int, config: Dict[str, Any]):
    """Main trading bot execution function"""
    with app.app_context():
        try:
            strategy = strategy_engine.get_strategy(config['strategy'], config['strategy_params'])
            symbols = config['symbols'] or get_top_symbols(config['instrument_type'])
            
            market_data.subscribe(symbols)
            
            log_entry = Log(
                user_id=config['user_id'],
                message=f"Bot {session_id} started with {len(symbols)} symbols. Strategy: {config['strategy']}",
                level="INFO"
            )
            db.session.add(log_entry)
            db.session.commit()
            
            print(f"🤖 Bot {session_id} started! Monitoring {len(symbols)} symbols")
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'monitoring',
                'message': f'Monitoring {len(symbols)} symbols'
            })
            
            session = BotSession.query.get(session_id)
            iteration = 0
            
            while session and session.status == 'running':
                if is_market_open() or config.get('test_mode', False):
                    market_data_dict = market_data.get_latest_data()
                    
                    if market_data_dict:
                        signals = strategy.generate_signals(market_data_dict)
                        
                        if signals:
                            print(f"📈 Generated {len(signals)} signals")
                            for signal in signals:
                                execute_trade(session_id, config, signal)
                        else:
                            if iteration % 30 == 0:
                                log_entry = Log(
                                    user_id=config['user_id'],
                                    message=f"Bot {session_id} running - no signals generated",
                                    level="DEBUG"
                                )
                                db.session.add(log_entry)
                                db.session.commit()
                    
                    iteration += 1
                
                time_module.sleep(5)
                
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
            
            socketio.emit('bot_status_update', {
                'session_id': session_id,
                'status': 'error',
                'message': error_msg
            })

def execute_trade(session_id: int, config: Dict[str, Any], signal: Dict[str, Any]):
    """Execute a trade based on signal"""
    try:
        user_id = config['user_id']
        trading_mode = config['trading_mode']
        
        print(f"🎯 Executing {signal['action']} trade for {signal['symbol']} at {signal['price']}")
        
        if trading_mode == 'paper':
            result = paper_trading.execute_trade(
                user_id=user_id,
                symbol=signal['symbol'],
                action=signal['action'],
                quantity=signal['quantity'],
                price=signal['price']
            )
            
            if result['success']:
                log_entry = Log(
                    user_id=user_id,
                    message=f"Paper trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {signal['price']}",
                    level="INFO"
                )
                db.session.add(log_entry)
                
                print(f"✅ Paper trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {signal['price']}")
                
                socketio.emit('trade_executed', {
                    'session_id': session_id,
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'quantity': signal['quantity'],
                    'price': signal['price'],
                    'mode': 'paper',
                    'timestamp': datetime.now().isoformat()
                })
            else:
                log_entry = Log(
                    user_id=user_id,
                    message=f"Paper trade failed: {result.get('error', 'Unknown error')}",
                    level="ERROR"
                )
                db.session.add(log_entry)
                print(f"❌ Paper trade failed: {result.get('error', 'Unknown error')}")
        
        else:
            # Live trading with Kite
            settings = UserSettings.query.filter_by(user_id=user_id).first()
            if settings and settings.kite_api_key and settings.kite_access_token:
                kite_auth.init_kite(settings.kite_api_key)
                kite_auth.kite.set_access_token(settings.kite_access_token)
                
                result = order_manager.place_order(
                    symbol=signal['symbol'],
                    action=signal['action'],
                    quantity=signal['quantity'],
                    price=signal['price'],
                    user_id=user_id,
                    kite=kite_auth.kite
                )
                
                if result['success']:
                    log_entry = Log(
                        user_id=user_id,
                        message=f"Live trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {signal['price']}",
                        level="INFO"
                    )
                    db.session.add(log_entry)
                    
                    socketio.emit('trade_executed', {
                        'session_id': session_id,
                        'symbol': signal['symbol'],
                        'action': signal['action'],
                        'quantity': signal['quantity'],
                        'price': signal['price'],
                        'mode': 'live',
                        'order_id': result.get('order_id'),
                        'timestamp': datetime.now().isoformat()
                    })
                    
                    print(f"✅ Live trade executed: {signal['action']} {signal['quantity']} {signal['symbol']} @ {signal['price']}")
                else:
                    log_entry = Log(
                        user_id=user_id,
                        message=f"Live trade failed: {result.get('error', 'Unknown error')}",
                        level="ERROR"
                    )
                    db.session.add(log_entry)
                    print(f"❌ Live trade failed: {result.get('error', 'Unknown error')}")
            else:
                print("❌ Kite credentials not configured for live trading")
        
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

def get_top_symbols(instrument_type: str, count: int = 20) -> List[str]:
    """Get top symbols based on volume"""
    if instrument_type == 'stocks':
        return ['RELIANCE', 'TCS', 'HDFC', 'INFY', 'HINDUNILVR', 'SBIN', 
                'BHARTIARTL', 'ITC', 'KOTAKBANK', 'ICICIBANK', 'LT', 'AXISBANK',
                'ASIANPAINT', 'MARUTI', 'SUNPHARMA', 'TITAN', 'ULTRACEMCO',
                'WIPRO', 'NESTLEIND', 'HCLTECH']
    else:
        return ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY', 'HDFC', 'SBIN']

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    emit('connection_response', {'data': 'Connected to trading bot', 'status': 'connected'})

@socketio.on('subscribe_market_data')
def handle_subscribe_market_data(data):
    """Subscribe to market data updates"""
    symbols = data.get('symbols', [])
    if symbols:
        market_data.subscribe(symbols)
        emit('subscription_confirmed', {'symbols': symbols})

@socketio.on('request_market_data')
def handle_request_market_data(data):
    """Request current market data for symbols"""
    symbols = data.get('symbols', [])
    market_data_dict = market_data.get_latest_data()
    
    if symbols:
        filtered_data = {symbol: market_data_dict.get(symbol) for symbol in symbols if symbol in market_data_dict}
        emit('market_data_batch', filtered_data)
    else:
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
        db.create_all()
        
        if not User.query.filter_by(username='demo').first():
            demo_user = User(username='demo', email='demo@tradingbot.com')
            demo_user.set_password('demo123')
            db.session.add(demo_user)
            db.session.commit()
            print("Created demo user: username='demo', password='demo123'")
        
        print("🚀 Starting Indian Stock Trading Bot...")
        print("📍 Access: http://localhost:5000")
        print("🔑 Demo: username='demo', password='demo123'")
        print("🤖 Bots will run in TEST MODE")
    
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)