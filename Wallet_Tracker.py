import json
import datetime
import threading
import requests
import nest_asyncio
import os
import time
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

from keep_alive import keep_alive

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
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

fetch_coin_map()

# In-memory dictionary to store the last known states of users' positions
# Format: { wallet_address: { coin: { "size": float, "entry_price": float, "type": "PERP" or "SPOT" } } }
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

def get_inline_keyboard(chat_id, command_name):
    """Generates an inline keyboard of tracked wallets for the given command."""
    wallets = load_wallets()
    user_wallets = wallets.get(str(chat_id), {})
    if not user_wallets:
        return None
        
    keyboard = []
    # Create a button for each nickname
    for nickname in user_wallets.keys():
        # format: "command:nickname"
        callback_data = f"{command_name}:{nickname}"
        keyboard.append([InlineKeyboardButton(nickname, callback_data=callback_data)])
        
    return InlineKeyboardMarkup(keyboard)

# ---------------- COMMANDS (1-10) ---------------- #

# 1. /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Bot Activated 🐳\nClick the Menu button (/) or type /help to see all 10 available commands!"
    )

# 2. /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📚 **Available Commands** 📚\n\n"
        "1. /start - Activate the bot.\n"
        "2. /help - Show this complete command list.\n"
        "3. /addwallet <nickname> <address> - Start tracking a new trader.\n"
        "4. /removewallet [nickname] - Stop tracking a trader.\n"
        "5. /listwallets - Display all currently tracked whales.\n"
        "6. /open [nickname] - View all live open positions (Crypto, Spot, Metals).\n"
        "7. /recent [nickname] - View the last 3 fully closed trades with PNL.\n"
        "8. /last [nickname] - View only the single most recent closed trade.\n"
        "9. /pnl [nickname] - Determine their total historical realized profit & loss.\n"
        "10. /balance [nickname] - Check their combined account equity.\n\n"
        "💡 *Tip: If you run a command without a nickname, an interactive menu will pop up!*"
    )
    await update.effective_message.reply_text(help_text, parse_mode="Markdown")

# 3. /addwallet
async def addwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /addwallet <nickname> <address>\nExample: /addwallet Whale 0x123...")
        return
        
    nickname = context.args[0]
    wallet = context.args[1]
    
    wallets = load_wallets()
    if chat_id not in wallets:
        wallets[chat_id] = {}
        
    wallets[chat_id][nickname] = wallet
    save_wallets(wallets)
    await update.effective_message.reply_text(f"Wallet added: {nickname} -> {wallet}")

# 4. /removewallet
async def removewallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "removewallet")
        if not markup:
            await update.effective_message.reply_text("You are not tracking any wallets.")
        else:
            await update.effective_message.reply_text("Select a wallet to remove:", reply_markup=markup)
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    
    if chat_id in wallets and nickname in wallets[chat_id]:
        del wallets[chat_id][nickname]
        save_wallets(wallets)
        await update.effective_message.reply_text(f"Wallet '{nickname}' removed.")
    else:
        await update.effective_message.reply_text("Nickname not found.")

# 5. /listwallets
async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    wallets = load_wallets()
    user_wallets = wallets.get(chat_id, {})
    
    if not user_wallets:
        await update.effective_message.reply_text("No wallets tracked currently.")
    else:
        text = "Tracked Wallets:\n\n"
        for name, addr in user_wallets.items():
            text += f"🔹 {name} : {addr}\n"
        await update.effective_message.reply_text(text)

# 6. /open
async def open_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "open")
        if not markup:
            await update.effective_message.reply_text("You are not tracking any wallets.")
        else:
            await update.effective_message.reply_text("Select a wallet to view Live Open Positions:", reply_markup=markup)
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    user_wallets = wallets.get(chat_id, {})
    if nickname not in user_wallets:
        await update.effective_message.reply_text(f"Nickname '{nickname}' not found.")
        return
        
    address = user_wallets[nickname]
    
    try:
        # Fetch Perpetuals Live State
        resp_perp = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "clearinghouseState", "user": address}
        ).json()
        
        # Fetch Spot/Metals Live State
        resp_spot = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "spotClearinghouseState", "user": address}
        ).json()
        
        asset_positions = resp_perp.get("assetPositions", [])
        spot_balances = resp_spot.get("balances", [])
        
        total_found = 0
        text = f"📊 Current Open Positions for {nickname}:\n\n"
        
        # Add Perpetuals
        for pos in asset_positions:
            pos_data = pos.get("position", {})
            coin = pos_data.get("coin")
            szi = float(pos_data.get("szi", 0))
            if szi == 0: continue
            
            total_found += 1
            entry_px = float(pos_data.get("entryPx", 0))
            unrealized_pnl = float(pos_data.get("unrealizedPnl", 0))
            leverage = pos_data.get("leverage", {}).get("value", 1)
            
            direction = "LONG 🟢" if szi > 0 else "SHORT 🔴"
            abs_size = abs(szi)
            pnl_sign = "+" if unrealized_pnl >= 0 else ""
            
            text += f"Token {total_found}: {coin} (Perp)\n"
            text += f"Action: {direction} ({leverage}x)\n"
            text += f"Avg Entry: ${round(entry_px, 4)}\n"
            text += f"Full Position Size: {round(abs_size, 6)}\n"
            text += f"Unrealized PNL: {pnl_sign}${round(unrealized_pnl, 2)}\n"
            text += "-" * 20 + "\n"
            
        # Add Spot / Metals (Gold, Silver, etc.)
        for bal in spot_balances:
            coin = bal.get("coin")
            total = float(bal.get("total", 0))
            # Ignore USDC and Dust
            if total > 1e-6 and coin != "USDC":
                total_found += 1
                text += f"Asset {total_found}: {coin} (Spot/Metal)\n"
                text += f"Action: HOLDING 🟡\n"
                text += f"Total Balance Held: {round(total, 6)}\n"
                text += "-" * 20 + "\n"
        
        if total_found == 0:
            await update.effective_message.reply_text(f"📊 {nickname} currently holds a flat portfolio (No open positions or assets).")
            return
            
        await update.effective_message.reply_text(text)
    except Exception as e:
        await update.effective_message.reply_text(f"Error fetching open positions: {e}")

# Helper: Parse historical userFills into fully closed trade cycles
def parse_historical_closed_trades(address):
    response = requests.post(
        "https://api.hyperliquid.xyz/info",
        headers={"Content-Type": "application/json"},
        json={"type": "userFills", "user": address}
    )
    fills = response.json()
    if not isinstance(fills, list) or len(fills) == 0:
        return []
        
    # Process from oldest to newest to reconstruct history
    fills.reverse()
    
    positions = {}
    closed_trades = []
    
    for fill in fills:
        coin = fill.get("coin")
        if not coin: continue
            
        sz = float(fill.get("sz", 0))
        px = float(fill.get("px", 0))
        fee = float(fill.get("fee", 0))
        closed_pnl = float(fill.get("closedPnl", 0))
        side = fill.get("side", "A")
        
        dir_mult = 1 if side == "B" else -1
        fill_sz = sz * dir_mult
        
        if coin not in positions:
            positions[coin] = {'sz': 0.0, 'open_time': 0, 'entry_qty': 0.0, 'entry_val': 0.0, 'exit_qty': 0.0, 'exit_val': 0.0, 'pnl': 0.0, 'is_long': None}
            
        state = positions[coin]
        curr_sz = state['sz']
        
        # Detect if we are opening/adding vs closing
        is_opening = False
        if abs(curr_sz) < 1e-8:
            is_opening = True
            state['open_time'] = fill.get('time', 0)
            state['is_long'] = True if dir_mult > 0 else False
        elif (curr_sz > 0 and dir_mult > 0) or (curr_sz < 0 and dir_mult < 0):
            is_opening = True
            
        if is_opening:
            state['entry_qty'] += sz
            state['entry_val'] += sz * px
            state['pnl'] -= fee
        else:
            state['exit_qty'] += sz
            state['exit_val'] += sz * px
            state['pnl'] += (closed_pnl - fee)
            
        state['sz'] += fill_sz
        
        # If position fully closed
        if abs(state['sz']) < 1e-8 and state['entry_qty'] > 0:
            avg_entry = state['entry_val'] / state['entry_qty']
            avg_exit = state['exit_val'] / state['exit_qty'] if state['exit_qty'] else avg_entry
            roi = (state['pnl'] / state['entry_val']) * 100 if state['entry_val'] > 0 else 0
            
            closed_trades.append({
                'coin': coin,
                'direction': 'LONG' if state['is_long'] else 'SHORT',
                'size': state['entry_qty'],
                'avg_entry': avg_entry,
                'avg_exit': avg_exit,
                'pnl': state['pnl'],
                'roi': roi,
                'open_time': state['open_time'],
                'close_time': fill.get('time', 0)
            })
            
            # Reset state for next cycle
            positions[coin] = {'sz': 0.0, 'open_time': 0, 'entry_qty': 0.0, 'entry_val': 0.0, 'exit_qty': 0.0, 'exit_val': 0.0, 'pnl': 0.0, 'is_long': None}
            
    # Newest closed trades last
    return closed_trades

# 7. /recent
async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "recent")
        if markup:
            await update.effective_message.reply_text("Select a wallet to view the last 3 closed trades:", reply_markup=markup)
        else:
            await update.effective_message.reply_text("You are not tracking any wallets.")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    user_wallets = wallets.get(chat_id, {})
    if nickname not in user_wallets: return
    address = user_wallets[nickname]
    
    try:
        closed_trades = parse_historical_closed_trades(address)
        recent_trades = closed_trades[-3:] # Last 3
        
        if not recent_trades:
            await update.effective_message.reply_text(f"No fully closed trades found in history for {nickname}.")
            return
            
        recent_trades.reverse() # Show newest of the 3 first
        text = f"🕒 Last 3 Closed Trades for {nickname}:\n\n"
        
        for trade in recent_trades:
            otime = datetime.datetime.fromtimestamp(trade['open_time'] / 1000).strftime('%Y-%m-%d %H:%M')
            ctime = datetime.datetime.fromtimestamp(trade['close_time'] / 1000).strftime('%Y-%m-%d %H:%M')
            pnl_sign = "+" if trade['pnl'] >= 0 else ""
            
            text += f"Token: {trade['coin']}\n"
            text += f"Action: {trade['direction']} CYCLE\n"
            text += f"Full Position Size: {round(trade['size'], 6)}\n"
            text += f"Avg Buy/Entry: ${round(trade['avg_entry'], 4)}\n"
            text += f"Avg Sell/Exit: ${round(trade['avg_exit'], 4)}\n"
            text += f"Total PNL: {pnl_sign}${round(trade['pnl'], 2)} ({pnl_sign}{round(trade['roi'], 2)}%)\n"
            text += f"Opened: {otime}\nClosed: {ctime}\n"
            text += "-" * 20 + "\n"
            
        await update.effective_message.reply_text(text)
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")

# 8. /last
async def last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "last")
        if markup:
            await update.effective_message.reply_text("Select a wallet to view the single most recent closed trade:", reply_markup=markup)
        else:
            await update.effective_message.reply_text("You are not tracking any wallets.")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    user_wallets = wallets.get(chat_id, {})
    if nickname not in user_wallets: return
    address = user_wallets[nickname]
    
    try:
        closed_trades = parse_historical_closed_trades(address)
        if not closed_trades:
            await update.effective_message.reply_text(f"No fully closed trades found in history for {nickname}.")
            return
            
        trade = closed_trades[-1]
        otime = datetime.datetime.fromtimestamp(trade['open_time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
        ctime = datetime.datetime.fromtimestamp(trade['close_time'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
        pnl_sign = "+" if trade['pnl'] >= 0 else ""
        
        text = f"🥇 Last Fully Closed Trade for {nickname}:\n\n"
        text += f"Token: {trade['coin']}\n"
        text += f"Action: {trade['direction']} CYCLE\n"
        text += f"Full Position Size: {round(trade['size'], 6)}\n"
        text += f"Avg Buy/Entry: ${round(trade['avg_entry'], 4)}\n"
        text += f"Avg Sell/Exit: ${round(trade['avg_exit'], 4)}\n"
        text += f"Total PNL: {pnl_sign}${round(trade['pnl'], 2)} ({pnl_sign}{round(trade['roi'], 2)}%)\n"
        text += f"Opened: {otime}\nClosed: {ctime}\n"
        
        await update.effective_message.reply_text(text)
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")

# 9. /pnl
async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "pnl")
        if markup:
            await update.effective_message.reply_text("Select a wallet to view historical PNL:", reply_markup=markup)
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    address = wallets.get(chat_id, {}).get(nickname)
    if not address: return
    
    try:
        closed_trades = parse_historical_closed_trades(address)
        total_pnl = sum(t['pnl'] for t in closed_trades)
        winning = sum(1 for t in closed_trades if t['pnl'] > 0)
        losing = sum(1 for t in closed_trades if t['pnl'] <= 0)
        
        pnl_sign = "+" if total_pnl >= 0 else ""
        text = f"💰 **Historical Target Output for {nickname}** 💰\n\n"
        text += f"Total Realized PNL: {pnl_sign}${round(total_pnl, 2)}\n"
        text += f"Completed Trade Cycles: {len(closed_trades)}\n"
        text += f"Win/Loss Ratio: {winning}W / {losing}L"
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")

# 10. /balance
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "balance")
        if markup:
            await update.effective_message.reply_text("Select a wallet to view total account equity:", reply_markup=markup)
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    address = wallets.get(chat_id, {}).get(nickname)
    if not address: return
    
    try:
        req = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "clearinghouseState", "user": address}
        ).json()
        
        equity = float(req.get("marginSummary", {}).get("accountValue", 0))
        await update.effective_message.reply_text(f"🏦 Custom Account Equity Profile:\n\n**Trader:** {nickname}\n**Current Value:** ${round(equity, 2)}", parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"Error fetching balance: {e}")


# ---------------- INLINE KEYBOARD DISPATCHER ---------------- #

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Ack the click
    data = query.data
    command, nickname = data.split(':', 1)
    
    # Inject nickname so functions work normally
    context.args = [nickname]
    
    if command == "open":
        await open_command(update, context)
    elif command == "removewallet":
        await removewallet(update, context)
    elif command == "recent":
        await recent(update, context)
    elif command == "last":
        await last(update, context)
    elif command == "pnl":
        await pnl_command(update, context)
    elif command == "balance":
        await balance_command(update, context)

# ---------------- POLLING (LIVE ALERTS) ---------------- #

def poll_positions():
    """
    Sleeps 10 seconds, then queries Perpetuals + Spot clearinghouseState to natively calculate global portfolio differences.
    """
    while True:
        time.sleep(10)
        
        wallets = load_wallets()
        unique_addresses = set()
        
        if isinstance(wallets, dict):
            for user_wallets in wallets.values():
                if isinstance(user_wallets, dict):
                    for addr in user_wallets.values():
                        unique_addresses.add(addr.lower())
                        
        for address in unique_addresses:
            interested_users = get_users_tracking_address(address)
            if not interested_users: continue
                
            try:
                # 1. Fetch Perpetuals
                resp_perp = requests.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": address}).json()
                
                # 2. Fetch Spot/Metals
                resp_spot = requests.post("https://api.hyperliquid.xyz/info", json={"type": "spotClearinghouseState", "user": address}).json()
                
                current_state = {}
                
                # Parse Perps
                if "assetPositions" in resp_perp:
                    for pos in resp_perp.get("assetPositions", []):
                        p = pos.get("position", {})
                        coin = p.get("coin")
                        szi = float(p.get("szi", 0))
                        entry_px = float(p.get("entryPx", 0))
                        if szi != 0 and coin:
                            current_state[f"PERP_{coin}"] = {"size": szi, "price": entry_px, "type": "PERP", "coin": coin}
                
                # Parse Spot
                if "balances" in resp_spot:
                    for bal in resp_spot.get("balances", []):
                        coin = bal.get("coin")
                        total = float(bal.get("total", 0))
                        if total > 1e-6 and coin != "USDC":
                            current_state[f"SPOT_{coin}"] = {"size": total, "price": 0, "type": "SPOT", "coin": coin}
                
                # Compare vs known_positions to find changes
                if address not in known_positions:
                    known_positions[address] = current_state
                    continue # Skip initial trigger spam when bot first touches a wallet
                    
                prev_state_map = known_positions[address]
                
                # Look for OPEN or UPDATE
                for key, data in current_state.items():
                    coin = data["coin"]
                    szi = data["size"]
                    p_type = data["type"]
                    
                    if key not in prev_state_map:
                        # NEW POSITION!
                        for chat_id, nickname in interested_users:
                            if p_type == "PERP":
                                direction = "LONG" if szi > 0 else "SHORT"
                                msg = f"🚀 POSITION OPENED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nAvg Entry: ${round(data['price'], 4)}\nTotal Position Size: {abs(szi)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            else:
                                msg = f"🪙 SPOT ASSET PURCHASED\nTrader: {nickname}\nToken: {coin} (Spot/Metal)\nTotal Balance: {round(abs(szi), 6)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            send_message(chat_id, msg)
                    
                    elif prev_state_map[key]["size"] != szi:
                        # POSITION SIZE UPDATED!
                        old_size = prev_state_map[key]["size"]
                        
                        if p_type == "PERP":
                            if (old_size > 0 and szi > old_size) or (old_size < 0 and szi < old_size):
                                action = "ADDED TO POSITION"
                            else:
                                action = "PARTIALLY CLOSED"
                            direction = "LONG" if szi > 0 else "SHORT"
                            
                            for chat_id, nickname in interested_users:
                                msg = f"⚖️ POSITION UPDATED\nTrader: {nickname}\nToken: {coin}\nAction: {action} ({direction})\nNew Avg Entry: ${round(data['price'], 4)}\nNew Absolute Size: {abs(szi)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                                send_message(chat_id, msg)
                        else:
                            # SPOT CHANGE
                            action = "ADDED HOLDINGS" if szi > old_size else "REDUCED HOLDINGS"
                            for chat_id, nickname in interested_users:
                                msg = f"⚖️ SPOT ASSET UPDATED\nTrader: {nickname}\nToken: {coin} (Spot/Metal)\nAction: {action}\nNew Balance: {round(abs(szi), 6)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                                send_message(chat_id, msg)
                                
                # Look for FULL EXITS (Keys that existed previously but are now 0 or missing)
                for key, old_data in prev_state_map.items():
                    if key not in current_state:
                         coin = old_data["coin"]
                         old_szi = old_data["size"]
                         p_type = old_data["type"]
                         
                         for chat_id, nickname in interested_users:
                            if p_type == "PERP":
                                direction = "LONG" if old_szi > 0 else "SHORT"
                                msg = f"📉 POSITION FULLY CLOSED\nTrader: {nickname}\nToken: {coin}\nAction: {direction}\nTotal Size Closed: {abs(old_szi)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            else:
                                msg = f"🛑 SPOT ASSET FULLY SOLD\nTrader: {nickname}\nToken: {coin} (Spot/Metal)\nAmount Sold: {abs(old_szi)}\nTime: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            send_message(chat_id, msg)
                            
                known_positions[address] = current_state
                
            except Exception as e:
                print(f"Polling error for {address}: {e}")

# ---------------- BOT INIT ---------------- #

async def post_init(application):
    # Set the Telegram commands menu!
    commands = [
        ("start", "Activate the bot"),
        ("help", "List all available commands"),
        ("addwallet", "Add a new wallet to track"),
        ("removewallet", "Remove a tracked wallet"),
        ("listwallets", "Show all tracked wallets"),
        ("open", "Show live open positions (Spot & Perp)"),
        ("recent", "Show last 3 fully closed trades"),
        ("last", "Show the most recent fully closed trade"),
        ("pnl", "Show total realized profit & loss"),
        ("balance", "Show combined account equity")
    ]
    await application.bot.set_my_commands(commands)
    print("Telegram commands menu registered!")

def start_bot():
    if not BOT_TOKEN: return
        
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addwallet", addwallet))
    app.add_handler(CommandHandler("removewallet", removewallet))
    app.add_handler(CommandHandler("listwallets", listwallets))
    app.add_handler(CommandHandler("open", open_command))
    app.add_handler(CommandHandler("recent", recent))
    app.add_handler(CommandHandler("last", last))
    app.add_handler(CommandHandler("pnl", pnl_command))
    app.add_handler(CommandHandler("balance", balance_command))
    
    # Register the Callback Query Handler for Inline Buttons
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Start Polling
    threading.Thread(target=poll_positions, daemon=True).start()
    
    print("Telegram Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    start_bot()