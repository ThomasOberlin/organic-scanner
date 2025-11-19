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

# --- KEYWORDS ---
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
    """High-Res 'Reading Glasses' for OCR"""
    img = ImageOps.grayscale(img)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
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
            images = convert_from_bytes(file.read(), dpi=300)
            for img in images:
                img = preprocess_image(img)
                # psm 4 = Assume a single column of text of variable sizes
                text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 4') + "\n"
        else:
            img = Image.open(file)
            if img.width < 2000:
                new_size = (img.width * 2, img.height * 2)
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            img = preprocess_image(img)
            text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 4')
    except Exception as e:
        st.error(f"Error: {e}")
    return text

def find_smart_date(text):
    candidates = []
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
    return max(candidates) if candidates else None

def check_fraud(text):
    text_lower = text.lower()
    for kw in STATUS_BAD_KEYWORDS:
        if kw in text_lower:
            return True, kw.upper()
    return False, None

# --- NEW: CLEANING FUNCTIONS ---

def clean_authority(text):
    """Removes standard boilerplate to show only the name."""
    # Common legal headers in EN/DE/FR
    boilerplate = [
        "name and address of the competent authority", 
        "control authority or control body", 
        "code number",
        "kontrollstelle", "kontrollbehörde",
        "autorité de contrôle"
    ]
    
    lines = text.split('\n')
    clean_lines = []
    for line in lines:
        # Only keep line if it DOESN'T contain the boilerplate
        if not any(bp in line.lower() for bp in boilerplate):
            if len(line) > 3: # Skip noise
                clean_lines.append(line)
    
    return "\n".join(clean_lines).strip()

def clean_products(text):
    """Filters the massive product block to show only categories."""
    lines = text.split('\n')
    concise_list = []
    
    # Look for lines that start with a bullet, a letter, or have a checkmark
    # EU Categories usually start with "a) Unprocessed...", "b) Livestock..."
    valid_starts = ["a)", "b)", "c)", "d)", "e)", "f)", "g)", "h)", "-"]
    keywords = ["organic production", "production method", "unprocessed", "processed", "livestock"]
    
    for line in lines:
        l = line.strip()
        # If line starts with a category marker (a, b, c...) OR contains "Organic"
        if any(l.lower().startswith(s) for s in valid_starts) or "organic" in l.lower():
             # Filter out the long legal headers
            if "regulation (eu)" not in l.lower() and "article" not in l.lower():
                concise_list.append(l)
                
    return "\n".join(concise_list) if concise_list else "Check Full Text (Complex Format)"

def extract_block_smart(text, start_marker, stop_markers):
    lines = text.split('\n')
    capture = False
    captured = []
    
    for line in lines:
        l_lower = line.lower()
        # START
        if any(s in l_lower for s in start_marker):
            capture = True
            continue # Skip the header line itself
        
        # STOP
        if capture and any(s in l_lower for s in stop_markers):
            break
            
        if capture:
            captured.append(line)
            
    return "\n".join(captured).strip()

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
        with st.spinner('Processing (High-Res Mode)...'):
            text = extract_text(uploaded_file)
        
        if text:
            expiry = find_smart_date(text)
            is_bad, bad_word = check_fraud(text)
            
            # --- NEW EXTRACTION LOGIC ---
            # 1. Operator (Box 3): Starts after "Name and address"
            op_raw = extract_block_smart(text, ["3. name", "3. name and address"], ["4. name", "4. name and address"])
            
            # 2. Authority (Box 4): Starts after "Competent authority"
            auth_raw = extract_block_smart(text, ["4. name", "competent authority"], ["5. activity", "certification department"])
            auth_clean = clean_authority(auth_raw)

            # 3. Products (Box 6): Starts after "Category"
            prod_raw = extract_block_smart(text, ["6. category", "category of products"], ["part ii", "date, place", "7. date"])
            prod_clean = clean_products(prod_raw)

            # DISPLAY DASHBOARD
            st.markdown("---")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.subheader("🚦 Compliance")
                if is_bad: st.error(f"🚨 FRAUD: '{bad_word}'")
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
                st.subheader("📄 Extraction")
                st.write("Automated Grid Analysis")

            st.markdown("---")
            col_left, col_right = st.columns(2)
            
            with col_left:
                st.markdown("### 🏭 Operator / Farm")
                # Fallback: If smart block fails, try generic search
                if len(op_raw) < 5: 
                    st.info("Detected in raw text (See below)")
                else:
                    st.info(op_raw)
                    
                st.markdown("### 📦 Certified Products")
                st.text(prod_clean)
            
            with col_right:
                st.markdown("### ⚖️ Certifying Authority")
                st.warning(auth_clean)
                
            with st.expander("🔍 View Full Scanned Text"):
                st.text(text)
else:
    st.title("🔒 Restricted Access")
    st.warning("Please login via the Sidebar.")