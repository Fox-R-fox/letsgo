from typing import Dict, Any, Optional
import logging
from modules.database import db, Trade
from datetime import datetime

try:
    from kiteconnect import KiteConnect
    KITECONNECT_AVAILABLE = True
except ImportError:
    KITECONNECT_AVAILABLE = False
    print("KiteConnect not available - running in paper trading mode")

class OrderManager:
    def __init__(self):
        self.kite = None
        self.is_connected = False
        self.logger = logging.getLogger(__name__)
        
    def connect(self, api_key: str, access_token: str):
        """Connect to Kite Connect"""
        if not KITECONNECT_AVAILABLE:
            self.logger.warning("KiteConnect not available - running in paper trading mode")
            return
        
        try:
            self.kite = KiteConnect(api_key=api_key)
            self.kite.set_access_token(access_token)
            self.is_connected = True
            self.logger.info("Successfully connected to Kite Connect")
        except Exception as e:
            self.logger.error(f"Failed to connect to Kite Connect: {str(e)}")
            raise
    
    def place_order(self, 
                   symbol: str, 
                   action: str, 
                   quantity: int, 
                   product: str = 'MIS',
                   order_type: str = 'MARKET',
                   price: float = 0,
                   user_id: int = None,
                   kite: KiteConnect = None) -> Dict[str, Any]:
        """Place an order through Kite Connect or paper trading"""
        
        try:
            # Paper trading or Kite not available
            if not KITECONNECT_AVAILABLE or kite is None:
                trade = Trade(
                    user_id=user_id,
                    symbol=symbol,
                    action=action.upper(),
                    quantity=quantity,
                    price=price,
                    order_type=order_type,
                    product=product,
                    status='PAPER_TRADING',
                    pnl=0
                )
                db.session.add(trade)
                db.session.commit()
                
                return {
                    'success': True,
                    'order_id': f'PAPER_{datetime.now().timestamp()}',
                    'message': 'Paper trading order simulated'
                }
            
            # Live trading with Kite
            exchange = self._get_exchange(symbol)
            tradingsymbol = self._get_tradingsymbol(symbol, exchange)
            
            order_params = {
                'tradingsymbol': tradingsymbol,
                'exchange': exchange,
                'transaction_type': self._get_transaction_type(action),
                'quantity': quantity,
                'order_type': order_type,
                'product': product,
                'variety': 'regular'
            }
            
            if order_type in ['LIMIT', 'SL', 'SL-M']:
                order_params['price'] = price
            
            order_response = kite.place_order(**order_params)
            
            # Log trade in database
            trade = Trade(
                user_id=user_id,
                symbol=symbol,
                action=action.upper(),
                quantity=quantity,
                price=price or self._get_current_price(tradingsymbol, exchange, kite),
                order_type=order_type,
                product=product,
                status='LIVE_ORDER'
            )
            db.session.add(trade)
            db.session.commit()
            
            return {
                'success': True,
                'order_id': order_response['order_id'],
                'message': 'Order placed successfully'
            }
                
        except Exception as e:
            self.logger.error(f"Order placement failed: {str(e)}")
            
            if user_id:
                trade = Trade(
                    user_id=user_id,
                    symbol=symbol,
                    action=action,
                    quantity=quantity,
                    price=price,
                    order_type=order_type,
                    product=product,
                    status='FAILED',
                    pnl=0
                )
                db.session.add(trade)
                db.session.commit()
            
            return {
                'success': False,
                'error': str(e),
                'message': 'Order placement failed'
            }
    
    def _get_exchange(self, symbol: str) -> str:
        if symbol in ['NIFTY', 'BANKNIFTY']:
            return 'NFO'
        else:
            return 'NSE'
    
    def _get_tradingsymbol(self, symbol: str, exchange: str) -> str:
        return symbol
    
    def _get_transaction_type(self, action: str) -> str:
        return 'BUY' if action.upper() == 'BUY' else 'SELL'
    
    def _get_current_price(self, tradingsymbol: str, exchange: str, kite: KiteConnect) -> float:
        try:
            quote = kite.ltp(f"{exchange}:{tradingsymbol}")
            key = f"{exchange}:{tradingsymbol}"
            return quote[key]['last_price']
        except Exception as e:
            self.logger.error(f"Failed to get current price: {str(e)}")
            return 0.0