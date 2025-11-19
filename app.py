import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps, ImageEnhance
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'

# --- IOTA CONFIG ---
IOTA_RPC_URL = "https://api.testnet.iota.cafe" 
SIMULATION_MODE = True 

# --- KEYWORDS & ANCHORS ---
EXPIRY_KEYWORDS = [
    "valid until", "expiry date", "date of expiry", "validity", "expires",
    "certificate valid", "valid from", "valid to", 
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

# --- HELPER FUNCTIONS ---

def preprocess_image(img):
    """
    The 'Reading Glasses' for the AI.
    1. Converts to Greyscale (removes color noise)
    2. Increases Contrast (makes text darker)
    3. Sharpens the image
    """
    # 1. Grayscale
    img = ImageOps.grayscale(img)
    
    # 2. Increase Contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0) # Double the contrast
    
    # 3. Increase Sharpness
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.5)
    
    return img

def check_token_balance(address):
    if SIMULATION_MODE: return address.startswith("0x")
    return False 

def extract_text(file):
    text = ""
    try:
        if file.type == "application/pdf":
            # PDF: Render at 300 DPI (High Def) instead of default 200
            images = convert_from_bytes(file.read(), dpi=300)
            for img in images:
                img = preprocess_image(img)
                # --psm 3 is default, but --psm 6 assumes a single block of text
                text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 3') + "\n"
        else:
            # IMAGES: Resize (Zoom) if it's too small
            img = Image.open(file)
            
            # If image width is small (< 2000px), double it for better OCR
            if img.width < 2000:
                new_size = (img.width * 2, img.height * 2)
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            img = preprocess_image(img)
            text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 3')
            
    except Exception as e:
        st.error(f"Error: {e}")
    return text

def find_smart_date(text):
    candidates = []
    # Regex for EU dates: DD.MM.YYYY or DD/MM/YYYY or YYYY-MM-DD
    date_pattern = r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})|(\d{4}[./-]\d{1,2}[./-]\d{1,2})'
    
    matches = re.findall(date_pattern, text)
    for match in matches:
        date_str = match[0] if match[0] else match[1]
        try:
            dt = parser.parse(date_str, dayfirst=True)
            if 2020 < dt.year < 2035: 
                candidates.append(dt)
        except:
            continue

    if not candidates: return None
    return max(candidates)

def check_fraud(text):
    text_lower = text.lower()
    for kw in STATUS_BAD_KEYWORDS:
        if kw in text_lower:
            return True, kw.upper()
    return False, None

def extract_block(text, start_markers, end_markers):
    lines = text.split('\n')
    capture = False
    captured_lines = []
    
    for line in lines:
        line_lower = line.lower()
        if capture and any(end in line_lower for end in end_markers): break
        if capture:
            if len(line.strip()) > 3: captured_lines.append(line.strip())
        if not capture and any(start in line_lower for start in start_markers):
            capture = True
            
    return "\n".join(captured_lines).strip() if captured_lines else "Not Detected"

# --- APP UI ---

st.set_page_config(page_title="EU Organic Scanner", layout="wide")

st.sidebar.title("🔐 Client Login")
wallet_address = st.sidebar.text_input("Wallet Address", placeholder="0x...")
has_access = False

if wallet_address:
    if check_token_balance(wallet_address):
        st.sidebar.success("✅ Access Granted")
        has_access = True
    else:
        st.sidebar.error("❌ Access Denied")

if has_access:
    st.title("🌱 Organic Certificate Scanner")
    st.info("✅ **Active Languages:** English, German, French, Italian, Spanish, Dutch, Portuguese")
    
    uploaded_file = st.file_uploader("Upload Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Enhancing Image & Scanning...'):
            text = extract_text(uploaded_file)
        
        if text:
            expiry = find_smart_date(text)
            is_bad, bad_word = check_fraud(text)
            
            # EXTRACT BLOCKS
            doc_id = extract_block(text, ["document number", "nummer", "code"], ["operator", "unternehmer", "opérateur"])
            operator_info = extract_block(text, ["1.3", "operator", "unternehmer", "name and address"], ["1.4", "authority", "activity", "tätigkeit"])
            authority_info = extract_block(text, ["1.4", "control authority", "code number"], ["1.5", "activity", "tätigkeit"])
            # Refined Product Search: Look for 1.6 OR 'Category'
            product_info = extract_block(text, ["1.6", "category", "products", "erzeugnis"], ["part ii", "date", "validity", "datum"])

            # DISPLAY
            st.markdown("---")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.subheader("🚦 Compliance")
                if is_bad: st.error(f"🚨 FRAUD ALERT: '{bad_word}'")
                elif expiry and (expiry - datetime.now()).days < 0: st.error("❌ EXPIRED")
                else: st.success("✅ Status: Active")
            
            with c2:
                st.subheader("📅 Validity")
                if expiry:
                    days = (expiry - datetime.now()).days
                    color = "red" if days < 60 else "orange" if days < 90 else "green"
                    st.markdown(f":{color}[**Expires: {expiry.strftime('%Y-%m-%d')}**]")
                    st.caption(f"({days} days remaining)")
                else: st.warning("Date not detected")
            
            with c3:
                st.subheader("📄 Doc ID")
                st.write(doc_id if len(doc_id) < 50 else "See details below")

            st.markdown("---")
            col_left, col_right = st.columns(2)
            with col_left:
                st.markdown("### 🏭 Operator / Farm")
                st.info(operator_info)
                st.markdown("### 📦 Products")
                st.text(product_info)
            
            with col_right:
                st.markdown("### ⚖️ Certifying Authority")
                st.warning(authority_info)
                
            with st.expander("🔍 View Full Scanned Text"):
                st.text(text)
else:
    st.title("🔒 Restricted Access")
    st.warning("Please login via the Sidebar.")