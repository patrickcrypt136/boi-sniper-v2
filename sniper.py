"""
BOI THE BEAR — MAXIMUM SPEED SNIPER v5.0
════════════════════════════════════════════════════════
EVERY MILLISECOND OPTIMISED:
  ✅ Pre-encoded tx calldata at startup (no ABI encode at fire time)
  ✅ Full tx body from WS (no get_transaction() RPC call)
  ✅ Gas war — fires 3 txs with escalating gas simultaneously
  ✅ Raw eth_sendRawTransaction (no build_transaction overhead)
  ✅ Nonce pre-cached locally
  ✅ Gas pre-cached (30s TTL, refreshed in background)
  ✅ Auto-calibrated first-buy price at startup
  ✅ WebSocket mempool (fires before API reflects new user)
  ✅ HTTP fallback poller (catches anything mempool misses)
  ✅ Zero balance / supply / quote checks at fire time
  ✅ Dedup lock (never double-fires same wallet)

INSTALL:
  pip install web3 python-dotenv requests websocket-client eth-account

.env:
  PRIVATE_KEY=0x...
  MY_WALLET=0x...
  BOT_TOKEN=...
  CHAT_ID=...
  COOKIE=...
  AVAX_RPC_HTTP=https://...
  AVAX_RPC_WS=wss://...
  BSC_RPC_HTTP=https://...
  BSC_RPC_WS=wss://...
════════════════════════════════════════════════════════
"""

import os, time, json, threading, requests, websocket
from web3 import Web3
from eth_account import Account
from eth_account.datastructures import SignedTransaction
from dotenv import load_dotenv

load_dotenv()

# ── ENV ───────────────────────────────────────────────
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
MY_WALLET   = os.getenv("MY_WALLET")
BOT_TOKEN   = os.getenv("BOT_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")
COOKIE      = os.getenv("COOKIE")

# ── CONFIG ────────────────────────────────────────────
SHARE_AMOUNT         = 100      # 100 units = 1 full ticket
SLIPPAGE             = 1.15     # 15% above calibrated price
GAS_LIMIT            = 300000
PRIORITY_GWEI        = 5
GAS_MULTIPLIER       = 1.25
GAS_CACHE_TTL        = 30
TX_TIMEOUT           = 60
HTTP_POLL_INTERVAL   = 0.5

# Gas war — fire 3 txs with these multipliers simultaneously
# First one to land wins. Higher = more gas spent but faster inclusion.
GAS_WAR_MULTIPLIERS  = [1.0, 1.5, 2.2]

CHAINS = {
    "avalanche": {
        "rpc_http": os.getenv("AVAX_RPC_HTTP", "https://api.avax.network/ext/bc/C/rpc"),
        "rpc_ws":   os.getenv("AVAX_RPC_WS",   "wss://api.avax.network/ext/bc/C/ws"),
        "contract": "0x2Fec21938e4d11117Bda59a5fE880c1d0AE54A7F",
        "chain_id": 43114,
        "symbol":   "AVAX",
        "explorer": "https://snowtrace.io/tx/",
    },
    "bsc": {
        "rpc_http": os.getenv("BSC_RPC_HTTP", "https://bsc-dataseed1.defibit.io"),
        "rpc_ws":   os.getenv("BSC_RPC_WS",   "wss://bsc-ws.nodies.app"),
        "contract": "0xC12ab9BC529809d6041564FE6aC65FAF8e190E7B",
        "chain_id": 56,
        "symbol":   "BNB",
        "explorer": "https://bscscan.com/tx/",
    },
}

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
]

HEADERS = {
    "accept":     "*/*",
    "cookie":     COOKIE,
    "referer":    "https://www.boithebear.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36",
}

API_URL = (
    "https://www.boithebear.com/api/socialfi/new-arrivals"
    "?limit=10&offset=0&userId=562acdb3-50d7-49aa-86d4-1b778da6ca12"
)

# buyShares(address,address,uint256) selector — precomputed
BUY_SEL = Web3.keccak(text="buyShares(address,address,uint256)")[:4].hex()


# ══════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════

def log(msg):
    ms = str(int(time.time() * 1000) % 1000).zfill(3)
    print(f"[{time.strftime('%H:%M:%S')}.{ms}] {msg}")

def tg(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log(f"⚠️ TG: {e}")


# ══════════════════════════════════════════════════════
#  PRE-ENCODED CALLDATA
#  buyShares(subject, me, SHARE_AMOUNT) encoded once at
#  startup. At fire time we only swap the subject bytes.
#  Zero ABI encoding overhead at hot path.
# ══════════════════════════════════════════════════════

def make_calldata(contract, subject: str, me: str) -> bytes:
    """Encode buyShares calldata."""
    return contract.encode_abi(
        "buyShares",
        args=[
            Web3.to_checksum_address(subject),
            Web3.to_checksum_address(me),
            SHARE_AMOUNT,
        ],
    )

def swap_subject_in_calldata(base_data: bytes, new_subject: str) -> bytes:
    """
    Replace subject address in pre-encoded calldata.
    Layout: [4 selector][32 subject][32 to][32 amount]
    Subject starts at byte 4, address is last 20 bytes of 32-byte word.
    """
    addr = bytes.fromhex(new_subject[2:].lower())   # 20 bytes
    padded = b'\x00' * 12 + addr                    # 32 bytes
    return base_data[:4] + padded + base_data[36:]  # swap subject word


# ══════════════════════════════════════════════════════
#  CHAIN CLIENT
# ══════════════════════════════════════════════════════

class ChainClient:
    def __init__(self, name: str):
        cfg           = CHAINS[name]
        self.name     = name
        self.cfg      = cfg
        self.symbol   = cfg["symbol"]
        self.w3       = Web3(Web3.HTTPProvider(cfg["rpc_http"]))
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg["contract"]),
            abi=ABI,
        )
        self.me          = Web3.to_checksum_address(MY_WALLET)
        self.contract_lc = cfg["contract"].lower()

        # First-buy price (set by calibrate())
        self.first_buy: int = 0

        # Pre-encoded base calldata with dummy subject (swapped at fire time)
        # We use a placeholder — it gets replaced per-snipe
        self._base_calldata: bytes = b""

        # Gas cache
        self._gas      = None
        self._gas_ts   = 0.0
        self._gas_lock = threading.Lock()

        # Nonce — local counter, never fetched mid-snipe
        self._nonce      = None
        self._nonce_lock = threading.Lock()

        # Fired wallets — dedup
        self._fired      = set()
        self._fired_lock = threading.Lock()

    def init(self):
        """Call once after construction."""
        block = self.w3.eth.block_number
        log(f"✅ [{self.name.upper()}] Connected | block #{block}")
        self._refresh_gas()
        self._refresh_nonce()
        # Pre-encode calldata with a dummy subject (address(0))
        self._base_calldata = make_calldata(
            self.contract,
            "0x0000000000000000000000000000000000000000",
            self.me,
        )

    # ── Gas ──────────────────────────────────────────

    def _refresh_gas(self):
        with self._gas_lock:
            try:
                latest   = self.w3.eth.get_block("latest")
                base_fee = latest["baseFeePerGas"]
                priority = self.w3.to_wei(str(PRIORITY_GWEI), "gwei")
                self._gas = {
                    "maxFeePerGas":         int(base_fee * GAS_MULTIPLIER) + priority,
                    "maxPriorityFeePerGas": priority,
                }
            except Exception:
                self._gas = {
                    "maxFeePerGas":         self.w3.to_wei("35", "gwei"),
                    "maxPriorityFeePerGas": self.w3.to_wei(str(PRIORITY_GWEI), "gwei"),
                }
            self._gas_ts = time.time()

    def gas(self) -> dict:
        if self._gas is None or (time.time() - self._gas_ts) > GAS_CACHE_TTL:
            # Refresh in background — don't block fire path
            threading.Thread(target=self._refresh_gas, daemon=True).start()
        return self._gas

    # ── Nonce ─────────────────────────────────────────

    def _refresh_nonce(self):
        with self._nonce_lock:
            self._nonce = self.w3.eth.get_transaction_count(self.me)

    def next_nonce(self) -> int:
        with self._nonce_lock:
            n = self._nonce
            self._nonce += 1
            return n

    def reset_nonce(self):
        threading.Thread(target=self._refresh_nonce, daemon=True).start()

    # ── Dedup ─────────────────────────────────────────

    def already_fired(self, wallet: str) -> bool:
        w = wallet.lower()
        with self._fired_lock:
            if w in self._fired:
                return True
            self._fired.add(w)
            return False

    # ── Calibrate ─────────────────────────────────────

    def calibrate(self):
        log(f"📐 [{self.name.upper()}] Calibrating…")
        try:
            res   = requests.get(API_URL, headers=HEADERS, timeout=15)
            users = res.json().get("users", [])
            for u in users:
                if u.get("selected_chain", "").lower() != self.name:
                    continue
                wallet = u.get("wallet_address")
                if not wallet:
                    continue
                try:
                    subject = Web3.to_checksum_address(wallet)
                    supply  = self.contract.functions.sharesSupply(subject).call()
                    if supply != 0:
                        continue
                    raw = self.contract.functions.getBuyPriceAfterFee(
                        subject, SHARE_AMOUNT
                    ).call()
                    self.first_buy = int(raw * SLIPPAGE)
                    log(
                        f"   ✅ [{self.name.upper()}] Price = "
                        f"{float(self.w3.from_wei(self.first_buy, 'ether')):.8f} "
                        f"{self.symbol} (+{int((SLIPPAGE-1)*100)}% slip)"
                    )
                    return
                except Exception:
                    continue

            # Fallback
            fallback = {"avalanche": "0.015", "bsc": "0.005"}[self.name]
            self.first_buy = self.w3.to_wei(fallback, "ether")
            log(f"   ⚠️  [{self.name.upper()}] No zero-supply found — fallback {fallback} {self.symbol}")
        except Exception as e:
            log(f"   ❌ [{self.name.upper()}] Calibration error: {e}")
            self.first_buy = self.w3.to_wei("0.02", "ether")


# ══════════════════════════════════════════════════════
#  BUILD + SIGN  — raw transaction, fastest possible
# ══════════════════════════════════════════════════════

def _build_raw(client: ChainClient, subject: str, nonce: int, gas_multiplier: float = 1.0) -> bytes:
    """
    Build and sign a raw transaction using pre-encoded calldata.
    Only swaps the subject address — no ABI encoding at call time.
    """
    data    = swap_subject_in_calldata(client._base_calldata, subject)
    g       = client.gas()
    max_fee = int(g["maxFeePerGas"] * gas_multiplier)
    pri_fee = int(g["maxPriorityFeePerGas"] * gas_multiplier)

    tx = {
        "to":                   Web3.to_checksum_address(client.cfg["contract"]),
        "value":                client.first_buy,
        "gas":                  GAS_LIMIT,
        "maxFeePerGas":         max_fee,
        "maxPriorityFeePerGas": pri_fee,
        "nonce":                nonce,
        "chainId":              client.cfg["chain_id"],
        "data":                 data,
        "type":                 2,   # EIP-1559
    }

    signed: SignedTransaction = Account.sign_transaction(tx, PRIVATE_KEY)
    return signed.raw_transaction


def _send_raw(client: ChainClient, raw: bytes, label: str) -> str | None:
    """Send a raw tx, return tx hex or None."""
    try:
        tx_hash = client.w3.eth.send_raw_transaction(raw)
        return "0x" + tx_hash.hex()
    except Exception as e:
        # Ignore "already known" — means another war tx landed
        if "already known" not in str(e).lower() and "nonce too low" not in str(e).lower():
            log(f"⚠️  Send error [{label}]: {e}")
        return None


# ══════════════════════════════════════════════════════
#  FIRE  — gas war: 3 txs at once, different gas prices
# ══════════════════════════════════════════════════════

def fire(username: str, wallet: str, chain: str, source: str):
    client = clients[chain]

    if client.already_fired(wallet):
        return

    if not client.first_buy:
        log(f"⚠️  Price not ready for {chain} — skipping @{username}")
        return

    t0      = time.time()
    subject = Web3.to_checksum_address(wallet)

    log(f"⚡ [{source}] @{username} ({chain.upper()}) — FIRING GAS WAR")

    # Pre-build all 3 signed txs (fast — just bytes manipulation + signing)
    raws   = []
    nonces = []
    for i, gm in enumerate(GAS_WAR_MULTIPLIERS):
        n = client.next_nonce()
        nonces.append(n)
        raws.append(_build_raw(client, subject, n, gm))

    # Fire all 3 simultaneously in threads
    tx_hexes = [None] * len(raws)

    def send(idx, raw):
        label    = f"war-{idx+1}"
        tx_hex   = _send_raw(client, raw, label)
        elapsed  = (time.time() - t0) * 1000
        if tx_hex:
            tx_hexes[idx] = tx_hex
            log(f"📡 [{label}] fired in {elapsed:.0f}ms → {tx_hex}")

    threads = [threading.Thread(target=send, args=(i, r), daemon=True) for i, r in enumerate(raws)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Confirm whichever landed (background)
    for tx_hex in tx_hexes:
        if tx_hex:
            threading.Thread(
                target=_confirm,
                args=(username, chain, tx_hex, client.first_buy, t0, source),
                daemon=True,
            ).start()
            break   # only need to confirm one


def _confirm(username, chain, tx_hex, price, t0, source):
    client = clients[chain]
    try:
        receipt  = client.w3.eth.wait_for_transaction_receipt(
            bytes.fromhex(tx_hex[2:]), timeout=TX_TIMEOUT
        )
        total_ms = (time.time() - t0) * 1000
        now      = time.strftime("%Y-%m-%d %I:%M %p")
        price_h  = float(client.w3.from_wei(price, "ether"))

        if receipt["status"] == 1:
            log(f"✅ CONFIRMED @{username} | gas: {receipt['gasUsed']:,} | {total_ms:.0f}ms")
            tg(
                f"🎯 <b>Sniped @{username}!</b>\n\n"
                f"⛓️ {chain.upper()} | 📡 {source}\n"
                f"💰 {price_h:.8f} {client.symbol}\n"
                f"⛽ Gas: {receipt['gasUsed']:,}\n"
                f"⚡ {total_ms:.0f}ms\n"
                f"🕐 {now}\n"
                f"🔗 {client.cfg['explorer']}{tx_hex}"
            )
            threading.Thread(target=client.calibrate, daemon=True).start()
        else:
            log(f"❌ REVERTED @{username} | {tx_hex}")
            tg(
                f"❌ <b>Reverted @{username}</b>\n\n"
                f"⛓️ {chain.upper()}\n"
                f"⚠️ Price moved or already bought\n"
                f"🔗 {client.cfg['explorer']}{tx_hex}"
            )
            threading.Thread(target=client.calibrate, daemon=True).start()
            client.reset_nonce()
    except Exception as e:
        log(f"⚠️  Receipt @{username}: {e}")
        client.reset_nonce()


# ══════════════════════════════════════════════════════
#  SELL
# ══════════════════════════════════════════════════════

def sell_shares(username: str, wallet: str, chain: str, amount="all"):
    client = clients[chain]
    try:
        subject = Web3.to_checksum_address(wallet)
        held    = client.contract.functions.sharesBalance(subject, client.me).call()
        if held == 0:
            log(f"⚠️  Nothing to sell @{username}")
            return
        sell_amt   = held if amount == "all" else min(int(amount), held)
        sell_price = client.contract.functions.getSellPriceAfterFee(subject, sell_amt).call()
        sell_eth   = float(client.w3.from_wei(sell_price, "ether"))
        log(f"💰 Selling {sell_amt} units @{username} → {sell_eth:.8f} {client.symbol}")

        data = client.contract.encode_abi(
            "sellShares",
            args=[subject, client.me, sell_amt],
        )
        nonce = client.next_nonce()
        g     = client.gas()
        tx    = {
            "to":                   Web3.to_checksum_address(client.cfg["contract"]),
            "value":                0,
            "gas":                  GAS_LIMIT,
            "maxFeePerGas":         g["maxFeePerGas"],
            "maxPriorityFeePerGas": g["maxPriorityFeePerGas"],
            "nonce":                nonce,
            "chainId":              client.cfg["chain_id"],
            "data":                 data,
            "type":                 2,
        }
        signed  = Account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = client.w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex  = "0x" + tx_hash.hex()
        log(f"📡 Sell: {tx_hex}")

        receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=TX_TIMEOUT)
        now     = time.strftime("%Y-%m-%d %I:%M %p")
        if receipt["status"] == 1:
            log(f"✅ Sold {sell_amt} @{username}")
            tg(
                f"💸 <b>Sold @{username}!</b>\n\n"
                f"⛓️ {chain.upper()}\n"
                f"📦 {sell_amt} units\n"
                f"💰 {sell_eth:.8f} {client.symbol}\n"
                f"🕐 {now}\n"
                f"🔗 {client.cfg['explorer']}{tx_hex}"
            )
        else:
            log(f"❌ Sell reverted @{username}")
            client.reset_nonce()
    except Exception as e:
        log(f"❌ Sell error @{username}: {e}")
        clients[chain].reset_nonce()


def sell_all_holdings():
    log("🔄 Selling all holdings…")
    try:
        res   = requests.get(API_URL, headers=HEADERS, timeout=15)
        users = res.json().get("users", [])
        count = 0
        for u in users:
            wallet = u.get("wallet_address")
            chain  = u.get("selected_chain", "").lower()
            uname  = u.get("username", "unknown")
            if not wallet or chain not in CHAINS:
                continue
            client  = clients[chain]
            subject = Web3.to_checksum_address(wallet)
            held    = client.contract.functions.sharesBalance(subject, client.me).call()
            if held > 0:
                sell_shares(uname, wallet, chain, amount="all")
                count += 1
                time.sleep(1.5)
        log(f"✅ Sold {count} positions.")
    except Exception as e:
        log(f"❌ Sell-all: {e}")


# ══════════════════════════════════════════════════════
#  MEMPOOL WATCHER
#  Subscribes to full pending tx bodies — no extra
#  get_transaction() RPC call needed.
# ══════════════════════════════════════════════════════

def _decode_subject(input_hex: str) -> str | None:
    """Extract _sharesSubject from buyShares calldata. Pure bytes — zero RPC."""
    try:
        data = input_hex[2:] if input_hex.startswith("0x") else input_hex
        if not data.startswith(BUY_SEL):
            return None
        # [4 sel][32 subject][32 to][32 amount]
        subject_word = data[8:72]                        # 32 bytes as hex
        return Web3.to_checksum_address("0x" + subject_word[24:])  # last 20 bytes
    except Exception:
        return None


def _watch_mempool(chain: str):
    cfg = CHAINS[chain]

    # Subscribe to newPendingTransactions with full tx bodies (True flag)
    sub_msg = json.dumps({
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "eth_subscribe",
        "params":  ["newPendingTransactions", True],   # True = full body
    })

    def on_message(ws, message):
        try:
            data = json.loads(message)
            tx   = data.get("params", {}).get("result")
            if not tx or not isinstance(tx, dict):
                return

            # Filter: only BOI contract txs
            if (tx.get("to") or "").lower() != cfg["contract"].lower():
                return

            subject = _decode_subject(tx.get("input", ""))
            if not subject:
                return

            log(f"🔭 [{chain.upper()}] Mempool → {subject[:12]}…")
            threading.Thread(
                target=fire,
                args=("mp_" + subject[:8], subject, chain, "MEMPOOL"),
                daemon=True,
            ).start()

        except Exception:
            pass

    def on_error(ws, err):
        log(f"⚠️  [{chain.upper()}] WS: {err}")

    def on_close(ws, *_):
        log(f"🔌 [{chain.upper()}] WS closed — retry in 3s")
        time.sleep(3)
        _watch_mempool(chain)

    def on_open(ws):
        log(f"✅ [{chain.upper()}] Mempool WS live")
        ws.send(sub_msg)

    websocket.WebSocketApp(
        cfg["rpc_ws"],
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    ).run_forever(ping_interval=30, ping_timeout=10)


# ══════════════════════════════════════════════════════
#  HTTP FALLBACK POLLER
# ══════════════════════════════════════════════════════

_seen   = set()
_seeded = False

def _http_loop():
    global _seeded
    log("🌐 HTTP fallback started")
    while True:
        try:
            res = requests.get(API_URL, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                users = res.json().get("users", [])
                if not _seeded:
                    _seen.update(u["id"] for u in users)
                    _seeded = True
                    log(f"🌱 HTTP seeded {len(_seen)} users")
                else:
                    for u in users:
                        if u["id"] not in _seen:
                            _seen.add(u["id"])
                            uname  = u.get("username", "unknown")
                            wallet = u.get("wallet_address")
                            chain  = u.get("selected_chain", "").lower()
                            log(f"🌐 [HTTP] @{uname} | {chain}")
                            if wallet and chain in CHAINS:
                                threading.Thread(
                                    target=fire,
                                    args=(uname, wallet, chain, "HTTP"),
                                    daemon=True,
                                ).start()
        except Exception as e:
            log(f"⚠️  HTTP: {e}")
        time.sleep(HTTP_POLL_INTERVAL)


# ══════════════════════════════════════════════════════
#  PERIODIC RE-CALIBRATION  (every 5 min)
# ══════════════════════════════════════════════════════

def _recal_loop():
    while True:
        time.sleep(300)
        log("🔄 Re-calibrating…")
        for c in clients.values():
            c.calibrate()


# ══════════════════════════════════════════════════════
#  INIT
# ══════════════════════════════════════════════════════

clients: dict[str, ChainClient] = {}

def init():
    for name in CHAINS:
        log(f"🔌 [{name.upper()}] Initialising…")
        c = ChainClient(name)
        c.init()
        clients[name] = c
    log("")
    log("📐 Calibrating prices…")
    for c in clients.values():
        c.calibrate()
    log("")


# ══════════════════════════════════════════════════════
#  BANNER
# ══════════════════════════════════════════════════════

def banner():
    print()
    print("╔═══════════════════════════════════════════════════╗")
    print("║    BOI THE BEAR — MAX SPEED SNIPER  v5.0         ║")
    print("╠═══════════════════════════════════════════════════╣")
    print(f"║  💳 {MY_WALLET[:38]}")
    print(f"║  ⛓️  AVALANCHE + BSC")
    print(f"║  🎯 {SHARE_AMOUNT} units | 🛡️ {int((SLIPPAGE-1)*100)}% slip | ⛽ {PRIORITY_GWEI} gwei priority")
    print(f"║  💣 Gas war: {GAS_WAR_MULTIPLIERS} multipliers")
    print(f"║  🔭 Mempool WS — full tx body (no extra RPC)")
    print(f"║  🌐 HTTP fallback — {int(HTTP_POLL_INTERVAL*1000)}ms")
    print(f"║  📐 Auto-calibrate — startup + post-snipe + 5min")
    print(f"║  🚫 Zero extra RPC calls at fire time")
    print("╚═══════════════════════════════════════════════════╝")
    print()


# ══════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    banner()
    init()

    # ── Manual sells (uncomment + exit) ──────────────
    # sell_shares("username", "0xWALLET", "bsc", amount="all")
    # sell_all_holdings()
    # exit()
    # ─────────────────────────────────────────────────

    tg(
        f"🤖 <b>BOI Max Speed Sniper v5.0</b>\n\n"
        f"💳 <code>{MY_WALLET}</code>\n"
        f"⛓️ AVAX + BSC\n"
        f"💣 Gas war: {GAS_WAR_MULTIPLIERS}\n"
        f"🔭 Mempool: LIVE\n"
        f"📐 Price: AUTO-CALIBRATED\n"
        f"🚫 Zero RPC calls at fire time"
    )

    # Mempool watchers
    for chain in CHAINS:
        threading.Thread(target=_watch_mempool, args=(chain,), daemon=True).start()

    # HTTP fallback
    threading.Thread(target=_http_loop, daemon=True).start()

    # Re-calibration loop
    threading.Thread(target=_recal_loop, daemon=True).start()

    log("🚀 Running. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(60)
            log("💓 alive")
    except KeyboardInterrupt:
        log("🛑 Stopped.")