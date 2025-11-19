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

# --- CONSTANTS ---
EXPIRY_KEYWORDS = [
    "valid until", "expiry date", "date of expiry", "validity", "expires",
    "certificate valid", "valid from", "valid to", "gültig bis", "ablaufdatum"
]

STATUS_BAD_KEYWORDS = [
    "suspended", "withdrawn", "revoked", "cancelled", "invalid", "suspendu"
]

REQUIRED_LEGAL_TEXT = [
    "regulation (eu) 2018/848", "organic production", "mandatory elements"
]

# --- IMAGE PROCESSING ---
def preprocess_image(img):
    """Enhances image to clear up the 'Grid' confusion"""
    img = ImageOps.grayscale(img)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.5)
    return img

# --- CORE EXTRACTION LOGIC ---
def extract_text_and_data(file):
    full_text = ""
    combined_data = {'conf': []}
    try:
        if file.type == "application/pdf":
            # 300 DPI is critical for reading the small Box Numbers (1. 2. 3.)
            images = convert_from_bytes(file.read(), dpi=300)
            for img in images:
                img = preprocess_image(img)
                # psm 6 assumes a single uniform block of text, often better for forms than psm 3
                full_text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 6') + "\n"
                data = pytesseract.image_to_data(img, lang=LANGUAGES_CONFIG, output_type=Output.DICT)
                combined_data['conf'].extend(data['conf'])
        else:
            img = Image.open(file)
            if img.width < 2000:
                new_size = (img.width * 2, img.height * 2)
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            img = preprocess_image(img)
            full_text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 6')
            data = pytesseract.image_to_data(img, lang=LANGUAGES_CONFIG, output_type=Output.DICT)
            combined_data['conf'].extend(data['conf'])
    except Exception as e:
        st.error(f"Error: {e}")
    return full_text, combined_data

# --- NEW: BOX PARSING ENGINE ---
def extract_eu_box(text, box_number_start, box_number_end):
    """
    Strictly cuts text between two Box Numbers (e.g., '4.' and '5.')
    """
    # Regex looks for "4." followed by text, stopping at "5." or a backup keyword
    # We use DOTALL so it captures newlines
    
    # Flexible pattern: Handles "4. Name" or "4 Name" or "4.Name"
    pattern = fr"(?:{box_number_start}\.|{box_number_start})\s*(.*?)(?={box_number_end}\.|{box_number_end}\s|[A-Z][a-z]+:)"
    
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        content = match.group(1).strip()
        # Clean up the Header Text inside the box (e.g., remove "Name and address...")
        lines = content.split('\n')
        clean_lines = [line for line in lines if len(line) > 3 and "name and address" not in line.lower()]
        return "\n".join(clean_lines)
    return None

def clean_products_list(text):
    """Filters the product section to remove legal noise."""
    lines = text.split('\n')
    products = []
    # Standard EU Categories
    categories = ["a)", "b)", "c)", "d)", "e)", "f)", "g)", "h)", "-"]
    
    for line in lines:
        l_lower = line.lower()
        # Keep lines that start with a category OR look like a product name
        if any(l_lower.startswith(cat) for cat in categories) or "organic" in l_lower:
            # Filter out legal headers
            if "regulation" not in l_lower and "production method" not in l_lower:
                products.append(line.strip())
    
    return "\n".join(products) if products else "See Full Text"

# --- ANALYSIS FUNCTIONS ---
def analyze_anomalies(text, ocr_data):
    issues = []
    risk_score = 0
    
    # 1. Blur Check
    confidences = [int(c) for c in ocr_data['conf'] if c != -1]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0
    if avg_conf < 50:
        issues.append(f"⚠️ Low Resolution Scan (Confidence: {int(avg_conf)}%)")
        risk_score += 20

    # 2. Box Structure Check (The "DNA" Test)
    # Real EU certs MUST have Box 1, 3, and 4.
    if "1." not in text or "3." not in text:
        issues.append("🚨 Invalid Document Structure (Missing Box Numbers)")
        risk_score += 40

    return risk_score, issues

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

# SIMULATION LOGIN
if wallet_address and wallet_address.startswith("0x"):
    st.sidebar.success("✅ Access Granted")
    has_access = True

if has_access:
    st.title("🌱 Organic Certificate Scanner")
    st.info("✅ **Active Languages:** English, German, French, Italian, Spanish, Dutch, Portuguese")
    
    uploaded_file = st.file_uploader("Upload Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Executing Strict Box Parsing...'):
            text, ocr_data = extract_text_and_data(uploaded_file)
        
        if text:
            risk_score, risk_issues = analyze_anomalies(text, ocr_data)
            expiry = find_smart_date(text)
            is_bad, bad_word = check_fraud(text)
            
            # --- STRICT BOX EXTRACTION ---
            # Box 3: Authority (Between "3." and "4.")
            authority_info = extract_eu_box(text, "3", "4")
            
            # Box 4: Operator (Between "4." and "5.")
            operator_info = extract_eu_box(text, "4", "5")
            
            # Box 9: Group Members (If applicable)
            group_members = extract_eu_box(text, "9", "Part II")
            
            # Products (Starts at Box 6, cleans up noise)
            product_raw = extract_eu_box(text, "