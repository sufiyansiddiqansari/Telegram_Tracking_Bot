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

# Global dictionary to buffer pending partial fills
# Format: { order_key: {"total_size": float, "total_value": float, "nickname": str, "coin": str, "dir": str, "trade_time": str, "timer": threading.Timer} }
pending_orders = {}
order_lock = threading.Lock()

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
        "Bot Activated 🐳\n\nUse commands:\n/addwallet <nickname> <address>\n/removewallet <nickname>\n/listwallets\n/recent <nickname>"
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

async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /recent <nickname>")
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
            
        # Group historical trades by Order ID (oid) so we get total positional sizes
        aggregated_history = {}
        for trade in data:
            oid = trade.get("oid")
            if not oid: 
                continue
                
            if oid not in aggregated_history:
                aggregated_history[oid] = []
            aggregated_history[oid].append(trade)
            
        # We only want the last 3 unique completed orders
        # The REST API returns chronological order, but we can verify by sorting
        sorted_oids = sorted(aggregated_history.keys(), key=lambda o: aggregated_history[o][0].get("time", 0), reverse=True)
        recent_oids = sorted_oids[:3]
        
        if not recent_oids:
            await update.message.reply_text(f"No recent aggregated trades found for {nickname}.")
            return

        text = f"🕒 Last 3 Complete Trades for {nickname}:\n\n"
        
        for oid in recent_oids:
            fills = aggregated_history[oid]
            coin = fills[0].get("coin", "Unknown")
            direction = fills[0].get("dir", "Unknown")
            ts = fills[0].get("time", 0)
            trade_time = datetime.datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
            
            # Calculate total average fill metrics for the single Order ID
            total_sz = 0.0
            total_val = 0.0
            for f in fills:
                sz = float(f.get("sz", 0))
                px = float(f.get("px", 0))
                total_sz += sz
                total_val += (sz * px)
                
            avg_px = total_val / total_sz if total_sz > 0 else 0
            
            text += f"Token: {coin}\n"
            text += f"Action: {direction}\n"
            text += f"Avg Price: ${round(avg_px, 4)}\n"
            text += f"Total Size: {round(total_sz, 6)}\n"
            text += f"Time: {trade_time}\n"
            text += "-" * 20 + "\n"
            
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Error fetching recent trades: {e}")

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

def process_aggregated_order(order_key, interested_users, wallet):
    with order_lock:
        if order_key not in pending_orders:
            return
            
        order_data = pending_orders.pop(order_key)
        
    total_size = order_data["total_size"]
    total_value = order_data["total_value"]
    avg_price = total_value / total_size if total_size > 0 else 0
    
    coin = order_data["coin"]
    direction = order_data["dir"]
    trade_time = order_data["trade_time"]
    
    pos_key = f"{wallet.lower()}_{coin}"
    
    if "Open" in direction:
        # Check if they are adding to an existing position
        if pos_key in open_positions:
            prev_price = open_positions[pos_key]["price"]
            prev_size = open_positions[pos_key].get("size", 0) # Fallback to 0 if not previously tracked
            
            # Very loose approximation of new average entry for display purposes
            new_total_size = prev_size + total_size
            if new_total_size > 0:
                new_avg = ((prev_price * prev_size) + (avg_price * total_size)) / new_total_size
            else:
                new_avg = avg_price
                
            open_positions[pos_key] = {
                "price": new_avg,
                "size": new_total_size,
                "time": trade_time,
                "dir": direction
            }
        else:
            open_positions[pos_key] = {
                "price": avg_price,
                "size": total_size,
                "time": trade_time,
                "dir": direction
            }
        
        for chat_id, nickname in interested_users:
            msg = f"🚀 POSITION OPENED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nAvg Entry: ${round(avg_price, 4)}\nTotal Size Issued: {round(total_size, 6)}\nTime: {trade_time}"
            send_message(chat_id, msg)
            
    elif "Close" in direction:
        entry_info = open_positions.get(pos_key)
        if entry_info:
            entry = entry_info["price"]
            entry_time = entry_info["time"]
            
            if "Long" in direction:
                pnl = ((avg_price - entry) / entry) * 100
            else:
                pnl = ((entry - avg_price) / entry) * 100
                
            for chat_id, nickname in interested_users:
                msg = f"📉 POSITION CLOSED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nEntry: ${round(entry, 4)}\nAvg Exit: ${round(avg_price, 4)}\nTotal Size Issued: {round(total_size, 6)}\nOpened: {entry_time}\nClosed: {trade_time}\nPnL: {round(pnl, 2)}%"
                send_message(chat_id, msg)
                
        else:
            for chat_id, nickname in interested_users:
                msg = f"📉 POSITION CLOSED (Missed Entry)\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nAvg Exit: ${round(avg_price, 4)}\nTotal Size Issued: {round(total_size, 6)}\nTime: {trade_time}"
                send_message(chat_id, msg)

        # For simplicity, we assume a "Close" signal indicates they've closed the position entirely.
        if pos_key in open_positions:
            del open_positions[pos_key]

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
        oid = trade.get("oid") # Order ID
        
        trade_time = datetime.datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M:%S')
        
        if not oid:
            # Fallback if no order ID is present (unlikely on Hyperliquid)
            oid = str(ts)
            
        order_key = f"{wallet.lower()}_{oid}"
        
        with order_lock:
            if order_key in pending_orders:
                # Add to existing pending order aggregation
                pending_orders[order_key]["total_size"] += size
                pending_orders[order_key]["total_value"] += (size * price)
            else:
                # Initialize new order buffer
                pending_orders[order_key] = {
                    "total_size": size,
                    "total_value": size * price,
                    "coin": coin,
                    "dir": direction,
                    "trade_time": trade_time,
                }
                
                # Create a 2.0 second timer buffer that will fire 'process_aggregated_order'
                timer = threading.Timer(
                    2.0, 
                    process_aggregated_order, 
                    args=(order_key, interested_users, wallet)
                )
                pending_orders[order_key]["timer"] = timer
                timer.start()

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
    app.add_handler(CommandHandler("recent", recent))
    
    threading.Thread(target=start_ws, daemon=True).start()
    
    print("Telegram Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    start_bot()