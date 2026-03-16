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

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

seen_trades = set()
open_positions = {}
ws_app = None

def load_wallets():
    if not os.path.exists("wallets.json"):
        save_wallets({})
        return {}
    try:
        with open("wallets.json", "r") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return dict(data)
    except:
        return {}

def save_wallets(data):
    with open("wallets.json", "w") as f:
        json.dump(data, f, indent=4)

def send_message(chat_id, text):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(TELEGRAM_API, data=payload)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Activated 🐳\n\nUse commands:\n/addwallet <nickname> <address>\n/removewallet <nickname>\n/listwallets\n/latest <nickname>"
    )

async def addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ws_app
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addwallet <nickname> <address>\nExample: /addwallet Whale 0x123...")
        return
        
    nickname = context.args[0]
    wallet = context.args[1]
    
    wallets = load_wallets()
    if chat_id not in wallets:
        wallets[chat_id] = {}
        
    wallets[chat_id][nickname] = wallet
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
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /removewallet <nickname>")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    
    if chat_id in wallets and nickname in wallets[chat_id]:
        del wallets[chat_id][nickname]
        save_wallets(wallets)
        await update.message.reply_text(f"Wallet '{nickname}' removed.")
    else:
        await update.message.reply_text("Nickname not found.")

async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    wallets = load_wallets()
    
    user_wallets = wallets.get(chat_id, {})
    if not user_wallets:
        await update.message.reply_text("No wallets tracked currently.")
        return
        
    text = "Tracked Wallets:\n\n"
    for name, addr in user_wallets.items():
        text += f"🔹 {name} : {addr}\n"
    await update.message.reply_text(text)

async def latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /latest <nickname>")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    user_wallets = wallets.get(chat_id, {})
    
    if nickname not in user_wallets:
        await update.message.reply_text(f"Nickname '{nickname}' not found in your tracked wallets.")
        return
        
    address = user_wallets[nickname]
    
    try:
        # Fetch recent fills via REST API
        response = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "userFills", "user": address}
        )
        data = response.json()
        
        if not data or len(data) == 0:
            await update.message.reply_text(f"No recent trades found for {nickname}.")
            return
            
        recent_trades = data[:5]
        text = f"🕒 Latest Trades for {nickname}:\n\n"
        
        for trade in recent_trades:
            coin = trade.get("coin")
            price = float(trade.get("px", 0))
            size = float(trade.get("sz", 0))
            direction = trade.get("dir", "Unknown")
            ts = trade.get("time", 0)
            trade_time = datetime.datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
            
            text += f"Token: {coin}\n"
            text += f"Action: {direction}\n"
            text += f"Price: ${price}\n"
            text += f"Size: {size}\n"
            text += f"Time: {trade_time}\n"
            text += "-" * 20 + "\n"
            
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Error fetching latest trades: {e}")

def get_users_tracking_address(address):
    wallets = load_wallets()
    interested_users = []
    if isinstance(wallets, dict):
        for chat_id, user_wallets in wallets.items():
            if isinstance(user_wallets, dict):
                for nickname, tracked_addr in user_wallets.items():
                    if tracked_addr.lower() == address.lower():
                        interested_users.append((chat_id, nickname))
    return interested_users

def on_message(ws, message):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    if data.get("channel") != "userFills":
        return

    payload = data.get("data", {})
    wallet = payload.get("user")
    if not wallet:
        return

    interested_users = get_users_tracking_address(wallet)
    if not interested_users:
        return

    fills = payload.get("fills", [])
    for trade in fills:
        trade_id = trade.get("tid")
        if trade_id in seen_trades:
            continue
        seen_trades.add(trade_id)

        coin = trade.get("coin")
        price = float(trade.get("px"))
        size = float(trade.get("sz"))
        direction = trade.get("dir", "")
        ts = trade.get("time")
        
        trade_time = datetime.datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
        key = f"{wallet.lower()}_{coin}"

        if "Open" in direction:
            open_positions[key] = {
                "price": price,
                "time": trade_time,
                "dir": direction
            }
            
            for chat_id, nickname in interested_users:
                msg = f"🚀 POSITION OPENED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nEntry Price: ${price}\nSize: {size}\nTime: {trade_time}"
                send_message(chat_id, msg)
            
        elif "Close" in direction:
            entry_info = open_positions.get(key)
            if entry_info:
                entry = entry_info["price"]
                entry_time = entry_info["time"]
                
                if "Long" in direction:
                    pnl = ((price - entry) / entry) * 100
                else:
                    pnl = ((entry - price) / entry) * 100
                    
                for chat_id, nickname in interested_users:
                    msg = f"📉 POSITION CLOSED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nEntry: ${entry}\nExit: ${price}\nSize: {size}\nOpened: {entry_time}\nClosed: {trade_time}\nPnL: {round(pnl, 2)}%"
                    send_message(chat_id, msg)
                    
            else:
                for chat_id, nickname in interested_users:
                    msg = f"📉 POSITION CLOSED (Missed Entry)\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nExit: ${price}\nSize: {size}\nTime: {trade_time}"
                    send_message(chat_id, msg)

            if key in open_positions:
                del open_positions[key]

def on_open(ws):
    print("Websocket connected")
    wallets = load_wallets()
    subscribed_addresses = set()
    
    if isinstance(wallets, dict):
        for chat_id, user_wallets in wallets.items():
            if isinstance(user_wallets, dict):
                for addr in user_wallets.values():
                    if addr.lower() not in subscribed_addresses:
                        sub = {
                            "method": "subscribe",
                            "subscription": {
                                "type": "userFills",
                                "user": addr
                            }
                        }
                        ws.send(json.dumps(sub))
                        subscribed_addresses.add(addr.lower())

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
    app.add_handler(CommandHandler("latest", latest))
    
    threading.Thread(target=start_ws, daemon=True).start()
    
    print("Telegram Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    start_bot()
