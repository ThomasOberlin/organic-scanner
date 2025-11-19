import streamlit as st
import requests
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
# Load ALL 7 languages
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'

# --- IOTA CONFIG ---
IOTA_RPC_URL = "https://api.testnet.iota.cafe" 
SIMULATION_MODE = True 
YOUR_TOKEN_ID = "0x123_PLACEHOLDER"

# --- 1. EXPIRY KEYWORDS (Dates) ---
EXPIRY_KEYWORDS = [
    "valid until", "expiry date", "date of expiry", "validity", "expires",
    "certificate valid", "valid from", "valid to", 
    "gültig bis", "ablaufdatum", "valable jusqu'au", "date d'expiration",
    "data di scadenza", "valido fino al", "fecha de caducidad", "válido hasta",
    "geldig tot", "vervaldatum", "válido até", "data de validade"
]

# --- 2. BAD STATUS KEYWORDS (Fraud Check) ---
STATUS_BAD_KEYWORDS = [
    "suspended", "withdrawn", "revoked", "cancelled", "invalid",
    "suspendu", "retiré", "révoqué", "suspendiert", "widerrufen", "ungültig",
    "sospeso", "revocato", "suspendido", "revocado", "geschorst", "ingetrokken",
    "suspenso", "revogado", "anulado"
]

# --- 3. SECTION HEADERS (For Smart Extraction) ---
# We use these to find the start of specific boxes in the EU Certificate

HEADER_DOC_NUM = ["document number", "nummer", "numéro", "numero", "número"]

HEADER_OPERATOR = [
    "name and address of the operator", "operator or group", "operator",
    "name und anschrift des unternehmers", "betreiber",
    "nom et adresse de l'opérateur", "opérateur"
]

HEADER_AUTHORITY = [
    "control authority", "control body", "competent authority", "code number",
    "kontrollstelle", "kontrollbehörde", "codenummer",
    "autorité de contrôle", "organisme de contrôle",
    "autorità di controllo", "organismo de control"
]

HEADER_ACTIVITY = [
    "activity or activities", "activities", "tätigkeit", "activité", "attività", "actividad"
]

HEADER_PRODUCTS = [
    "category or categories", "category of products", "products",
    "erzeugniskategorie", "kategorie",
    "catégorie de produits", "categoria"
]

# --- FUNCTIONS ---

def check_token_balance(address):
    if SIMULATION_MODE: return address.startswith("0x")
    return False 

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
    """Finds the expiration date."""
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
    """Checks for bad status keywords."""
    text_lower = text.lower()
    for kw in STATUS_BAD_KEYWORDS:
        if kw in text_lower:
            return True, kw.upper()
    return False, None

def extract_section_data(text, start_keywords, lines_to_grab=4):
    """
    Generic function to find a header and grab the text immediately following it.
    This extracts the 'Value' inside the 'Box'.
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line_lower = line.lower()
        # Check if any of the start_keywords are in this line
        if any(kw in line_lower for kw in start_keywords):
            # We found the header! Capture the next few lines.
            # We skip lines that are too short (noise)
            captured_data = []
            scan_range = min(len(lines), i + 1 + lines_to_grab)
            
            for j in range(i + 1, scan_range):
                content = lines[j].strip()
                if len(content) > 3: # Filter out noise/empty lines
                    captured_data.append(content)
            
            return "\n".join(captured_data) if captured_data else "Found header, but data was unclear."
            
    return "Not Detected"

# --- APP UI ---

st.set_page_config(page_title="EU Organic Scanner", layout="wide")

# SIDEBAR LOGIN
st.sidebar.title("🔐 Client Login")
wallet_address = st.sidebar.text_input("Wallet Address", placeholder="0x...")
has_access = False

if wallet_address:
    if check_token_balance(wallet_address):
        st.sidebar.success("✅ Access Granted")
        has_access = True
    else:
        st.sidebar.error("❌ Access Denied")

# MAIN APP
if has_access:
    st.title("🌱 Organic Certificate Scanner")
    st.info("✅ **Active Languages:** English, German, French, Italian, Spanish, Dutch, Portuguese")
    
    uploaded_file = st.file_uploader("Upload Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Analyzing Certificate Structure...'):
            text = extract_text(uploaded_file)
        
        if text:
            # 1. Run Intelligence Extraction
            expiry = find_date(text)
            is_bad, bad_word = check_fraud(text)
            
            # Extracting specific boxes based on EU Standard Layout
            doc_number = extract_section_data(text, HEADER_DOC_NUM, lines_to_grab=1)
            operator_data = extract_section_data(text, HEADER_OPERATOR, lines_to_grab=3)
            authority_data = extract_section_data(text, HEADER_AUTHORITY, lines_to_grab=3)
            activity_data = extract_section_data(text, HEADER_ACTIVITY, lines_to_grab=5)
            products_data = extract_section_data(text, HEADER_PRODUCTS, lines_to_grab=4)

            # 2. Display Dashboard
            st.markdown("---")
            
            # Row 1: Critical Status
            c1, c2, c3 = st.columns(3)
            
            with c1:
                st.subheader("🚦 Compliance")
                if is_bad: 
                    st.error(f"🚨 FRAUD ALERT: '{bad_word}'")
                elif expiry and (expiry - datetime.now()).days < 0:
                    st.error("❌ EXPIRED")
                else: 
                    st.success("✅ Status: Active")
            
            with c2:
                st.subheader("📅 Validity")
                if expiry:
                    days = (expiry - datetime.now()).days
                    color = "red" if days < 60 else "orange" if days < 90 else "green"
                    st.markdown(f":{color}[**Expires: {expiry.strftime('%Y-%m-%d')}**]")
                    st.caption(f"({days} days remaining)")
                else:
                    st.warning("Date not detected")

            with c3:
                st.subheader("📄 Doc ID")
                st.markdown(f"**{doc_number}**")

            st.markdown("---")

            # Row 2: Detailed Extraction
            col_left, col_right = st.columns(2)
            
            with col_left:
                st.markdown("### 🏭 Operator / Farm")
                st.info(operator_data)
                
                st.markdown("### 📦 Certified Products")
                st.write(products_data)

            with col_right:
                st.markdown("### ⚖️ Certifying Authority")
                st.warning(authority_data)
                
                st.markdown("### 🚜 Activities Certified")
                st.caption(activity_data)

            # Row 3: Raw Data
            with st.expander("🔍 View Full Scanned Text (Debug)"):
                st.text(text)

else:
    st.title("🔒 Restricted Access")
    st.warning("Please login via the Sidebar.")