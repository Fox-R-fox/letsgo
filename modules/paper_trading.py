from typing import Dict, List, Any
from modules.database import db, Trade
from datetime import datetime
import threading

class PaperTrading:
    def __init__(self, initial_capital: float = 1000000):
        self.initial_capital = initial_capital
        self.user_portfolios = {}  # user_id -> portfolio_data
        self.lock = threading.Lock()
    
    def execute_trade(self, 
                     user_id: int, 
                     symbol: str, 
                     action: str, 
                     quantity: int, 
                     price: float) -> Dict[str, Any]:
        """Execute a paper trade"""
        
        with self.lock:
            portfolio = self._get_portfolio(user_id)
            
            # Calculate trade value
            trade_value = quantity * price
            brokerage = min(20, trade_value * 0.0003)  # Mock brokerage
            transaction_charges = trade_value * 0.0000325  # NSE charges
            gst = (brokerage + transaction_charges) * 0.18
            total_charges = brokerage + transaction_charges + gst
            
            if action.upper() == 'BUY':
                if portfolio['available_cash'] < (trade_value + total_charges):
                    return {
                        'success': False,
                        'error': 'Insufficient capital'
                    }
                
                # Update portfolio
                portfolio['available_cash'] -= (trade_value + total_charges)
                portfolio['total_charges'] += total_charges
                
                # Update position
                if symbol not in portfolio['positions']:
                    portfolio['positions'][symbol] = {
                        'quantity': 0,
                        'average_price': 0,
                        'invested_amount': 0
                    }
                
                position = portfolio['positions'][symbol]
                total_quantity = position['quantity'] + quantity
                total_investment = position['invested_amount'] + trade_value
                
                position['quantity'] = total_quantity
                position['average_price'] = total_investment / total_quantity
                position['invested_amount'] = total_investment
            
            else:  # SELL
                if symbol not in portfolio['positions']:
                    return {
                        'success': False,
                        'error': 'No position to sell'
                    }
                
                position = portfolio['positions'][symbol]
                if position['quantity'] < quantity:
                    return {
                        'success': False,
                        'error': 'Insufficient quantity to sell'
                    }
                
                # Calculate P&L
                buy_value = quantity * position['average_price']
                sell_value = quantity * price
                pnl = sell_value - buy_value - total_charges
                
                # Update portfolio
                portfolio['available_cash'] += (sell_value - total_charges)
                portfolio['total_charges'] += total_charges
                portfolio['realized_pnl'] += pnl
                
                # Update position
                position['quantity'] -= quantity
                position['invested_amount'] = position['quantity'] * position['average_price']
                
                if position['quantity'] == 0:
                    del portfolio['positions'][symbol]
            
            # Log trade
            trade = Trade(
                user_id=user_id,
                symbol=symbol,
                action=action.upper(),
                quantity=quantity,
                price=price,
                order_type='MARKET',
                product='MIS',
                status='COMPLETE',
                pnl=pnl if action.upper() == 'SELL' else 0
            )
            db.session.add(trade)
            db.session.commit()
            
            return {
                'success': True,
                'trade_id': trade.id,
                'charges': total_charges,
                'pnl': pnl if action.upper() == 'SELL' else 0
            }
    
    def get_portfolio(self, user_id: int) -> Dict[str, Any]:
        """Get user's paper trading portfolio"""
        return self._get_portfolio(user_id)
    
    def get_positions(self, user_id: int) -> List[Dict[str, Any]]:
        """Get user's current positions"""
        portfolio = self._get_portfolio(user_id)
        positions = []
        
        for symbol, position in portfolio['positions'].items():
            positions.append({
                'symbol': symbol,
                'quantity': position['quantity'],
                'average_price': position['average_price'],
                'invested_amount': position['invested_amount']
            })
        
        return positions
    
    def get_pnl(self, user_id: int, current_prices: Dict[str, float]) -> Dict[str, float]:
        """Calculate unrealized P&L based on current prices"""
        portfolio = self._get_portfolio(user_id)
        unrealized_pnl = 0
        current_value = 0
        
        for symbol, position in portfolio['positions'].items():
            current_price = current_prices.get(symbol, position['average_price'])
            position_value = position['quantity'] * current_price
            current_value += position_value
            unrealized_pnl += position_value - position['invested_amount']
        
        total_pnl = portfolio['realized_pnl'] + unrealized_pnl
        total_value = portfolio['available_cash'] + current_value
        
        return {
            'realized_pnl': portfolio['realized_pnl'],
            'unrealized_pnl': unrealized_pnl,
            'total_pnl': total_pnl,
            'portfolio_value': total_value,
            'return_percent': (total_pnl / self.initial_capital) * 100
        }
    
    def reset_portfolio(self, user_id: int):
        """Reset user's paper trading portfolio"""
        with self.lock:
            self.user_portfolios[user_id] = self._create_new_portfolio()
    
    def _get_portfolio(self, user_id: int) -> Dict[str, Any]:
        """Get or create user portfolio"""
        if user_id not in self.user_portfolios:
            self.user_portfolios[user_id] = self._create_new_portfolio()
        return self.user_portfolios[user_id]
    
    def _create_new_portfolio(self) -> Dict[str, Any]:
        """Create a new paper trading portfolio"""
        return {
            'available_cash': self.initial_capital,
            'positions': {},
            'realized_pnl': 0,
            'total_charges': 0,
            'initial_capital': self.initial_capital,
            'created_at': datetime.now()
        }