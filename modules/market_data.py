import threading
import time
from typing import Dict, List, Any
from flask_socketio import SocketIO
import random
from datetime import datetime

class MarketDataHandler:
    def __init__(self, socketio: SocketIO):
        self.socketio = socketio
        self.subscribed_symbols = set()
        self.market_data = {}
        self.is_running = False
        self.thread = None
        
    def subscribe(self, symbols: List[str]):
        """Subscribe to symbols for real-time data"""
        self.subscribed_symbols.update(symbols)
        
        # Start data thread if not running
        if not self.is_running:
            self.is_running = True
            self.thread = threading.Thread(target=self._data_feeder)
            self.thread.daemon = True
            self.thread.start()
    
    def unsubscribe(self, symbols: List[str]):
        """Unsubscribe from symbols"""
        self.subscribed_symbols.difference_update(symbols)
    
    def get_latest_data(self, symbol: str = None) -> Dict[str, Any]:
        """Get latest market data"""
        if symbol:
            return self.market_data.get(symbol)
        return self.market_data
    
    def _data_feeder(self):
        """Mock real-time data feeder (replace with Kite WebSocket)"""
        base_prices = {
            'RELIANCE': 2400, 'TCS': 3400, 'HDFC': 1600, 'INFY': 1500,
            'HINDUNILVR': 2500, 'SBIN': 600, 'BHARTIARTL': 800,
            'ITC': 230, 'KOTAKBANK': 1800, 'ICICIBANK': 950,
            'NIFTY': 19500, 'BANKNIFTY': 44000
        }
        
        while self.is_running and self.subscribed_symbols:
            for symbol in list(self.subscribed_symbols):
                base_price = base_prices.get(symbol, 1000)
                
                # Generate realistic price movement
                change_percent = random.uniform(-0.5, 0.5)
                current_price = base_price * (1 + change_percent / 100)
                volume = random.randint(1000, 100000)
                
                market_data = {
                    'symbol': symbol,
                    'last_price': round(current_price, 2),
                    'change': round(current_price - base_price, 2),
                    'change_percent': round(change_percent, 2),
                    'volume': volume,
                    'timestamp': datetime.now().isoformat(),
                    'open': base_price,
                    'high': round(base_price * 1.01, 2),
                    'low': round(base_price * 0.99, 2),
                    'close': base_price
                }
                
                self.market_data[symbol] = market_data
                
                # Emit via WebSocket
                self.socketio.emit('market_data_update', market_data)
            
            time.sleep(1)  # Update every second
    
    def get_top_symbols(self, instrument_type: str, count: int = 20) -> List[Dict[str, Any]]:
        """Get top symbols by volume (mock implementation)"""
        symbols = []
        base_volume = 1000000
        
        if instrument_type == 'stocks':
            stock_symbols = ['RELIANCE', 'TCS', 'HDFC', 'INFY', 'HINDUNILVR', 
                           'SBIN', 'BHARTIARTL', 'ITC', 'KOTAKBANK', 'ICICIBANK']
        else:
            stock_symbols = ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY']
        
        for i, symbol in enumerate(stock_symbols[:count]):
            symbols.append({
                'symbol': symbol,
                'last_price': self.market_data.get(symbol, {}).get('last_price', 1000),
                'change': random.uniform(-50, 50),
                'volume': base_volume - i * 50000,
                'instrument_type': 'EQ' if instrument_type == 'stocks' else 'FUT'
            })
        
        return sorted(symbols, key=lambda x: x['volume'], reverse=True)