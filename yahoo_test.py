import yfinance as yf
import requests
import json
import time
import os
import pandas as pd
from datetime import datetime, time as dt_time

# ==========================================
# CONFIGURATION
# ==========================================
TICKERS = ["ET", "TSLA", "AAPL"]  

# SECURE CONFIGURATION: Pulling keys from Environment Variables
# In GitHub, set these in Settings > Secrets and variables > Actions
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Updated to the new Gemini 3 production model (April 2026)
MODEL_ID = "gemini-3-flash"

def is_market_open():
    """Checks if current time is within NYSE hours (8:30 AM - 3:00 PM CST)."""
    now = datetime.now()
    if now.weekday() > 4: return False
    current_time = now.time()
    return dt_time(8, 30) <= current_time <= dt_time(15, 0)

def get_options_activity(symbol):
    """Scans nearest expiration for Volume > Open Interest."""
    try:
        stock = yf.Ticker(symbol)
        expirations = stock.options
        if not expirations: return None
        target_expiry = expirations[0] 
        opts = stock.option_chain(target_expiry)
        all_options = pd.concat([opts.calls.assign(type="Call"), opts.puts.assign(type="Put")])
        unusual = all_options[(all_options['volume'] > 500) & (all_options['volume'] > all_options['openInterest'])]
        if unusual.empty: return None
        top_unusual = unusual.sort_values(by='volume', ascending=False).head(3)
        return [{"type": r['type'], "strike": r['strike'], "expiry": target_expiry, 
                 "vol": int(r['volume']), "oi": int(r['openInterest']), "lastPrice": r['lastPrice']} 
                for _, r in top_unusual.iterrows()]
    except Exception as e:
        print(f"Options scan failed for {symbol}: {e}")
        return None

def ask_gemini_for_analysis(symbol, price_data, options_data):
    """Analyzes stock metrics using Gemini 3."""
    if not GEMINI_API_KEY:
        return "Error: GEMINI_API_KEY environment variable is not set."
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={GEMINI_API_KEY}"
    options_context = "No unusual options activity detected."
    if options_data:
        options_context = "Unusual Options Activity Detected:\n" + "\n".join(
            [f"- {o['type']} {o['strike']} Exp {o['expiry']}: Vol {o['vol']} vs OI {o['oi']}" for o in options_data]
        )
    prompt = (
        f"Analyze {symbol}:\nStock Price: ${price_data['price']} (Change: ${price_data['change']}, RSI: {price_data['rsi']})\n"
        f"{options_context}\n\nDecision Criteria:\n1. Alert if RSI < 30 or > 70.\n2. Alert if Unusual Options Volume detected.\n"
        "Output Format: Start with '[ALERT]' + 1 sentence, else ONLY '[IGNORE]'."
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        for attempt in range(3):
            response = requests.post(url, json=payload, timeout=20)
