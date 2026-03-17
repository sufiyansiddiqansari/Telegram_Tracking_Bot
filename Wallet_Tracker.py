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
        "Bot Activated 🐳\nClick the Menu button (/) or type /help to see all 13 available commands!"
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
        "6. /open  - View all live open positions (Crypto, Spot, Metals).\n"
        "7. /recent - View the last 3 fully closed trades with PNL.\n"
        "8. /last - View only the single most recent closed trade.\n"
        "9. /pnl - Determine their total historical realized profit & loss.\n"
        "10. /balance - Check their combined account equity.\n"
        "11. /metrics - View advanced statistics (Win Rate, Drawdown).\n"
        "12. /toptraders - Discover top hyperliquid wallets to copy.\n"
        "13. /market - View 24h market volatility for top 10 tokens.\n\n"
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
        return

    # Inform the user before performing potentially long queries
    await update.effective_message.reply_text(f"Fetching 7-day performance data for {len(user_wallets)} wallets. Please wait...")
    
    stats_list = []
    for name, addr in user_wallets.items():
        try:
            pnl_7d, roi_7d = get_7d_stats(addr)
            stats_list.append({
                'name': name,
                'address': addr,
                'pnl': pnl_7d,
                'roi': roi_7d
            })
        except Exception as e:
            print(f"Error fetching stats for {name}: {e}")
            stats_list.append({
                'name': name,
                'address': addr,
                'pnl': 0.0,
                'roi': 0.0
            })
            
    # Sort by ROI descending
    stats_list.sort(key=lambda x: x['roi'], reverse=True)
    
    text = f"📊 **Tracked Wallets Leaderboard ({len(user_wallets)} Total)** 📊\n\n"
    for idx, stat in enumerate(stats_list, 1):
        pnl_str = f"+${round(stat['pnl'], 2):,}" if stat['pnl'] >= 0 else f"-${abs(round(stat['pnl'], 2)):,}"
        roi_str = f"+{round(stat['roi'], 2)}%" if stat['roi'] >= 0 else f"{round(stat['roi'], 2)}%"
        
        entry_str = f"**#{idx} {stat['name']}**\n"
        entry_str += f"`{stat['address']}`\n"
        entry_str += f"7D PnL: {pnl_str} | 7D ROI: {roi_str}\n"
        entry_str += "-" * 20 + "\n"
        
        # Telegram max message size is 4096 chars, gracefully split if too large
        if len(text) + len(entry_str) > 4000:
            await update.effective_message.reply_text(text, parse_mode="Markdown")
            text = entry_str
        else:
            text += entry_str
            
    if text:
        await update.effective_message.reply_text(text, parse_mode="Markdown")

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
                'entry_val': state['entry_val'],
                'open_time': state['open_time'],
                'close_time': fill.get('time', 0)
            })
            
            # Reset state for next cycle
            positions[coin] = {'sz': 0.0, 'open_time': 0, 'entry_qty': 0.0, 'entry_val': 0.0, 'exit_qty': 0.0, 'exit_val': 0.0, 'pnl': 0.0, 'is_long': None}
            
    # Newest closed trades last
    return closed_trades

# Helper: Fetch 7-day stats for leaderboard
def get_7d_stats(address):
    try:
        response = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "userFills", "user": address}
        )
        fills = response.json()
        if not isinstance(fills, list) or len(fills) == 0:
            fills = []
        else:
            fills.reverse()
    except Exception:
        fills = []
        
    now_ms = int(time.time() * 1000)
    seven_days_ms = 7 * 24 * 60 * 60 * 1000
    
    positions = {}
    total_pnl_7d = 0.0
    total_entry_val_7d = 0.0

    for fill in fills:
        coin = fill.get("coin")
        if not coin: continue

        sz = float(fill.get("sz", 0))
        px = float(fill.get("px", 0))
        fee = float(fill.get("fee", 0))
        closed_pnl = float(fill.get("closedPnl", 0))
        side = fill.get("side", "A")
        fill_time = fill.get("time", 0)

        dir_mult = 1 if side == "B" else -1
        fill_sz = sz * dir_mult

        if coin not in positions:
            positions[coin] = {'sz': 0.0}

        curr_sz = positions[coin]['sz']

        is_closing = False
        if (curr_sz > 0 and dir_mult < 0) or (curr_sz < 0 and dir_mult > 0):
            is_closing = True

        positions[coin]['sz'] += fill_sz

        if now_ms - fill_time <= seven_days_ms:
            total_pnl_7d += (closed_pnl - fee)
            if is_closing:
                closing_val = sz * px
                if dir_mult < 0: # Selling to close LONG
                    entry_val = closing_val - closed_pnl
                else: # Buying to close SHORT
                    entry_val = closing_val + closed_pnl
                
                total_entry_val_7d += entry_val

    # Fetch unrealized PnL from open positions
    try:
        resp_perp = requests.post(
            "https://api.hyperliquid.xyz/info",
            headers={"Content-Type": "application/json"},
            json={"type": "clearinghouseState", "user": address}
        ).json()
        
        asset_positions = resp_perp.get("assetPositions", [])
        for pos in asset_positions:
            pos_data = pos.get("position", {})
            szi = float(pos_data.get("szi", 0))
            if szi == 0: continue
            
            unrealized_pnl = float(pos_data.get("unrealizedPnl", 0))
            position_value = float(pos_data.get("positionValue", 0))
            
            total_pnl_7d += unrealized_pnl
            total_entry_val_7d += position_value
    except Exception as e:
        print(f"Error fetching open positions for {address}: {e}")

    roi = (total_pnl_7d / total_entry_val_7d * 100) if total_entry_val_7d > 0 else 0.0
    return total_pnl_7d, roi

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


# 11. /metrics
async def metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if len(context.args) < 1:
        markup = get_inline_keyboard(chat_id, "metrics")
        if markup:
            await update.effective_message.reply_text("Select a wallet to view advanced metrics:", reply_markup=markup)
        else:
            await update.effective_message.reply_text("You are not tracking any wallets.")
        return
        
    nickname = context.args[0]
    wallets = load_wallets()
    address = wallets.get(chat_id, {}).get(nickname)
    if not address: return
    
    try:
        closed_trades = parse_historical_closed_trades(address)
        if not closed_trades:
            await update.effective_message.reply_text(f"No fully closed trades found in history for {nickname}.")
            return
            
        winning_trades = [t for t in closed_trades if t['pnl'] > 0]
        losing_trades = [t for t in closed_trades if t['pnl'] <= 0]
        
        win_rate = (len(winning_trades) / len(closed_trades)) * 100
        
        avg_profit = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = abs(sum(t['pnl'] for t in losing_trades) / len(losing_trades)) if losing_trades else 0
        rr_ratio = (avg_profit / avg_loss) if avg_loss > 0 else float('inf')
        
        # Calculate max drawdown
        cumulative_pnl = 0
        peak = 0
        max_drawdown = 0
        
        for t in closed_trades: # Already essentially chronological from parse_historical_closed_trades conceptually (Wait, actually they were returned oldest to newest? No, the parsing appends as it scans so it's oldest to newest. Wait, parse_historical_closed_trades returns newest LAST. So it IS chronological!)
            cumulative_pnl += t['pnl']
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                
        # Trades per day
        first_trade_ts = min(t['open_time'] for t in closed_trades)
        last_trade_ts = max(t['close_time'] for t in closed_trades)
        days = (last_trade_ts - first_trade_ts) / (1000 * 60 * 60 * 24)
        if days < 1: days = 1
        trades_per_day = len(closed_trades) / days
        
        text = f"📊 **Advanced Metrics for {nickname}** 📊\n\n"
        text += f"Daily Trade Frequency: {round(trades_per_day, 2)} trades/day\n"
        text += f"Win Rate: {round(win_rate, 2)}%\n"
        if rr_ratio != float('inf'):
            text += f"Risk/Reward Ratio: {round(rr_ratio, 2)}\n"
        else:
            text += f"Risk/Reward Ratio: Perfect (No Losses)\n"
        text += f"Max Drawdown: -${round(max_drawdown, 2)}\n"
        
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"Error fetching metrics: {e}")

# 12. /toptraders
async def toptraders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        url = "https://api.hyperdash.com/graphql"
        payload = {
            "operationName": "ExploreTraders",
            "variables": {
                "page": 1,
                "pageSize": 10,
                "timeframe": "thirty_days",
                "sortBy": {"field": "pnl", "order": "desc"}
            },
            "query": """
            query ExploreTraders($page: Int, $pageSize: Int, $timeframe: TraderTimeframe!, $sortBy: TraderSortInput) {
              exploreTraders(page: $page, pageSize: $pageSize, timeframe: $timeframe, sortBy: $sortBy) {
                data {
                  address
                  displayName
                  pnl
                  winrate
                  sharpe
                  drawdown
                }
              }
            }
            """
        }
        res = requests.post(url, json=payload).json()
        top_traders = res['data']['exploreTraders']['data']
        
        text = "🏆 **Global Top 10 Traders (Last 30 Days)** 🏆\n\n"
        keyboard = []
        
        for idx, t in enumerate(top_traders, 1):
            addr = t.get('address', '')
            name = t.get('displayName') or f"{addr[:6]}...{addr[-4:]}"
            
            pnl = float(t.get('pnl') or 0)
            winrate = float(t.get('winrate') or 0) * 100
            sharpe = float(t.get('sharpe') or 0)
            
            pnl_str = f"+${round(pnl, 2):,}" if pnl > 0 else f"-${abs(round(pnl, 2)):,}"
            hd_link = f"https://hyperdash.com/address/{addr}"
            
            text += f"{idx}. **{name}**\n"
            text += f"💰 30D PNL: {pnl_str} | Win Rate: {round(winrate, 1)}%\n"
            text += f"🔗 [View Hyperdash Analytics]({hd_link})\n\n"
            
            # The callback data is compressed to 'add:<address>' to fit Telegram's 64 byte limit
            keyboard.append([InlineKeyboardButton(f"Track {name}", callback_data=f"add:{addr}")])
            
        markup = InlineKeyboardMarkup(keyboard)
        await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)
    except Exception as e:
        await update.effective_message.reply_text(f"Error fetching Top Traders: {e}")

# 13. /market
async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        res = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}).json()
        if not isinstance(res, list) or len(res) < 2: return
        meta = res[0]
        ctxs = res[1]
        universe = meta.get("universe", [])
        
        assets = []
        for i, ctx in enumerate(ctxs):
            if i < len(universe):
                coin = universe[i]["name"]
                vol = float(ctx.get("dayNtlVlm", 0))
                px = float(ctx.get("markPx", 0))
                prev_px = float(ctx.get("prevDayPx", px))
                volatility = ((px - prev_px) / prev_px) * 100 if prev_px > 0 else 0
                assets.append({"coin": coin, "vol": vol, "volatility": volatility})
                
        assets.sort(key=lambda x: x["vol"], reverse=True)
        top_10 = assets[:10]
        
        text = "📈 **Top 10 Tokens by 24h Volume & Volatility** 📉\n\n"
        for idx, a in enumerate(top_10, 1):
            sign = "+" if a['volatility'] >= 0 else ""
            text += f"{idx}. **{a['coin']}** | {sign}{round(a['volatility'], 2)}% | Vol: ${round(a['vol']/1e6, 2)}M\n"
            
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.effective_message.reply_text(f"Error fetching market data: {e}")

# ---------------- INLINE KEYBOARD DISPATCHER ---------------- #
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Ack the click
    data = query.data
    
    # Handle the instant Add shortcut from /toptraders
    if data.startswith("add:"):
        _, addr = data.split(":", 1)
        # Create a compressed default nickname
        name = f"TopTrader_{addr[:4]}"
        chat_id = str(update.effective_chat.id)
        wallets = load_wallets()
        if chat_id not in wallets:
            wallets[chat_id] = {}
        wallets[chat_id][name] = addr
        save_wallets(wallets)
        await update.effective_message.reply_text(f"✅ Successfully auto-added Top Trader to your list: {name} -> {addr}\n(You can use /removewallet to remove them if you want to re-add them with a custom nickname later)")
        return

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
    elif command == "metrics":
        await metrics_command(update, context)

def get_users_tracking_address(address):
    interested_users = []
    wallets = load_wallets()
    if isinstance(wallets, dict):
        for chat_id, user_wallets in wallets.items():
            if isinstance(user_wallets, dict):
                for nickname, tracked_addr in user_wallets.items():
                    if tracked_addr.lower() == address.lower():
                        interested_users.append((chat_id, nickname))
    return interested_users

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
                                hd_link = f"https://hyperdash.com/address/{address}"
                                ai_insight = "🚨 **AI Analysis:** Heavy New Conviction Entry" if abs(szi) * data['price'] > 20000 else "🧠 **AI Analysis:** Standard Size Entry"
                                msg = f"🚀 **POSITION OPENED**\n**Trader:** {nickname}\n**Token:** {coin}\n**Action:** {direction}\n**Avg Entry:** ${round(data['price'], 4)}\n**Total Size:** {abs(szi)}\n\n{ai_insight}\n🔗 [View Trader on Hyperdash]({hd_link})\n⏱ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            else:
                                msg = f"🪙 **SPOT ASSET PURCHASED**\n**Trader:** {nickname}\n**Token:** {coin} (Spot/Metal)\n**Total Balance:** {round(abs(szi), 6)}\n⏱ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            send_message(chat_id, msg)
                    
                    elif prev_state_map[key]["size"] != szi:
                        # POSITION SIZE UPDATED!
                        old_size = prev_state_map[key]["size"]
                        
                        if p_type == "PERP":
                            is_adding = (old_size > 0 and szi > old_size) or (old_size < 0 and szi < old_size)
                            action = "ADDED TO POSITION" if is_adding else "PARTIALLY CLOSED"
                            direction = "LONG" if szi > 0 else "SHORT"
                            
                            # Basic AI Analysis Logic
                            change_pct = abs((szi - old_size) / old_size) * 100
                            if is_adding:
                                ai_insight = "🔥 **AI Analysis:** Doubling Down (High Conviction)" if change_pct > 50 else "📈 **AI Analysis:** Scaling In Slowly"
                            else:
                                ai_insight = "🏃‍♂️ **AI Analysis:** Panic / Rapid Exit" if change_pct > 70 else "💰 **AI Analysis:** Taking Partial Profits"
                                
                            hd_link = f"https://hyperdash.com/address/{address}"
                            
                            for chat_id, nickname in interested_users:
                                msg = f"⚖️ **POSITION UPDATED**\n**Trader:** {nickname}\n**Token:** {coin}\n**Action:** {action} ({direction})\n**New Avg Entry:** ${round(data['price'], 4)}\n**New Size:** {abs(szi)}\n\n{ai_insight}\n🔗 [View Trader on Hyperdash]({hd_link})\n⏱ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
                                hd_link = f"https://hyperdash.com/address/{address}"
                                ai_insight = "🚪 **AI Analysis:** Position Fully Closed and Exited"
                                msg = f"📉 **POSITION FULLY CLOSED**\n**Trader:** {nickname}\n**Token:** {coin}\n**Action:** {direction}\n**Size Closed:** {abs(old_szi)}\n\n{ai_insight}\n🔗 [View Trader on Hyperdash]({hd_link})\n⏱ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            else:
                                msg = f"🛑 **SPOT ASSET FULLY SOLD**\n**Trader:** {nickname}\n**Token:** {coin} (Spot/Metal)\n**Amount Sold:** {abs(old_szi)}\n⏱ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
        ("balance", "Show combined account equity"),
        ("metrics", "View advanced statistics (Win Rate, Drawdown)"),
        ("toptraders", "Discover top hyperliquid wallets to copy"),
        ("market", "View 24h market volatility for top 10 tokens")
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
    app.add_handler(CommandHandler("metrics", metrics_command))
    app.add_handler(CommandHandler("toptraders", toptraders_command))
    app.add_handler(CommandHandler("market", market_command))
    
    # Register the Callback Query Handler for Inline Buttons
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Start Polling
    threading.Thread(target=poll_positions, daemon=True).start()
    
    print("Telegram Bot Started!")
    app.run_polling()

if __name__ == "__main__":
    keep_alive()
    start_bot()