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
    try:
        entry_price = float(entry_price)
        sl = float(sl)
    except ValueError as e:
        print(f"Error converting entry price or SL to float: {e}")
        return None

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
    Extended format: ORDER_TYPE ORDER_KIND SYMBOL RISK_PERCENT/FIXED_LOT ENTRY_PRICE SL TP [EXPIRATION] [COMMENT]
    Example:
        SELL LIMIT BTCUSD 50% 93984.85 94984.85 92984.85
        SELL LIMIT BTCUSD 0.01 93984.85 94984.85 92984.85
    """
    try:
        pattern = (
            r"(?P<order_type>BUY|SELL)\s+"
            r"(?P<order_kind>LIMIT|STOP|MARKET)\s+"
            r"(?P<symbol>\w+)\s+"
            r"(?P<risk_or_lot>\d*\.?\d+%|\d*\.?\d+)\s+"
            r"(?P<entry_price>\d*\.?\d+)\s+"
            r"(?P<sl>\d*\.?\d+)\s+"
            r"(?P<tp>\d*\.?\d+)(?:\s+(?P<expiration>DAY|WEEK))?(?:\s+(?P<comment>[^\s]+))?"
        )
        match = re.match(pattern, message)
        if match:
            return match.groupdict()
        else:
            return None
    except Exception as e:
        print(f"Error parsing signal: {e}")
        return None

def parse_multiple_orders_signal(message):
    """
    Parse signals for multiple orders format.
    Example:
        SELL LIMIT BTCUSD 5% 98200.00 98600.00 5 98900.00 98000.00
        SELL LIMIT BTCUSD 0.05 98200.00 98600.00 5 98900.00 98000.00
    """
    try:
        pattern = (
            r"(?P<order_type>BUY|SELL)\s+"          # Order type (BUY/SELL)
            r"(?P<order_kind>LIMIT|STOP)\s+"        # Order kind (LIMIT/STOP)
            r"(?P<symbol>\w+)\s+"                   # Symbol (e.g., BTCUSD)
            r"(?P<risk_or_lot>\d*\.?\d+%|\d*\.?\d+)\s+"  # Risk percentage or fixed lot size
            r"(?P<entry_price_range>\d*\.?\d+)\s+"  # Entry price range start
            r"(?P<end_price_range>\d*\.?\d+)\s+"    # Entry price range end
            r"(?P<num_orders>\d+)\s+"               # Number of orders
            r"(?P<sl>\d*\.?\d+)\s+"                 # Stop loss
            r"(?P<tp>\d*\.?\d+)(?:\s+(?P<expiration>DAY|WEEK))?(?:\s+(?P<comment>[^\s]+))?"  # TP, optional expiration, comment
        )
        match = re.match(pattern, message)
        if match:
            return match.groupdict()
        else:
            return None
    except Exception as e:
        print(f"Error parsing multiple orders signal: {e}")
        return None

def place_multiple_orders(order_type, order_kind, symbol, risk_or_lot, entry_price, end_price, num_orders, sl, tp, comment=None, expiration=None):
    """
    Place multiple orders across a price range evenly spaced.
    The lot size is calculated separately for each order based on a per-order risk percentage.
    """
    try:
        # Ensure the symbol is available in the Market Watch
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol {symbol}")
            return False

        # Get account info for balance
        account_info = mt5.account_info()
        if not account_info:
            print("Failed to retrieve account information")
            return False

        balance = account_info.balance
        print(f"Account Balance: {balance}")

        # Get symbol info
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            print(f"Symbol info not found for {symbol}")
            return False

        # Convert inputs to numeric types
        try:
            entry_price = float(entry_price)
            sl = float(sl)
            tp = float(tp)
            end_price = float(end_price)
        except ValueError as e:
            print(f"Error converting numeric values: {e}")
            return False

        # Calculate price intervals for multiple orders
        price_step = (end_price - entry_price) / (num_orders - 1)
        print(f"Price Step between orders: {price_step}")

        # Determine if risk_or_lot is a percentage or fixed lot size
        if isinstance(risk_or_lot, str) and risk_or_lot.endswith('%'):
            total_risk_percentage = float(risk_or_lot[:-1])  # Total risk percentage
            risk_percentage_per_order = total_risk_percentage / num_orders  # Spread the risk across orders
            print(f"Risk Percentage per Order: {risk_percentage_per_order}%")
            fixed_lot_size = None  # No fixed lot size, we calculate based on risk
        else:
            risk_percentage_per_order = None  # No risk percentage, we use fixed lot size
            fixed_lot_size = float(risk_or_lot)  # Assuming fixed lot size if no '%' is present
            print(f"Using fixed lot size: {fixed_lot_size}")

        # Place multiple orders at different price levels
        for i in range(num_orders):
            new_entry_price = entry_price + price_step * i  # Place the first order at entry_price, last at end_price
            print(f"Placing order {i+1} at price {new_entry_price}")
            
            if fixed_lot_size:
                volume = fixed_lot_size  # Use the fixed lot size for each order
            else:
                # Calculate lot size for this specific order based on the per-order risk percentage
                distance_to_sl = abs(new_entry_price - sl)
                volume = calculate_lot_size(balance, risk_percentage_per_order, symbol, new_entry_price, sl)

            # Ensure the volume is within the acceptable range for the symbol
            if volume < symbol_info.volume_min or volume > symbol_info.volume_max:
                print(f"Invalid lot size: {volume}. Must be between {symbol_info.volume_min} and {symbol_info.volume_max}.")
                return False

            # Determine order type for MT5
            if order_kind.upper() == "LIMIT":
                order_type_mt5 = mt5.ORDER_TYPE_BUY_LIMIT if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
            elif order_kind.upper() == "STOP":
                order_type_mt5 = mt5.ORDER_TYPE_BUY_STOP if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL_STOP
            elif order_kind.upper() == "MARKET":
                order_type_mt5 = mt5.ORDER_TYPE_BUY if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL
            else:
                print(f"Invalid order kind: {order_kind}")
                return False

            # Prepare order request
            request = {
                "action": mt5.TRADE_ACTION_PENDING if order_kind != "MARKET" else mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type_mt5,
                "price": new_entry_price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 234000,
                "type_filling": mt5.ORDER_FILLING_IOC,
                "type_time": mt5.ORDER_TIME_GTC,  # Default Good-Till-Cancelled
            }

            # Handle expiration
            if expiration:
                current_time = datetime.now()
                if expiration.upper() == "DAY":
                    expiration_time = int(current_time.replace(hour=23, minute=59, second=59).timestamp())
                elif expiration.upper() == "WEEK":
                    days_until_friday = (4 - current_time.weekday()) % 7
                    expiration_time = int((current_time + timedelta(days=days_until_friday)).replace(hour=23, minute=59, second=59).timestamp())
                else:
                    print(f"Invalid expiration value: {expiration}")
                    return False
                request["type_time"] = mt5.ORDER_TIME_SPECIFIED
                request["expiration"] = expiration_time

            # Add optional comment
            if comment:
                request["comment"] = comment

            # Log the request for debugging
            print("\nOrder Request:")
            for key, value in request.items():
                print(f"  {key}: {value}")

            # Send order request
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
        print(f"Unexpected error in place_multiple_orders: {str(e)}")
        return False

def place_trade(order_type, order_kind, symbol, risk_or_lot, entry_price, sl, tp, comment=None, expiration=None):
    """
    Places a trade on MT5 with the given parameters using either risk percentage or fixed lot size.
    """
    try:
        # Ensure the symbol is available in the Market Watch
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol {symbol}")
            return False

        # Get account info for balance
        account_info = mt5.account_info()
        if not account_info:
            print("Failed to retrieve account information")
            return False

        balance = account_info.balance
        print(f"Account Balance: {balance}")

        # Get symbol info
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            print(f"Symbol info not found for {symbol}")
            return False

        # Calculate lot size
        if "%" in risk_or_lot:  # Risk percentage
            risk_percentage = float(risk_or_lot.strip('%'))
            volume = calculate_lot_size(balance, risk_percentage, symbol, entry_price, sl)
        else:  # Fixed lot size
            volume = float(risk_or_lot)

        if volume is None or volume < symbol_info.volume_min or volume > symbol_info.volume_max:
            print(f"Invalid lot size: {volume}. Must be between {symbol_info.volume_min} and {symbol_info.volume_max}.")
            return False

        # Convert inputs to numeric types
        try:
            entry_price = float(entry_price)
            sl = float(sl)
            tp = float(tp)
        except ValueError as e:
            print(f"Error converting numeric values: {e}")
            return False

        # Determine order type for MT5
        if order_kind.upper() == "LIMIT":
            order_type_mt5 = mt5.ORDER_TYPE_BUY_LIMIT if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        elif order_kind.upper() == "STOP":
            order_type_mt5 = mt5.ORDER_TYPE_BUY_STOP if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL_STOP
        elif order_kind.upper() == "MARKET":
            order_type_mt5 = mt5.ORDER_TYPE_BUY if order_type.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        else:
            print(f"Invalid order kind: {order_kind}")
            return False

        # Prepare order request
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
            "type_time": mt5.ORDER_TIME_GTC,  # Default Good-Till-Cancelled
        }

        # Handle expiration
        if expiration:
            current_time = datetime.now()
            if expiration.upper() == "DAY":
                expiration_time = int(current_time.replace(hour=23, minute=59, second=59).timestamp())
            elif expiration.upper() == "WEEK":
                days_until_friday = (4 - current_time.weekday()) % 7
                expiration_time = int((current_time + timedelta(days=days_until_friday)).replace(hour=23, minute=59, second=59).timestamp())
            else:
                print(f"Invalid expiration value: {expiration}")
                return False
            request["type_time"] = mt5.ORDER_TIME_SPECIFIED
            request["expiration"] = expiration_time

        # Add optional comment
        if comment:
            request["comment"] = comment

        # Log the request for debugging
        print("\nOrder Request:")
        for key, value in request.items():
            print(f"  {key}: {value}")

        # Send order request
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
async def on_message(message):
    if message.author == client.user:
        return

    lines = message.content.strip().split('\n')
    
    for line in lines:
        try:
            # Try parsing as multiple orders first
            trade_signal = parse_multiple_orders_signal(line)
            if trade_signal:
                print(f"Received multiple orders signal: {trade_signal}")
                success = place_multiple_orders(
                    order_type=trade_signal['order_type'],
                    order_kind=trade_signal['order_kind'],
                    symbol=trade_signal['symbol'],
                    risk_or_lot=trade_signal['risk_or_lot'],
                    entry_price=trade_signal['entry_price_range'],
                    end_price=trade_signal['end_price_range'],
                    num_orders=int(trade_signal['num_orders']),
                    sl=trade_signal['sl'],
                    tp=trade_signal['tp'],
                    comment=trade_signal.get('comment'),
                    expiration=trade_signal.get('expiration')
                )
            else:
                # Try parsing as a single order
                trade_signal = parse_trade_signal(line)
                if trade_signal:
                    print(f"Received single order signal: {trade_signal}")
                    success = place_trade(
                        order_type=trade_signal['order_type'],
                        order_kind=trade_signal['order_kind'],
                        symbol=trade_signal['symbol'],
                        risk_or_lot=trade_signal['risk_or_lot'],
                        entry_price=trade_signal['entry_price'],
                        sl=trade_signal['sl'],
                        tp=trade_signal['tp'],
                        comment=trade_signal.get('comment'),
                        expiration=trade_signal.get('expiration')
                    )
                else:
                    success = False
                    print(f"Invalid signal format received: {line}")
                    await message.channel.send(f"❌ Invalid signal format: {line}\nExpected format for single orders: ORDER_TYPE ORDER_KIND SYMBOL RISK_PERCENT ENTRY_PRICE SL TP [EXPIRATION] [COMMENT]\nExpected format for multiple orders: ORDER_TYPE ORDER_KIND SYMBOL RISK_PERCENT/FIXED_LOT ENTRY_PRICE END_PRICE NUM_ORDERS SL TP [EXPIRATION] [COMMENT]")
            
            if success:
                await message.channel.send(f"✅ Trade placed successfully for: {line}")
            else:
                await message.channel.send(f"❌ Failed to place trade for: {line}. Please check the logs for details.\nIf you're receiving a MetaTrader specific error, please refer to this link: https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes")
        
        except Exception as e:
            print(f"Error processing message: {str(e)}")
            await message.channel.send(f"❌ Error processing trade: {str(e)}")

# Start the Discord bot
client.run(DISCORD_TOKEN)

# Shutdown MetaTrader 5 on exit
mt5.shutdown()