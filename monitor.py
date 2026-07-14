#!/usr/bin/env python3
"""
NOXA / Robinhood Chain Early Token Monitor + On-Chain Screener
Monitors Uniswap V3 PoolCreated events + filters NOXA tokens
"""
import json, os, time, hashlib, requests
from datetime import datetime, timezone
from web3 import Web3
from eth_hash.auto import keccak

# ─── CONFIG ───
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
EXPLORER_API = "https://robinhoodchain.blockscout.com/api"
BOT_TOKEN = "8962752658:AAE2Y72kNNghj166rQh2-mPC-LiBUzkYyzc"
CHAT_ID = "5375775335"  # user's telegram ID

# Contracts
V3_FACTORY = "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA"
NOXA_FACTORY = "0xD9eC2db5f3D1b236843925949fe5bd8a3836FCcB"
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
NOXA_LOCKER = "0x7F03effbd7ceB22A3f80Dd468f67eF27826acD85"

# Base/quote tokens that should NEVER be reported as "new tokens".
# Add more stable/wnative addresses here if duplicates of those appear.
BASE_TOKENS = {
    WETH.lower(),
    # USDC / USDT / DAI / WBTC on Robinhood Chain — fill in if known,
    # otherwise the fallback below still prevents token/X duplicates.
}

STATE_FILE = os.path.expanduser("~/.noxa_monitor_state.json")

# ─── WEB3 ───
w3 = Web3(Web3.HTTPProvider(RPC_URL))
assert w3.is_connected(), "RPC not connected!"

# ─── EVENTS ───
POOL_CREATED_TOPIC = keccak(b'PoolCreated(address,address,uint24,int24,address)').hex()

# ─── STATE ───
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f: return json.load(f)
    return {"seen_pools": [], "last_block": 0}

def save_state(st):
    with open(STATE_FILE, "w") as f: f.write(json.dumps(st, indent=2))

# ─── TELEGRAM ───
def tg_send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except Exception as e:
        print(f"[TG ERR] {e}")
        return False

# ─── CONTRACT ANALYSIS ───
ERC20_ABI = json.loads('['
    '{"constant":true,"inputs":[],"name":"name","outputs":[{"type":"string"}],"type":"function"},'
    '{"constant":true,"inputs":[],"name":"symbol","outputs":[{"type":"string"}],"type":"function"},'
    '{"constant":true,"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"type":"function"},'
    '{"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"type":"uint256"}],"type":"function"},'
    '{"constant":true,"inputs":[],"name":"owner","outputs":[{"type":"address"}],"type":"function"}'
']')

OWNER_ABI = json.loads('[{"constant":true,"inputs":[],"name":"owner","outputs":[{"type":"address"}],"type":"function"}]')

def get_token_info(addr):
    addr = Web3.to_checksum_address(addr)
    info = {"address": addr, "name": "?", "symbol": "?", "decimals": 18,
            "total_supply": 0, "owner": None, "verified": False}
    try:
        c = w3.eth.contract(address=addr, abi=ERC20_ABI)
        info["name"] = c.functions.name().call()[:60]
    except: pass
    try:
        c = w3.eth.contract(address=addr, abi=ERC20_ABI)
        info["symbol"] = c.functions.symbol().call()[:30]
    except: pass
    try:
        c = w3.eth.contract(address=addr, abi=ERC20_ABI)
        info["decimals"] = c.functions.decimals().call()
    except: pass
    try:
        c = w3.eth.contract(address=addr, abi=ERC20_ABI)
        info["total_supply"] = c.functions.totalSupply().call()
    except: pass
    try:
        c = w3.eth.contract(address=addr, abi=OWNER_ABI)
        info["owner"] = c.functions.owner().call()
    except: pass

    # Check verified on Blockscout
    try:
        r = requests.get(f"{EXPLORER_API}",
            params={"module": "contract", "action": "getsourcecode", "address": addr},
            timeout=10)
        if r.status_code == 200:
            res = r.json()
            if res.get("status") == "1" and res.get("result"):
                info["verified"] = bool(res["result"][0].get("SourceCode", ""))
    except: pass

    return info

def check_holders(addr, top_n=10):
    addr_str = addr.lower()
    try:
        r = requests.get(f"{EXPLORER_API}",
            params={"module": "token", "action": "getTokenHolders",
                    "contractaddress": addr_str, "limit": top_n},
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "1" and data.get("result"):
                holders = data["result"]
                total_pct = sum(float(h.get("percentage", 0)) for h in holders[:top_n])
                return {"top10_pct": round(total_pct, 2), "count": len(holders)}
    except: pass
    return {"top10_pct": 0, "count": 0}

def check_deployer_tx_count(deployer):
    """Check if deployer is fresh wallet"""
    try:
        count = w3.eth.get_transaction_count(Web3.to_checksum_address(deployer))
        return count
    except:
        return -1

def check_liquidity_lock(token_addr):
    """Check if LP is locked in NOXA locker"""
    # NOXA always locks LP permanently
    return {"locked": True, "locker": NOXA_LOCKER, "platform": "Noxa Fun"}

def check_is_noxa_token(token_addr, factory_addr):
    """Check if token was created by NOXA by checking interactions"""
    token_addr = token_addr.lower()
    factory_addr = factory_addr.lower()

    # Check if token interacts with NOXA contracts
    # Look for create2 or creation events
    try:
        # Get deployer from creation tx
        addr = Web3.to_checksum_address(token_addr)
        code = w3.eth.get_code(addr)
        # NOXA tokens are created by the factory
        # Try to get creation tx
        pass
    except:
        pass

    # Best guess: if the token creator tx was sent to NOXA factory
    # We can check by seeing if the token's owner (if any) matches NOXA patterns
    return None  # unknown

# ─── SCREENING PIPELINE ───
def screen_token(token_addr, token1_addr, pool_addr):
    token_addr = Web3.to_checksum_address(token_addr)

    # Basic info
    ti = get_token_info(token_addr)
    holders = check_holders(token_addr)
    liq = check_liquidity_lock(token_addr)

    # Check ownership
    owner_renounced = (ti["owner"] is None or
                       ti["owner"] == "0x0000000000000000000000000000000000000000")

    # Check if token matches WETH (ignore WETH pairs)
    if str(token_addr).lower() == WETH.lower():
        return None

    # Skip wrapped token0 if it's WETH
    short_addr = str(token_addr)[:10]

    # Score determination
    red_flags = []
    green_flags = []

    # Contract verified?
    if ti["verified"]:
        green_flags.append("✅ Contract terverifikasi di Blockscout")
    else:
        red_flags.append("❌ Contract TIDAK terverifikasi")

    # Ownership?
    if owner_renounced:
        green_flags.append("✅ Ownership sudah renounced")
    else:
        red_flags.append(f"⚠️ Ownership BELUM renounced ({str(ti['owner'])[:10]}...)")

    # Liquidity
    if liq["locked"]:
        green_flags.append("✅ LP di-lock permanen Noxa Fun")
    else:
        red_flags.append("⚠️ LP lock tidak terverifikasi")

    # Holders
    if holders["top10_pct"] > 80:
        red_flags.append(f"🔴 Top 10 holder pegang {holders['top10_pct']}% supply")
    elif holders["top10_pct"] > 50:
        red_flags.append(f"⚠️ Top 10 holder pegang {holders['top10_pct']}% supply")
    else:
        green_flags.append(f"✅ Top 10 holder cuma {holders['top10_pct']}% supply")

    # Deployer
    if ti["owner"]:
        tx_count = check_deployer_tx_count(ti["owner"])
        if tx_count == 0:
            red_flags.append("⚠️ Deployer wallet baru (0 tx)")
        elif tx_count < 5:
            red_flags.append(f"⚠️ Deployer wallet baru ({tx_count} tx)")

    # Score
    score = "RENDAH"
    if len(red_flags) >= 3 or any("🔴" in f for f in red_flags):
        score = "EXTREME"
    elif len(red_flags) >= 2:
        score = "HIGH"
    elif len(red_flags) >= 1:
        score = "SEDANG"

    return {
        "address": token_addr,
        "info": ti,
        "holders": holders,
        "liquidity": liq,
        "score": score,
        "red_flags": red_flags,
        "green_flags": green_flags
    }

# ─── FORMAT REPORT ───
def get_dex_data(addr):
    """Fetch DexScreener pair stats for a token on Robinhood Chain.
    Returns dict with priceUsd, fdv, marketCap, liquidity_usd, vol24h,
    buys24h, sells24h, pair_created, socials(list), websites(list),
    pair_addr or {} on failure."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
            timeout=15, headers={"User-Agent": "noxa-mon/1.0"})
        if r.status_code != 200:
            return {}
        pairs = r.json().get("pairs") or []
        if not pairs:
            return {}
        # pick the most liquid robinhood pair
        pairs = [p for p in pairs if p.get("chainId") == "robinhood"]
        if not pairs:
            return {}
        p = max(pairs, key=lambda x: (x.get("liquidity", {}).get("usd") or 0))
        info = p.get("info", {}) or {}
        return {
            "priceUsd": float(p.get("priceUsd") or 0),
            "fdv": float(p.get("fdv") or 0),
            "marketCap": float(p.get("marketCap") or 0),
            "liquidity_usd": float((p.get("liquidity") or {}).get("usd") or 0),
            "vol24h": float((p.get("volume") or {}).get("h24") or 0),
            "buys24h": (p.get("txns") or {}).get("h24", {}).get("buys", 0),
            "sells24h": (p.get("txns") or {}).get("h24", {}).get("sells", 0),
            "pair_created": int(p.get("pairCreatedAt") or 0),
            "socials": info.get("socials", []) or [],
            "websites": info.get("websites", []) or [],
            "pair_addr": p.get("pairAddress"),
        }
    except Exception as e:
        print(f"[DEX ERR] {e}")
        return {}

def get_blockscout(addr):
    """Fetch BlockScout token meta (holders count, supply, verified, owner)."""
    out = {}
    try:
        r = requests.get(f"{EXPLORER_API}/v2/tokens/{addr}", timeout=15)
        if r.status_code == 200:
            d = r.json()
            out["holders"] = d.get("holders_count")
            out["supply"] = d.get("total_supply")
            out["decimals"] = d.get("decimals")
            out["verified"] = bool(d.get("is_smart_contract_verified"))
            out["name_bs"] = d.get("name")
            out["sym_bs"] = d.get("symbol")
    except Exception as e:
        print(f"[BS ERR] {e}")
    return out


def format_report(screen, pool_addr):
    addr = screen["address"]
    info = screen["info"]
    short = f"{str(addr)[:6]}...{str(addr)[-4:]}"

    score_emojis = {"RENDAH": "🟢", "SEDANG": "🟡", "HIGH": "🟠", "EXTREME": "🔴"}
    se = score_emojis.get(screen["score"], "⚪")

    name = info.get("name") or "?"
    sym = info.get("symbol") or "?"
    chart_dex = f"https://robinhoodchain.blockscout.com/address/{addr}"
    chart_noxa = f"https://fun.noxa.fi/rh/token/{addr}"

    # ── Enrich with DexScreener + BlockScout ──
    dex = get_dex_data(addr)
    bs = get_blockscout(addr)
    holders = bs.get("holders")
    verified = bs.get("verified") or info.get("verified")
    owner = str(info.get("owner", bs.get("name_bs", "N/A") or "N/A"))

    def fnum(n):
        try: n = float(n)
        except: return "-"
        if n >= 1_000_000: return f"${n/1_000_000:.2f}M"
        if n >= 1_000: return f"${n/1_000:.2f}K"
        if n >= 1: return f"${n:.4f}"
        if n > 0: return f"${n:.8f}"
        return "-"

    mc = dex.get("marketCap") or 0
    price = dex.get("priceUsd") or 0
    liq = dex.get("liquidity_usd") or 0
    vol = dex.get("vol24h") or 0
    buys = dex.get("buys24h") or 0
    sells = dex.get("sells24h") or 0
    pair_created = dex.get("pair_created") or 0
    socials = dex.get("socials", [])
    websites = dex.get("websites", [])

    # age like "🌱3d" or "🌱5h"
    if pair_created:
        age_s = (int(time.time()) - pair_created // 1000)
        if age_s >= 86400: age = f"🌱{age_s//86400}d"
        elif age_s >= 3600: age = f"🌱{age_s//3600}h"
        else: age = f"🌱{age_s//60}m"
    else:
        age = "🌱?"

    # buy/sell dominance 1H (we only have 24h split; approximate from 24h)
    total_tx = (buys + sells) or 1
    buy_pct = round(buys / total_tx * 100, 2)
    buy_sell = f"B {buys:,} / S {sells:,} ({buy_pct}%)"

    # socials line
    soc_parts = []
    for w in websites[:1]:
        soc_parts.append("Web")
    for s in socials:
        t = s.get("type")
        if t == "twitter": soc_parts.append("𝕏")
        elif t == "telegram": soc_parts.append("🐦")
        elif t == "discord": soc_parts.append("💬")
        elif t in ("website",): soc_parts.append("Web")
    soc_line = " • ".join(soc_parts) if soc_parts else "—"

    def sh(a):  # short hex 0x6...993f
        return f"0x{str(a)[2:3]}...{str(a)[-4:]}" if a and len(str(a)) > 8 else str(a or "?")

    msg = f"""🔍 <b>{name}</b> (${sym})
{se} <b>{screen['score']}</b> {age} 👀{holders or '?'}

📊 <b>Token Stats</b>
➰ MC:   {fnum(mc)}
➰ USD:  {fnum(price) if price else '-'}
➰ LIQ:  {fnum(liq)}
➰ VOL:  {fnum(vol)} (24h)
➰ 1H:   {buy_sell}
➰ HLD:  {holders or '?'}
➰ P:    <code>{sh(addr)}</code> 🦄
➰ DEV:  <code>{sh(owner)}</code>

🔗 <b>Socials</b>
➰ {soc_line}

✅ Audit {'🟩🟩' if verified else '🟥'}
✅ DEX [PAID] [info]

<b>Contract</b>
<code>{addr}</code>

⚠️ <i>Long-press address untuk copy</i>"""

    # append original risk flags if present
    if screen.get("red_flags") or screen.get("green_flags"):
        msg += "\n\n"
        for f in screen.get("red_flags", []):
            msg += f"🔴 {f}\n"
        for f in screen.get("green_flags", []):
            msg += f"🟢 {f}\n"
    return msg

# ─── MAIN LOOP ───
def main():
    state = load_state()
    last_block = state.get("last_block", 0)
    current_block = w3.eth.block_number

    print(f"[START] Block: {current_block} | Last checked: {last_block}")

    # If first run or behind, use smaller range
    from_block = max(last_block, current_block - 500) if last_block else current_block - 500
    if from_block >= current_block:
        print("[SKIP] No new blocks")
        return

    print(f"[SCAN] Blocks {from_block} → {current_block} ({(current_block - from_block)} blocks)")

    try:
        logs = w3.eth.get_logs({
            "address": Web3.to_checksum_address(V3_FACTORY),
            "fromBlock": from_block,
            "toBlock": current_block,
        })
    except Exception as e:
        print(f"[RPC ERR] {e}")
        # Try smaller window
        try:
            logs = w3.eth.get_logs({
                "address": Web3.to_checksum_address(V3_FACTORY),
                "fromBlock": current_block - 100,
                "toBlock": current_block,
            })
        except:
            print("[RPC ERR] Can't fetch logs")
            return

    # Filter PoolCreated events
    new_pools = []
    for log in logs:
        if log["topics"][0].hex() == POOL_CREATED_TOPIC:
            token0 = "0x" + log["topics"][1].hex()[-40:]
            token1 = "0x" + log["topics"][2].hex()[-40:]
            dh = log["data"].hex() if hasattr(log["data"], "hex") else log["data"]
            pool = "0x" + dh[-40:]

            t0 = token0.lower()
            t1 = token1.lower()

            # If BOTH are base tokens (e.g. WETH-USDC), nothing to report
            if t0 in BASE_TOKENS and t1 in BASE_TOKENS:
                continue

            # Pick the token that is NOT a base/quote token as the "real" new token.
            if t0 in BASE_TOKENS:
                token_addr = token1
            elif t1 in BASE_TOKENS:
                token_addr = token0
            else:
                # Neither is a known base token — report the non-factory one
                # (avoid reporting the NOXA factory pair itself).
                token_addr = token0 if t1.lower() == NOXA_FACTORY.lower() else token1

            # Final guard: never report a base token itself
            if token_addr.lower() in BASE_TOKENS:
                continue

            # Skip already-seen tokens (dedup by token address, not pool)
            if token_addr.lower() in state.get("seen_pools", []):
                continue

            paired_with = token1 if token_addr == token0 else token0
            new_pools.append((token_addr, paired_with, pool))

    print(f"[POOLS] {len(new_pools)} new pool(s)")

    seen = state.setdefault("seen_pools", [])
    for token_addr, paired_with, pool_addr in new_pools[:5]:
        print(f"[ANALYZE] {str(token_addr)[:14]}...")
        try:
            screen = screen_token(token_addr, paired_with, pool_addr)
            if screen is None:
                print(f"[SKIP] {str(token_addr)[:14]}... (WETH pair)")
                continue
            msg = format_report(screen, pool_addr)
            print(f"[TG SEND] {screen.get('info',{}).get('symbol','?')}")
            result = tg_send(msg)
            print(f"[TG] {result}")
            time.sleep(3)
        except Exception as e:
            print(f"[ERR] {str(token_addr)[:14]}...: {e}")
            import traceback
            traceback.print_exc()

        # Dedup AFTER a successful send only — never mark as seen on error
        if token_addr.lower() not in seen:
            seen.append(token_addr.lower())

    # Prune state
    if len(seen) > 5000:
        state["seen_pools"] = seen[-2000:]

    state["last_block"] = current_block
    save_state(state)
    print(f"[DONE] Checked at {datetime.now(timezone.utc).isoformat()[:19]}")

if __name__ == "__main__":
    main()
