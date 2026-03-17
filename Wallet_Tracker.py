import json
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

# To map Hyperliquid's asset indices to actual coin names (e.g. BTC, ETH)
COIN_MAP = []

def fetch_coin_map():
    global COIN_MAP
    try:
        response = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "meta"}
        )
        data = response.json()
        if "universe" in data:
            COIN_MAP = [asset["name"] for asset in data["universe"]]
            print(f"Loaded {len(COIN_MAP)} assets from Hyperliquid.")
    except Exception as e:
        print(f"Error fetching coin map: {e}")

# Call this once on startup
fetch_coin_map()

# In-memory dictionary to store the last known states of users' positions
# Used to detect actual position changes rather than partial order fills
# Format: { wallet_address: { coin: { "size": float, "entry_price": float } } }
known_positions = {}

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
        "Bot Activated 🐳\n\nUse commands:\n/addwallet <nickname> <address>\n/removewallet <nickname>\n/listwallets\n/open <nickname>"
    )

async def addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def open_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /open <nickname>")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    user_wallets = wallets.get(chat_id, {})
    
    if nickname not in user_wallets:
        await update.message.reply_text(f"Nickname '{nickname}' not found in your tracked wallets.")
        return
        
    address = user_wallets[nickname]
    
    try:
        # Fetch actual live current positions using clearinghouseState
        response = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "clearinghouseState", "user": address}
        )
        data = response.json()
        
        asset_positions = data.get("assetPositions", [])
        
        if not asset_positions:
            await update.message.reply_text(f"📊 {nickname} currently has no open positions.")
            return

        text = f"📊 Current Open Positions for {nickname}:\n\n"
        
        for pos in asset_positions:
            pos_data = pos.get("position", {})
            coin = pos_data.get("coin")
            
            # If coin string is not provided directly, try to map from universe index
            if not coin and "item" in pos_data and len(COIN_MAP) > 0:
                 # Hyperliquid changed structure slightly over time; sometimes it's 'coin', sometimes it needs mapped if missing
                 # However, 'coin' is usually explicitly inside 'position' in latest API
                 pass 

            szi = float(pos_data.get("szi", 0))
            if szi == 0:
                continue
                
            entry_px = float(pos_data.get("entryPx", 0))
            unrealized_pnl = float(pos_data.get("unrealizedPnl", 0))
            leverage = pos_data.get("leverage", {}).get("value", 1)
            
            direction = "LONG 🟢" if szi > 0 else "SHORT 🔴"
            abs_size = abs(szi)
            pnl_sign = "+" if unrealized_pnl >= 0 else ""
            
            text += f"Token: {coin}\n"
            text += f"Action: {direction} ({leverage}x)\n"
            text += f"Avg Entry: ${round(entry_px, 4)}\n"
            text += f"Full Position Size: {round(abs_size, 6)}\n"
            text += f"Unrealized PNL: {pnl_sign}${round(unrealized_pnl, 2)}\n"
            text += "-" * 20 + "\n"
            
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Error fetching open positions: {e}")

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

def poll_positions():
    """
    Instead of using the userFills websocket (which sends partial order chunks),
    we simply check every trader's actual portfolio state every 10 seconds.
    If the absolute position size or coin changes, we send an alert with the complete size!
    """
    while True:
        time.sleep(10) # Check every 10 seconds
        
        wallets = load_wallets()
        unique_addresses = set()
        
        if isinstance(wallets, dict):
            for user_wallets in wallets.values():
                if isinstance(user_wallets, dict):
                    for addr in user_wallets.values():
                        unique_addresses.add(addr.lower())
                        
        for address in unique_addresses:
            interested_users = get_users_tracking_address(address)
            if not interested_users:
                continue
                
            try:
                response = requests.post(
                    "https://api.hyperliquid.xyz/info",
                    headers={"Content-Type": "application/json"},
                    json={"type": "clearinghouseState", "user": address}
                )
                data = response.json()
                asset_positions = data.get("assetPositions", [])
                
                if address not in known_positions:
                    known_positions[address] = {}
                    
                current_state = {}
                
                # Parse the live state
                for pos in asset_positions:
                    pos_data = pos.get("position", {})
                    coin = pos_data.get("coin")
                    if not coin:
                        continue
                        
                    szi = float(pos_data.get("szi", 0))
                    if szi == 0:
                        continue
                        
                    entry_px = float(pos_data.get("entryPx", 0))
                    current_state[coin] = {
                        "size": szi,
                        "entry_price": entry_px
                    }
                    
                    prev_state = known_positions[address].get(coin)
                    
                    if not prev_state:
                         # Brand new position opened
                         direction = "LONG" if szi > 0 else "SHORT"
                         
                         for chat_id, nickname in interested_users:
                            msg = f"🚀 POSITION OPENED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nAvg Entry: ${round(entry_px, 4)}\nTotal Position Size: {abs(szi)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            send_message(chat_id, msg)
                            
                    elif prev_state["size"] != szi:
                         # Position size changed indicating an add or partial close, but we ONLY report the total new absolute size
                         old_size = prev_state["size"]
                         
                         # Determine logic
                         if (old_size > 0 and szi > old_size) or (old_size < 0 and szi < old_size):
                             action_text = "ADDED TO POSITION"
                         else:
                             action_text = "PARTIALLY CLOSED"
                             
                         direction = "LONG" if szi > 0 else "SHORT"
                         
                         for chat_id, nickname in interested_users:
                            msg = f"⚖️ POSITION UPDATED\nTrader: {nickname}\nToken: {coin}\nAction: {action_text} ({direction})\nNew Avg Entry: ${round(entry_px, 4)}\nTotal Position Size: {abs(szi)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            send_message(chat_id, msg)
                
                # Check for fully closed positions
                for coin in list(known_positions[address].keys()):
                    if coin not in current_state:
                         # It was fully closed
                         old_state = known_positions[address][coin]
                         direction = "LONG" if old_state["size"] > 0 else "SHORT"
                         
                         for chat_id, nickname in interested_users:
                            msg = f"📉 POSITION FULLY CLOSED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nTotal Size Closed: {abs(old_state['size'])}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            send_message(chat_id, msg)
                            
                # Update our known state memory
                known_positions[address] = current_state
                
            except Exception as e:
                print(f"Polling error for {address}: {e}")

def start_bot():
    if not BOT_TOKEN:
        print("Cannot start bot without BOT_TOKEN.")
        return
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addwallet", addwallet))
    app.add_handler(CommandHandler("removewallet", removewallet))
    app.add_handler(CommandHandler("listwallets", listwallets))
    app.add_handler(CommandHandler("open", open_command))
    
    # Start Portfolio Polling instead of WebSocket to avoid partial-fill spam entirely
    threading.Thread(target=poll_positions, daemon=True).start()
    
    print("Telegram Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    start_bot()