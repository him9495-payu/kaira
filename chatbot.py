# chatbot.py
"""
Flowless PayU Finance WhatsApp chatbot orchestration.

- No WhatsApp "flows".
- Bilingual (English/Hindi).
- Onboarding: Name -> DOB -> Employment -> Salary -> Purpose -> Consent -> Offer generation -> Offer selection -> KYC -> Selfie -> Bank details -> Final checks -> NACH -> Agreement -> Disbursement
- Support via Bedrock (anthropic.claude-3-haiku by default; configurable via BEDROCK_MODEL_ID env var).
- Integrates with:
    - db_io.py (UserProfileStore, LoanRecordStore, InteractionStore)
    - whatsapp_messaging.MetaWhatsAppClient
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, validator

from whatsapp_messaging import MetaWhatsAppClient
from db_io import (
    ConversationState,
    InteractionStore,
    LoanRecordStore,
    UserProfile,
    UserProfileStore,
    iso_timestamp,
    now_ts,
    serialize_conversation_state,
)

try:
    import boto3
except Exception:
    boto3 = None

try:
    from mangum import Mangum
except ImportError:  # pragma: no cover - mangum optional for local runs
    Mangum = None

# --- Configuration & logging ---
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("payu.flowless.chatbot")

app = FastAPI(title="PayU Flowless Chatbot", version="1.0.0")
_lambda_adapter = Mangum(app) if Mangum else None

# Environment / defaults
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "payu-verify-token")
BACKEND_DECISION_URL = os.getenv("BACKEND_DECISION_URL")
BACKEND_API_KEY = os.getenv("BACKEND_DECISION_API_KEY")
USER_TABLE_NAME = os.getenv("USER_TABLE_NAME", "user_profiles")
INTERACTION_TABLE_NAME = os.getenv("INTERACTION_TABLE_NAME", "interaction_events")
LOAN_TABLE_NAME = os.getenv("LOAN_TABLE_NAME", "loan_records")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
INACTIVITY_MINUTES = int(os.getenv("INACTIVITY_MINUTES", "30"))
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku")
HUMAN_HANDOFF_QUEUE = os.getenv("HUMAN_HANDOFF_QUEUE", "payu-finance-support")
DEFAULT_LANGUAGE = "en"

# --- Language packs ---
LANGUAGE_PACKS: Dict[str, Dict[str, Any]] = {
    "en": {
        "welcome": "👋 Welcome to PayU Finance — I am your Personal Loan assistant.",
        "language_prompt": "Please choose your preferred language.",
        "language_option_en": "English",
        "language_option_hi": "हिंदी",
        "main_offer_intro": "Get a loan up to ₹5,00,000 in under 5 minutes. Apply Now!",
        "get_loan": "Get Loan",
        "support": "Support",
        "support_prompt_existing": "Tell me briefly how I can help or choose an option below.",
        "support_prompt_new": "Tell me briefly how I can help you?",
        "support_closing": "If you need further help, connect to an agent.",
        "support_handoff": "Connecting you to a PayU specialist now.",
        "support_escalation_ack": "A PayU specialist has been notified and will reach out shortly.",
        "ask_name": "Please share your full name (as per PAN)",
        "ask_dob": "Please enter your date of birth in DD-MM-YYYY format\ne.g. 31-12-1995",
        "invalid_dob": "Invalid date. Please provide in DD-MM-YYYY format\ne.g. 31-12-1995",
        "ask_employment": "Select your Employment type",
        "employment_options": ["Salaried", "Self-Employed", "Others"],
        "ask_salary": "What's your Monthly Income in INR\nOnly enter Numbers",
        "invalid_number": "Please enter numbers only (e.g. 45000)",
        "ask_purpose": "What will this loan help you with?",
        "purpose_options": ["Personal", "Education", "Medical", "Home", "Travel", "Others"],
        "ask_consent": "I authorize PayU Finance to process my information and pull credit bureau records.",
        "consent_required": "Consent is required to proceed with credit evaluation.",
        "decision_submit": "Processing your loan application...",
        "decision_rejected": "We're sorry!\nYour profile is rejected due to {reason}. Please come back later.\n Try our LazyPay app today.",
        "decision_approved_intro": "🎉 You're eligible for a loan. Below are few curated offers for you",
        "offers_prompt": "Select an offer to proceed or type Support for help",
        "offer_button_accept": "Accept",
        "offer_button_view_details": "View Details",
        "ask_kyc": "Complete KYC to proceed. Tap Complete KYC.",
        "kyc_completed": "KYC is successfully completed. Moving to Selfie now.",
        "ask_selfie": "Please take a selfie now using WhatsApp camera and send it here.",
        "selfie_received": "Looking good, smarty!",
        "ask_bank": "Please provide bank details in the format:\n<IFSC>\n<account_number>",
        "bank_details_received": "Bank details received. Submitting your application.",
        "final_approval": "✅ Loan approved!\nAmount: ₹{amount:,.0f}.\nLoan ID: {ref}",
        "final_reject": "We're unable to disburse the loan because: {reason}. Please contact Support.",
        "nach_prompt": "Complete NACH (mandate) to enable auto-debit. Tap Complete NACH.",
        "agreement_prompt": "Please review and agree to the Customer Agreement to proceed.",
        "agreement_sent": "Read the Agreement carefully and tap Agree to sign and continue.",
        "agreement_signed": "🎉 Congratulations! Everything's done and your amount will be credited to your account soon.",
        "disbursed": "Your loan has been disbursed. Congratulations!",
        "text_only_warning": "I currently support text and buttons. Please respond using text or buttons.",
        "download_app": "Download App",
        "send_email": "Mail Us",
        "connect_agent": "Connect to Agent",
        "post_loan_menu_intro": "Choose an option",
        "post_loan_view_details": "View Loan Details",
        "post_loan_download_pdf": "Download Loan PDF",
        "post_loan_repay": "Repay Loan",
        "confirm_agree": "Agree",
        "confirm_disagree": "Not Agree",
        "language_changed": "Language updated.",
        "invalid_choice": "Please choose from the available options.",
    },
    "hi": {
        "welcome": "👋 पेयू फाइनेंस में आपका स्वागत है — आपका पर्सनल लोन असिस्टेंट।",
        "language_prompt": "कृपया अपनी पसंदीदा भाषा चुनें:",
        "language_option_en": "English",
        "language_option_hi": "हिंदी",
        "main_offer_intro": "आप 5 मिनट में ₹5,00,000 तक का लोन प्राप्त कर सकते हैं। आप क्या करना चाहेंगे?",
        "get_loan": "लोन लें",
        "support": "सपोर्ट",
        "support_prompt_existing": "कृपया बताएं कि आपको किस प्रकार मदद चाहिए या नीचे से विकल्प चुनें।",
        "support_prompt_new": "आवेदन से पहले, आप मुझसे सवाल कर सकते हैं या मदद ले सकते हैं। कैसे मदद करूँ?",
        "support_closing": "यदि आपको और सहायता चाहिए तो एजेंट से कनेक्ट करें।",
        "support_handoff": "मैं आपको PayU विशेषज्ञ से जोड़ रहा हूँ।",
        "support_escalation_ack": "PayU विशेषज्ञ को सूचित कर दिया गया है, वे जल्द ही संपर्क करेंगे।",
        "ask_name": "कृपया अपना पूरा नाम लिखें (आधिकारिक आईडी के अनुसार)।",
        "ask_dob": "कृपया अपनी जन्मतिथि DD-MM-YYYY फॉर्मेट में दें (उदा. 31-12-1990)।",
        "invalid_dob": "अमान्य तिथि फॉर्मेट। कृपया DD-MM-YYYY (उदा. 31-12-1990) में दें।",
        "ask_employment": "अपना रोजगार प्रकार चुनें:",
        "employment_options": ["नौकरीपेशा (Salaried)", "स्वरोज़गार (Self-Employed)", "अन्य (Other)"],
        "ask_salary": "कृपया अपनी औसत मासिक आय ₹ में लिखें (सिर्फ अंक).",
        "invalid_number": "कृपया केवल संख्याएँ भेजें (उदा. 45000)।",
        "ask_purpose": "इस लोन का मुख्य उद्देश्य क्या है? विकल्प चुनें या लिखें।",
        "purpose_options": ["Personal", "Education", "Medical", "Home", "Travel", "Other"],
        "ask_consent": "क्या आप PayU को अपने विवरण प्रोसेस करने और क्रेडिट ब्यूरो जांच करने की सहमति देते हैं? (Yes / No)",
        "consent_required": "आगे बढ़ने के लिए सहमति आवश्यक है।",
        "decision_submit": "आपकी जानकारी जाँच के लिए भेज रहा हूँ...",
        "decision_rejected": "क्षमा करें — हम अभी लोन स्वीकृत नहीं कर पाए क्योंकि: {reason}. कृपया Support का उपयोग करें।",
        "decision_approved_intro": "🎉 आप प्रावधानिक रूप से पात्र हैं। उपलब्ध ऑफ़र नीचे हैं:",
        "offers_prompt": "किसी ऑफ़र का चयन करें या Support चुनें।",
        "offer_button_accept": "स्वीकार करें",
        "offer_button_view_details": "विवरण देखें",
        "ask_kyc": "कृपया KYC पूरा करें। Complete KYC दबाएँ।",
        "kyc_completed": "KYC पूरा हो गया। कृपया WhatsApp कैमरा से अपनी सेल्फ़ी भेजें।",
        "ask_selfie": "कृपया अब WhatsApp कैमरा का उपयोग कर सेल्फ़ी लें और भेजें।",
        "selfie_received": "सेल्फ़ी प्राप्त हो गई।",
        "ask_bank": "कृपया बैंक विवरण दें\n<IFSC>\n<account_number>",
        "bank_details_received": "बैंक विवरण प्राप्त। अंतिम जाँच कर रहा हूँ...",
        "final_approval": "✅ लोन स्वीकृत और जारी किया गया! राशि: ₹{amount:,.2f}. संदर्भ: {ref}",
        "final_reject": "हम लोन जारी नहीं कर पा रहे हैं क्योंकि: {reason}. कृपया Support से संपर्क करें।",
        "nach_prompt": "NACH (मंडेट) पूरा करें। Complete NACH दबाएँ।",
        "agreement_prompt": "कृपया ग्राहक समझौते पढ़ें और सहमति दें।",
        "agreement_sent": "समझौता भेजा गया। Agree दबाएँ।",
        "agreement_signed": "धन्यवाद — समझौता स्वीकार कर लिया गया।",
        "disbursed": "आपका लोन डिस्बर्स कर दिया गया है। बधाई!",
        "text_only_warning": "मैं अभी टेक्स्ट और बटन रिस्पॉन्स का समर्थन करता हूँ। कृपया टेक्स्ट या बटन का उपयोग करें।",
        "download_app": "एप डाउनलोड करें",
        "send_email": "ईमेल भेजें",
        "connect_agent": "एजेंट से कनेक्ट करें",
        "post_loan_menu_intro": "एक विकल्प चुनें:",
        "post_loan_view_details": "लोन विवरण देखें",
        "post_loan_download_pdf": "लोन पीडीएफ डाउनलोड करें",
        "post_loan_repay": "लोन चुका दें",
        "confirm_agree": "Agree",
        "confirm_disagree": "Not Agree",
        "language_changed": "भाषा बदल दी गई है।",
        "invalid_choice": "कृपया उपलब्ध विकल्पों में से चुनें।",
    },
}

# ---------------------------------------------------------------------------
# Reuse & stores
# ---------------------------------------------------------------------------

BOOLEAN_SYNONYMS = {
    True: {"yes", "y", "haan", "haanji", "consent", "agree", "ok", "sure", "accept"},
    False: {"no", "n", "nah", "na", "stop", "reject"},
}

INTENT_KEYWORDS = {
    "apply": {"apply", "loan", "new loan", "finance", "start", "continue"},
    "support": {"support", "help", "emi", "statement", "status", "issue", "problem", "agent"},
}

user_store = UserProfileStore(USER_TABLE_NAME, AWS_REGION)
interaction_store = InteractionStore(INTERACTION_TABLE_NAME, AWS_REGION)
loan_store = LoanRecordStore(LOAN_TABLE_NAME, AWS_REGION)

messenger = MetaWhatsAppClient(META_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID)

# Bedrock wrapper (Anthropic recommended model by default)
class BedrockSupportResponder:
    def __init__(self, model_id: Optional[str], region: str):
        self.model_id = model_id
        self.region = region
        self._client = None
        if model_id and boto3:
            try:
                self._client = boto3.client("bedrock-runtime", region_name=region)
            except Exception as exc:
                logger.error("Bedrock init failed: %s", exc)
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def answer(self, question: str, language: str, context: str) -> Optional[str]:
        if not self.enabled:
            return None
        language_name = "English" if language == "en" else "Hindi"
        instructions = (
            "You are PayU Finance's bilingual support assistant. Answer concisely "
            f"in {language_name}. If unsure, acknowledge and suggest connecting to an agent."
        )
        prompt = f"{instructions}\n\nContext:\n{context}\n\nCustomer question:\n{question}\n\nAnswer:"
        payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}], "max_tokens": 400, "temperature": 0.0}
        try:
            response = self._client.invoke_model(modelId=self.model_id, contentType="application/json", accept="application/json", body=json.dumps(payload))
            raw_body = response["body"].read()
            data = json.loads(raw_body.decode("utf-8"))
            if "output" in data:
                return data["output"][0].get("content", [{}])[0].get("text")
            if "results" in data:
                return data["results"][0].get("outputText")
        except Exception as exc:
            logger.error("Bedrock answered with error: %s", exc)
        return None

bedrock_responder = BedrockSupportResponder(BEDROCK_MODEL_ID, AWS_REGION)

# --- Decision client fallback (local) -------------------------------------
class DecisionClient:
    """
    Minimal fallback decision client providing a _local_rules(app) method
    compatible with the rest of the code. This avoids a NameError when the
    external decision backend is not configured.
    """
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url

    def _local_rules(self, application: LoanApplication):
        # Reuse generate_offers to create a DummyDecision and offers
        decision, offers = generate_offers(application)
        # ensure returned decision has expected attributes (approved, offer_amount, apr, max_term_months, reason, reference_id)
        return decision

    def evaluate(self, application: LoanApplication):
        # Placeholder: if you later plug a real backend, call it here.
        return self._local_rules(application)

# instantiate a module-level decision_client so run_final_checks_and_disburse can call it

decision_client = DecisionClient(base_url=BACKEND_DECISION_URL)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LoanApplication(BaseModel):
    application_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    customer_phone: str
    full_name: str
    age: int = Field(ge=18, le=75)
    employment_status: str
    monthly_income: float = Field(gt=0)
    requested_amount: float = Field(gt=0)
    purpose: str
    consent_to_credit_check: bool

    @validator("employment_status")
    def normalize_employment(cls, v: str) -> str:
        return v.strip().title()

    @validator("purpose")
    def normalize_purpose(cls, v: str) -> str:
        return v.strip().capitalize()

class DecisionResult(BaseModel):
    approved: bool
    offer_amount: float
    apr: float
    max_term_months: int
    reason: Optional[str]
    reference_id: str

# ---------------------------------------------------------------------------
# Onboarding sequence & helpers
# ---------------------------------------------------------------------------

ONBOARDING_SEQUENCE = ["full_name", "dob", "employment_status", "monthly_income", "purpose", "consent_to_credit_check"]

def get_pack(lang: Optional[str]) -> Dict[str, Any]:
    return LANGUAGE_PACKS.get(lang or DEFAULT_LANGUAGE, LANGUAGE_PACKS[DEFAULT_LANGUAGE])

def set_current_field(state: ConversationState, field: Optional[str]) -> None:
    state.answers = state.answers or {}
    if field is None:
        state.answers.pop("_current_field", None)
    else:
        state.answers["_current_field"] = field

def get_current_field(state: ConversationState) -> Optional[str]:
    return (state.answers or {}).get("_current_field")

def advance_to_next_field(state: ConversationState) -> Optional[str]:
    current = get_current_field(state)
    if current is None:
        set_current_field(state, ONBOARDING_SEQUENCE[0])
        return ONBOARDING_SEQUENCE[0]
    try:
        idx = ONBOARDING_SEQUENCE.index(current)
    except ValueError:
        idx = -1
    next_idx = idx + 1
    if next_idx < len(ONBOARDING_SEQUENCE):
        set_current_field(state, ONBOARDING_SEQUENCE[next_idx])
        return ONBOARDING_SEQUENCE[next_idx]
    set_current_field(state, None)
    return None

def normalize_boolean(value: str) -> Optional[bool]:
    candidate = value.strip().lower()
    for bool_value, synonyms in BOOLEAN_SYNONYMS.items():
        if candidate in synonyms:
            return bool_value
    return None

def parse_numeric(value: str, value_type=float):
    try:
        cleaned = value.replace(",", "").strip()
        return value_type(cleaned)
    except Exception:
        raise ValueError("invalid_number")

def compute_age_from_dob(dob: str) -> int:
    dt = datetime.strptime(dob, "%d-%m-%Y")
    today = datetime.today()
    return today.year - dt.year - ((today.month, today.day) < (dt.month, dt.day))

# ---------------------------------------------------------------------------
# Messaging helpers (split buttons, lists, etc.)
# ---------------------------------------------------------------------------

def send_buttons_split(phone: str, text: str, buttons: List[Tuple[str, str]]) -> None:
    if len(buttons) <= 3:
        messenger.send_interactive_buttons(phone, text, buttons)
        return
    messenger.send_interactive_buttons(phone, text, buttons[:3])
    remaining = buttons[3:]
    messenger.send_interactive_buttons(phone, "More options", remaining)

def send_language_buttons(phone: str) -> None:
    pack = get_pack("en")
    messenger.send_interactive_buttons(phone, pack["language_prompt"], [("lang_en", pack["language_option_en"]), ("lang_hi", pack["language_option_hi"])])

# ---------------------------------------------------------------------------
# Offer generation & presentation
# ---------------------------------------------------------------------------

from dataclasses import dataclass

@dataclass
class DummyDecision:
    approved: bool
    reference_id: str
    offer_amount: int
    apr: float | None
    max_term_months: int | None
    reason: str | None = None


def generate_offers(application):
    """
    Fully local dummy credit decision + offer generation.
    No external service calls.
    Returns (decision, offers) where offers use the normalized schema:
      {
        "offer_id": str,
        "amount": int,
        "tenure": int,
        "apr": float,
        "processing_fee": float,
        "roi": float,
        "monthly_emi": float
      }
    """
    import math
    import random

    # Dummy approval logic
    score = random.randint(700, 900)
    approved = score >= 690

    if not approved:
        decision = DummyDecision(
            approved=False,
            reference_id=f"REF-{random.randint(100000, 999999)}",
            offer_amount=0,
            apr=None,
            max_term_months=None,
            reason="Low credit score",
        )
        return decision, []

    max_amount = min(int(application.monthly_income * 10), 150000)
    base_amount = int(max_amount * 0.6)

    raw_offers = [
        {"offer_id": "OFFER1", "amount": base_amount, "tenure": 6, "apr": 18.0, "processing_fee": 3.0, "roi": 16.5},
        {"offer_id": "OFFER2", "amount": int(base_amount * 1.15), "tenure": 9, "apr": 21.0, "processing_fee": 2.5, "roi": 18.0},
        {"offer_id": "OFFER3", "amount": int(base_amount * 1.35), "tenure": 12, "apr": 24.0, "processing_fee": 2.0, "roi": 20.0},
    ]

    # Compute an approximate EMI for each offer (simple formula)
    def compute_emi(principal, monthly_rate_percent, months):
        r = monthly_rate_percent / 100.0 / 12.0
        if r == 0:
            return principal / months
        emi = principal * r * ((1 + r) ** months) / (((1 + r) ** months) - 1)
        return math.ceil(emi)

    offers = []
    for o in raw_offers:
        monthly_emi = compute_emi(o["amount"], o["apr"], o["tenure"])
        o_norm = {
            "offer_id": o["offer_id"],
            "amount": o["amount"],
            "tenure": o["tenure"],
            "apr": o["apr"],
            "processing_fee": o["processing_fee"],
            "roi": o["roi"],
            "monthly_emi": monthly_emi,
        }
        offers.append(o_norm)

    decision = DummyDecision(
        approved=True,
        reference_id=f"REF-{random.randint(100000, 999999)}",
        offer_amount=max_amount,
        apr=offers[0]["apr"],
        max_term_months=offers[-1]["tenure"],
    )
    return decision, offers

def present_offers(phone: str, lang: str, decision: DummyDecision, offers: List[Dict[str, Any]], profile: UserProfile) -> None:
    """
    Show offers to the user in one interactive-button message only.
    """
    pack = get_pack(lang)

    if not decision.approved:
        messenger.send_text(phone, pack["decision_rejected"].format(reason=decision.reason or "policy"))
        return

    # Save offers to profile
    profile.metadata["offers"] = offers
    user_store.save(profile)

    # Build combined offer text
    offer_lines = [pack["decision_approved_intro"], ""]
    buttons = []

    for idx, offer in enumerate(offers, start=1):
        offer_lines.append(
            f"⭐ *Offer {idx}*\n"
            f"• Amount: ₹{offer['amount']:,.0f}\n"
            f"• Tenure: {offer['tenure']} months\n"
            f"• APR: {offer['apr']:.2f}%\n"
            f"• ROI: {offer['roi']:.2f}%\n"
            f"• Processing fee: {offer['processing_fee']:.2f}%\n"
            f"• EMI: ₹{offer['monthly_emi']:,.0f}\n"
        )

        # One button per offer
        buttons.append((
            f"offer_select_{offer['offer_id']}",
            f"{pack['offer_button_accept']} {idx}"
        ))

    # Final body text for the SINGLE message
    body = "\n".join(offer_lines).strip()

    # Send ONE message: body + 3 buttons
    messenger.send_interactive_buttons(phone, body, buttons)
    messenger.send_text(phone, pack["offers_prompt"])


# ---------------------------------------------------------------------------
# KYC / Selfie / Bank / NACH / Agreement
# ---------------------------------------------------------------------------

def send_kyc_prompt(phone: str, lang: str) -> None:
    pack = get_pack(lang)
    messenger.send_interactive_buttons(phone, pack["ask_kyc"], [("kyc_complete", "Complete KYC")])

def handle_kyc_complete(phone: str, profile: UserProfile, state: ConversationState, lang: str) -> None:
    profile.metadata["kyc_completed"] = True
    user_store.save(profile)
    messenger.send_text(phone, get_pack(lang)["kyc_completed"])
    set_current_field(state, "selfie")
    messenger.send_text(phone, get_pack(lang)["ask_selfie"])

def handle_selfie(phone: str, profile: UserProfile, state: ConversationState, lang: str) -> None:
    profile.metadata["selfie_received"] = True
    user_store.save(profile)
    messenger.send_text(phone, get_pack(lang)["selfie_received"])
    set_current_field(state, "bank_details")
    messenger.send_text(phone, get_pack(lang)["ask_bank"])

def parse_bank_details(text: str) -> Optional[Dict[str, str]]:
    parts = [p.strip() for p in text.split("\n")]
    if len(parts) < 2:
        return None
    return {"account_number": parts[1], "ifsc": parts[0]}

def send_nach_prompt(phone: str, lang: str) -> None:
    messenger.send_interactive_buttons(phone, get_pack(lang)["nach_prompt"], [("nach_complete", "Complete NACH")])

def send_agreement(phone: str, lang: str, profile: UserProfile) -> None:
    pack = get_pack(lang)
    tmp_path = "/tmp/payu_agreement.pdf"
    try:
        with open(tmp_path, "wb") as f:
            f.write(b"%PDF-1.4\n% Dummy PayU agreement\n")
        messenger.send_document(phone, tmp_path, filename="PayU_Agreement.pdf")
    except Exception:
        messenger.send_text(phone, "Agreement: [link]")
    messenger.send_interactive_buttons(phone, pack["agreement_sent"], [("agree_yes", pack["confirm_agree"]), ("agree_no", pack["confirm_disagree"])])

# ---------------------------------------------------------------------------
# Support handling
# ---------------------------------------------------------------------------

SUPPORT_KB = [
    {"q_en": "How can I pay my EMI?", "a_en": "You can pay via PayU app, netbanking or UPI. Reply PAY LINK for a payment link."},
    {"q_en": "How do I check my loan status?", "a_en": "Open PayU app > My Loans, or ask me to show loan details."},
]

def handle_support(phone: str, text: str, state: ConversationState, lang: str, profile: UserProfile) -> None:
    pack = get_pack(lang)
    normalized = (text or "").strip().lower()
    if normalized in {pack.get("download_app", "").lower(), "download app"}:
        messenger.send_text(phone, "Download the PayU Finance app from Play Store / App Store: https://play.google.com/store/apps/details?id=com.citrus.citruspay&hl=en_IN")
        return
    if normalized in {pack.get("send_email", "").lower(), "send email"}:
        messenger.send_text(phone, "Drop us a line at care@payufin.com and we'll get back at the earliest.")
        return
    loan_ctx = loan_store.get_record(phone) or profile.metadata.get("loan_record", {})
    context = json.dumps(loan_ctx) if loan_ctx else ""
    bedrock_answer = bedrock_responder.answer(text, lang, context) if bedrock_responder and bedrock_responder.enabled else None
    if bedrock_answer:
        messenger.send_text(phone, bedrock_answer)
        messenger.send_interactive_buttons(phone, pack["support_closing"], [("connect_agent", pack["connect_agent"])])
        record_interaction(phone, "outbound", "support_answer", {"source": "bedrock", "question": text})
        return
    for entry in SUPPORT_KB:
        if entry["q_en"].lower() in (text or "").lower():
            messenger.send_text(phone, entry["a_en"])
            messenger.send_interactive_buttons(phone, pack["support_closing"], [("connect_agent", pack["connect_agent"])])
            record_interaction(phone, "outbound", "support_answer", {"source": "kb", "question": text})
            return
    messenger.send_text(phone, "I couldn't find a precise answer. You want to connect to a PayU specialist?")
    messenger.send_interactive_buttons(phone, pack["support_closing"], [("connect_agent", pack["connect_agent"]), ("send_email", pack["send_email"])])
    record_interaction(phone, "system", "support_escalation", {"reason": "no_match", "question": text})

# ---------------------------------------------------------------------------
# Persistence & interaction helpers
# ---------------------------------------------------------------------------

def record_interaction(phone: str, direction: str, category: str, payload: Optional[Dict[str, Any]] = None) -> None:
    payload = payload or {}
    try:
        interaction_store.put(phone, direction, category, payload)
    except Exception:
        logger.exception("Failed to record interaction")

def persist_state_on_profile(profile: UserProfile, state: ConversationState) -> None:
    try:
        profile.metadata["conversation_state"] = serialize_conversation_state(state)
        user_store.save(profile)
    except Exception:
        logger.exception("Failed to persist conversation state")

# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------

def extract_message_text(message: Dict[str, Any]) -> Optional[str]:
    if "text" in message and message["text"].get("body"):
        return message["text"]["body"]
    if "button" in message:
        return message["button"].get("text")
    interactive = message.get("interactive")
    if interactive:
        if interactive.get("type") == "button_reply":
            return interactive["button_reply"].get("title")
        if interactive.get("type") == "list_reply":
            return interactive["list_reply"].get("title")
    return None

def extract_button_reply_id(message: Dict[str, Any]) -> Optional[str]:
    interactive = message.get("interactive")
    if interactive and interactive.get("type") == "button_reply":
        return interactive["button_reply"].get("id")
    return None

# ---------------------------------------------------------------------------
# Main message handler (router)
# ---------------------------------------------------------------------------

def handle_incoming_message(message: Dict[str, Any]) -> None:
    phone = message.get("from")
    if not phone:
        return
    profile = user_store.get(phone) or UserProfile(phone=phone)
    state_snapshot = profile.metadata.get("conversation_state")
    if state_snapshot:
        try:
            conversation_state = ConversationState(**state_snapshot)
        except Exception:
            conversation_state = ConversationState()
    else:
        conversation_state = ConversationState()
    profile.touch()
    user_store.save(profile)

    reply_id = extract_button_reply_id(message)
    text = extract_message_text(message)
    normalized = text.strip().lower() if text else ""
    language = conversation_state.language or profile.language or DEFAULT_LANGUAGE
    pack = get_pack(language)

    # inbound record
    try:
        record_interaction(phone, "inbound", "whatsapp_message", {
            "message_id": message.get("id"),
            "text": text,
            "reply_id": reply_id,
            "language": language,
            "profile_exists": bool(profile.is_existing),
            "has_image": bool(message.get("image")),
            "has_document": bool(message.get("document")),
        })
    except Exception:
        logger.exception("Failed to record inbound")

    # reset language
    if normalized == "language":
        conversation_state.language = None
        set_current_field(conversation_state, None)
        conversation_state.journey = None
        conversation_state.answers = {}
        messenger.send_text(phone, pack["language_prompt"])
        send_language_buttons(phone)
        persist_state_on_profile(profile, conversation_state)
        return

    # language selection
    if reply_id == "lang_en" or normalized == "1":
        conversation_state.language = "en"
        profile.language = "en"
        user_store.save(profile)
        # messenger.send_text(phone, get_pack("en")["language_changed"])
        messenger.send_interactive_buttons(phone, get_pack("en")["main_offer_intro"], [("intent_get_loan", get_pack("en")["get_loan"]), ("intent_support", get_pack("en")["support"])])
        persist_state_on_profile(profile, conversation_state)
        return
    if reply_id == "lang_hi" or normalized == "2":
        conversation_state.language = "hi"
        profile.language = "hi"
        user_store.save(profile)
        # messenger.send_text(phone, get_pack("hi")["language_changed"])
        messenger.send_interactive_buttons(phone, get_pack("hi")["main_offer_intro"], [("intent_get_loan", get_pack("hi")["get_loan"]), ("intent_support", get_pack("hi")["support"])])
        persist_state_on_profile(profile, conversation_state)
        return

    # if language not set, prompt
    if conversation_state.language is None:
        conversation_state.language = profile.language or DEFAULT_LANGUAGE
        messenger.send_text(phone, get_pack(conversation_state.language)["welcome"])
        send_language_buttons(phone)
        persist_state_on_profile(profile, conversation_state)
        return

    # global support triggers
    if normalized in {"support", "help"} or reply_id in {"intent_support", "connect_agent", "post_support"}:
        conversation_state.journey = "support"
        set_current_field(conversation_state, None)
        # messenger.send_text(phone, pack["support_prompt_existing"] if profile.is_existing else pack["support_prompt_new"])
        messenger.send_interactive_buttons(phone, pack["support_prompt_existing"] if profile.is_existing else pack["support_prompt_new"], [("download_app", pack["download_app"]), ("send_email", pack["send_email"]), ("connect_agent", pack["connect_agent"])])
        persist_state_on_profile(profile, conversation_state)
        return

    # start onboarding
    if reply_id == "intent_get_loan" or (conversation_state.journey is None and any(k in (normalized or "") for k in INTENT_KEYWORDS["apply"])):
        conversation_state.journey = "onboarding"
        conversation_state.answers = conversation_state.answers or {}
        set_current_field(conversation_state, ONBOARDING_SEQUENCE[0])
        messenger.send_text(phone, pack["ask_name"])
        persist_state_on_profile(profile, conversation_state)
        return

    # onboarding handlers
    if conversation_state.journey == "onboarding":
        current = get_current_field(conversation_state)

        # kyc/nach/agreement buttons
        if reply_id == "kyc_complete":
            handle_kyc_complete(phone, profile, conversation_state, language)
            persist_state_on_profile(profile, conversation_state)
            return
        if reply_id == "nach_complete":
            profile.metadata["nach_completed"] = True
            user_store.save(profile)
            messenger.send_text(phone, "Auto-debit successfully setup.")
            set_current_field(conversation_state, "agreement")
            persist_state_on_profile(profile, conversation_state)
            messenger.send_text(phone, get_pack(language)["agreement_prompt"])
            send_agreement(phone, language, profile)
            return
        if reply_id == "agree_yes":
            profile.metadata["agreement_signed"] = True
            user_store.save(profile)
            messenger.send_text(phone, pack["agreement_signed"])
            conversation_state.journey = "completed"
            set_current_field(conversation_state, None)
            persist_state_on_profile(profile, conversation_state)
            return
        if reply_id == "agree_no":
            profile.metadata["agreement_signed"] = False
            user_store.save(profile)
            messenger.send_text(phone, "You did not agree to the terms. Application cannot proceed.")
            persist_state_on_profile(profile, conversation_state)
            return

        # offer handling
        if reply_id and reply_id.startswith("offer_select_"):
            offer_id = reply_id.replace("offer_select_", "")
            offers = profile.metadata.get("offers", [])
            chosen = next((o for o in offers if o.get("offer_id") == offer_id), None)
            if not chosen:
                messenger.send_text(phone, pack["invalid_choice"])
                persist_state_on_profile(profile, conversation_state)
                return
            profile.metadata["chosen_offer"] = chosen
            user_store.save(profile)
            messenger.send_text(phone, f"You selected:\n₹{chosen['amount']:,.0f}\n{chosen['tenure']} months\nAPR {chosen['apr']}%")
            send_kyc_prompt(phone, language)
            set_current_field(conversation_state, "kyc")
            persist_state_on_profile(profile, conversation_state)
            return

        if reply_id and reply_id.startswith("offer_view_"):
            offer_id = reply_id.replace("offer_view_", "")
            offers = profile.metadata.get("offers", [])
            chosen = next((o for o in offers if o.get("offer_id") == offer_id), None)
            if not chosen:
                messenger.send_text(phone, pack["invalid_choice"])
                persist_state_on_profile(profile, conversation_state)
                return
            text_offer = (
                f"Offer Details:\nAmount: ₹{chosen['amount']:,.0f}\nTenure: {chosen['tenure']} months\n"
                f"APR: {chosen['apr']:.2f}%\nProcessing fee: ₹{chosen['processing_fee']:,.2f}\nEMI: ₹{chosen['monthly_emi']:,.2f}"
            )
            messenger.send_text(phone, text_offer)
            messenger.send_interactive_buttons(phone, "Choose:", [(f"offer_select_{chosen['offer_id']}", pack["offer_button_accept"]), ("connect_agent", pack["connect_agent"])])
            persist_state_on_profile(profile, conversation_state)
            return

        # selfie detection (message may have "image")
        if current == "selfie" and message.get("image"):
            handle_selfie(phone, profile, conversation_state, language)
            persist_state_on_profile(profile, conversation_state)
            return

        # bank details
        if current == "bank_details" and text:
            parsed = parse_bank_details(text)
            if not parsed:
                messenger.send_text(phone, pack["invalid_choice"])
                messenger.send_text(phone, pack["ask_bank"])
                persist_state_on_profile(profile, conversation_state)
                return
            profile.metadata["bank_details"] = parsed
            user_store.save(profile)
            messenger.send_text(phone, pack["bank_details_received"])
            set_current_field(conversation_state, "post_bank")
            persist_state_on_profile(profile, conversation_state)
            run_final_checks_and_disburse(phone, profile, conversation_state, language)
            send_nach_prompt(phone, language)
            set_current_field(conversation_state, "nach")
            persist_state_on_profile(profile, conversation_state)
            return

        # no current field -> prompt main menu
        if current is None:
            messenger.send_interactive_buttons(phone, pack["main_offer_intro"], [("intent_get_loan", pack["get_loan"]), ("intent_support", pack["support"])])
            persist_state_on_profile(profile, conversation_state)
            return

        # employment button (emp_0..2)
        if reply_id and reply_id.startswith("emp_"):
            try:
                idx = int(reply_id.split("_")[1])
                options = get_pack(language)["employment_options"]
                selected = options[idx] if idx < len(options) else options[0]
            except Exception:
                selected = get_pack(language)["employment_options"][0]
            conversation_state.answers["employment_status"] = selected
            next_field = advance_to_next_field(conversation_state)
            if next_field:
                prompt_for_field(phone, next_field, language)
            else:
                handle_onboarding_complete(phone, profile, conversation_state, language)
            persist_state_on_profile(profile, conversation_state)
            return

        # purpose button (purpose_#)
        if reply_id and reply_id.startswith("purpose_"):
            try:
                idx = int(reply_id.split("_")[1])
                options = get_pack(language)["purpose_options"]
                selected = options[idx] if idx < len(options) else options[0]
            except Exception:
                selected = get_pack(language)["purpose_options"][0]
            conversation_state.answers["purpose"] = selected
            next_field = advance_to_next_field(conversation_state)
            if next_field:
                prompt_for_field(phone, next_field, language)
            else:
                handle_onboarding_complete(phone, profile, conversation_state, language)
            persist_state_on_profile(profile, conversation_state)
            return

        # consent buttons
        if reply_id in {"consent_yes", "consent_no"}:
            consent = (reply_id == "consent_yes")
            if not consent:
                messenger.send_text(phone, pack["consent_required"])
                persist_state_on_profile(profile, conversation_state)
                return
            conversation_state.answers["consent_to_credit_check"] = True
            next_field = advance_to_next_field(conversation_state)
            if next_field:
                prompt_for_field(phone, next_field, language)
            else:
                handle_onboarding_complete(phone, profile, conversation_state, language)
            persist_state_on_profile(profile, conversation_state)
            return

        # typed input for current field
        if text:
            try:
                handle_typed_onboarding_input(phone, text, profile, conversation_state, language)
            except Exception:
                messenger.send_text(phone, pack["invalid_choice"])
            persist_state_on_profile(profile, conversation_state)
            return

    # support journey
    if conversation_state.journey == "support" and text:
        handle_support(phone, text, conversation_state, language, profile)
        persist_state_on_profile(profile, conversation_state)
        return

    # post-loan menu (interactive)
    if message.get("interactive"):
        rid = extract_button_reply_id(message)
        if rid and rid.startswith("post_"):
            if rid == "post_view":
                rec = loan_store.get_record(phone) or profile.metadata.get("loan_record", {})
                messenger.send_text(phone, "Loan details:\n" + json.dumps(rec))
                persist_state_on_profile(profile, conversation_state)
                return
            if rid == "post_download":
                try:
                    messenger.send_document(phone, "/tmp/loan_statement.pdf", filename="Loan_Details.pdf")
                except Exception:
                    messenger.send_text(phone, "Loan PDF: [link]")
                persist_state_on_profile(profile, conversation_state)
                return
            if rid == "post_repay":
                messenger.send_text(phone, "To repay, visit the PayU App or reply PAY LINK to get a payment link.")
                persist_state_on_profile(profile, conversation_state)
                return
            if rid == "post_support":
                messenger.send_text(phone, pack["support_handoff"])
                escalate_to_agent(phone, "Post-loan support requested", profile)
                messenger.send_text(phone, pack["support_escalation_ack"])
                persist_state_on_profile(profile, conversation_state)
                return

    # fallback: show main menu
    messenger.send_interactive_buttons(phone, pack["main_offer_intro"], [("intent_get_loan", pack["get_loan"]), ("intent_support", pack["support"])])
    persist_state_on_profile(profile, conversation_state)

# ---------------------------------------------------------------------------
# Prompt helpers and typed input handler
# ---------------------------------------------------------------------------

def prompt_for_field(phone: str, field: str, lang: str) -> None:
    pack = get_pack(lang)
    if field == "full_name":
        messenger.send_text(phone, pack["ask_name"])
        return
    if field == "dob":
        messenger.send_text(phone, pack["ask_dob"])
        return
    if field == "employment_status":
        options = pack["employment_options"]
        buttons = [(f"emp_{i}", title) for i, title in enumerate(options)]
        messenger.send_interactive_buttons(phone, pack["ask_employment"], buttons[:3])
        return
    if field == "monthly_income":
        messenger.send_text(phone, pack["ask_salary"])
        return
    if field == "purpose":
        options = pack["purpose_options"]
        buttons = [(f"purpose_{i}", title) for i, title in enumerate(options)]
        send_buttons_split(phone, pack["ask_purpose"], buttons)
        return
    if field == "consent_to_credit_check":
        messenger.send_interactive_buttons(phone, pack["ask_consent"], [("consent_yes", "Yes"), ("consent_no", "No")])
        return

def handle_typed_onboarding_input(phone: str, text: str, profile: UserProfile, state: ConversationState, lang: str) -> None:
    pack = get_pack(lang)
    field = get_current_field(state)
    if not field:
        messenger.send_text(phone, pack["invalid_choice"])
        return

    if field == "full_name":
        state.answers["full_name"] = text.strip()
        advance_to_next_field(state)
        prompt_for_field(phone, get_current_field(state), lang)
        return

    if field == "dob":
        try:
            dt = datetime.strptime(text.strip(), "%d-%m-%Y")
            age = compute_age_from_dob(text.strip())
            if age < 18 or age > 75:
                messenger.send_text(phone, "Applicant must be between 18 and 75 years old.")
                return
            state.answers["dob"] = text.strip()
            state.answers["age"] = age
            advance_to_next_field(state)
            prompt_for_field(phone, get_current_field(state), lang)
            return
        except Exception:
            messenger.send_text(phone, pack["invalid_dob"])
            return

    if field == "employment_status":
        state.answers["employment_status"] = text.strip().title()
        advance_to_next_field(state)
        prompt_for_field(phone, get_current_field(state), lang)
        return

    if field == "monthly_income":
        try:
            amt = parse_numeric(text.strip(), float)
            state.answers["monthly_income"] = round(float(amt), 2)
            advance_to_next_field(state)
            prompt_for_field(phone, get_current_field(state), lang)
            return
        except Exception:
            messenger.send_text(phone, pack["invalid_number"])
            return

    if field == "purpose":
        state.answers["purpose"] = text.strip()
        advance_to_next_field(state)
        prompt_for_field(phone, get_current_field(state), lang)
        return

    if field == "consent_to_credit_check":
        consent = normalize_boolean(text)
        if consent is None:
            messenger.send_text(phone, pack["consent_required"])
            return
        if not consent:
            messenger.send_text(phone, pack["consent_required"])
            return
        state.answers["consent_to_credit_check"] = True
        handle_onboarding_complete(phone, profile, state, lang)
        return

# ---------------------------------------------------------------------------
# Onboarding complete => generate offers
# ---------------------------------------------------------------------------

def handle_onboarding_complete(phone: str, profile: UserProfile, state: ConversationState, lang: str) -> None:
    pack = get_pack(lang)
    answers = state.answers or {}
    try:
        application = LoanApplication(
            customer_phone=phone,
            full_name=answers["full_name"],
            age=answers.get("age", 30),
            employment_status=answers.get("employment_status", "Other"),
            monthly_income=answers.get("monthly_income", 20000.0),
            requested_amount=min(500000.0, answers.get("requested_amount", answers.get("monthly_income", 50000.0) * 2)),
            purpose=answers.get("purpose", "Personal"),
            consent_to_credit_check=bool(answers.get("consent_to_credit_check", True)),
        )
    except Exception:
        messenger.send_text(phone, "There was a problem with your details. Please restart by typing 'Get Loan'.")
        return

    messenger.send_text(phone, pack["decision_submit"])
    decision, offers = generate_offers(application)
    profile.metadata["offers"] = offers
    profile.metadata["last_application_id"] = decision.reference_id
    profile.state = "AWAITING_OFFER_SELECTION"
    user_store.save(profile)
    present_offers(phone, lang, decision, offers, profile)

# ---------------------------------------------------------------------------
# Final checks & disbursement (dummy)
# ---------------------------------------------------------------------------

def run_final_checks_and_disburse(phone: str, profile: UserProfile, state: ConversationState, lang: str) -> None:
    pack = get_pack(lang)
    answers = state.answers or {}
    chosen = profile.metadata.get("chosen_offer")
    try:
        requested_amount = chosen["amount"] if chosen else answers.get("requested_amount", answers.get("monthly_income", 50000.0) * 2)
    except Exception:
        requested_amount = answers.get("requested_amount", 50000.0)
    application = LoanApplication(
        customer_phone=phone,
        full_name=answers.get("full_name", profile.metadata.get("full_name", "Applicant")),
        age=answers.get("age", 30),
        employment_status=answers.get("employment_status", "Other"),
        monthly_income=answers.get("monthly_income", 20000.0),
        requested_amount=requested_amount,
        purpose=answers.get("purpose", "Personal"),
        consent_to_credit_check=bool(answers.get("consent_to_credit_check", True)),
    )
    decision = decision_client._local_rules(application) if not decision_client.base_url else decision_client.evaluate(application)
    if not decision.approved:
        messenger.send_text(phone, pack["final_reject"].format(reason=decision.reason or "internal policy"))
        profile.metadata["disbursement_status"] = "rejected"
        user_store.save(profile)
        record_interaction(phone, "system", "final_reject", {"reason": decision.reason})
        return

    if chosen:
        if chosen["amount"] > decision.offer_amount:
            messenger.send_text(phone, "Selected amount exceeds eligible amount. Please select a different offer.")
            return
        disbursed_amount = chosen["amount"]
        ref = decision.reference_id
    else:
        disbursed_amount = decision.offer_amount
        ref = decision.reference_id

    profile.metadata["disbursement_status"] = "disbursed"
    profile.metadata["disbursed_amount"] = disbursed_amount
    profile.metadata["disbursement_reference"] = ref
    user_store.save(profile)
    try:
        loan_store.upsert_from_decision(profile.phone, decision, application)
    except Exception:
        logger.exception("Failed to save loan record")
    messenger.send_text(phone, pack["final_approval"].format(amount=disbursed_amount, ref=ref))
    record_interaction(phone, "outbound", "disbursed", {"amount": disbursed_amount, "reference": ref})
    

# ---------------------------------------------------------------------------
# Post-loan menu & escalation
# ---------------------------------------------------------------------------

def send_post_loan_menu(phone: str, lang: str, profile: UserProfile) -> None:
    pack = get_pack(lang)
    buttons = [
        ("post_view", pack["post_loan_view_details"]),
        ("post_download", pack["post_loan_download_pdf"]),
        ("post_repay", pack["post_loan_repay"]),
    ]
    messenger.send_interactive_buttons(phone, pack["post_loan_menu_intro"], buttons[:3])
    messenger.send_interactive_buttons(phone, "Need help?", [("post_support", pack["support"])])
    record_interaction(phone, "outbound", "post_loan_menu", {})

def escalate_to_agent(phone: str, question: str, profile: UserProfile) -> None:
    profile.metadata["last_escalation"] = {"question": question, "timestamp": iso_timestamp(), "queue": HUMAN_HANDOFF_QUEUE}
    user_store.save(profile)
    record_interaction(phone, "system", "agent_handoff", {"question": question, "queue": HUMAN_HANDOFF_QUEUE})

# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------

def extract_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = value.get("contacts", [])
            for message in value.get("messages", []):
                payload = {"from": message.get("from"), "id": message.get("id"), **message}
                if contacts:
                    payload["profile"] = contacts[0].get("profile", {})
                messages.append(payload)
    return messages

@app.get("/webhook")
def verify_webhook(hub_mode: Optional[str] = Query(default=None, alias="hub.mode"), hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"), hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge")):
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid mode")
    if hub_verify_token != META_VERIFY_TOKEN:
        raise HTTPException(status_code=403, detail="Verification token mismatch")
    return PlainTextResponse(hub_challenge or "")

@app.post("/webhook")
def receive_webhook(payload: Dict[str, Any]):
    messages = extract_messages(payload)
    if not messages:
        return JSONResponse({"status": "ignored"})
    for msg in messages:
        handle_incoming_message(msg)
    return JSONResponse({"status": "processed"})

@app.get("/healthz")
def healthcheck():
    return {"status": "ok", "messenger_enabled": messenger.enabled, "decision_backend": bool(BACKEND_DECISION_URL), "dynamo_enabled": user_store.uses_dynamo}

# ---------------------------------------------------------------------------
# Local runner
# ---------------------------------------------------------------------------

def run():
    import uvicorn
    uvicorn.run("chatbot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=bool(int(os.environ.get("RELOAD", "0"))))

def lambda_handler(event, context):
    if not _lambda_adapter:
        raise RuntimeError("Mangum is not installed. Cannot handle Lambda events.")
    return _lambda_adapter(event, context)

if __name__ == "__main__":
    run()
