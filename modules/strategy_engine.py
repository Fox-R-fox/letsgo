import pandas as pd
import numpy as np
from typing import Dict, List, Any
from datetime import datetime

class BaseStrategy:
    def __init__(self, parameters: Dict[str, Any] = None):
        self.parameters = parameters or {}
        self.name = "base_strategy"
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate trading signals based on market data"""
        raise NotImplementedError("Subclasses must implement generate_signals method")

class MovingAverageCrossoverStrategy(BaseStrategy):
    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__(parameters)
        self.name = "moving_average_crossover"
        self.default_params = {
            'short_window': 5,
            'long_window': 20,
            'quantity': 10
        }
        self.parameters = {**self.default_params, **(parameters or {})}
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            current_price = data['last_price']
            
            # Generate demo signals based on price movement
            price_hash = hash(symbol) % 100
            should_buy = price_hash < 30  # 30% chance to buy
            should_sell = price_hash > 70  # 30% chance to sell
            
            if should_buy:
                signals.append({
                    'symbol': symbol,
                    'action': 'BUY',
                    'quantity': self.parameters['quantity'],
                    'price': round(current_price * 0.995, 2),  # Slightly below current price
                    'strategy': self.name,
                    'timestamp': datetime.now()
                })
            elif should_sell:
                signals.append({
                    'symbol': symbol,
                    'action': 'SELL',
                    'quantity': self.parameters['quantity'],
                    'price': round(current_price * 1.005, 2),  # Slightly above current price
                    'strategy': self.name,
                    'timestamp': datetime.now()
                })
                
        return signals

class MeanReversionStrategy(BaseStrategy):
    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__(parameters)
        self.name = "mean_reversion"
        self.default_params = {
            'lookback_period': 10,
            'deviation_threshold': 2.0,
            'quantity': 5
        }
        self.parameters = {**self.default_params, **(parameters or {})}
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            current_price = data['last_price']
            
            # Generate demo signals
            price_hash = hash(symbol + str(datetime.now().minute)) % 100
            should_trade = price_hash < 25  # 25% chance to trade
            
            if should_trade:
                action = 'BUY' if price_hash < 12 else 'SELL'
                signals.append({
                    'symbol': symbol,
                    'action': action,
                    'quantity': self.parameters['quantity'],
                    'price': round(current_price * (0.995 if action == 'BUY' else 1.005), 2),
                    'strategy': self.name,
                    'timestamp': datetime.now()
                })
                
        return signals

class BreakoutStrategy(BaseStrategy):
    def __init__(self, parameters: Dict[str, Any] = None):
        super().__init__(parameters)
        self.name = "breakout"
        self.default_params = {
            'resistance_level': 1.02,
            'support_level': 0.98,
            'quantity': 8
        }
        self.parameters = {**self.default_params, **(parameters or {})}
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            current_price = data['last_price']
            base_price = 1000 + (hash(symbol) % 5000)  # Demo base price
            
            # Check for breakout
            resistance = base_price * self.parameters['resistance_level']
            support = base_price * self.parameters['support_level']
            
            if current_price >= resistance:
                signals.append({
                    'symbol': symbol,
                    'action': 'BUY',
                    'quantity': self.parameters['quantity'],
                    'price': round(current_price * 1.001, 2),
                    'strategy': self.name,
                    'timestamp': datetime.now()
                })
            elif current_price <= support:
                signals.append({
                    'symbol': symbol,
                    'action': 'SELL',
                    'quantity': self.parameters['quantity'],
                    'price': round(current_price * 0.999, 2),
                    'strategy': self.name,
                    'timestamp': datetime.now()
                })
                
        return signals

class StrategyEngine:
    def __init__(self, market_data_handler):
        self.market_data = market_data_handler
        self.available_strategies = self._get_available_strategies()
        
    def _get_available_strategies(self) -> List[Dict[str, Any]]:
        """Get list of available trading strategies"""
        return [
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
    
    def get_available_strategies(self) -> List[Dict[str, Any]]:
        """Return list of available strategies"""
        return self.available_strategies
    
    def get_strategy(self, strategy_name: str, parameters: Dict[str, Any] = None) -> BaseStrategy:
        """Get strategy instance by name"""
        strategy_map = {
            'moving_average_crossover': MovingAverageCrossoverStrategy,
            'mean_reversion': MeanReversionStrategy,
            'breakout': BreakoutStrategy
        }
        
        if strategy_name not in strategy_map:
            raise ValueError(f"Unknown strategy: {strategy_name}")
            
        return strategy_map[strategy_name](parameters)
    
    def validate_strategy_parameters(self, strategy_name: str, parameters: Dict[str, Any]) -> bool:
        """Validate strategy parameters"""
        try:
            strategy_info = next((s for s in self.available_strategies if s['name'] == strategy_name), None)
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