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

# Pulling credentials from Environment Variables for GitHub Actions security
# Default values provided for local testing in Jupyter
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1492010557568712876/9RLLAduSb04dQa9Wks0Tg74K4r4DN2v9jZ4Ppm01hQaedAVX1ljZUAo67bfwpOzSvRWe")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCXbH-4rv8GhrdlJXp_OsYEiJklgEqIJYM")

# Updated to stable model ID for 2026
MODEL_ID = "gemini-2.5-flash"

def is_market_open():
    """
    Checks if current time is within NYSE hours (8:30 AM - 3:00 PM CST).
    Note: If running on a cloud server, ensure the server time matches CST.
    """
    now = datetime.now()
    if now.weekday() > 4:  # Saturday or Sunday
        return False
        
    current_time = now.time()
    start = dt_time(8, 30)
    end = dt_time(15, 0)
    
    return start <= current_time <= end

def get_options_activity(symbol):
    try:
        stock = yf.Ticker(symbol)
        expirations = stock.options
        if not expirations: return None
        target_expiry = expirations[0] 
        opts = stock.option_chain(target_expiry)
        calls = opts.calls.assign(type="Call")
        puts = opts.puts.assign(type="Put")
        all_options = pd.concat([calls, puts])
        
        # Identify aggressive positioning where Volume is higher than the current Open Interest
        unusual = all_options[(all_options['volume'] > 500) & (all_options['volume'] > all_options['openInterest'])]
        
        if unusual.empty: return None
        
        top_unusual = unusual.sort_values(by='volume', ascending=False).head(3)
        activity_summary = []
        for _, row in top_unusual.iterrows():
            activity_summary.append({
                "type": row['type'], "strike": row['strike'], "expiry": target_expiry,
                "vol": int(row['volume']), "oi": int(row['openInterest']), "lastPrice": row['lastPrice']
            })
        return activity_summary
    except Exception as e:
        print(f"Options scan failed for {symbol}: {e}")
        return None

def ask_gemini_for_analysis(symbol, price_data, options_data):
    """Analyzes stock and options metrics using the Gemini API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={GEMINI_API_KEY}"
    
    options_context = "No unusual options activity detected."
    if options_data:
        options_context = "Unusual Options Activity Detected:\n" + "\n".join(
            [f"- {o['type']} {o['strike']} Exp {o['expiry']}: Vol {o['vol']} vs OI {o['oi']}" for o in options_data]
        )
        
    prompt = (
        f"Analyze {symbol}:\nStock Price: ${price_data['price']} (Change: ${price_data['change']}, RSI: {price_data['rsi']})\n"
        f"{options_context}\n\nDecision Criteria:\n1. Alert if RSI < 30 (Oversold) or > 70 (Overbought).\n2. Alert if Unusual Options Volume detected.\n"
        "Output Format: Start with '[ALERT]' + 1 sentence explaining why, else '[IGNORE]'."
    )
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        # Retry loop for resilience against intermittent network errors
        for attempt in range(3):
            response = requests.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                result = response.json()
                return result['candidates'][0]['content']['parts'][0]['text']
            elif response.status_code == 404:
                return "Error: Model not found. Check if MODEL_ID is deprecated."
            elif response.status_code == 429:
                print(f"Rate limited. Waiting {2**attempt}s before retry...")
            else:
                print(f"API Error {response.status_code}: {response.text}")
                
            time.sleep(2 ** attempt) 
        return "Error: Gemini API reached maximum retries."
    except Exception as e:
        return f"System Error contacting Gemini: {str(e)}"

def get_stock_data(symbol):
    """Retrieves current price and technicals from Yahoo Finance."""
    try:
        stock = yf.Ticker(symbol)
        df = stock.history(period="5d", interval="1h")
        if df.empty: return None
        
        current_price = df['Close'].iloc[-1]
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return {
            "price": round(current_price, 2), 
            "rsi": round(rsi.iloc[-1], 2), 
            "change": round(current_price - df['Close'].iloc[-2], 2)
        }
    except Exception as e:
        print(f"Price fetch error {symbol}: {e}")
        return None

def send_to_discord(symbol, message):
    """Sends the final decision to Discord only if Gemini flags an ALERT."""
    if "[IGNORE]" in message: return
    
    clean_msg = message.replace('[ALERT]', '').strip()
    payload = {
        "username": "Gemini Analyst Pro", 
        "content": f"🚨 **{symbol} Market Intelligence** 🚨\n{clean_msg}"
    }
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"Discord post error: {e}")

def run_cycle():
    """Executes one full sweep of the watch list."""
    print(f"--- Starting Cycle: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    for ticker in TICKERS:
        print(f"Processing {ticker}...")
        price_data = get_stock_data(ticker)
        if price_data:
            options_data = get_options_activity(ticker)
            analysis = ask_gemini_for_analysis(ticker, price_data, options_data)
            print(f"AI Decision: {analysis}")
            send_to_discord(ticker, analysis)
        time.sleep(5) # Delay to respect API pacing
    print("--- Cycle Complete. ---")

if __name__ == "__main__":
    # Automatic mode detection: 
    # Runs once in GitHub Actions, but loops every 15 mins on a local PC/Jupyter.
    if os.getenv("GITHUB_ACTIONS"):
        run_cycle()
    else:
        while True:
            if is_market_open():
                run_cycle()
                print("Waiting 15 minutes for next local check...")
                time.sleep(900) 
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Market is closed. Checking again in 10 mins...")
                time.sleep(600)
