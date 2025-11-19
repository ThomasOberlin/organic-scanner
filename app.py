import streamlit as st
from paddleocr import PaddleOCR
from pdf2image import convert_from_bytes
from PIL import Image, ImageOps, ImageEnhance
import numpy as np
from datetime import datetime
from dateutil import parser
import re

# --- CONFIGURATION ---
SIMULATION_MODE = True 

# --- INITIALIZE PADDLE OCR (The "Brain") ---
@st.cache_resource
def get_ocr_engine():
    # We enable the angle classifier HERE, so we don't need to ask for it later
    return PaddleOCR(use_angle_cls=True, lang='en', show_log=False)

ocr_engine = get_ocr_engine()

# --- IMAGE PROCESSING ---
def preprocess_image(img):
    """
    Standard enhancement for OCR.
    """
    img = ImageOps.grayscale(img)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.5)
    return img

def pil_to_numpy(img):
    """Converts PIL Image to Numpy Array for PaddleOCR"""
    return np.array(img.convert("RGB"))

# --- SPATIAL EXTRACTION ---
def find_anchor_y(ocr_data, keywords):
    """
    Finds the Y-coordinate of a keyword using Paddle's result format.
    """
    # Paddle format: [ [ [ [x1,y1]..], ("text", conf) ] ... ]
    for line in ocr_data:
        box, (text, conf) = line
        text = text.lower()
        if any(kw in text for kw in keywords):
            return int(box[0][1])
    return None

def surgical_crop(img, y_start, y_end, split_vertical=False, side="left"):
    """
    Cuts the image and runs PaddleOCR on the specific zone.
    """
    w, h = img.size
    if y_start is None: y_start = 0
    if y_end is None: y_end = h
    
    if y_end <= y_start: y_end = min(y_start + 500, h)

    if split_vertical:
        x_start = 0 if side == "left" else int(w * 0.5)
        x_end = int(w * 0.5) if side == "left" else w
    else:
        x_start, x_end = 0, w
        
    try:
        crop = img.crop((x_start, y_start, x_end, y_end))
        crop_np = pil_to_numpy(crop)
        # FIX: Removed cls=True here
        result = ocr_engine.ocr(crop_np)
        
        full_text = ""
        if result and result[0]:
            for line in result[0]:
                full_text += line[1][0] + "\n"
        return full_text
    except Exception:
        return ""

def extract_full_data_paddle(file):
    full_text = ""
    
    try:
        # Convert PDF/Image
        if file.type == "application/pdf":
            images = convert_from_bytes(file.read(), dpi=200, first_page=1, last_page=2)
            img = preprocess_image(images[0])
            prod_img = preprocess_image(images[1]) if len(images) > 1 else img
        else:
            img = Image.open(file)
            if img.width < 2000: img = img.resize((img.width * 2, img.height * 2))
            img = preprocess_image(img)
            prod_img = img

        # 1. Get Landmarks (Full Page Scan)
        img_np = pil_to_numpy(img)
        # FIX: Removed cls=True here
        raw_results = ocr_engine.ocr(img_np)
        
        flat_text = ""
        ocr_list = []
        if raw_results and raw_results[0]:
             ocr_list = raw_results[0]
             for line in ocr_list: flat_text += line[1][0] + "\n"

        h = img.height
        
        # 2. Find Anchors
        y_box_3 = find_anchor_y(ocr_list, ["1.3", "3.", "address", "operator"]) or int(h * 0.15)
        y_box_5 = find_anchor_y(ocr_list, ["1.5", "5.", "activity"]) 
        if not y_box_5: y_box_5 = int(h * 0.50)
        
        y_box_6 = find_anchor_y(ocr_list, ["1.6", "6.", "category"]) or int(h * 0.60)
        y_footer = int(h * 0.95)

        # 3. Extract Zones
        header_text = surgical_crop(img, 0, y_box_3, split_vertical=False)
        y_cols_start = y_box_3 + 50
        operator_text = surgical_crop(img, y_cols_start, y_box_5, split_vertical=True, side="left")
        authority_text = surgical_crop(img, y_cols_start, y_box_5, split_vertical=True, side="right")
        
        # Products (Page 2 if avail)
        products_text = ""
        if file.type == "application/pdf" and 'images' in locals() and len(images) > 1:
            p2_np = pil_to_numpy(prod_img)
            # FIX: Removed cls=True here
            p2_res = ocr_engine.ocr(p2_np)
            if p2_res and p2_res[0]:
                for line in p2_res[0]: products_text += line[1][0] + "\n"
        else:
            products_text = surgical_crop(img, y_box_6, y_footer, split_vertical=False)

        return {
            "full_text": flat_text,
            "header": header_text,
            "operator": operator_text,
            "authority": authority_text,
            "products": products_text
        }

    except Exception as e:
        st.error(f"Processing Error: {e}")
        return None

# --- PARSING ---
def parse_checkbox_products(text):
    lines = text.split('\n')
    active = []
    checked_marks = ['X', 'x', 'V', '8', '☑', '[x]', 'v']
    categories = ("a)", "b)", "c)", "d)", "e)", "f)", "g)", "h)", "-")
    
    for line in lines:
        l = line.strip()
        l_low = l.lower()
        if "page" in l_low or "regulation" in l_low: continue
        
        if l_low.startswith(categories):
            active.append(f"**{l}**")
            continue
        
        is_checked = False
        if any(l.startswith(m) for m in checked_marks): is_checked = True
        if "organic" in l_low and not l.startswith("O") and not l.startswith("0"): is_checked = True
        
        if is_checked:
            clean = l
            for m in checked_marks: clean = clean.replace(m, "")
            active.append(f"✅ {clean.strip()}")
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

def validate_compliance(data):
    report = {"score": 0, "total": 8, "details": []}
    
    doc_num = re.search(r'[A-Z]{2}-.*?-\d+', data['header'])
    if not doc_num: doc_num = re.search(r'0\d{4,}', data['header'])
    if doc_num:
        report["score"] += 1
        report["details"].append(f"✅ (1) Document ID: {doc_num.group(0)}")
    else:
        report["details"].append("❌ (1) Document ID Missing")

    if len(data['operator']) > 5:
        report["score"] += 1
        report["details"].append("✅ (2) Operator Details Found")
    else:
        report["details"].append("❌ (2) Operator Details Unclear")

    cb_code = re.search(r'[A-Z]{2}-[A-ZÖÄÜ]{3,}-\d+', data['authority']) or re.search(r'[A-Z]{2}-[A-Z]{3,}-\d+', data['full_text'])
    if cb_code:
        report["score"] += 1
        report["details"].append(f"✅ (2) Control Body: {cb_code.group(0)}")
    else:
        report["details"].append("⚠️ (2) Control Body Code not found")

    if "activity" in data['full_text'].lower():
        report["score"] += 1
        report["details"].append("✅ (3) Activities Found")
    else:
        report["details"].append("⚠️ (3) Activities Missing")

    active_prods = parse_checkbox_products(data['products'])
    if len(active_prods) > 0:
        report["score"] += 1
        report["details"].append(f"✅ (4) Active Products: {len(active_prods)}")
    else:
        report["details"].append("❌ (4) No Active Products")

    if "2018/848" in data['full_text'] or "2021/1378" in data['full_text']:
        report["score"] += 1
        report["details"].append("✅ (7) EU Regulation Cited")
    else:
        report["details"].append("❌ (7) Missing Legal Reference")

    if "electronically signed" in data['full_text'].lower() or "traces" in data['full_text'].lower():
        report["score"] += 1
        report["details"].append("✅ (8) Electronic Seal")
    else:
        report["details"].append("⚠️ (8) Seal Not Detected")

    expiry = find_smart_date(data['full_text'])
    if expiry:
        if expiry > datetime.now():
            report["score"] += 1
            report["details"].append(f"✅ (9) Valid Until: {expiry.strftime('%Y-%m-%d')}")
        else:
            report["details"].append(f"❌ (9) EXPIRED: {expiry.strftime('%Y-%m-%d')}")
    else:
        report["details"].append("❌ (9) Validity Date Missing")

    return report, active_prods

# --- APP UI ---
st.set_page_config(page_title="VeriPura Compliance Tool", layout="wide")
st.title("🇪🇺 VeriPura Compliance Engine")
st.markdown("**Engine:** PaddleOCR (Neural Network) | **Mode:** Spatial Grid")

uploaded_file = st.file_uploader("Upload TRACES Certificate", type=['png', 'jpg', 'pdf'])

if uploaded_file:
    with st.spinner('Initializing PaddleOCR & Scanning... (First run takes 1 min)'):
        data = extract_full_data_paddle(uploaded_file)
        
        if data:
            report, products = validate_compliance(data)

            st.markdown("### 📋 Compliance Status")
            if report['score'] >= 7:
                st.success(f"PASS: {report['score']}/{report['total']}")
            else:
                st.error(f"FAIL: {report['score']}/{report['total']}")
            
            with st.expander("View Validation Details"):
                for line in report['details']: st.write(line)

            st.markdown("---")
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("🏭 Operator")
                st.info(data['operator'])
            with c2:
                st.subheader("⚖️ Authority")
                st.warning(data['authority'])

            st.subheader("📦 Certified Products")
            if products:
                for p in products: st.markdown(p)
            else:
                st.caption("No active products found.")