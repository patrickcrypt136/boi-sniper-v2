import time
import requests
from web3 import Web3
from dotenv import load_dotenv
import os

load_dotenv()

# ── ENV ───────────────────────────────────────────────
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
COOKIE      = os.getenv("COOKIE")
MY_WALLET   = os.getenv("MY_WALLET")
BOT_TOKEN   = os.getenv("BOT_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

# ── CONFIG ────────────────────────────────────────────
SHARE_AMOUNT    = 100        # 100 = 1 full ticket (basis points)
SLIPPAGE        = 1.10       # 10% slippage buffer
GAS_LIMIT       = 250000     # Slightly higher for safety
PRIORITY_GWEI   = 2          # maxPriorityFeePerGas
GAS_MULTIPLIER  = 1.25       # base fee multiplier
POLL_INTERVAL   = 1          # seconds between new-arrival checks
TX_TIMEOUT      = 60         # seconds to wait for receipt

API_URL = (
    "https://www.boithebear.com/api/socialfi/new-arrivals"
    "?limit=10&offset=0&userId=562acdb3-50d7-49aa-86d4-1b778da6ca12"
)

# ── CHAINS ────────────────────────────────────────────
CHAINS = {
    "avalanche": {
        "rpc":      "https://api.avax.network/ext/bc/C/rpc",
        "contract": "0x2Fec21938e4d11117Bda59a5fE880c1d0AE54A7F",
        "chain_id": 43114,
        "symbol":   "AVAX",
        "explorer": "https://snowtrace.io/tx/",
    },
    "bsc": {
        "rpc":      "https://bsc-dataseed1.defibit.io",
        "contract": "0xC12ab9BC529809d6041564FE6aC65FAF8e190E7B",
        "chain_id": 56,
        "symbol":   "BNB",
        "explorer": "https://bscscan.com/tx/",
    },
}

# ── ABI ───────────────────────────────────────────────
ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "address", "name": "_to",            "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"},
        ],
        "name": "buyShares",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"},
        ],
        "name": "getBuyPriceAfterFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "address", "name": "_to",            "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"},
        ],
        "name": "sellShares",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"},
        ],
        "name": "getSellPriceAfterFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "address", "name": "_holder",        "type": "address"},
        ],
        "name": "sharesBalance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
        ],
        "name": "sharesSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

HEADERS = {
    "accept":     "*/*",
    "cookie":     COOKIE,
    "referer":    "https://www.boithebear.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36"
    ),
}

seen_ids  = set()
first_run = True


# ══════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════

def log(msg: str):
    """Timestamped console print."""
    ts = time.strftime("%Y-%m-%d %I:%M:%S %p")
    print(f"[{ts}] {msg}")


def send_telegram(text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log(f"⚠️  Telegram error: {e}")


def get_contract(chain: str):
    cfg      = CHAINS[chain]
    w3       = Web3(Web3.HTTPProvider(cfg["rpc"]))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(cfg["contract"]),
        abi=ABI,
    )
    return w3, contract, cfg


def get_dynamic_gas(w3) -> dict:
    """Fetch live base fee and build EIP-1559 gas params."""
    try:
        latest   = w3.eth.get_block("latest")
        base_fee = latest["baseFeePerGas"]
        priority = w3.to_wei(str(PRIORITY_GWEI), "gwei")
        max_fee  = int(base_fee * GAS_MULTIPLIER) + priority
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority}
    except Exception:
        # Fallback to safe static values if block fetch fails
        log("⚠️  Dynamic gas fetch failed — using fallback 35 gwei")
        return {
            "maxFeePerGas":         w3.to_wei("35", "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(str(PRIORITY_GWEI), "gwei"),
        }


# ══════════════════════════════════════════════════════
#  SUPPLY CHECK
# ══════════════════════════════════════════════════════

def has_zero_supply(chain: str, wallet_address: str) -> bool:
    """Return True only if sharesSupply == 0 (no one has bought yet)."""
    try:
        w3, contract, _ = get_contract(chain)
        subject = Web3.to_checksum_address(wallet_address)
        supply  = contract.functions.sharesSupply(subject).call()
        log(f"📊 Supply for {wallet_address[:10]}…: {supply}")
        return supply == 0
    except Exception as e:
        log(f"⚠️  Supply check failed: {e}")
        return False   # fail-safe: skip rather than buy blind


# ══════════════════════════════════════════════════════
#  BUY
# ══════════════════════════════════════════════════════

def buy_shares(username: str, wallet_address: str, chain: str):
    w3, contract, cfg = get_contract(chain)
    symbol = cfg["symbol"]

    try:
        subject = Web3.to_checksum_address(wallet_address)
        me      = Web3.to_checksum_address(MY_WALLET)

        # ── Quote ────────────────────────────────────
        raw_price  = contract.functions.getBuyPriceAfterFee(subject, SHARE_AMOUNT).call()
        price      = int(raw_price * SLIPPAGE)          # apply slippage buffer
        price_coin = float(w3.from_wei(price, "ether"))
        log(f"💰 Price for {SHARE_AMOUNT} units: {price_coin:.6f} {symbol} (incl. {int((SLIPPAGE-1)*100)}% buffer)")

        # ── Balance check ────────────────────────────
        balance      = w3.eth.get_balance(me)
        balance_coin = float(w3.from_wei(balance, "ether"))
        log(f"💳 Wallet balance: {balance_coin:.6f} {symbol}")

        if balance < price:
            msg = (
                f"❌ Insufficient balance on {chain.upper()} for @{username}\n"
                f"Need: {price_coin:.6f} {symbol} | Have: {balance_coin:.6f} {symbol}"
            )
            log(msg)
            send_telegram(msg)
            return

        # ── Build tx ─────────────────────────────────
        gas_params = get_dynamic_gas(w3)
        nonce      = w3.eth.get_transaction_count(me)

        tx = contract.functions.buyShares(subject, me, SHARE_AMOUNT).build_transaction(
            {
                "from":    me,
                "value":   price,
                "gas":     GAS_LIMIT,
                "nonce":   nonce,
                "chainId": cfg["chain_id"],
                **gas_params,
            }
        )

        # ── Sign & send ──────────────────────────────
        signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex  = "0x" + tx_hash.hex()
        log(f"📡 TX submitted: {tx_hex}")
        log(f"⏳ Waiting for on-chain confirmation (timeout {TX_TIMEOUT}s)…")

        # ── Wait for receipt ─────────────────────────
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=TX_TIMEOUT)
        now     = time.strftime("%Y-%m-%d %I:%M %p")

        if receipt["status"] == 1:
            # ✅ Confirmed success
            gas_used = receipt["gasUsed"]
            log(f"✅ Sniped @{username} on {chain.upper()}! Gas used: {gas_used}")
            log(f"🔗 {cfg['explorer']}{tx_hex}")

            send_telegram(
                f"🎯 <b>Sniped @{username}!</b>\n\n"
                f"⛓️ Chain: {chain.upper()}\n"
                f"💰 Paid: {price_coin:.6f} {symbol}\n"
                f"⛽ Gas used: {gas_used:,}\n"
                f"🕐 {now}\n"
                f"🔗 {cfg['explorer']}{tx_hex}"
            )
        else:
            # ❌ TX reverted on-chain
            log(f"❌ TX REVERTED on-chain for @{username}!")
            log(f"🔗 {cfg['explorer']}{tx_hex}")

            send_telegram(
                f"❌ <b>TX REVERTED for @{username}</b>\n\n"
                f"⛓️ Chain: {chain.upper()}\n"
                f"💸 Gas lost: {receipt['gasUsed']:,} units\n"
                f"🕐 {now}\n"
                f"🔗 {cfg['explorer']}{tx_hex}\n\n"
                f"⚠️ Possible cause: price moved (someone sniped first)"
            )

    except Exception as e:
        log(f"❌ Buy failed for @{username}: {e}")
        send_telegram(f"❌ Buy error for @{username} on {chain.upper()}\n<code>{e}</code>")


# ══════════════════════════════════════════════════════
#  SELL
# ══════════════════════════════════════════════════════

def sell_shares(username: str, wallet_address: str, chain: str, amount=SHARE_AMOUNT):
    """
    Sell shares of a specific user.
    amount: integer unit count, or "all" to sell everything held.
    """
    w3, contract, cfg = get_contract(chain)
    symbol = cfg["symbol"]

    try:
        subject = Web3.to_checksum_address(wallet_address)
        me      = Web3.to_checksum_address(MY_WALLET)

        # ── Check holdings ───────────────────────────
        held = contract.functions.sharesBalance(subject, me).call()
        log(f"📦 You hold {held} units of @{username} on {chain.upper()}")

        if held == 0:
            log(f"⚠️  Nothing to sell for @{username}")
            return

        sell_amt = held if amount == "all" else min(int(amount), held)

        # ── Quote ────────────────────────────────────
        sell_price     = contract.functions.getSellPriceAfterFee(subject, sell_amt).call()
        sell_price_eth = float(w3.from_wei(sell_price, "ether"))
        log(f"💰 Sell return for {sell_amt} units: {sell_price_eth:.6f} {symbol}")

        # ── Build tx ─────────────────────────────────
        gas_params = get_dynamic_gas(w3)
        nonce      = w3.eth.get_transaction_count(me)

        tx = contract.functions.sellShares(subject, me, sell_amt).build_transaction(
            {
                "from":    me,
                "gas":     GAS_LIMIT,
                "nonce":   nonce,
                "chainId": cfg["chain_id"],
                **gas_params,
            }
        )

        # ── Sign & send ──────────────────────────────
        signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex  = "0x" + tx_hash.hex()
        log(f"📡 Sell TX submitted: {tx_hex}")
        log(f"⏳ Waiting for confirmation…")

        # ── Wait for receipt ─────────────────────────
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=TX_TIMEOUT)
        now     = time.strftime("%Y-%m-%d %I:%M %p")

        if receipt["status"] == 1:
            log(f"✅ Sold {sell_amt} units of @{username}! Received {sell_price_eth:.6f} {symbol}")
            log(f"🔗 {cfg['explorer']}{tx_hex}")

            send_telegram(
                f"💸 <b>Sold @{username}!</b>\n\n"
                f"⛓️ Chain: {chain.upper()}\n"
                f"📦 Amount: {sell_amt} units\n"
                f"💰 Received: {sell_price_eth:.6f} {symbol}\n"
                f"🕐 {now}\n"
                f"🔗 {cfg['explorer']}{tx_hex}"
            )
        else:
            log(f"❌ Sell TX reverted for @{username}!")
            send_telegram(
                f"❌ <b>Sell REVERTED for @{username}</b>\n"
                f"⛓️ {chain.upper()}\n"
                f"🔗 {cfg['explorer']}{tx_hex}"
            )

    except Exception as e:
        log(f"❌ Sell failed for @{username}: {e}")
        send_telegram(f"❌ Sell error for @{username}\n<code>{e}</code>")


# ══════════════════════════════════════════════════════
#  SELL ALL HOLDINGS
# ══════════════════════════════════════════════════════

def sell_all_holdings():
    log("🔄 Fetching holdings from BOI API…")
    try:
        res   = requests.get(API_URL, headers=HEADERS, timeout=15)
        users = res.json().get("users", [])

        sold_count = 0
        for u in users:
            wallet = u.get("wallet_address")
            chain  = u.get("selected_chain", "").lower()
            uname  = u.get("username", "unknown")

            if not wallet or chain not in CHAINS:
                continue

            w3, contract, _ = get_contract(chain)
            subject = Web3.to_checksum_address(wallet)
            me      = Web3.to_checksum_address(MY_WALLET)
            held    = contract.functions.sharesBalance(subject, me).call()

            if held > 0:
                log(f"📦 Selling {held} units of @{uname} on {chain.upper()}")
                sell_shares(uname, wallet, chain, amount="all")
                sold_count += 1
                time.sleep(1.5)   # small delay between sells

        if sold_count == 0:
            log("ℹ️  No holdings found to sell.")

    except Exception as e:
        log(f"❌ Sell-all error: {e}")


# ══════════════════════════════════════════════════════
#  NEW ARRIVALS WATCHER
# ══════════════════════════════════════════════════════

def check_new_arrivals():
    global first_run

    try:
        res = requests.get(API_URL, headers=HEADERS, timeout=15)

        if res.status_code != 200:
            log(f"⚠️  API error {res.status_code}")
            return

        users = res.json().get("users", [])

        # ── First run: seed known IDs ────────────────
        if first_run:
            for u in users:
                seen_ids.add(u["id"])
            first_run = False
            log(f"🌱 Seeded {len(seen_ids)} existing users. Watching for new arrivals…\n")
            return

        # ── Check for new entries ────────────────────
        for u in users:
            if u["id"] in seen_ids:
                continue

            seen_ids.add(u["id"])

            username = u.get("username", "unknown")
            wallet   = u.get("wallet_address")
            chain    = u.get("selected_chain", "").lower()

            log(f"🆕 New arrival: @{username} | chain: {chain}")

            if chain not in CHAINS:
                log(f"⏭️  Chain '{chain}' not supported — skipping\n")
                continue

            if not wallet:
                log(f"⚠️  No wallet for @{username} — skipping\n")
                continue

            # ── Zero-supply gate ─────────────────────
            if has_zero_supply(chain, wallet):
                log(f"✅ Zero supply confirmed — sniping @{username}!")
                buy_shares(username, wallet, chain)
            else:
                log(f"⏭️  @{username} already has buyers — skipping\n")

    except Exception as e:
        log(f"❌ Poll error: {e}")


# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════

def print_banner():
    print("=" * 54)
    print("   BOI THE BEAR — TICKET SNIPER  v2.0")
    print("=" * 54)
    print(f"  💳 Wallet  : {MY_WALLET}")
    print(f"  ⛓️  Chains  : AVALANCHE + BSC")
    print(f"  🎯 Amount  : {SHARE_AMOUNT} units (1 ticket)")
    print(f"  🛡️  Slippage: {int((SLIPPAGE - 1) * 100)}%")
    print(f"  ⛽ Gas     : dynamic (base × {GAS_MULTIPLIER} + {PRIORITY_GWEI} gwei)")
    print(f"  ✅ Receipt : on-chain confirmation before reporting")
    print("=" * 54)
    print()


if __name__ == "__main__":
    print_banner()

    send_telegram(
        f"🤖 <b>BOI Sniper v2.0 is live!</b>\n\n"
        f"⛓️ Chains: AVALANCHE + BSC\n"
        f"💳 Wallet: <code>{MY_WALLET}</code>\n"
        f"🎯 Amount: {SHARE_AMOUNT} units per snipe\n"
        f"🛡️ Slippage buffer: {int((SLIPPAGE - 1) * 100)}%\n"
        f"✅ On-chain receipt check: ENABLED"
    )

    # ── Manual sell examples (uncomment to use) ──────────────────────────
    # Sell specific amount for a user:
    # sell_shares("username", "0xWALLET", "bsc", amount=100)
    #
    # Sell all units for a user:
    # sell_shares("username", "0xWALLET", "avalanche", amount="all")
    #
    # Sell every holding across all users:
    # sell_all_holdings()
    # ─────────────────────────────────────────────────────────────────────

    # Main sniper loop
    while True:
        check_new_arrivals()
        time.sleep(POLL_INTERVAL)