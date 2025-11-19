import streamlit as st
import requests
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
# This loads ALL 7 languages into the brain at once
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'

# --- IOTA CONFIG ---
IOTA_RPC_URL = "https://api.testnet.iota.cafe" 
SIMULATION_MODE = True 
YOUR_TOKEN_ID = "0x123_PLACEHOLDER"

# --- KEYWORDS ---
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

# --- FUNCTIONS ---

def check_token_balance(address):
    if SIMULATION_MODE: return address.startswith("0x")
    return False # Placeholder for real logic

def extract_text(file):
    text = ""
    try:
        if file.type == "application/pdf":
            images = convert_from_bytes(file.read())
            for img in images:
                # We force Tesseract to look for all languages
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

# --- APP UI ---

st.set_page_config(page_title="EU Organic Scanner", layout="wide")

# SIDEBAR
st.sidebar.title("🔐 Client Login")
wallet_address = st.sidebar.text_input("Wallet Address", placeholder="0x...")
has_access = False

if wallet_address:
    if check_token_balance(wallet_address):
        st.sidebar.success("✅ Access Granted")
        has_access = True
    else:
        st.sidebar.error("❌ Access Denied")

# MAIN SCREEN
if has_access:
    st.title("🌱 Organic Certificate Scanner")
    
    # --- EVIDENCE OF LANGUAGES ---
    st.info("✅ **Active Languages:** English, German, French, Italian, Spanish, Dutch, Portuguese")
    
    uploaded_file = st.file_uploader("Upload Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Scanning with Multi-Language OCR Engine...'):
            text = extract_text(uploaded_file)
        
        if text:
            expiry = find_date(text)
            is_bad, bad_word = check_fraud(text)
            farm = extract_farm_name(text)
            
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Risk Analysis")
                if is_bad: st.error(f"🚨 CRITICAL: Found '{bad_word}'")
                else: st.success("✅ Status: Clear")
                
                if expiry:
                    days = (expiry - datetime.now()).days
                    st.metric("Expiration", expiry.strftime("%Y-%m-%d"), f"{days} days")
                    if days < 0: st.error("EXPIRED")
                    elif days < 60: st.error("⚠️ Expires < 60 days")
                else: st.warning("Date not found.")
            
            with c2:
                st.subheader("Farm Details")
                st.info(farm)
                
                # DEBUG EXPANDER
                with st.expander("🔍 View Raw Scanned Text"):
                    st.write(text)
else:
    st.title("🔒 Restricted Access")
    st.warning("Please login via the Sidebar.")