import os, csv, time, requests
from dataclasses import dataclass, asdict
from datetime import date
from typing import Dict, Any, Optional
import streamlit as st
import pandas as pd
import phonenumbers

# ============================ CONFIG (via st.secrets) ============================
PROVIDER = st.secrets.get("PROVIDER", "none").lower()  # "cloud_api" | "twilio" | "none"
CSV_PATH = st.secrets.get("CSV_PATH", "rsvp.csv")
DEFAULT_REGION = st.secrets.get("DEFAULT_REGION", "IN")

# WhatsApp Cloud API
WHATSAPP_PHONE_NUMBER_ID = st.secrets.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN = st.secrets.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_TEMPLATE_NAME = st.secrets.get("WHATSAPP_TEMPLATE_NAME", "rsvp_confirmation")
WHATSAPP_TEMPLATE_LANG = st.secrets.get("WHATSAPP_TEMPLATE_LANG", "en")

# Twilio
TWILIO_ACCOUNT_SID = st.secrets.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = st.secrets.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = st.secrets.get("TWILIO_WHATSAPP_FROM", "")

UPLOAD_DIR = "uploads"   # local folder to store photos

# ============================ DATA MODEL ============================
@dataclass
class RSVP:
    ts: int
    mobile_e164: str
    full_name: str
    dob: str
    age_years: int
    full_address: str
    education: str
    occupation: str
    is_host: str
    attended_before: str
    referral: str
    # NEW FIELDS
    p3y_prapti_din: str         # P3Y प्राप्ति दिन (ISO date or empty)
    experience: str             # अनुभव | Experience
    skill: str                  # कौशल | Skill
    photo_path: str             # saved path or empty

# ============================ HELPERS ============================
def ensure_csv(path: str):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                "ts","mobile_e164","full_name","dob","age_years","full_address",
                "education","occupation","is_host","attended_before","referral",
                "p3y_prapti_din","experience","skill","photo_path"
            ])

def save_rsvp(path: str, r: RSVP):
    ensure_csv(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            r.ts, r.mobile_e164, r.full_name, r.dob, r.age_years, r.full_address,
            r.education, r.occupation, r.is_host, r.attended_before, r.referral,
            r.p3y_prapti_din, r.experience, r.skill, r.photo_path
        ])

def already_registered(path: str, phone_e164: str) -> bool:
    if not os.path.exists(path): return False
    try:
        df = pd.read_csv(path, dtype=str)
        return bool((df["mobile_e164"] == phone_e164).any())
    except Exception:
        return False

def normalize_phone(raw: str, region: str = DEFAULT_REGION) -> str:
    num = phonenumbers.parse(raw, region)
    if not phonenumbers.is_valid_number(num):
        raise ValueError("Invalid phone number.")
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)

def calc_age(dob: date, today: Optional[date] = None) -> int:
    today = today or date.today()
    return max(0, today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))

def dob_bounds_100y():
    today = date.today()
    try:
        min_d = today.replace(year=today.year - 100)
    except ValueError:  # Feb 29 safety
        min_d = today.replace(month=2, day=28, year=today.year - 100)
    return min_d, today

def ensure_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)

def save_photo(uploaded_file, filename_prefix: str) -> str:
    """Save UploadedFile (from file_uploader or camera_input) to uploads/, return relative path."""
    if not uploaded_file:
        return ""
    ensure_upload_dir()
    # choose extension safely
    ext = ".jpg"
    if hasattr(uploaded_file, "type") and uploaded_file.type in ("image/png", "png"):
        ext = ".png"
    path = os.path.join(UPLOAD_DIR, f"{filename_prefix}{ext}")
    with open(path, "wb") as out:
        out.write(uploaded_file.getbuffer())
    return path

# ============================ WHATSAPP SENDERS ============================
def send_whatsapp_cloud_api(to_e164: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not (WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN and WHATSAPP_TEMPLATE_NAME):
        raise RuntimeError("Cloud API secrets missing.")
    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}
    body_params = [
        {"type": "text", "text": payload["full_name"]},
        {"type": "text", "text": str(payload["age_years"])},
        {"type": "text", "text": payload["full_address"][:900]},
        {"type": "text", "text": payload["education"] or "-"},
        {"type": "text", "text": payload["occupation"] or "-"},
        {"type": "text", "text": payload["is_host"]},
        {"type": "text", "text": payload["attended_before"]},
        {"type": "text", "text": payload["referral"] or "-"},
    ]
    data = {
        "messaging_product": "whatsapp",
        "to": to_e164,
        "type": "template",
        "template": {
            "name": WHATSAPP_TEMPLATE_NAME,
            "language": {"code": WHATSAPP_TEMPLATE_LANG},
            "components": [{"type": "body", "parameters": body_params}],
        },
    }
    r = requests.post(url, headers=headers, json=data, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Cloud API error {r.status_code}: {r.text}")
    return r.json()

def send_whatsapp_twilio(to_e164: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        raise RuntimeError("Twilio secrets missing.")
    from twilio.rest import Client
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    body = (
        f"Hi {payload['full_name']}! Thanks for your RSVP.\n"
        f"DOB: {payload['dob']} | Age: {payload['age_years']}\n"
        f"Address: {payload['full_address']}\n"
        f"Education: {payload['education']} | Occupation: {payload['occupation']}\n"
        f"Host: {payload['is_host']} | Attended before: {payload['attended_before']}\n"
        f"Source: {payload['referral']}"
    )
    msg = client.messages.create(
        from_=TWILIO_WHATSAPP_FROM,
        to=f"whatsapp:{to_e164}" if not to_e164.startswith("whatsapp:") else to_e164,
        body=body[:1500],
    )
    return {"sid": msg.sid, "status": msg.status}

def send_confirmation(provider: str, to_e164: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if provider == "none":   return {"status": "skipped"}
    if provider == "cloud_api": return send_whatsapp_cloud_api(to_e164, payload)
    if provider == "twilio":    return send_whatsapp_twilio(to_e164, payload)
    raise ValueError("Unsupported PROVIDER")

# ============================ UI (single flow) ============================
st.set_page_config(page_title="P3Y RSVP", page_icon="✅", layout="centered")

# Simple card styling (unchanged layout)
st.markdown("""
<style>
.block-container {max-width: 760px;}
.card {background:#fff;border:1px solid #edf0f2;border-radius:18px;padding:20px 22px;box-shadow:0 6px 24px rgba(0,0,0,.06);}
.stTextInput label, .stDateInput label, .stSelectbox label, .stTextArea label {font-weight:600;}
.stButton > button[kind="primary"]{background:linear-gradient(135deg,#6b8cff,#5162ff);border:none;color:#fff;font-weight:700;border-radius:12px;padding:.6rem 1.1rem;}
</style>
""", unsafe_allow_html=True)

st.markdown("### P3Y RSVP · कृपया नीचे जानकारी भरें", unsafe_allow_html=True)

min_dob, max_dob = dob_bounds_100y()

# Keep your field order; new fields are appended later as requested
mobile_raw = st.text_input("Enter Mobile Number / मोबाइल नंबर", value="+91 ", help="Editable default.")
full_name  = st.text_input("Full Name / पूरा नाम")

c1, c2 = st.columns([0.58, 0.42], vertical_alignment="bottom")
with c1:
    dob_val = st.date_input("Date of Birth / जन्म दिन", value=None, min_value=min_dob, max_value=max_dob, format="DD/MM/YYYY", key="dob")
with c2:
    age_years = calc_age(dob_val) if dob_val else None
    st.text_input("Age / उम्र (auto)", value=(str(age_years) if age_years is not None else ""), disabled=True)

full_address    = st.text_area("Full Address / पूरा पता", height=96)
education       = st.text_input("Education / शिक्षा")
occupation      = st.text_input("Occupation / व्यवसाय")
is_host         = st.selectbox("क्या आप क्लास के यजमान हैं? (Are you the class host?)", ["No", "Yes"])
attended_before = st.selectbox("पहले P3Y क्लास अटेंड किए हैं? (Attended P3Y before?)", ["No", "Yes"])
referral        = st.selectbox("आपको यह P3Y क्लास की जानकारी कैसे मिली? (How did you hear?)",
                               ["Friend/परिचित", "WhatsApp", "Facebook/Instagram", "Flyer/Poster", "Organizer", "Other (type below)"])
referral_other  = st.text_input("If 'Other', specify / अन्य स्रोत")

# ------- NEW FIELDS -------
p3y_prapti_din = st.date_input("P3Y प्राप्ति दिन", value=None, min_value=min_dob, max_value=max_dob, format="DD/MM/YYYY")
experience     = st.text_area("अनुभव | Experience", height=80, placeholder="संक्षेप में अनुभव लिखें / Brief experience")
skill          = st.text_input("कौशल | Skill", placeholder="e.g., Teaching, Organizing, Design")

# Photo option: upload OR camera
photo_option = st.radio("Photo", ["Upload from device", "Use camera", "Skip"], horizontal=True)
uploaded_file = None
if photo_option == "Upload from device":
    uploaded_file = st.file_uploader("Upload Photo (JPG/PNG)", type=["jpg","jpeg","png"])
elif photo_option == "Use camera":
    uploaded_file = st.camera_input("Click a Photo")

# debounce to prevent double-click sends
if "SUBMIT_LOCK_UNTIL" not in st.session_state: st.session_state.SUBMIT_LOCK_UNTIL = 0
disabled = time.time() < st.session_state.SUBMIT_LOCK_UNTIL
submit = st.button("Submit / जमा करें", type="primary", disabled=disabled)
st.markdown('</div>', unsafe_allow_html=True)

# Optional admin download
if os.path.exists(CSV_PATH):
    try:
        df = pd.read_csv(CSV_PATH)
        st.download_button("Download RSVPs (CSV)", df.to_csv(index=False).encode("utf-8"),
                           file_name="rsvp.csv", mime="text/csv")
    except Exception:
        pass

# ============================ SUBMIT ============================
if submit:
    try:
        now = time.time()
        if now < st.session_state.SUBMIT_LOCK_UNTIL:
            st.warning("Processing…")
            st.stop()
        st.session_state.SUBMIT_LOCK_UNTIL = now + 3

        # Basic validations
        if not dob_val:                          st.error("Please select Date of Birth."); st.stop()
        if age_years is None or not (0 <= age_years <= 120): st.error("DOB/age looks invalid."); st.stop()
        if not full_name.strip():                st.error("Full Name required."); st.stop()
        if not mobile_raw.strip():               st.error("Mobile Number required."); st.stop()
        if not full_address.strip():             st.error("Full Address required."); st.stop()

        # Save photo if provided
        phone_for_name = mobile_raw.strip().replace("+", "").replace(" ", "").replace("-", "")
        photo_path = ""
        if uploaded_file is not None:
            photo_path = save_photo(uploaded_file, f"{int(now)}_{phone_for_name}")

        mobile_e164 = normalize_phone(mobile_raw.strip(), DEFAULT_REGION)

        r = RSVP(
            ts=int(now),
            mobile_e164=mobile_e164,
            full_name=full_name.strip(),
            dob=dob_val.isoformat(),
            age_years=age_years,
            full_address=full_address.strip(),
            education=education.strip(),
            occupation=occupation.strip(),
            is_host=is_host,
            attended_before=attended_before,
            referral=(referral_other.strip() if referral.startswith("Other") and referral_other.strip() else referral),
            p3y_prapti_din=(p3y_prapti_din.isoformat() if p3y_prapti_din else ""),
            experience=experience.strip(),
            skill=skill.strip(),
            photo_path=photo_path,
        )

        if already_registered(CSV_PATH, r.mobile_e164):
            st.info("Already registered. Sending confirmation again…")
        else:
            save_rsvp(CSV_PATH, r)

        payload = asdict(r)
        try:
            resp = send_confirmation(PROVIDER, r.mobile_e164, payload)
            st.success("RSVP saved ✅  WhatsApp confirmation sent 📲" if PROVIDER != "none" else "RSVP saved ✅")
        except Exception as send_err:
            st.warning(f"RSVP saved, but WhatsApp send failed: {send_err}")
            resp = {"status": "send_failed", "error": str(send_err)}

        with st.expander("Submission"):
            st.json(payload)
        if PROVIDER != "none":
            with st.expander("WhatsApp API Response"):
                st.json(resp)

    except Exception as e:
        st.error(f"Failed: {e}")
        st.stop()
