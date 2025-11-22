# PayU Flowless WhatsApp Chatbot – Product Documentation

The Flowless chatbot delivers PayU Finance experiences over WhatsApp—from new-loan onboarding to post-loan care and collections. This document explains the current capabilities, how they are implemented, and what remains on the roadmap.

---

## 1. Purpose & Value
- **For customers**: Provide a familiar, always-on channel for onboarding, loan tracking, document access, and repayment actions without installing another app.
- **For PayU Finance**: Drive engagement, collections, and cross-sell through automated yet auditable conversations while keeping operational costs low.
- **Channels & languages**: WhatsApp with English/Hindi parity; experiences are designed to be button-first but fall back to typed input gracefully.

---

## 2. Product Capabilities
1. **Intent & language orchestration**
   - Welcomes users, captures language preference, and routes them to onboarding or support journeys.
   - Language packs live in `chatbot.py` and drive every prompt and button label.
2. **End-to-end onboarding**
   - Guided sequence: name → DOB → employment → income → purpose → consent.
   - Validations: numeric parsing, DOB-to-age computation, boolean synonyms.
   - Dummy decisioning generates multiple offers, captured in profile metadata.
3. **Offer acceptance & KYC trail**
   - Interactive buttons let customers select an offer, then progress through KYC, selfie upload, bank detail capture, NACH, and agreement signature prompts.
4. **Decision & disbursement**
   - `run_final_checks_and_disburse` re-evaluates the application, persists loan data, and pushes the final approval/rejection back to WhatsApp.
5. **Support & collections**
   - Knowledge-base answers, Bedrock-powered agent (when enabled), and escalation helpers.
   - Post-loan menu exposes repayment links, statement downloads, and human handoff.
6. **Notifications & document delivery**
   - WhatsApp interactive messages convey offers, agreements, and reminders with audit logs recorded via `InteractionStore`.

---

## 3. Code Structure
| File | Responsibility |
| --- | --- |
| `chatbot.py` | FastAPI app, webhook router, conversation engine, onboarding/support logic, KYC/disbursement helpers, post-loan flows. |
| `db_io.py` | Dataclasses (`UserProfile`, `ConversationState`) and DynamoDB-backed stores for profiles, loans, and interaction logs. |
| `whatsapp_messaging.py` | Meta WhatsApp client with helpers for text, interactive buttons/lists, documents, images, templates, URL buttons, and location/selfie prompts. |

The application can run via `uvicorn chatbot:app` or as an AWS Lambda function (`lambda_handler`) using Mangum.

---

## 4. Conversation Blueprint
1. **Entry**: Language prompt → interactive offer/support menu.
2. **Onboarding branch**:
   - Each field uses `prompt_for_field` and `handle_typed_onboarding_input` to collect validated data.
   - Answers persist into `conversation_state.answers`; offers + selections saved inside `UserProfile.metadata`.
3. **Support branch**:
   - `handle_support` evaluates quick intents (download app/email), tries Bedrock, falls back to KB text, and finally offers escalation.
4. **Post-offer workflow**:
   - Chosen offer triggers KYC/selfie/bank prompts, then `run_final_checks_and_disburse`.
   - Successful disbursement feeds `LoanRecordStore` and triggers post-loan menus.

State is stored across sessions by serializing `ConversationState` into Dynamo, so users can return hours later without restarting.

---

## 5. Integrations & Config
- **Meta WhatsApp Cloud API**: requires `META_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `META_VERIFY_TOKEN`.
- **AWS DynamoDB**: tables for users (`USER_TABLE_NAME`), interactions (`INTERACTION_TABLE_NAME`), and loans (`LOAN_TABLE_NAME`) within `AWS_REGION`.
- **Decision backend**: optional URL/API key pair (`BACKEND_DECISION_URL`, `BACKEND_DECISION_API_KEY`)—currently unused but wired for future HTTP calls.
- **Bedrock support**: toggled via `BEDROCK_MODEL_ID`; silently disabled when boto3 or credentials are missing.
- **Operational toggles**: `LOG_LEVEL`, `INACTIVITY_MINUTES`, `HUMAN_HANDOFF_QUEUE`.

When WhatsApp credentials are absent, the messenger logs payloads (`[dry-run]`). When DynamoDB is unavailable, the app fails fast because the stores instantiate boto3 resources at import time.

---

## 6. Operational Considerations
- **Logging**: Python logging with module-level loggers (`payu.flowless.chatbot`, `db_io`, `whatsapp_messaging`).
- **Health checks**: `/healthz` reports messenger connectivity, decision-backend presence, and Dynamo availability.
- **Observability**: Every inbound/outbound/support event is stored in `InteractionStore`, creating an auditable trail for regulators and collections officers.
- **Error handling**: Conversation flow tries to continue even when interactions or loan writes fail, but the errors are logged for manual follow-up.

---

## 7. Current Limitations
1. **Hard AWS dependency** – No local mock for DynamoDB; boto3 must be configured even for unit tests.
2. **Decision client stub** – Always uses local dummy scores; remote decision engine integration remains TODO.
3. **WhatsApp location bug** – `send_location_request` assumes `_post` returns a response object; today `_post` returns `None`, leading to attribute errors.
4. **Document/KYC placeholders** – Selfies and agreements are acknowledged but never stored; bank details lack masking and IFSC validation.
5. **Offer overflow** – WhatsApp only supports three buttons per message; additional offers are silently dropped.
6. **Inactivity rules** – `INACTIVITY_MINUTES` is unused; stale sessions never reset automatically.
7. **Security & compliance** – Sensitive metadata is stored unencrypted; no masking, retention policies, or access controls are defined.
8. **Testing & CI** – No automated tests or pipelines cover the router, decision helpers, or WhatsApp payload generation.
9. **Agent handoff** – `escalate_to_agent` records metadata but does not push to a real queue or CRM.

---

## 8. Vision & Future Capabilities
### Customer experience
- Seamless onboarding for every PayU Finance product, even when users drop off on partner funnels; WhatsApp re-engages them later to improve conversion.
- Lone hub for proactive updates: personalized offers, overdue EMI nudges, payoff quotes, and digital document delivery.
- Quick access to repayment actions, loan histories, and L1 support with LLM copilots, keeping human agents for escalations only.

### PayU Finance outcomes
- Higher engagement and collections without building another app—WhatsApp already sits on every phone and boasts the highest open rates.
- Automated yet auditable operations: every interaction is logged, making compliance reviews and dispute resolutions straightforward.
- Gateway for cross-sells before, during, and after loan maturity, with smart prompts tuned by behavioral data.

### Platform roadmap
1. **True end-to-end journeys** for all PayU Finance offerings (not just personal loans).
2. **Lifecycle orchestration** that reactivates inactive users, handles repayment workflows, and shares statements on demand.
3. **Intelligent collections & support** using LLMs for contextual replies and scripted nudges, while keeping human agents in the loop.
4. **Enterprise integration** with CRMs, ticketing systems, and analytics pipelines to turn WhatsApp into the definitive gateway between PayU Finance and its customers.

---

## 9. Execution Priorities
1. Ship a local-friendly persistence layer or mock to unblock testing.
2. Fix the WhatsApp client bug and add retries/telemetry to outbound requests.
3. Implement the real decision-backend call path and harden KYC/document handling.
4. Introduce automated tests plus CI to protect the router, stores, and messaging helpers.

Delivering these will move the product closer to the roadmap while keeping today’s conversations reliable.

