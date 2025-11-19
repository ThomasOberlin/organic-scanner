import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps, ImageEnhance
from datetime import datetime
from dateutil import parser
import re
from difflib import SequenceMatcher

# --- CONFIGURATION ---
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'
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

# --- MANDATORY EU HEADERS (The "DNA" of the document) ---
# If these are missing or misspelled, the document is likely fake.
REQUIRED_LEGAL_TEXT = [
    "regulation (eu) 2018/848",
    "organic production",
    "article 35",
    "mandatory elements",
    "competent authority"
]

# --- HELPER FUNCTIONS ---

def preprocess_image(img):
    img = ImageOps.grayscale(img)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.5)
    return img

def check_token_balance(address):
    if SIMULATION_MODE: return address.startswith("0x")
    return False 

def analyze_anomalies(text, ocr_data):
    """
    Forensic Scan:
    1. Check if average OCR confidence is suspiciously low (Blurry/Paste job).
    2. Check if mandatory legal text is missing (Fake Template).
    """
    issues = []
    risk_score = 0 # 0 = Good, 100 = Bad
    
    # 1. CONFIDENCE CHECK
    # Filter out empty reads
    confidences = [int(c) for c in ocr_data['conf'] if c != -1]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0
    
    if avg_conf < 40:
        issues.append(f"⚠️ High Blur / Low Resolution (Avg Confidence: {int(avg_conf)}%)")
        risk_score += 30
    elif avg_conf < 60:
        issues.append(f"⚠️ Poor Quality Scan (Avg Confidence: {int(avg_conf)}%)")
        risk_score += 10

    # 2. LEGAL TEMPLATE CHECK (Fuzzy Matching)
    # We look for the "DNA" text. If missing, it's a huge red flag.
    text_lower = text.lower()
    missing_headers = 0
    
    for header in REQUIRED_LEGAL_TEXT:
        if header not in text_lower:
            # Double check with fuzzy match (in case of simple typos)
            # If even a fuzzy match fails, the text is definitely missing.
            found_fuzzy = False
            lines = text_lower.split('\n')
            for line in lines:
                ratio = SequenceMatcher(None, header, line).ratio()
                if ratio > 0.75: # 75% similar
                    found_fuzzy = True
                    break
            
            if not found_fuzzy:
                missing_headers += 1
    
    if missing_headers > 1:
        issues.append(f"🚨 Template Mismatch: Missing {missing_headers} mandatory EU legal headers.")
        risk_score += 50
    
    return risk_score, issues

def extract_text_and_data(file):
    """Returns both the raw text AND the data table (confidence scores)."""
    full_text = ""
    combined_data = {'conf': []}
    
    try:
        if file.type == "application/pdf":
            images = convert_from_bytes(file.read(), dpi=300)
            for img in images:
                img = preprocess_image(img)
                # Get Text
                full_text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 4') + "\n"
                # Get Data (Confidence scores)
                data = pytesseract.image_to_data(img, lang=LANGUAGES_CONFIG, output_type=Output.DICT)
                combined_data['conf'].extend(data['conf'])
        else:
            img = Image.open(file)
            if img.width < 2000:
                new_size = (img.width * 2, img.height * 2)
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            img = preprocess_image(img)
            
            full_text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 4')
            data = pytesseract.image_to_data(img, lang=LANGUAGES_CONFIG, output_type=Output.DICT)
            combined_data['conf'].extend(data['conf'])
            
    except Exception as e:
        st.error(f"Error: {e}")
        
    return full_text, combined_data

# --- CLEANING & EXTRACTION FUNCTIONS (From previous step) ---
def clean_authority(text):
    boilerplate = ["competent authority", "control authority", "code number", "kontrollstelle"]
    lines = text.split('\n')
    clean = [l for l in lines if not any(bp in l.lower() for bp in boilerplate) and len(l) > 3]
    return "\n".join(clean).strip()

def clean_products(text):
    lines = text.split('\n')
    valid_starts = ["a)", "b)", "c)", "d)", "e)", "f)", "g)", "h)", "-"]
    clean = [l for l in lines if (any(l.lower().startswith(s) for s in valid_starts) or "organic" in l.lower()) and "regulation" not in l.lower()]
    return "\n".join(clean) if clean else "Check Full Text"

def extract_block_smart(text, start_marker, stop_markers):
    lines = text.split('\n')
    capture = False
    captured = []
    for line in lines:
        l_lower = line.lower()
        if any(s in l_lower for s in start_marker):
            capture = True
            continue
        if capture and any(s in l_lower for s in stop_markers):
            break
        if capture:
            captured.append(line)
    return "\n".join(captured).strip()

def find_smart_date(text):
    candidates = []
    date_pattern = r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})|(\d{4}[./-]\d{1,2}[./-]\d{1,2})'
    matches = re.findall(date_pattern, text)
    for match in matches:
        d = match[0] if match[0] else match[1]
        try:
            dt = parser.parse(d, dayfirst=True)
            if 2020 < dt.year < 2035: candidates.append(dt)
        except: continue
    return max(candidates) if candidates else None

def check_fraud(text):
    text_lower = text.lower()
    for kw in STATUS_BAD_KEYWORDS:
        if kw in text_lower: return True, kw.upper()
    return False, None

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
        with st.spinner('Running Forensic Analysis & OCR...'):
            # 1. Extract Text AND Confidence Data
            text, ocr_data = extract_text_and_data(uploaded_file)
        
        if text:
            # 2. Run Forensic Checks
            risk_score, risk_issues = analyze_anomalies(text, ocr_data)
            
            expiry = find_smart_date(text)
            is_bad, bad_word = check_fraud(text)
            
            # Extraction
            op_raw = extract_block_smart(text, ["3. name", "3. name and address"], ["4. name", "4. name and address"])
            auth_raw = extract_block_smart(text, ["4. name", "competent authority"], ["5. activity", "certification department"])
            auth_clean = clean_authority(auth_raw)
            prod_raw = extract_block_smart(text, ["6. category", "category of products"], ["part ii", "date, place", "7. date"])
            prod_clean = clean_products(prod_raw)

            # --- DASHBOARD ---
            st.markdown("---")
            
            # FORENSIC BANNER
            if risk_score > 40:
                st.error(f"🛡️ ANOMALIES DETECTED (Risk Score: {risk_score}/100)")
                for issue in risk_issues:
                    st.markdown(f"- {issue}")
            else:
                st.success(f"🛡️ Forensic Check Passed (Risk Score: {risk_score}/100)")

            st.markdown("---")
            
            c1, c2, c3 = st.columns(3)
            with c1:
                st.subheader("🚦 Compliance")
                if is_bad: st.error(f"🚨 FRAUD STATUS: '{bad_word}'")
                elif expiry and (expiry - datetime.now()).days < 0: st.error("❌ EXPIRED")
                else: st.success("✅ Status: Active")
            
            with c2:
                st.subheader("📅 Validity")
                if expiry:
                    days = (expiry - datetime.now()).days
                    color = "red" if days < 60 else "orange" if days < 90 else "green"
                    st.markdown(f":{color}[**Expires: {expiry.strftime('%Y-%m-%d')}**]")
                else: st.warning("Date not detected")
            
            with c3:
                st.subheader("📄 Extraction Mode")
                st.caption("Grid Analysis + Forensic Scan")

            st.markdown("---")
            col_left, col_right = st.columns(2)
            with col_left:
                st.markdown("### 🏭 Operator / Farm")
                st.info(op_raw if len(op_raw) > 5 else "Detected in Raw Text")
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