import pandas as pd
import numpy as np
from typing import Dict, List, Any
from datetime import datetime
import random

class MovingAverageCrossStrategy:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.name = "Moving Average Crossover"
        self.fast_period = self.config.get('fast_period', 5)  # Shorter period for demo
        self.slow_period = self.config.get('slow_period', 10)  # Shorter period for demo
        self.position = {}  # Track positions per symbol
        self.capital_per_trade = self.config.get('capital_per_trade', 10000)
        self.data_history = {}
        self.signal_count = 0
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            # Add current data to history
            self.add_data_point(symbol, data)
            
            # Get price history for this symbol
            if symbol not in self.data_history or len(self.data_history[symbol]) < self.slow_period:
                continue
            
            # Calculate moving averages
            prices = [point['last_price'] for point in self.data_history[symbol]]
            fast_ma = self.calculate_sma(prices, self.fast_period)
            slow_ma = self.calculate_sma(prices, self.slow_period)
            
            if len(fast_ma) < 2 or len(slow_ma) < 2:
                continue
            
            current_fast = fast_ma[-1]
            previous_fast = fast_ma[-2] if len(fast_ma) > 1 else current_fast
            current_slow = slow_ma[-1]
            previous_slow = slow_ma[-2] if len(slow_ma) > 1 else current_slow
            
            # Generate signals
            current_position = self.position.get(symbol, 'OUT')
            
            # Golden Cross - BUY signal (Fast MA crosses above Slow MA)
            if (previous_fast <= previous_slow and 
                current_fast > current_slow and 
                current_position != 'LONG'):
                
                quantity = self.calculate_quantity(data['last_price'])
                
                signals.append({
                    'symbol': symbol,
                    'action': 'BUY',
                    'quantity': quantity,
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'GOLDEN_CROSS',
                    'timestamp': datetime.now(),
                    'fast_ma': current_fast,
                    'slow_ma': current_slow
                })
                self.position[symbol] = 'LONG'
                self.signal_count += 1
                print(f"🎯 BUY signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f}")
                print(f"   Fast MA: {current_fast:.2f}, Slow MA: {current_slow:.2f}")
            
            # Death Cross - SELL signal (Fast MA crosses below Slow MA)
            elif (previous_fast >= previous_slow and 
                  current_fast < current_slow and 
                  current_position == 'LONG'):
                
                quantity = self.calculate_quantity(data['last_price'])
                
                signals.append({
                    'symbol': symbol,
                    'action': 'SELL',
                    'quantity': quantity,
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'DEATH_CROSS',
                    'timestamp': datetime.now(),
                    'fast_ma': current_fast,
                    'slow_ma': current_slow
                })
                self.position[symbol] = 'OUT'
                self.signal_count += 1
                print(f"🎯 SELL signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f}")
                print(f"   Fast MA: {current_fast:.2f}, Slow MA: {current_slow:.2f}")
            
            # Demo mode: Generate random signals if no real signals (for testing)
            elif self.config.get('demo_mode', True) and random.random() < 0.1:  # 10% chance
                if current_position != 'LONG':
                    # Random BUY signal
                    quantity = self.calculate_quantity(data['last_price'])
                    signals.append({
                        'symbol': symbol,
                        'action': 'BUY',
                        'quantity': quantity,
                        'price': data['last_price'],
                        'strategy': self.name + " (DEMO)",
                        'signal_type': 'RANDOM_BUY',
                        'timestamp': datetime.now()
                    })
                    self.position[symbol] = 'LONG'
                    self.signal_count += 1
                    print(f"🎲 DEMO BUY signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f}")
                else:
                    # Random SELL signal
                    quantity = self.calculate_quantity(data['last_price'])
                    signals.append({
                        'symbol': symbol,
                        'action': 'SELL',
                        'quantity': quantity,
                        'price': data['last_price'],
                        'strategy': self.name + " (DEMO)",
                        'signal_type': 'RANDOM_SELL',
                        'timestamp': datetime.now()
                    })
                    self.position[symbol] = 'OUT'
                    self.signal_count += 1
                    print(f"🎲 DEMO SELL signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f}")
        
        return signals
    
    def add_data_point(self, symbol: str, data_point: Dict[str, Any]):
        """Add data point to history"""
        if symbol not in self.data_history:
            self.data_history[symbol] = []
        
        # Add the data point
        self.data_history[symbol].append({
            'timestamp': datetime.now(),
            'last_price': data_point.get('last_price', 0),
            'volume': data_point.get('volume', 0),
            'change': data_point.get('change', 0)
        })
        
        # Keep only last 50 data points (optimized for demo)
        if len(self.data_history[symbol]) > 50:
            self.data_history[symbol] = self.data_history[symbol][-50:]
    
    def calculate_sma(self, prices: List[float], period: int) -> List[float]:
        """Calculate Simple Moving Average manually"""
        if len(prices) < period:
            return []
        
        sma_values = []
        for i in range(period - 1, len(prices)):
            sma = sum(prices[i-period+1:i+1]) / period
            sma_values.append(sma)
        
        return sma_values
    
    def calculate_quantity(self, price: float) -> int:
        """Calculate quantity based on position sizing"""
        quantity = max(1, int(self.capital_per_trade / price))
        # For demo, use smaller quantities
        return min(quantity, 10)  # Max 10 shares per trade for demo
    
    def get_strategy_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters for display"""
        return {
            'fast_period': self.fast_period,
            'slow_period': self.slow_period,
            'capital_per_trade': self.capital_per_trade,
            'total_signals': self.signal_count,
            'current_positions': len([p for p in self.position.values() if p == 'LONG'])
        }