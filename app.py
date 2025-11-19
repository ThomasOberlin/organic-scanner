import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps, ImageEnhance
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
LANGUAGES_CONFIG = 'eng+deu+fra+ita+spa+nld+por'
IOTA_RPC_URL = "https://api.testnet.iota.cafe" 
SIMULATION_MODE = True 

# --- IMAGE UTILS ---
def preprocess_image(img):
    """Standard enhancement for OCR"""
    img = ImageOps.grayscale(img)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    return img

def split_image_layout(img):
    """
    VIRTUAL SCISSORS: Cuts the document into logical zones to prevent text mixing.
    Returns: header_img, left_col_img, right_col_img, bottom_img
    """
    w, h = img.size
    
    # 1. Define Cut Points (Approximations based on EU Standard Layout)
    # Header: Top 20% (Doc number, Title)
    header_h = int(h * 0.20)
    
    # Columns: 20% down to 50% down (Operator / Authority Area)
    cols_h_start = header_h
    cols_h_end = int(h * 0.50)
    
    # Products: Bottom 50%
    prod_h_start = cols_h_end
    
    # 2. Perform Crops
    # Header
    header_crop = img.crop((0, 0, w, header_h))
    
    # Split Columns (Left and Right halves of the middle section)
    mid_crop = img.crop((0, cols_h_start, w, cols_h_end))
    mid_w, mid_h = mid_crop.size
    left_col = mid_crop.crop((0, 0, mid_w // 2, mid_h))
    right_col = mid_crop.crop((mid_w // 2, 0, mid_w, mid_h))
    
    # Bottom (Products)
    bottom_crop = img.crop((0, prod_h_start, w, h))
    
    return header_crop, left_col, right_col, bottom_crop

# --- EXTRACTION LOGIC ---
def ocr_zone(img, config='--psm 6'):
    """Runs OCR on a specific image slice"""
    img = preprocess_image(img)
    return pytesseract.image_to_string(img, lang=LANGUAGES_CONFIG, config=config)

def extract_document_data(file):
    """
    Orchestrates the split-scan process.
    """
    full_text_debug = ""
    extracted_data = {
        "header": "",
        "col_1": "",
        "col_2": "",
        "products": "",
        "ocr_data": None # For forensic check (simplified here)
    }
    
    try:
        # Convert file to Image
        if file.type == "application/pdf":
            images = convert_from_bytes(file.read(), dpi=300)
            main_img = images[0] # Analyze first page for Operator/Authority
        else:
            main_img = Image.open(file)

        # Enforce minimum size for splitting
        if main_img.width < 2000:
            new_size = (main_img.width * 2, main_img.height * 2)
            main_img = main_img.resize(new_size, Image.Resampling.LANCZOS)

        # SPLIT THE IMAGE
        header_img, left_img, right_img, bottom_img = split_image_layout(main_img)
        
        # OCR EACH ZONE
        extracted_data["header"] = ocr_zone(header_img)
        extracted_data["col_1"] = ocr_zone(left_img)
        extracted_data["col_2"] = ocr_zone(right_img)
        extracted_data["products"] = ocr_zone(bottom_img, config='--psm 4') # psm 4 is good for tables/lists
        
        # Combine for date search
        full_text_debug = extracted_data["header"] + "\n" + extracted_data["col_1"] + "\n" + extracted_data["col_2"] + "\n" + extracted_data["products"]
        
    except Exception as e:
        st.error(f"Error: {e}")
        
    return extracted_data, full_text_debug

# --- PARSING & CLEANING ---
def identify_columns(col1_text, col2_text):
    """
    Determines which column is 'Operator' and which is 'Authority' 
    by looking for keywords in the text of that specific column.
    """
    # Default assumption
    operator_text = col2_text
    authority_text = col1_text
    
    # Smart Check
    c1 = col1_text.lower()
    c2 = col2_text.lower()
    
    # If Column 1 has "Operator" headers but Column 2 has "Authority" headers...
    # Note: Box 3 is usually Authority in some layouts, Operator in others.
    # We look for the CONTENT or Header hints.
    
    if "frujo" in c2 or "operator" in c2:
        operator_text = col2_text
        authority_text = col1_text
    elif "frujo" in c1 or "operator" in c1:
        operator_text = col1_text
        authority_text = col2_text
        
    return operator_text, authority_text

def format_product_summary(text):
    """
    Turns raw product text into clean bullet points.
    Removes legal boilerplate.
    """
    lines = text.split('\n')
    summary_bullets = []
    
    # Patterns to Capture
    # 1. Categories: "a) Unprocessed plants"
    # 2. Checkbox Items: "[x] Organic production" (OCR sees this as various symbols)
    
    for line in lines:
        l = line.strip()
        l_lower = l.lower()
        
        # Skip empty or legal noise
        if len(l) < 5: continue
        if "regulation" in l_lower or "production method" in l_lower or "part i" in l_lower: continue
        
        # Detection Logic
        is_category = re.match(r'^[a-z]\)', l_lower) # Matches a), b), etc.
        is_product_line = "organic" in l_lower and ("production" not in l_lower or "non-organic" in l_lower)
        
        # Formatting
        if is_category:
            # Bold the category
            clean_cat = l.replace(")", "").strip()
            summary_bullets.append(f"**{clean_cat}**")
        elif is_product_line:
            # Clean up checkbox noise
            clean_prod = l.replace("O ", "").replace("0 ", "").replace("[]", "").replace("_", "").strip()
            # Only add if it looks meaningful
            if len(clean_prod) > 10:
                summary_bullets.append(f"* {clean_prod}")
        elif l[0].isupper() and len(l) < 50: 
            # Capture short capitalized lines (often specific product names)
            summary_bullets.append(f"* {l}")
            
    return summary_bullets

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
        with st.spinner('Splitting Document Columns & Analyzing...'):
            data_map, full_text = extract_document_data(uploaded_file)
        
        if full_text:
            # 1. Intelligence
            expiry = find_smart_date(full_text)
            is_bad, bad_word = check_fraud(full_text)
            
            # 2. Identify Columns
            op_text, auth_text = identify_columns(data_map["col_1"], data_map["col_2"])
            
            # 3. Summarize Products
            product_bullets = format_product_summary(data_map["products"])

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
                st.metric("Parsing Mode", "Column Split")

            st.markdown("---")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("🏭 Operator")
                # Display just the text, cleaning up the header numbers slightly
                clean_op = op_text.replace("4.", "").replace("Name and address", "").strip()
                st.info(clean_op)

            with col2:
                st.subheader("⚖️ Authority")
                clean_auth = auth_text.replace("3.", "").replace("Name and address", "").strip()
                st.warning(clean_auth)

            st.markdown("### 📦 Certified Products (Summary)")
            
            if product_bullets:
                with st.container(border=True):
                    for bullet in product_bullets:
                        st.markdown(bullet)
            else:
                st.caption("No product categories detected clearly.")
                
            with st.expander("View Raw OCR Text"):
                st.text(full_text)
else:
    st.title("🔒 Restricted Access")
    st.warning("Please login via the Sidebar.")