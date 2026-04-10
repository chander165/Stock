import yfinance as yf
import requests
import json
import time
import pandas as pd
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
TICKERS = ["ET", "TSLA", "AAPL"]  
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1492010557568712876/9RLLAduSb04dQa9Wks0Tg74K4r4DN2v9jZ4Ppm01hQaedAVX1ljZUAo67bfwpOzSvRWe"
GEMINI_API_KEY = "AIzaSyCXbH-4rv8GhrdlJXp_OsYEiJklgEqIJYM"

# Use the stable model ID for 2026
MODEL_ID = "gemini-2.5-flash"

def get_options_activity(symbol):
    """
    Scans the nearest expiration options chain for unusual activity.
    Unusual = High Volume relative to Open Interest (OI).
    """
    try:
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})
        stock = yf.Ticker(symbol, session=session)
        
        # Get nearest expiration date
        expirations = stock.options
        if not expirations:
            return None
        
        target_expiry = expirations[0] # Focus on front-month/nearest
        opts = stock.option_chain(target_expiry)
        
        # Combine calls and puts for analysis
        calls = opts.calls.assign(type="Call")
        puts = opts.puts.assign(type="Put")
        all_options = pd.concat([calls, puts])
        
        # Filter for 'Unusual' activity: 
        # 1. Volume > 500 (Significant activity)
        # 2. Volume > Open Interest (New aggressive positioning)
        unusual = all_options[(all_options['volume'] > 500) & (all_options['volume'] > all_options['openInterest'])]
        
        if unusual.empty:
            return None
            
        # Format the top 3 most unusual contracts for Gemini
        top_unusual = unusual.sort_values(by='volume', ascending=False).head(3)
        activity_summary = []
        for _, row in top_unusual.iterrows():
            activity_summary.append({
                "type": row['type'],
                "strike": row['strike'],
                "expiry": target_expiry,
                "vol": int(row['volume']),
                "oi": int(row['openInterest']),
                "lastPrice": row['lastPrice']
            })
            
        return activity_summary
    except Exception as e:
        print(f"Options scan failed for {symbol}: {e}")
        return None

def ask_gemini_for_analysis(symbol, price_data, options_data):
    """Calls the updated Gemini API endpoint with Stock + Options data."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_ID}:generateContent?key={GEMINI_API_KEY}"
    
    options_context = "No unusual options activity detected."
    if options_data:
        options_context = "Unusual Options Activity Detected:\n" + "\n".join(
            [f"- {o['type']} {o['strike']} Exp {o['expiry']}: Vol {o['vol']} vs OI {o['oi']}" for o in options_data]
        )
    
    prompt = (
        f"Analyze {symbol}:\n"
        f"Stock Price: ${price_data['price']} (Change: ${price_data['change']}, RSI: {price_data['rsi']})\n"
        f"{options_context}\n\n"
        "Instructions:\n"
        "1. Alert if RSI < 30 or > 70.\n"
        "2. Alert if Unusual Options Volume is detected (Bullish if Calls, Bearish if Puts).\n"
        "Start with '[ALERT]' + 1 concise sentence if needed, else '[IGNORE]'."
    )
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        for attempt in range(3):
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            time.sleep(2 ** attempt) 
        return "Error: Gemini API failure."
    except Exception as e:
        return f"System Error: {str(e)}"

def get_stock_data(symbol):
    try:
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})
        stock = yf.Ticker(symbol, session=session)
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
    if "[IGNORE]" in message:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No alert for {symbol}.")
        return
    clean_msg = message.replace('[ALERT]', '').strip()
    payload = {
        "username": "Gemini Analyst Pro",
        "content": f"🚨 **{symbol} Market Intelligence** 🚨\n{clean_msg}"
    }
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

if __name__ == "__main__":
    print(f"--- Starting Full Intelligence Cycle for {', '.join(TICKERS)} ---")
    for ticker in TICKERS:
        print(f"Processing {ticker}...")
        price_data = get_stock_data(ticker)
        if price_data:
            options_data = get_options_activity(ticker)
            analysis = ask_gemini_for_analysis(ticker, price_data, options_data)
            print(f"AI Decision: {analysis}")
            send_to_discord(ticker, analysis)
        time.sleep(3) # Delay to respect API limits
    print("--- Cycle Complete ---")