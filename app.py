import os
import json
import imaplib
import email
from email.header import decode_header
import urllib.parse
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from groq import Groq
from supabase import create_client, Client
from pydantic import BaseModel, Field

# Load environment configs
load_dotenv()

st.set_page_config(page_title="EduPriority Smart Hub", page_icon="🛡️", layout="wide")

# Modern Premium UI Styles
st.markdown("""
    <style>
        .main { background-color: #070b12; color: #f1f5f9; }
        .login-box-container {
            background: rgba(15, 23, 42, 0.7) !important;
            backdrop-filter: blur(10px) !important;
            border-radius: 12px !important;
            border: 1px solid rgba(255, 255, 255, 0.08) !important;
            padding: 22px !important;
            margin-bottom: 20px;
        }
        .important-alert-hub {
            background: linear-gradient(135deg, rgba(220, 38, 38, 0.15) 0%, rgba(15, 23, 42, 0.9) 100%);
            border: 2px solid rgba(220, 38, 38, 0.5);
            padding: 22px; register_blueprint
            border-radius: 16px;
            margin-bottom: 30px;
        }
        .parent-feed-tile {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.8) 100%);
            border: 1px solid rgba(255, 255, 255, 0.06);
            padding: 22px;
            border-radius: 16px;
            margin-bottom: 20px;
        }
        .status-pill { padding: 4px 12px; border-radius: 50px; font-size: 0.75rem; font-weight: 700; }
        .pill-critical { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
        .pill-regular { background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }
    </style>
""", unsafe_allow_html=True)

def get_system_token(key_name):
    val = os.getenv(key_name)
    if val: return val
    try:
        if hasattr(st, "secrets") and key_name in st.secrets:
            return st.secrets[key_name]
    except Exception: pass
    return ""

# Initialize API connections
try:
    url: str = get_system_token("SUPABASE_URL")
    key: str = get_system_token("SUPABASE_KEY")
    supabase: Client = create_client(url, key)
    
    gemini_client = genai.Client(api_key=get_system_token("GEMINI_API_KEY"))
    groq_client = Groq(api_key=get_system_token("GROQ_API_KEY"))
except Exception as e:
    st.error(f"⚠️ Infrastructure Setup Error: {e}")

VALID_CATEGORIES = ["Exam Results", "Leave & Attendance", "Fee Deadlines", "Emergency Notice", "Studies & Academics", "General Update"]

class ParentEmailAnalysis(BaseModel):
    category: str = Field(description="Must match exactly one option from VALID_CATEGORIES")
    urgency_level: str = Field(description="High, Medium, or Low")
    parent_action_required: bool = Field(description="True or False")
    extracted_deadline: str = Field(description="YYYYMMDD string or 'None'")

# Deep Mailbox Extraction Engine
def fetch_unread_school_emails():
    email_user = st.session_state.get("session_gmail_username")
    email_pass = st.session_state.get("session_gmail_password")
    
    if not email_user or not email_pass:
        st.error("Missing login username or password inside the session cache context.")
        return []
        
    fetched_emails = []
    try:
        # Secure SSL Port handshake configuration
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_user, email_pass)
        
        # Scans the entire repository archive to capture both read/unread elements
        mail.select('"[Gmail]/All Mail"') 
        status, messages = mail.search(None, 'ALL')
        mail_ids = messages[0].split()
        
        if not mail_ids:
            st.warning("Connected successfully, but 0 emails were found in '[Gmail]/All Mail'.")
            return []
            
        # Parse through the last 10 entries for tracking diagnostics
        for i in mail_ids[-10:]:
            res, msg_data = mail.fetch(i, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                        
                    from_sender, encoding = decode_header(msg["From"])[0]
                    if isinstance(from_sender, bytes):
                        from_sender = from_sender.decode(encoding if encoding else "utf-8", errors="ignore")
                    
                    email_body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disp = str(part.get("Content-Disposition"))
                            if content_type == "text/plain" and "attachment" not in content_disp:
                                email_body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        email_body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                        
                    fetched_emails.append({
                        "sender": str(from_sender),
                        "subject": str(subject),
                        "body": str(email_body[:4000])
                    })
        mail.close()
        mail.logout()
    except Exception as e:
        st.error(f"❌ Gmail IMAP Connection Error: {e}")
    return fetched_emails

def run_intelligent_triage(sender: str, subject: str, body: str):
    prompt = f"Analyze this school message. Classify category strictly as one of {VALID_CATEGORIES}.\nFrom: {sender}\nSubject: {subject}\nBody: {body}"
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=ParentEmailAnalysis, temperature=0.1),
        )
        return ParentEmailAnalysis.model_validate_json(response.text)
    except Exception as e:
        try:
            completion = groq_client.chat.completions.create(
                messages=[{"role": "user", "content": f"Extract details into JSON matching schema parameters for text: \"{body}\""}],
                model="llama-3.1-8b-instant",
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            return ParentEmailAnalysis.model_validate_json(completion.choices[0].message.content)
        except Exception:
            return ParentEmailAnalysis(category="General Update", urgency_level="Low", parent_action_required=False, extracted_deadline="None")

def compile_parent_takeaway(body: str, category: str):
    try:
        prompt = f"Analyze this notification text regarding '{category}'. Output exactly 3 markdown bullet points mapping Context, Impact, and Action for a parent. Content Body: \"{body}\""
        completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.1,
        )
        return completion.choices[0].message.content.strip()
    except Exception:
        return "• 📌 **Context:** General operational update message logged.\n• ⚠️ **Impact:** Standard administrative visibility.\n• ✅ **Action:** Review original transcript details."

def save_log_to_supabase(sender, subject, body, triage: ParentEmailAnalysis, summary: str):
    current_active_user = st.session_state.get("session_gmail_username")
    if not current_active_user: return False
    try:
        data = {
            "user_owner_identity": str(current_active_user).lower().strip(),
            "sender_email": str(sender),
            "email_subject": str(subject),
            "email_body": str(body),
            "extracted_category": str(triage.category),
            "urgency_level": str(triage.urgency_level),
            "parent_action_required": bool(triage.parent_action_required),
            "target_deadline": str(triage.extracted_deadline),
            "clean_bullet_summary": str(summary)
        }
        # Execute row ingestion mapping explicitly
        supabase.table("parent_email_alerts").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"❌ Supabase Writing Error: {e}")
        return False

def pull_historical_records():
    current_active_user = st.session_state.get("session_gmail_username")
    if not current_active_user: return []
    try:
        response = supabase.table("parent_email_alerts")\
            .select("*")\
            .eq("user_owner_identity", str(current_active_user).lower().strip())\
            .order("created_at", desc=True)\
            .execute()
        return response.data
    except Exception as e:
        st.error(f"❌ Supabase Reading Error: {e}")
        return []

# Session state variable validation initialization
if "session_gmail_username" not in st.session_state:
    st.session_state["session_gmail_username"] = ""
if "session_gmail_password" not in st.session_state:
    st.session_state["session_gmail_password"] = ""

st.markdown('<h1 style="text-align:center;">🛡️ EduPriority AI Workspace</h1>', unsafe_allow_html=True)

# Instantly read current database profile records
logs_data = pull_historical_records()
df = pd.DataFrame(logs_data) if logs_data else pd.DataFrame()

col_side_control, col_main_feed = st.columns([1.2, 2.5])

# ==================== CONTROLLER PANEL (LEFT SIDE) ====================
with col_side_control:
    st.markdown("### 🔐 Account Access")
    
    st.markdown('<div class="login-box-container">', unsafe_allow_html=True)
    u_email = st.text_input("Gmail Account Address", value=st.session_state["session_gmail_username"], placeholder="user@gmail.com")
    u_pass = st.text_input("16-Character App Password", type="password", value=st.session_state["session_gmail_password"], placeholder="abcd efgh ijkl mnop")
    
    if st.button("🔑 Mount Profile Session", use_container_width=True):
        st.session_state["session_gmail_username"] = u_email.strip().lower()
        st.session_state["session_gmail_password"] = u_pass.strip()
        st.success("Credentials saved to session!")
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state["session_gmail_username"]:
        st.info(f"Connected to profile: `{st.session_state['session_gmail_username']}`")
        
        if st.button("🔄 Fetch & Analyze Mailbox Now", use_container_width=True):
            with st.spinner("Extracting message packets..."):
                unread_packets = fetch_unread_school_emails()
                
                if not unread_packets:
                    st.info("No emails were extracted or found during this pass.")
                else:
                    success_count = 0
                    for packet in unread_packets:
                        meta = run_intelligent_triage(packet["sender"], packet["subject"], packet["body"])
                        bullets = compile_parent_takeaway(packet["body"], meta.category)
                        if save_log_to_supabase(packet["sender"], packet["subject"], packet["body"], meta, bullets):
                            success_count += 1
                    
                    if success_count > 0:
                        st.success(f"Successfully processed {success_count} updates!")
                        st.rerun()
                        
        if st.button("🚪 Disconnect Session", use_container_width=True):
            st.session_state["session_gmail_username"] = ""
            st.session_state["session_gmail_password"] = ""
            st.rerun()

# ==================== FEED DASHBOARD (RIGHT SIDE) ====================
with col_main_feed:
    st.markdown("### 📋 Filtered Notification Stream")
    
    if df.empty:
        st.warning("Your dashboard feed is empty. Enter your credentials, click 'Mount Profile', then click 'Fetch & Analyze Mailbox Now' to download your mail items.")
    else:
        for _, row in df.iterrows():
            is_high = str(row['urgency_level']).lower() == 'high'
            badge = '<span class="status-pill pill-critical">🚨 CRITICAL</span>' if is_high else '<span class="status-pill pill-regular">ℹ️ STABLE</span>'
            
            st.markdown(f"""
                <div class="parent-feed-tile">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-weight:700; font-size:1.1rem; color:#ffffff;">{row['email_subject']}</span>
                        {badge}
                    </div>
                    <div style="font-size:0.85rem; color:#94a3b8; margin-top:4px;">
                        From: {row['sender_email']} | Category: {row['extracted_category']}
                    </div>
                </div>
            """, unsafe_allow_html=True)
            st.markdown(row['clean_bullet_summary'])
            with st.expander("Show Original Email Content"):
                st.text(row['email_body'])