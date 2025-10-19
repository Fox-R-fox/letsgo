import pandas as pd
import numpy as np
from typing import Dict, List, Any
from abc import ABC, abstractmethod
from datetime import datetime

class BaseStrategy(ABC):
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.name = "Base Strategy"
        self.data_history = {}
        
    @abstractmethod
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        pass
    
    def add_data_point(self, symbol: str, data_point: Dict[str, Any]):
        """Add data point to history for technical analysis"""
        if symbol not in self.data_history:
            self.data_history[symbol] = []
        
        self.data_history[symbol].append(data_point)
        
        # Keep only last 200 data points
        if len(self.data_history[symbol]) > 200:
            self.data_history[symbol] = self.data_history[symbol][-200:]
    
    def get_data_frame(self, symbol: str) -> pd.DataFrame:
        """Convert history to pandas DataFrame"""
        if symbol not in self.data_history or not self.data_history[symbol]:
            return pd.DataFrame()
        
        return pd.DataFrame(self.data_history[symbol])
    
    def calculate_sma(self, prices: List[float], period: int) -> List[float]:
        """Calculate Simple Moving Average manually"""
        if len(prices) < period:
            return []
        
        sma_values = []
        for i in range(period - 1, len(prices)):
            sma = sum(prices[i-period+1:i+1]) / period
            sma_values.append(sma)
        
        return sma_values
    
    def calculate_rsi(self, prices: List[float], period: int) -> List[float]:
        """Calculate RSI manually"""
        if len(prices) < period + 1:
            return []
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        rsi_values = []
        for i in range(len(gains) - period + 1):
            avg_gain = np.mean(gains[i:i+period])
            avg_loss = np.mean(losses[i:i+period])
            
            if avg_loss == 0:
                rsi = 100
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            
            rsi_values.append(rsi)
        
        return rsi_values

class MovingAverageCrossStrategy(BaseStrategy):
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.name = "Moving Average Crossover"
        self.fast_period = self.config.get('fast_period', 10)
        self.slow_period = self.config.get('slow_period', 20)
        self.position = {}  # Track positions per symbol
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            # Add current data to history
            self.add_data_point(symbol, data)
            df = self.get_data_frame(symbol)
            
            if len(df) < self.slow_period:
                continue  # Not enough data
            
            # Calculate moving averages manually
            prices = df['last_price'].tolist()
            fast_ma = self.calculate_sma(prices, self.fast_period)
            slow_ma = self.calculate_sma(prices, self.slow_period)
            
            if len(fast_ma) < 2 or len(slow_ma) < 2:
                continue
            
            current_fast = fast_ma[-1]
            previous_fast = fast_ma[-2]
            current_slow = slow_ma[-1]
            previous_slow = slow_ma[-2]
            
            # Generate signals
            current_position = self.position.get(symbol, 'OUT')
            
            # Golden Cross - BUY signal
            if (previous_fast <= previous_slow and 
                current_fast > current_slow and 
                current_position != 'LONG'):
                
                signals.append({
                    'symbol': symbol,
                    'action': 'BUY',
                    'quantity': self.calculate_quantity(data['last_price']),
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'GOLDEN_CROSS'
                })
                self.position[symbol] = 'LONG'
            
            # Death Cross - SELL signal
            elif (previous_fast >= previous_slow and 
                  current_fast < current_slow and 
                  current_position == 'LONG'):
                
                signals.append({
                    'symbol': symbol,
                    'action': 'SELL',
                    'quantity': self.calculate_quantity(data['last_price']),
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'DEATH_CROSS'
                })
                self.position[symbol] = 'OUT'
        
        return signals
    
    def calculate_quantity(self, price: float) -> int:
        """Calculate quantity based on position sizing"""
        capital_per_trade = self.config.get('capital_per_trade', 10000)
        return max(1, int(capital_per_trade / price))

class RSIStrategy(BaseStrategy):
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.name = "RSI Mean Reversion"
        self.rsi_period = self.config.get('rsi_period', 14)
        self.oversold = self.config.get('oversold', 30)
        self.overbought = self.config.get('overbought', 70)
        self.position = {}
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            self.add_data_point(symbol, data)
            df = self.get_data_frame(symbol)
            
            if len(df) < self.rsi_period + 1:
                continue
            
            # Calculate RSI manually
            prices = df['last_price'].tolist()
            rsi = self.calculate_rsi(prices, self.rsi_period)
            
            if len(rsi) < 1:
                continue
            
            current_rsi = rsi[-1]
            current_position = self.position.get(symbol, 'OUT')
            
            # Oversold - BUY signal
            if current_rsi < self.oversold and current_position != 'LONG':
                signals.append({
                    'symbol': symbol,
                    'action': 'BUY',
                    'quantity': self.calculate_quantity(data['last_price']),
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'OVERSOLD',
                    'rsi_value': current_rsi
                })
                self.position[symbol] = 'LONG'
            
            # Overbought - SELL signal
            elif current_rsi > self.overbought and current_position == 'LONG':
                signals.append({
                    'symbol': symbol,
                    'action': 'SELL',
                    'quantity': self.calculate_quantity(data['last_price']),
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'OVERBOUGHT',
                    'rsi_value': current_rsi
                })
                self.position[symbol] = 'OUT'
        
        return signals
    
    def calculate_quantity(self, price: float) -> int:
        capital_per_trade = self.config.get('capital_per_trade', 10000)
        return max(1, int(capital_per_trade / price))

class StrategyEngine:
    def __init__(self, market_data_handler):
        self.market_data_handler = market_data_handler
        self.strategies = {
            'moving_average': MovingAverageCrossStrategy,
            'rsi': RSIStrategy
        }
        self.active_strategies = {}
        
    def get_strategy(self, strategy_name: str, config: Dict[str, Any] = None) -> BaseStrategy:
        """Get strategy instance by name"""
        if strategy_name not in self.strategies:
            raise ValueError(f"Strategy {strategy_name} not found")
        
        if strategy_name not in self.active_strategies:
            self.active_strategies[strategy_name] = self.strategies[strategy_name](config)
        
        return self.active_strategies[strategy_name]
    
    def register_strategy(self, name: str, strategy_class):
        """Register a new strategy"""
        self.strategies[name] = strategy_class
    
    def get_available_strategies(self) -> List[Dict[str, Any]]:
        """Get list of available strategies"""
        return [
            {
                'name': 'moving_average',
                'display_name': 'Moving Average Crossover',
                'description': 'Buys when fast MA crosses above slow MA, sells when crosses below',
                'parameters': [
                    {'name': 'fast_period', 'type': 'number', 'default': 10},
                    {'name': 'slow_period', 'type': 'number', 'default': 20},
                    {'name': 'capital_per_trade', 'type': 'number', 'default': 10000}
                ]
            },
            {
                'name': 'rsi',
                'display_name': 'RSI Mean Reversion',
                'description': 'Buys when RSI is oversold (<30), sells when overbought (>70)',
                'parameters': [
                    {'name': 'rsi_period', 'type': 'number', 'default': 14},
                    {'name': 'oversold', 'type': 'number', 'default': 30},
                    {'name': 'overbought', 'type': 'number', 'default': 70},
                    {'name': 'capital_per_trade', 'type': 'number', 'default': 10000}
                ]
            }
        ]