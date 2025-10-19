from abc import ABC, abstractmethod
from typing import Dict, List, Any
import pandas as pd

class BaseStrategy(ABC):
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.name = "Base Strategy"
        self.data_history = {}
        
    @abstractmethod
    def generate_signals(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        pass
    
    def add_data_point(self, symbol: str, data_point: Dict[str, Any]):
        if symbol not in self.data_history:
            self.data_history[symbol] = []
        
        self.data_history[symbol].append(data_point)
        
        # Keep only last 200 data points
        if len(self.data_history[symbol]) > 200:
            self.data_history[symbol] = self.data_history[symbol][-200:]
    
    def get_data_frame(self, symbol: str) -> pd.DataFrame:
        if symbol not in self.data_history or not self.data_history[symbol]:
            return pd.DataFrame()
        
        return pd.DataFrame(self.data_history[symbol])
    
    def calculate_position_size(self, price: float, risk_per_trade: float = 0.02) -> int:
        """Calculate position size based on risk management"""
        capital = self.config.get('capital', 100000)
        max_risk = capital * risk_per_trade
        quantity = int(max_risk / price)
        return max(1, quantity)