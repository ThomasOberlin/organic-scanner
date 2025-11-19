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

# --- IMAGE PROCESSING ---
def preprocess_image(img):
    """
    High-contrast B&W for OCR accuracy.
    """
    img = ImageOps.grayscale(img)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    thresh = 200
    fn = lambda x : 255 if x > thresh else 0
    img = img.point(fn, mode='1')
    return img

# --- SPATIAL EXTRACTION ENGINE ---
def find_anchor_y(ocr_data, keywords):
    """Finds the vertical position (Y) of a keyword."""
    n_boxes = len(ocr_data['text'])
    for i in range(n_boxes):
        if any(kw in ocr_data['text'][i].lower() for kw in keywords):
            if int(ocr_data['conf'][i]) > 50:
                return ocr_data['top'][i]
    return None

def surgical_crop(img, y_start, y_end, split_vertical=False, side="left"):
    """Cuts specific zones of the image."""
    w, h = img.size
    if y_start is None: y_start = 0
    if y_end is None: y_end = h
    
    # Crop X-axis (Left/Right/Full)
    if split_vertical:
        x_start = 0 if side == "left" else int(w * 0.5)
        x_end = int(w * 0.5) if side == "left" else w
    else:
        x_start, x_end = 0, w
        
    crop = img.crop((x_start, y_start, x_end, y_end))
    
    # psm 6 = Assume a single uniform block of text
    return pytesseract.image_to_string(crop, lang=LANGUAGES_CONFIG, config='--psm 6')

def extract_full_data_spatial(file):
    """
    Combines Spatial Extraction (for Grid Layouts) with Full Page scanning.
    """
    full_text = ""
    ocr_data = None
    
    try:
        # Convert to Image
        if file.type == "application/pdf":
            images = convert_from_bytes(file.read(), dpi=300)
            img = preprocess_image(images[0])
        else:
            img = Image.open(file)
            if img.width < 2000:
                img = img.resize((img.width * 2, img.height * 2))
            img = preprocess_image(img)

        # 1. Get Landmarks
        ocr_data = pytesseract.image_to_data(img, output_type=Output.DICT)
        full_text = pytesseract.image_to_string(img)
        
        # Find "Box 3" (Authority) and "Box 5" (Activity)
        # We assume standard EU Layout vertical ordering
        y_box_3 = find_anchor_y(ocr_data, ["3.", "address"]) or int(img.height * 0.15)
        y_box_5 = find_anchor_y(ocr_data, ["5.", "activity"]) or int(img.height * 0.45)
        y_box_6 = find_anchor_y(ocr_data, ["6.", "category"]) or int(img.height * 0.60)
        y_footer = int(img.height * 0.90)

        # 2. Extract Zones
        # Header (Doc Num)
        header_text = surgical_crop(img, 0, y_box_3, split_vertical=False)
        
        # Columns (Operator vs Authority)
        # Adjust y_start to skip the header row "Name and address..."
        y_cols_start = y_box_3 + 60 
        operator_text = surgical_crop(img, y_cols_start, y_box_5, split_vertical=True, side="left")
        authority_text = surgical_crop(img, y_cols_start, y_box_5, split_vertical=True, side="right")
        
        # Products (Bottom)
        products_text = surgical_crop(img, y_box_6, y_footer, split_vertical=False)

        return {
            "full_text": full_text,
            "header": header_text,
            "operator": operator_text,
            "authority": authority_text,
            "products": products_text
        }

    except Exception as e:
        st.error(f"Extraction Error: {e}")
        return None

# --- PARSING HELPERS ---
def parse_checkbox_products(text):
    """Clean list of active products"""
    lines = text.split('\n')
    active = []
    checked_marks = ['X', 'x', 'V', '8', '☑', '[x]']
    categories = ("a)", "b)", "c)", "d)", "e)", "f)", "g)", "h)", "-")
    
    for line in lines:
        l = line.strip()
        l_low = l.lower()
        
        # Keep Headers
        if l_low.startswith(categories):
            active.append(l)
            continue
            
        # Keep Checked Items
        is_checked = False
        if any(l.startswith(m) for m in checked_marks): is_checked = True
        # If it says "Organic" and doesn't start with "O" (Empty)
        if "organic" in l_low and not l.startswith("O") and not l.startswith("0"): is_checked = True
        
        if is_checked:
            # Clean
            clean = l
            for m in checked_marks: clean = clean.replace(m, "")
            active.append(clean.strip())
            
    return active

def find_smart_date(text):
    candidates = []
    date_pattern = r'(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'
    matches = re.findall(date_pattern, text)
    for d in matches:
        try:
            dt = parser.parse(d, dayfirst=True)
            if 2020 < dt.year < 2035: candidates.append(dt)
        except: continue
    return max(candidates) if candidates else None

# --- 10-POINT COMPLIANCE ENGINE ---
def validate_compliance(data):
    report = {"score": 0, "total": 8, "details": []}
    
    # 1. Document Identification
    doc_num = re.search(r'0\d{4,}', data['header'])
    if doc_num:
        report["score"] += 1
        report["details"].append(f"✅ (1) Document ID Found: {doc_num.group(0)}")
    else:
        report["details"].append("❌ (1) Document ID Missing")

    # 2. Entity Information (Operator)
    if len(data['operator']) > 10:
        report["score"] += 1
        report["details"].append("✅ (2) Operator Details Detected")
    else:
        report["details"].append("❌ (2) Operator Details Unclear")

    # 3. Control Authority (The Annex II Check)
    # We look for ANY code pattern like XX-BIO-XXX
    cb_code = re.search(r'[A-Z]{2}-[A-Z]{3,}-\d{3}', data['authority'])
    if cb_code:
        report["score"] += 1
        report["details"].append(f"✅ (2) Control Body Code: {cb_code.group(0)}")
        # Note: We are NOT failing if it's not in the database, per user request.
    else:
        report["details"].append("⚠️ (2) Control Body Code (e.g. TH-BIO-121) not found")

    # 4. Activities
    if "activity" in data['full_text'].lower():
        report["score"] += 1
        report["details"].append("✅ (3) Activities Section Found")
    else:
        report["details"].append("⚠️ (3) Activities Section Missing")

    # 5. Product Categories
    active_prods = parse_checkbox_products(data['products'])
    if len(active_prods) > 0:
        report["score"] += 1
        report["details"].append(f"✅ (4) Active Products: {len(active_prods)} items")
    else:
        report["details"].append("❌ (4) No Active Products Detected")

    # 7. Legal Statement
    if "2018/848" in data['full_text'] or "2021/1378" in data['full_text']:
        report["score"] += 1
        report["details"].append("✅ (7) EU Regulation Cited")
    else:
        report["details"].append("❌ (7) Missing Legal Reference (2018/848)")

    # 8. Authentication
    if "electronically signed" in data['full_text'].lower() or "traces" in data['full_text'].lower():
        report["score"] += 1
        report["details"].append("✅ (8) Electronic/TRACES Seal")
    else:
        report["details"].append("⚠️ (8) Seal/Signature Not Detected")

    # 9. Validity
    expiry = find_smart_date(data['full_text'])
    if expiry:
        if expiry > datetime.now():
            report["score"] += 1
            report["details"].append(f"✅ (9) Valid Until: {expiry.strftime('%Y-%m-%d')}")
        else:
            report["details"].append(f"❌ (9) EXPIRED: {expiry.strftime('%Y-%m-%d')}")
    else:
        report["details"].append("❌ (9) Validity Date Not Found")

    return report, active_prods

# --- APP UI ---
st.set_page_config(page_title="VeriPura Compliance Tool", layout="wide")

st.sidebar.title("🔐 VeriPura Access")
wallet = st.sidebar.text_input("Wallet Address")

if wallet:
    st.title("🇪🇺 Organic Compliance Engine")
    st.markdown("**Standard:** EU Regulation 2021/1378 | **Mode:** Spatial Analysis")

    uploaded_file = st.file_uploader("Upload TRACES Certificate", type=['png', 'jpg', 'pdf'])

    if uploaded_file:
        with st.spinner('Extracting & Validating...'):
            data = extract_full_data_spatial(uploaded_file)
            
            if data:
                report, products = validate_compliance(data)

                # --- RESULTS ---
                st.markdown("### 📋 10-Point Compliance Check")
                
                # Score Banner
                if report['score'] >= 7:
                    st.success(f"PASSING SCORE: {report['score']}/{report['total']}")
                else:
                    st.error(f"FAILING SCORE: {report['score']}/{report['total']}")
                
                with st.expander("View Validation Details", expanded=True):
                    for line in report['details']:
                        st.write(line)

                st.markdown("---")

                # Extraction Visuals
                c1, c2 = st.columns(2)
                with c1:
                    st.subheader("🏭 Operator (Box 2)")
                    st.info(data['operator'])
                with c2:
                    st.subheader("⚖️ Authority (Box 3)")
                    st.warning(data['authority'])

                st.subheader("📦 Validated Products (Box 6)")
                if products:
                    for p in products: st.markdown(f"- {p}")
                else:
                    st.caption("No active products found.")

else:
    st.warning("Please log in.")