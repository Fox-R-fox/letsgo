import pandas as pd
import numpy as np
from typing import Dict, List, Any
from datetime import datetime
import random

class RSIStrategy:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.name = "RSI Mean Reversion"
        self.rsi_period = self.config.get('rsi_period', 6)  # Shorter for demo
        self.oversold = self.config.get('oversold', 30)
        self.overbought = self.config.get('overbought', 70)
        self.position = {}
        self.capital_per_trade = self.config.get('capital_per_trade', 10000)
        self.data_history = {}
        self.signal_count = 0
        
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals = []
        
        for symbol, data in market_data.items():
            if not data or 'last_price' not in data:
                continue
                
            self.add_data_point(symbol, data)
            
            # Get enough data for RSI calculation
            if symbol not in self.data_history or len(self.data_history[symbol]) < self.rsi_period + 1:
                continue
            
            # Calculate RSI manually
            prices = [point['last_price'] for point in self.data_history[symbol]]
            rsi = self.calculate_rsi(prices, self.rsi_period)
            
            if len(rsi) < 1:
                continue
            
            current_rsi = rsi[-1]
            current_position = self.position.get(symbol, 'OUT')
            
            # Oversold - BUY signal
            if current_rsi < self.oversold and current_position != 'LONG':
                quantity = self.calculate_quantity(data['last_price'])
                
                signals.append({
                    'symbol': symbol,
                    'action': 'BUY',
                    'quantity': quantity,
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'OVERSOLD',
                    'rsi_value': current_rsi,
                    'timestamp': datetime.now()
                })
                self.position[symbol] = 'LONG'
                self.signal_count += 1
                print(f"🎯 RSI BUY signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f} (RSI: {current_rsi:.1f})")
            
            # Overbought - SELL signal
            elif current_rsi > self.overbought and current_position == 'LONG':
                quantity = self.calculate_quantity(data['last_price'])
                
                signals.append({
                    'symbol': symbol,
                    'action': 'SELL',
                    'quantity': quantity,
                    'price': data['last_price'],
                    'strategy': self.name,
                    'signal_type': 'OVERBOUGHT',
                    'rsi_value': current_rsi,
                    'timestamp': datetime.now()
                })
                self.position[symbol] = 'OUT'
                self.signal_count += 1
                print(f"🎯 RSI SELL signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f} (RSI: {current_rsi:.1f})")
            
            # Demo mode: Generate random signals if no real signals
            elif self.config.get('demo_mode', True) and random.random() < 0.08:  # 8% chance
                if current_position != 'LONG':
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
                    print(f"🎲 RSI DEMO BUY signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f}")
                else:
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
                    print(f"🎲 RSI DEMO SELL signal #{self.signal_count} for {symbol} at ₹{data['last_price']:.2f}")
        
        return signals
    
    def add_data_point(self, symbol: str, data_point: Dict[str, Any]):
        """Add data point to history"""
        if symbol not in self.data_history:
            self.data_history[symbol] = []
        
        self.data_history[symbol].append({
            'timestamp': datetime.now(),
            'last_price': data_point.get('last_price', 0)
        })
        
        if len(self.data_history[symbol]) > 50:
            self.data_history[symbol] = self.data_history[symbol][-50:]
    
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
    
    def calculate_quantity(self, price: float) -> int:
        """Calculate quantity based on position sizing"""
        quantity = max(1, int(self.capital_per_trade / price))
        return min(quantity, 10)  # Max 10 shares per trade for demo
    
    def get_strategy_parameters(self) -> Dict[str, Any]:
        """Get strategy parameters for display"""
        return {
            'rsi_period': self.rsi_period,
            'oversold': self.oversold,
            'overbought': self.overbought,
            'capital_per_trade': self.capital_per_trade,
            'total_signals': self.signal_count,
            'current_positions': len([p for p in self.position.values() if p == 'LONG'])
        }