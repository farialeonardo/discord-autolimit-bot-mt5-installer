import configparser
import discord
import MetaTrader5 as mt5
import re
import logging
from datetime import datetime, timedelta

# Load configuration from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Discord bot token
DISCORD_TOKEN = config['DEFAULT'].get('DISCORD_TOKEN', '')

if not DISCORD_TOKEN:
    print("Discord token not found in config.ini. Please enter it to proceed.")
    exit()

# Initialize MetaTrader 5
if not mt5.initialize():
    print("MT5 initialization failed")
    exit()

# Create the Discord client
intents = discord.Intents.default()
intents.message_content = True  # Ensure message content is enabled
client = discord.Client(intents=intents)

def calculate_lot_size(balance, risk_percentage, symbol, entry_price, sl):
    """
    Calculate lot size based on account balance, risk percentage, and symbol details.
    This handles various asset classes, including exotic forex pairs, metals, commodities, and indices.
    """
    # Ensure entry_price and sl are floats
    entry_price = float(entry_price)
    sl = float(sl)

    # Calculate the risk amount based on the balance and risk percentage
    risk_amount = balance * (risk_percentage / 100)
    print(f"Risk Amount: {risk_amount}")

    # Retrieve symbol information
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        print(f"Symbol info not found for {symbol}")
        return None

    # Contract size and tick size for the symbol
    contract_size = symbol_info.trade_contract_size
    tick_size = symbol_info.point
    tick_value = symbol_info.trade_tick_value  # Tick value per minimum price movement

    # Debug print for symbol info
    print(f"Symbol Info for {symbol}:")
    print(f"  Contract Size: {contract_size}")
    print(f"  Tick Size (Point): {tick_size}")
    print(f"  Tick Value: {tick_value}")
    print(f"  Min Volume: {symbol_info.volume_min}")
    print(f"  Max Volume: {symbol_info.volume_max}")
    print(f"  Volume Step: {symbol_info.volume_step}")

    # Calculate the stop loss in ticks
    stop_loss_ticks = abs(entry_price - sl) / tick_size
    print(f"Stop Loss in Ticks: {stop_loss_ticks}")

    # Calculate potential loss per lot based on stop loss and tick value
    potential_loss_per_lot = stop_loss_ticks * tick_value
    print(f"Potential Loss per Lot: {potential_loss_per_lot}")

    # Handle potential zero or extremely small values
    if potential_loss_per_lot == 0:
        print("Potential loss per lot is zero or negligible, check SL and entry price.")
        return None

    # Calculate the initial lot size based on risk amount and potential loss per lot
    lot_size = risk_amount / potential_loss_per_lot
    print(f"Calculated Lot Size before Rounding: {lot_size}")

    # Ensure lot size respects the broker's volume restrictions and rounds to nearest step
    if lot_size < symbol_info.volume_min:
        lot_size = symbol_info.volume_min
        print("Adjusted Lot Size to Min Volume")
    elif lot_size > symbol_info.volume_max:
        lot_size = symbol_info.volume_max
        print("Adjusted Lot Size to Max Volume")
    else:
        # Round down to nearest valid increment
        lot_size = (int(lot_size / symbol_info.volume_step) * symbol_info.volume_step)

    print(f"Final Calculated Lot Size for {symbol}: {lot_size}")
    return lot_size

def parse_trade_signal(message):
    """
    Parse trade signals from the message content.
    Extended format: ORDER_TYPE ORDER_KIND SYMBOL RISK_PERCENT ENTRY_PRICE SL TP [EXPIRATION] [COMMENT]
    Example: SELL LIMIT XAUUSD 1.5% 2558 2573.6 2520 DAY my_trade
    """
    try:
        pattern = r"(?P<order_type>BUY|SELL)\s+(?P<order_kind>LIMIT|STOP|MARKET)\s+(?P<symbol>\w+)\s+(?P<risk_percentage>\d*\.?\d+)%\s+(?P<entry_price>\d*\.?\d+)\s+(?P<sl>\d*\.?\d+)\s+(?P<tp>\d*\.?\d+)(?:\s+(?P<expiration>DAY|WEEK))?(?:\s+(?P<comment>[^\s]+))?"
        match = re.match(pattern, message)
        if match:
            return match.groupdict()
        else:
            return None
    except Exception as e:
        print(f"Error parsing signal: {e}")
        return None

def place_trade(order_type, order_kind, symbol, risk_percentage, entry_price, sl, tp, comment=None, expiration=None):
    """
    Places a trade on MT5 with the given parameters using risk percentage-based position sizing.
    """
    try:
        # Ensure the symbol is available in the Market Watch
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol {symbol}")
            return False

        # Get account info for balance
        account_info = mt5.account_info()
        if not account_info:
            print("Failed to get account info")
            return False
            
        print(f"Account Info:")
        print(f"  Balance: {account_info.balance}")
        print(f"  Equity: {account_info.equity}")
        print(f"  Margin: {account_info.margin}")
        print(f"  Free Margin: {account_info.margin_free}")
        
        # Get symbol info
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            print(f"Symbol info not found for {symbol}")
            return False
        
        # Calculate lot size based on risk percentage
        volume = calculate_lot_size(account_info.balance, float(risk_percentage), symbol, entry_price, sl)
        if volume is None:
            print("Failed to calculate lot size")
            return False

        # Convert string values to proper numeric types
        try:
            entry_price = float(entry_price)
            sl = float(sl)
            tp = float(tp)
        except ValueError as e:
            print(f"Error converting numeric values: {e}")
            return False

        # Check for valid volume
        if volume < symbol_info.volume_min or volume > symbol_info.volume_max:
            print(f"Invalid volume: {volume} for {symbol}. Min: {symbol_info.volume_min}, Max: {symbol_info.volume_max}")
            return False

        # Determine the order type for MT5
        if order_kind.upper() == "LIMIT":
            order_type_mt5 = mt5.ORDER_TYPE_BUY_LIMIT if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        elif order_kind.upper() == "STOP":
            order_type_mt5 = mt5.ORDER_TYPE_BUY_STOP if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL_STOP
        else:  # MARKET
            order_type_mt5 = mt5.ORDER_TYPE_BUY if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL

        # Create the base request without expiration and comment
        request = {
            "action": mt5.TRADE_ACTION_PENDING if order_kind != "MARKET" else mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type_mt5,
            "price": entry_price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234000,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        print("\nTrade Request Details:")
        print(f"  Action: {'PENDING' if order_kind != 'MARKET' else 'DEAL'}")
        print(f"  Symbol: {symbol}")
        print(f"  Volume: {volume}")
        print(f"  Order Type: {order_type_mt5}")
        print(f"  Entry Price: {entry_price}")
        print(f"  Stop Loss: {sl}")
        print(f"  Take Profit: {tp}")
        
        # Add comment only if provided
        if comment:
            request["comment"] = comment
        else:
            request["comment"] = ""

        # Add expiration only if provided
        if expiration:
            current_time = datetime.now()
            if expiration.upper() == "DAY":
                expiration_time = int(current_time.replace(hour=23, minute=59, second=59, microsecond=0).timestamp())
                request["type_time"] = mt5.ORDER_TIME_SPECIFIED
                request["expiration"] = expiration_time
            elif expiration.upper() == "WEEK":
                days_until_friday = (4 - current_time.weekday()) % 5
                if days_until_friday == 0 and current_time.hour >= 23:
                    days_until_friday = 5
                expiration_time = int((current_time + timedelta(days=days_until_friday)).replace(hour=23, minute=59, second=59, microsecond=0).timestamp())
                request["type_time"] = mt5.ORDER_TIME_SPECIFIED
                request["expiration"] = expiration_time
        else:
            request["type_time"] = mt5.ORDER_TIME_GTC
            # Don't include expiration field when not needed

        # Print the request for debugging
        print(f"Sending order request: {request}")

        # Send the order
        result = mt5.order_send(request)
        
        if result is None:
            error_code = mt5.last_error()
            print(f"Order failed with error code: {error_code}")
            return False
            
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Order failed: {result.retcode} - {mt5.last_error()}")
            return False
        
        print(f"Order placed successfully: {result}")
        return True

    except Exception as e:
        print(f"Unexpected error in place_trade: {str(e)}")
        return False

@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    lines = message.content.strip().split('\n')
    
    for line in lines:
        try:
            trade_signal = parse_trade_signal(line)
            if trade_signal:
                print(f"Received trade signal: {trade_signal}")
                success = place_trade(
                    order_type=trade_signal['order_type'],
                    order_kind=trade_signal['order_kind'],
                    symbol=trade_signal['symbol'],
                    risk_percentage=trade_signal['risk_percentage'].replace('%', ''),
                    entry_price=trade_signal['entry_price'],
                    sl=trade_signal['sl'],
                    tp=trade_signal['tp'],
                    comment=trade_signal.get('comment'),
                    expiration=trade_signal.get('expiration')
                )
                if success:
                    await message.channel.send(f"✅ Trade placed successfully for: {line}")
                else:
                    await message.channel.send(f"❌ Failed to place trade for: {line}. Please check the logs for details.")
            else:
                print(f"Invalid signal format received: {line}")
                await message.channel.send(f"❌ Invalid signal format: {line}\nExpected format: ORDER_TYPE ORDER_KIND SYMBOL RISK_PERCENT ENTRY_PRICE SL TP [EXPIRATION] [COMMENT]")
        except Exception as e:
            print(f"Error processing message: {str(e)}")
            await message.channel.send(f"❌ Error processing trade: {str(e)}")

# Start the Discord bot
client.run(DISCORD_TOKEN)

# Shutdown MetaTrader 5 on exit
mt5.shutdown()
