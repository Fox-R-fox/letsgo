from datetime import datetime, time
import pandas as pd
from typing import Dict, Any, List
import logging

def is_market_open() -> bool:
    """Check if market is currently open (IST)"""
    now = datetime.now().time()
    market_open = time(9, 15)  # 9:15 AM
    market_close = time(15, 30)  # 3:30 PM
    
    return market_open <= now <= market_close

def format_currency(amount: float) -> str:
    """Format amount as Indian currency"""
    return f"₹{amount:,.2f}"

def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """Calculate percentage change between two values"""
    if old_value == 0:
        return 0
    return ((new_value - old_value) / old_value) * 100

def validate_quantity(price: float, quantity: int, max_position_size: float) -> bool:
    """Validate if position size is within limits"""
    position_value = price * quantity
    return position_value <= max_position_size

def get_working_days(start_date: datetime, end_date: datetime) -> List[datetime]:
    """Get list of working days (Monday to Friday) between two dates"""
    dates = pd.date_range(start_date, end_date, freq='B')
    return [date.to_pydatetime() for date in dates]

def setup_logging():
    """Setup application logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('trading_bot.log'),
            logging.StreamHandler()
        ]
    )

def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert value to int"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default