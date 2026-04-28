import time
import requests
from web3 import Web3
from dotenv import load_dotenv
import os

load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
COOKIE      = os.getenv("COOKIE")
MY_WALLET   = os.getenv("MY_WALLET")
BOT_TOKEN   = os.getenv("BOT_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")

API_URL = "https://www.boithebear.com/api/socialfi/new-arrivals?limit=10&offset=0&userId=6363489c-f1de-4ddc-9029-91327f17063d"

CHAINS = {
    "avalanche": {
        "rpc":      "https://api.avax.network/ext/bc/C/rpc",
        "contract": "0x2Fec21938e4d11117Bda59a5fE880c1d0AE54A7F",
        "chain_id": 43114,
        "symbol":   "AVAX",
        "explorer": "https://snowtrace.io/tx/"
    },
    "bsc": {
        "rpc":      "https://bsc-dataseed1.defibit.io",
        "contract": "0xC12ab9BC529809d6041564FE6aC65FAF8e190E7B",
        "chain_id": 56,
        "symbol":   "BNB",
        "explorer": "https://bscscan.com/tx/"
    }
}

ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "address", "name": "_to",            "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"}
        ],
        "name": "buyShares",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"}
        ],
        "name": "getBuyPriceAfterFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "address", "name": "_to",            "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"}
        ],
        "name": "sellShares",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "uint256", "name": "_amount",        "type": "uint256"}
        ],
        "name": "getSellPriceAfterFee",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"},
            {"internalType": "address", "name": "_holder",        "type": "address"}
        ],
        "name": "sharesBalance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "_sharesSubject", "type": "address"}
        ],
        "name": "sharesSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

HEADERS = {
    "accept":       "*/*",
    "cookie":       COOKIE,
    "referer":      "https://www.boithebear.com/",
    "user-agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36"
}

seen_ids  = set()
first_run = True


# ── TELEGRAM ─────────────────────────────────────────
def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}")


# ── GET CONTRACT ──────────────────────────────────────
def get_contract(chain):
    cfg      = CHAINS[chain]
    w3       = Web3(Web3.HTTPProvider(cfg["rpc"]))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(cfg["contract"]),
        abi=ABI
    )
    return w3, contract, cfg


# ── CHECK SUPPLY (zero buy check) ────────────────────
def has_zero_buys(chain, wallet_address):
    try:
        w3, contract, cfg = get_contract(chain)
        subject = Web3.to_checksum_address(wallet_address)
        supply  = contract.functions.sharesSupply(subject).call()
        print(f"📊 Shares supply for {wallet_address[:8]}: {supply}")
        return supply == 0
    except Exception as e:
        print(f"⚠️ Supply check failed: {e}")
        return False  # if check fails, skip to be safe


# ── BUY ───────────────────────────────────────────────
def buy_shares(username, wallet_address, chain):
    w3, contract, cfg = get_contract(chain)
    symbol = cfg["symbol"]

    try:
        subject = Web3.to_checksum_address(wallet_address)
        me      = Web3.to_checksum_address(MY_WALLET)

        # Get price
        price      = contract.functions.getBuyPriceAfterFee(subject, 100).call()
        price_coin = float(w3.from_wei(price, "ether"))
        print(f"💰 1 ticket costs {price_coin} {symbol}")

        # Check balance
        balance      = w3.eth.get_balance(me)
        balance_coin = float(w3.from_wei(balance, "ether"))
        print(f"💳 Wallet balance: {balance_coin} {symbol}")

        if balance_coin < price_coin:
            msg = f"❌ Insufficient balance on {chain.upper()} for @{username}"
            print(msg)
            send_telegram(msg)
            return

        # Build tx
        nonce = w3.eth.get_transaction_count(me)
        tx    = contract.functions.buyShares(
            subject, me, 100
        ).build_transaction({
            "from":                 me,
            "value":                price,
            "gas":                  200000,
            "maxFeePerGas":         w3.to_wei("30", "gwei"),
            "maxPriorityFeePerGas": w3.to_wei("2",  "gwei"),
            "nonce":                nonce,
            "chainId":              cfg["chain_id"]
        })

        # Sign and send
        signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        now = time.strftime("%Y-%m-%d %I:%M %p")
        print(f"✅ Sniped @{username} on {chain.upper()}!")
        print(f"🔗 {cfg['explorer']}0x{tx_hash.hex()}\n")

        send_telegram(
            f"🎯 Sniped @{username}!\n\n"
            f"⛓️ Chain: {chain.upper()}\n"
            f"💰 Paid: {price_coin} {symbol}\n"
            f"🕐 {now}\n"
            f"🔗 {cfg['explorer']}0x{tx_hash.hex()}"
        )

    except Exception as e:
        print(f"❌ Buy failed for @{username}: {e}\n")
        send_telegram(f"❌ Buy failed for @{username} on {chain.upper()}\n{e}")


# ── SELL ──────────────────────────────────────────────
def sell_shares(username, wallet_address, chain, amount=100):
    w3, contract, cfg = get_contract(chain)
    symbol = cfg["symbol"]

    try:
        subject = Web3.to_checksum_address(wallet_address)
        me      = Web3.to_checksum_address(MY_WALLET)

        # Check how many I hold
        balance = contract.functions.sharesBalance(subject, me).call()
        print(f"📦 You hold {balance} shares of @{username}")

        if balance == 0:
            print(f"⚠️ You don't hold any shares of @{username}")
            return

        # Sell all or specified amount
        sell_amount = balance if amount == "all" else min(amount, balance)

        # Get sell price
        sell_price     = contract.functions.getSellPriceAfterFee(subject, sell_amount).call()
        sell_price_eth = float(w3.from_wei(sell_price, "ether"))
        print(f"💰 Sell price: {sell_price_eth} {symbol}")

        # Build tx
        nonce = w3.eth.get_transaction_count(me)
        tx    = contract.functions.sellShares(
            subject, me, sell_amount
        ).build_transaction({
            "from":                 me,
            "gas":                  200000,
            "maxFeePerGas":         w3.to_wei("30", "gwei"),
            "maxPriorityFeePerGas": w3.to_wei("2",  "gwei"),
            "nonce":                nonce,
            "chainId":              cfg["chain_id"]
        })

        signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        now = time.strftime("%Y-%m-%d %I:%M %p")
        print(f"✅ Sold {sell_amount} shares of @{username}!")
        print(f"🔗 {cfg['explorer']}0x{tx_hash.hex()}\n")

        send_telegram(
            f"💸 Sold shares of @{username}!\n\n"
            f"⛓️ Chain: {chain.upper()}\n"
            f"📦 Amount: {sell_amount}\n"
            f"💰 Received: {sell_price_eth} {symbol}\n"
            f"🕐 {now}\n"
            f"🔗 {cfg['explorer']}0x{tx_hash.hex()}"
        )

    except Exception as e:
        print(f"❌ Sell failed for @{username}: {e}\n")


# ── SELL ALL HOLDINGS ─────────────────────────────────
def sell_all_holdings():
    print("\n🔄 Fetching your holdings from BOI API...")
    try:
        res   = requests.get(API_URL, headers=HEADERS, timeout=15)
        users = res.json().get("users", [])

        for u in users:
            wallet = u.get("wallet_address")
            chain  = u.get("selected_chain", "").lower()
            uname  = u.get("username")

            if not wallet or chain not in CHAINS:
                continue

            w3, contract, cfg = get_contract(chain)
            subject = Web3.to_checksum_address(wallet)
            me      = Web3.to_checksum_address(MY_WALLET)
            balance = contract.functions.sharesBalance(subject, me).call()

            if balance > 0:
                print(f"📦 Selling {balance} shares of @{uname} on {chain.upper()}")
                sell_shares(uname, wallet, chain, amount="all")
                time.sleep(1)

    except Exception as e:
        print(f"❌ Sell all error: {e}")


# ── NEW ARRIVALS ──────────────────────────────────────
def check_new_arrivals():
    global first_run

    try:
        res = requests.get(API_URL, headers=HEADERS, timeout=15)

        if res.status_code != 200:
            print(f"⚠️ API error: {res.status_code}")
            return

        users = res.json().get("users", [])

        if first_run:
            for u in users:
                seen_ids.add(u["id"])
            first_run = False
            print(f"🌱 Seeded {len(seen_ids)} existing users. Watching...\n")
            return

        for u in users:
            if u["id"] not in seen_ids:
                seen_ids.add(u["id"])

                username = u.get("username", "unknown")
                wallet   = u.get("wallet_address")
                chain    = u.get("selected_chain", "").lower()

                print(f"🆕 New arrival: @{username} | chain: {chain}")

                if chain not in CHAINS:
                    print(f"⏭️ Chain '{chain}' not supported\n")
                    continue

                if not wallet:
                    print(f"⚠️ No wallet for @{username}\n")
                    continue

                # Only buy if zero previous buys
                if has_zero_buys(chain, wallet):
                    print(f"✅ Zero buys confirmed — sniping @{username}!")
                    buy_shares(username, wallet, chain)
                else:
                    print(f"⏭️ @{username} already has buyers — skipping\n")

    except Exception as e:
        print(f"❌ Error: {e}")


# ── MAIN ─────────────────────────────────────────────
print("==================================================")
print("   BOI THE BEAR — TICKET SNIPER")
print("==================================================")
print(f"💳 Wallet: {MY_WALLET}")
print(f"⛓️  Chains: AVALANCHE + BSC")
print(f"🎯 Buy only on zero supply | Sell anytime")
print("==================================================\n")

send_telegram(
    f"🤖 BOI Sniper is live!\n\n"
    f"⛓️ Chains: AVALANCHE + BSC\n"
    f"💳 Wallet: {MY_WALLET}\n"
    f"🎯 Only buying zero-supply tickets"
)

# ── TO SELL SPECIFIC TICKET ───────────────────────────
# Uncomment and fill to sell a specific user's ticket:
# sell_shares("username", "0xwalletaddress", "bsc", amount=100)
# sell_shares("username", "0xwalletaddress", "bsc", amount="all")

# ── TO SELL ALL HOLDINGS ──────────────────────────────
# Uncomment to sell everything:
# sell_all_holdings()

# Main sniper loop — checks every 1 second
while True:
    check_new_arrivals()
    time.sleep(1)