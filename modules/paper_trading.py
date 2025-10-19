import json
import os
from datetime import datetime
from typing import Dict, List, Any

class PaperTrading:
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.data_file = 'paper_trading_data.json'
        self.portfolio_data = self._load_portfolio_data()
    
    def _load_portfolio_data(self) -> Dict[str, Any]:
        """Load portfolio data from file"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading portfolio data: {e}")
        
        # Default portfolio structure
        return {
            'users': {},
            'last_updated': datetime.now().isoformat()
        }
    
    def _save_portfolio_data(self):
        """Save portfolio data to file"""
        try:
            self.portfolio_data['last_updated'] = datetime.now().isoformat()
            with open(self.data_file, 'w') as f:
                json.dump(self.portfolio_data, f, indent=2)
        except Exception as e:
            print(f"Error saving portfolio data: {e}")
    
    def _get_user_portfolio(self, user_id: int) -> Dict[str, Any]:
        """Get or create user portfolio"""
        user_id_str = str(user_id)
        if user_id_str not in self.portfolio_data['users']:
            self.portfolio_data['users'][user_id_str] = {
                'initial_capital': self.initial_capital,
                'available_cash': self.initial_capital,
                'positions': {},
                'total_charges': 0.0,
                'realized_pnl': 0.0,
                'trades_count': 0,
                'created_at': datetime.now().isoformat()
            }
            self._save_portfolio_data()
        return self.portfolio_data['users'][user_id_str]
    
    def get_portfolio(self, user_id: int) -> Dict[str, Any]:
        """Get user portfolio"""
        portfolio = self._get_user_portfolio(user_id)
        
        # Calculate current portfolio value
        positions_value = 0.0
        for symbol, position in portfolio['positions'].items():
            positions_value += position['quantity'] * position['average_price']
        
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
        """Get user positions"""
        portfolio = self._get_user_portfolio(user_id)
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
        """Execute a paper trade"""
        try:
            portfolio = self._get_user_portfolio(user_id)
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
                        'action': 'BUY',
                        'last_traded': datetime.now().isoformat()
                    }
                else:
                    portfolio['positions'][symbol] = {
                        'quantity': quantity,
                        'average_price': price,
                        'action': 'BUY',
                        'last_traded': datetime.now().isoformat()
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
                    portfolio['positions'][symbol]['last_traded'] = datetime.now().isoformat()
            
            # Update charges and trade count
            portfolio['total_charges'] += total_charges
            portfolio['trades_count'] += 1
            
            self._save_portfolio_data()
            
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
        """Calculate total value of all positions"""
        total_value = 0.0
        for symbol, position in positions.items():
            total_value += position['quantity'] * position['average_price']
        return total_value
    
    def get_pnl(self, user_id: int, current_prices: Dict[str, float]) -> Dict[str, float]:
        """Calculate P&L with current prices"""
        portfolio = self._get_user_portfolio(user_id)
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
    
    def reset_portfolio(self, user_id: int) -> Dict[str, Any]:
        """Reset user portfolio to initial state"""
        try:
            user_id_str = str(user_id)
            self.portfolio_data['users'][user_id_str] = {
                'initial_capital': self.initial_capital,
                'available_cash': self.initial_capital,
                'positions': {},
                'total_charges': 0.0,
                'realized_pnl': 0.0,
                'trades_count': 0,
                'created_at': datetime.now().isoformat()
            }
            self._save_portfolio_data()
            return {'success': True, 'message': 'Portfolio reset successfully'}
        except Exception as e:
            return {'success': False, 'error': str(e)}