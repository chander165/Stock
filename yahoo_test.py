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
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1492010557568712876/9RLLAduSb04dQa9Wks0Tg74K4r4DN2v9jZ4Ppm01hQaedAVX1ljZUAo67bfwpOzSvRWe")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCXbH-4rv8GhrdlJXp_OsYEiJklgEqIJYM")

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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={GEMINI_API_KEY}"
    options_context = "No unusual options activity detected."
    if options_data:
        options_context = "Unusual Options Activity Detected:\n" + "\n".join(
            [f"- {o['type']} {o['strike']} Exp {o['expiry']}: Vol {o['vol']} vs OI {o['oi']}" for o in options_data]
        )
    prompt = (
        f"Analyze {symbol}:\nStock Price: ${price_data['price']} (Change: ${price_data['change']}, RSI: {price_data['rsi']})\n"
        f"{options_context}\n\nDecision Criteria:\n1. Alert if RSI < 30 or > 70.\n2. Alert if Unusual Options Volume detected.\n"
        "Output Format: Start with '[ALERT]' + 1 sentence, else '[IGNORE]'."
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        return "Error: Gemini API failure."
    except Exception as e:
        return f"System Error: {str(e)}"

def get_stock_data(symbol):
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
        return {"price": round(current_price, 2), "rsi": round(rsi.iloc[-1], 2), "change": round(current_price - df['Close'].iloc[-2], 2)}
    except Exception as e:
        print(f"Price fetch error {symbol}: {e}")
        return None

def send_to_discord(symbol, message):
    if "[IGNORE]" in message: return
    clean_msg = message.replace('[ALERT]', '').strip()
    payload = {"username": "Gemini Analyst Pro", "content": f"🚨 **{symbol} Alert** 🚨\n{clean_msg}"}
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

def run_cycle():
    print(f"--- Starting Cycle: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    for ticker in TICKERS:
        print(f"Processing {ticker}...")
        price_data = get_stock_data(ticker)
        if price_data:
            options_data = get_options_activity(ticker)
            analysis = ask_gemini_for_analysis(ticker, price_data, options_data)
            print(f"AI Decision: {analysis}")
            send_to_discord(ticker, analysis)
        time.sleep(5)
    print("--- Cycle Complete. ---")

if __name__ == "__main__":
    # If 'GITHUB_ACTIONS' environment variable exists, run once and exit.
    # Otherwise, maintain the local loop for Jupyter/PC testing.
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
