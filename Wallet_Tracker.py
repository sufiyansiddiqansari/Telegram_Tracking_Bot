import json
import websocket
import datetime
import threading
import requests
import nest_asyncio
import os
import time
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from keep_alive import keep_alive

# Apply nest_asyncio to allow nested event loops (often needed in Replit/Jupyter)
nest_asyncio.apply()
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN not found in environment variables. Please set it in .env or Replit Secrets.")
    
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

CHAT_ID = None
seen_trades = set()
open_positions = {}
ws_app = None

def load_wallets():
    if not os.path.exists("wallets.json"):
        save_wallets({})
        return {}
    try:
        with open("wallets.json", "r") as f:
            return json.load(f)
    except:
        return {}

def save_wallets(data):
    with open("wallets.json", "w") as f:
        json.dump(data, f)

def send_message(text):
    global CHAT_ID
    if CHAT_ID is None:
        return
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }
    requests.post(TELEGRAM_API, data=payload)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    await update.message.reply_text(
        "Bot Activated 🐳\n\nUse commands:\n/addwallet <nickname> <address>\n/removewallet <nickname>\n/listwallets"
    )

async def addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ws_app
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addwallet <nickname> <address>\nExample: /addwallet Whale 0x123...")
        return
        
    nickname = context.args[0]
    wallet = context.args[1]
    
    wallets = load_wallets()
    wallets[nickname] = wallet
    save_wallets(wallets)
    
    # Subscribe dynamically if WS is already running
    if ws_app:
        sub = {
            "method": "subscribe",
            "subscription": {
                "type": "userFills",
                "user": wallet
            }
        }
        ws_app.send(json.dumps(sub))
        
    await update.message.reply_text(f"Wallet added: {nickname} -> {wallet}")

async def removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /removewallet <nickname>")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    
    if nickname in wallets:
        del wallets[nickname]
        save_wallets(wallets)
        await update.message.reply_text(f"Wallet '{nickname}' removed.")
    else:
        await update.message.reply_text("Nickname not found.")

async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text("No wallets tracked currently.")
        return
    text = "Tracked Wallets:\n\n"
    for name, addr in wallets.items():
        text += f"🔹 {name} : {addr}\n"
    await update.message.reply_text(text)

def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    # Only process userFills channel messages
    if data.get("channel") != "userFills":
        return

    payload = data.get("data", {})
    wallet = payload.get("user")
    if not wallet:
        return

    wallets = load_wallets()
    nickname = None
    for name, addr in wallets.items():
        if addr.lower() == wallet.lower():
            nickname = name
            break
            
    if nickname is None:
        return

    # The payload contains a list of fills
    fills = payload.get("fills", [])
    for trade in fills:
        trade_id = trade.get("tid")
        if trade_id in seen_trades:
            continue
        seen_trades.add(trade_id)

        coin = trade.get("coin")
        price = float(trade.get("px"))
        size = float(trade.get("sz"))
        direction = trade.get("dir", "") # e.g. "Open Long", "Close Short"
        ts = trade.get("time")
        
        # Convert timestamp to human readable
        trade_time = datetime.datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
        key = f"{nickname}_{coin}"

        if "Open" in direction:
            open_positions[key] = {
                "price": price,
                "time": trade_time,
                "dir": direction
            }
            msg = f"""
🚀 POSITION OPENED
Trader: {nickname}
Token: {coin}
Action: {direction}
Entry Price: ${price}
Size: {size}
Time: {trade_time}
"""
            send_message(msg)
            
        elif "Close" in direction:
            entry_info = open_positions.get(key)
            if entry_info:
                entry = entry_info["price"]
                entry_time = entry_info["time"]
                
                if "Long" in direction: # Closing a Long
                    pnl = ((price - entry) / entry) * 100
                else: # Closing a Short
                    pnl = ((entry - price) / entry) * 100
                    
                msg = f"""
📉 POSITION CLOSED
Trader: {nickname}
Token: {coin}
Action: {direction}
Entry: ${entry}
Exit: ${price}
Size: {size}
Opened: {entry_time}
Closed: {trade_time}
PnL: {round(pnl, 2)}%
"""
                send_message(msg)
                # Note: In a robust system, we would handle partial closures accurately. 
                # Here we clear the open position on the first Close event.
                del open_positions[key]
            else:
                # We saw a close event but missed the open event
                msg = f"""
📉 POSITION CLOSED (Missed Entry)
Trader: {nickname}
Token: {coin}
Action: {direction}
Exit: ${price}
Size: {size}
Time: {trade_time}
"""
                send_message(msg)

def on_open(ws):
    print("Websocket connected")
    wallets = load_wallets()
    for addr in wallets.values():
        sub = {
            "method": "subscribe",
            "subscription": {
                "type": "userFills",
                "user": addr
            }
        }
        ws.send(json.dumps(sub))

def on_error(ws, error):
    print(f"Websocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("Websocket closed. Reconnecting in 5 seconds...")
    time.sleep(5)
    start_ws()

def start_ws():
    global ws_app
    ws_app = websocket.WebSocketApp(
        "wss://api.hyperliquid.xyz/ws",
        on_message=on_message,
        on_open=on_open,
        on_error=on_error,
        on_close=on_close
    )
    ws_app.run_forever()

def start_bot():
    if not BOT_TOKEN:
        print("Cannot start bot without BOT_TOKEN.")
        return
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addwallet", addwallet))
    app.add_handler(CommandHandler("removewallet", removewallet))
    app.add_handler(CommandHandler("listwallets", listwallets))
    
    # Start Websocket in a separate thread
    threading.Thread(target=start_ws, daemon=True).start()
    
    print("Telegram Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    # Start background web server for Replit 24/7 keeping alive
    keep_alive()
    # Start bot
    start_bot()