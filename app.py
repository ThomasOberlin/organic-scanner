import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'
IOTA_RPC_URL = "https://api.testnet.iota.cafe" 
SIMULATION_MODE = True 

# --- IMAGE PROCESSING ---
def preprocess_for_checkboxes(img):
    """
    Aggressive processing to distinguish 'O' (empty) from 'X' (checked).
    """
    # 1. Grayscale
    img = ImageOps.grayscale(img)
    
    # 2. High Contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5) # Very high contrast
    
    # 3. Binarization (Thresholding)
    # This turns gray pixels white, and dark pixels black. 
    # It helps remove the 'fuzz' around checkmarks.
    thresh = 200
    fn = lambda x : 255 if x > thresh else 0
    img = img.point(fn, mode='1')
    
    return img

# --- EXTRACTION LOGIC ---
def extract_text_full_page(file):
    """
    Scans the full page at high resolution without splitting.
    """
    full_text = ""
    try:
        if file.type == "application/pdf":
            # 300 DPI is standard for forms
            images = convert_from_bytes(file.read(), dpi=300)
            # Scan first 2 pages (Page 1 has Operator/Date, Page 2 usually has Products)
            for i, img in enumerate(images[:2]): 
                img = preprocess_for_checkboxes(img)
                # psm 4 = Assume a single column of text of variable sizes (good for tables)
                text = pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 4')
                full_text += f"\n--- PAGE {i+1} ---\n" + text
        else:
            img = Image.open(file)
            if img.width < 2000:
                new_size = (img.width * 2, img.height * 2)
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            img = preprocess_for_checkboxes(img)
            full_text += pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config='--psm 4')
            
    except Exception as e:
        st.error(f"Error: {e}")
        
    return full_text

# --- PARSING FUNCTIONS ---

def extract_box_content(text, start_marker, end_markers):
    """
    Finds text between '4.' and '5.'.
    Handles cases where the text is messy or headers are repeated.
    """
    lines = text.split('\n')
    capture = False
    captured_lines = []
    
    for line in lines:
        l = line.strip()
        # Check End Markers first (to stop capturing)
        if capture and any(l.startswith(m) for m in end_markers):
            break
            
        # Capture logic
        if capture:
            # Filter out noise and the repeated header name
            if len(l) > 2 and "name and address" not in l.lower():
                captured_lines.append(l)
        
        # Check Start Marker
        # We look for "4." or "3." at the START of the line
        if any(l.startswith(m) for m in start_marker):
            capture = True
            
    return "\n".join(captured_lines).strip()

def parse_checkbox_products(text):
    """
    The 'Smart Filter' for Products.
    - Keeps Category Headers (a, b, c...)
    - IGNORES lines starting with 'O', '0', '[]' (Empty Boxes)
    - KEEPS lines starting with 'X', 'x', 'V' (Checked Boxes)
    """
    lines = text.split('\n')
    clean_products = []
    
    # Indicators of an empty box (OCR often reads square as O or 0)
    empty_indicators = ('O ', '0 ', 'o ', '[] ', '( )')
    
    # Indicators of a checked box (OCR often reads X, x, V, 8, or just a blob)
    checked_indicators = ('X ', 'x ', 'V ', '8 ', '☑', '[x]')
    
    # Valid Categories
    categories = ("a)", "b)", "c)", "d)", "e)", "f)", "g)", "h)", "-")
    
    capture_mode = False
    
    for line in lines:
        l = line.strip()
        if not l: continue
        
        # Detect Start of Product Section
        if "category or categories" in l.lower():
            capture_mode = True
            continue
            
        # Stop at Date/Validity section
        if "validity" in l.lower() or "certificate valid" in l.lower():
            capture_mode = False
            
        if capture_mode:
            # 1. Always keep Category Headers
            if l.lower().startswith(categories):
                clean_products.append(f"\n**{l}**") # Bold the header
                continue
                
            # 2. Checkbox Logic
            # If line starts with 'O ' or '0 ', it is EMPTY -> SKIP
            if l.startswith(empty_indicators):
                continue
                
            # If line looks like a product line but didn't start with 'O', 
            # it is likely the checked one (or the checkmark confused the OCR)
            if "production" in l.lower() or "organic" in l.lower():
                # Clean up the 'X' or '8' from the start
                clean_line = l
                for mark in checked_indicators:
                    clean_line = clean_line.replace(mark, "").strip()
                    
                clean_products.append(f"✅ {clean_line}")

    return "\n".join(clean_products) if clean_products else "Could not automatically determine active products."

def find_smart_date(text):
    candidates = []
    # Look for dates in 2020-2030 range
    date_pattern = r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'
    matches = re.findall(date_pattern, text)
    for d in matches:
        try:
            dt = parser.parse(d, dayfirst=True)
            if 2020 < dt.year < 2035: candidates.append(dt)
        except: continue
    return max(candidates) if candidates else None

def check_fraud(text):
    text_lower = text.lower()
    bad_keywords = ["suspended", "withdrawn", "revoked", "cancelled", "invalid"]
    for kw in bad_keywords:
        if kw in text_lower: return True, kw.upper()
    return False, None

def check_token_balance(address):
    return address.startswith("0x") if SIMULATION_MODE else False

# --- APP UI ---
st.set_page_config(page_title="EU Organic Scanner", layout="wide")

st.sidebar.title("🔐 Client Login")
wallet_address = st.sidebar.text_input("Wallet Address", placeholder="0x...")
has_access = False

if wallet_address and wallet_address.startswith("0x"):
    st.sidebar.success("✅ Access Granted")
    has_access = True

if has_access:
    st.title("🌱 Organic Certificate Scanner")
    st.info("✅ **Active Languages:** English, German, French, Italian, Spanish, Dutch, Portuguese")
    
    uploaded_file = st.file_uploader("Upload Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Scanning Full Document (Thresholding Enabled)...'):
            full_text = extract_text_full_page(uploaded_file)
        
        if full_text:
            # 1. Intelligence
            expiry = find_smart_date(full_text)
            is_bad, bad_word = check_fraud(full_text)
            
            # 2. Extraction (Regex Anchors)
            # Box 3: Authority (Stop at 4.)
            authority_info = extract_box_content(full_text, ["3."], ["4.", "4 "])
            
            # Box 4: Operator (Stop at 5.)
            operator_info = extract_box_content(full_text, ["4."], ["5.", "5 "])
            
            # 3. Products (Smart Checkbox Logic)
            products_summary = parse_checkbox_products(full_text)

            # --- DASHBOARD ---
            st.markdown("---")
            c1, c2, c3 = st.columns(3)
            with c1:
                if is_bad: st.error(f"🚨 FRAUD: {bad_word}")
                elif expiry and (expiry - datetime.now()).days < 0: st.error("❌ EXPIRED")
                else: st.success("✅ Status: Active")
            
            with c2:
                if expiry: st.metric("Expiration", expiry.strftime("%Y-%m-%d"))
                else: st.warning("Date Not Found")
            
            with c3:
                st.metric("Parsing Mode", "Smart Checkbox Filter")

            st.markdown("---")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("🏭 Operator (Box 4)")
                if operator_info: st.info(operator_info)
                else: st.warning("Could not detect Box 4 headers")

            with col2:
                st.subheader("⚖️ Authority (Box 3)")
                if authority_info: st.success(authority_info)
                else: st.warning("Could not detect Box 3 headers")

            st.markdown("### 📦 Certified Products (Active Only)")
            st.markdown(products_summary)
                
            with st.expander("View Raw OCR Text (For Debugging)"):
                st.text(full_text)
else:
    st.title("🔒 Restricted Access")
    st.warning("Please login via the Sidebar.")