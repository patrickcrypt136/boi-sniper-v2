from web3 import Web3
from dotenv import load_dotenv
import requests
import os

load_dotenv()

COOKIE    = os.getenv("COOKIE")
MY_WALLET = os.getenv("MY_WALLET")

API_URL = "https://www.boithebear.com/api/socialfi/new-arrivals?limit=10&offset=0&userId=6363489c-f1de-4ddc-9029-91327f17063d"

HEADERS = {
    "accept":     "*/*",
    "cookie":     COOKIE,
    "referer":    "https://www.boithebear.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36"
}

ABI = [
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

# Test 1 — API
print("Testing API...")
res   = requests.get(API_URL, headers=HEADERS, timeout=15)
users = res.json().get("users", [])
print(f"✅ Got {len(users)} users")

# Test 2 — Check supply for each user
print("\nChecking supply for each user...")
for u in users:
    wallet = u.get("wallet_address")
    chain  = u.get("selected_chain", "").lower()
    uname  = u.get("username")

    if not wallet:
        print(f"⚠️ @{uname} — no wallet")
        continue

    rpc = None
    contract_addr = None

    if chain == "bsc":
        rpc           = "https://bsc-dataseed1.defibit.io"
        contract_addr = "0xC12ab9BC529809d6041564FE6aC65FAF8e190E7B"
    elif chain == "avalanche":
        rpc           = "https://api.avax.network/ext/bc/C/rpc"
        contract_addr = "0x2Fec21938e4d11117Bda59a5fE880c1d0AE54A7F"
    else:
        print(f"⏭️ @{uname} — chain '{chain}' not supported")
        continue

    try:
        w3       = Web3(Web3.HTTPProvider(rpc))
        contract = w3.eth.contract(address=Web3.to_checksum_address(contract_addr), abi=ABI)
        supply   = contract.functions.sharesSupply(Web3.to_checksum_address(wallet)).call()
        print(f"@{uname} | chain: {chain} | supply: {supply} | {'🟢 ZERO BUY' if supply == 0 else '🔴 already bought'}")
    except Exception as e:
        print(f"❌ @{uname} — error: {e}")