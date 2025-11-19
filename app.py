import streamlit as st
import requests
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'

# --- IOTA BLOCKCHAIN CONFIGURATION ---
# This is the IOTA Rebased Testnet Node
IOTA_RPC_URL = "https://api.testnet.iota.cafe" 

# THIS IS THE KEY VARIABLE:
# Once you create your own token, you will paste its ID here.
# For now, we use a "Simulation Mode" so you can see the app work.
SIMULATION_MODE = True 
YOUR_TOKEN_ID = "0x123...PasteYourRealTokenIDHere"

# --- KEYWORDS (Scanner Logic) ---
EXPIRY_KEYWORDS = [
    "valid until", "expiry date", "date of expiry", "validity", "expires",
    "gültig bis", "ablaufdatum", "valable jusqu'au", "date d'expiration",
    "data di scadenza", "valido fino al", "fecha de caducidad", "válido hasta",
    "geldig tot", "vervaldatum", "válido até", "data de validade"
]

STATUS_BAD_KEYWORDS = [
    "suspended", "withdrawn", "revoked", "cancelled", "invalid",
    "suspendu", "retiré", "révoqué", "suspendiert", "widerrufen", "ungültig",
    "sospeso", "revocato", "suspendido", "revocado", "geschorst", "ingetrokken",
    "suspenso", "revogado", "anulado"
]

OPERATOR_KEYWORDS = [
    "operator", "producer", "name of operator", "manufacturer",
    "erzeuger", "unternehmen", "opérateur", "producteur",
    "operatore", "produttore", "exploitant", "producent",
    "operador", "produtor", "fabricante"
]

# --- BLOCKCHAIN FUNCTIONS ---

def check_token_balance(address):
    """Checks the IOTA blockchain for your token."""
    if SIMULATION_MODE:
        # In simulation, any address starting with '0x' is accepted
        return address.startswith("0x")
    
    # REAL LOGIC (For later)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "suix_getBalance", # IOTA Rebased uses the Sui API structure
        "params": [address, YOUR_TOKEN_ID]
    }
    try:
        response = requests.post(IOTA_RPC_URL, json=payload, timeout=5).json()
        if "result" in response:
            balance = int(response["result"].get("totalBalance", 0))
            return balance > 0
    except:
        return False
    return False

# --- SCANNER FUNCTIONS ---

def extract_text(file):
    text = ""
    try:
        if file.type == "application/pdf":
            images = convert_from_bytes(file.read())
            for img in images:
                text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG) + "\n"
        else:
            img = Image.open(file)
            text = pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG)
    except Exception as e:
        st.error(f"Error: {e}")
    return text

def find_date(text):
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line_lower = line.lower()
        for kw in EXPIRY_KEYWORDS:
            if kw in line_lower:
                scan_text = line + " " + (lines[i+1] if i+1 < len(lines) else "")
                match = re.search(r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})', scan_text)
                if match:
                    try:
                        return parser.parse(match.group(0), dayfirst=True)
                    except:
                        continue
    return None

def check_fraud(text):
    text_lower = text.lower()
    for kw in STATUS_BAD_KEYWORDS:
        if kw in text_lower:
            return True, kw.upper()
    return False, None

def extract_farm_name(text):
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line_lower = line.lower()
        for kw in OPERATOR_KEYWORDS:
            if kw in line_lower and len(line) < 100:
                return "\n".join(lines[i+1 : i+3]).strip()
    return "Not detected automatically"

# --- APP INTERFACE ---

st.set_page_config(page_title="EU Organic Scanner", layout="wide")

# 1. THE SIDEBAR (Login)
st.sidebar.title("🔐 Client Login")
st.sidebar.markdown("Access to this scanner is restricted to **Utility Token Holders**.")

wallet_address = st.sidebar.text_input("Enter IOTA Wallet Address", placeholder="0x...")
check_button = st.sidebar.button("Verify Access")

has_access = False

if wallet_address:
    if check_token_balance(wallet_address):
        st.sidebar.success("✅ Token Verified")
        has_access = True
    else:
        st.sidebar.error("❌ Access Denied: No Token Found")

# 2. THE MAIN APP (Guarded)
if has_access:
    st.title("🌱 Organic Certificate Scanner (Premium)")
    st.markdown("**Status:** Active | **License:** Valid Utility Token")
    
    uploaded_file = st.file_uploader("Upload Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Processing...'):
            text = extract_text(uploaded_file)
        
        if text:
            expiry = find_date(text)
            is_bad, bad_word = check_fraud(text)
            farm = extract_farm_name(text)
            
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Risk Analysis")
                if is_bad:
                    st.error(f"🚨 CRITICAL: Found '{bad_word}'")
                else:
                    st.success("✅ Status: Clear")
                
                if expiry:
                    days = (expiry - datetime.now()).days
                    st.metric("Expiration", expiry.strftime("%Y-%m-%d"), f"{days} days")
                    if days < 0: st.error("EXPIRED")
                    elif days < 60: st.error("⚠️ Expires < 60 days")
                    elif days < 90: st.warning("⚠️ Expires < 90 days")
                    else: st.success("Valid")
                else:
                    st.warning("Date not found.")
            
            with c2:
                st.subheader("Farm Details")
                st.info(farm)
                with st.expander("Raw Text"):
                    st.text(text)
else:
    # BLOCKED STATE
    
    st.title("🔒 Restricted Access")
    st.markdown("""
    ### Welcome to the Organic Compliance Platform.
    
    To use this tool, you must hold the **Organic Utility Token** in your IOTA wallet.
    
    **How to Login:**
    1. Open the Sidebar (left).
    2. Enter your Wallet Address.
    3. Click Verify.
    """)