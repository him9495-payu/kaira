# PayU Flowless WhatsApp Chatbot

This document captures the current feature set, architecture, and known gaps/remaining work for the Flowless PayU Finance WhatsApp chatbot contained in this repository (`chatbot.py`, `db_io.py`, `whatsapp_messaging.py`).

---

## 1. System Overview
- **Purpose**: Automate PayU Finance’s bilingual (English/Hindi) personal-loan journey on WhatsApp, from lead capture to disbursement and post-loan support.
- **Tech stack**: FastAPI application (`chatbot.py`) that exposes Meta WhatsApp webhook endpoints, talks to AWS Bedrock (optional) for support answers, and uses AWS DynamoDB (via `db_io.py`) for persistence. WhatsApp delivery is handled by `MetaWhatsAppClient` in `whatsapp_messaging.py`.
- **Deployment targets**: Runs as a regular FastAPI/uvicorn service (`python chatbot.py`) or as an AWS Lambda via `mangum`’s adapter (`lambda_handler`).

---

## 2. Module Breakdown

### `chatbot.py`
- Entry point defining the FastAPI app, webhook routes, and overall conversation orchestrator.
- Holds configuration (env-driven), language packs, onboarding sequence, offer generation logic, support handling, document/selfie/NACH helpers, and the routing logic in `handle_incoming_message`.
- Integrates with:
  - `MetaWhatsAppClient` for outbound messaging.
  - `UserProfileStore`, `LoanRecordStore`, and `InteractionStore` for persistence.
  - `BedrockSupportResponder` (optional) for LLM-powered support replies.

### `db_io.py`
- Contains DynamoDB-facing dataclasses (`UserProfile`, `ConversationState`) plus lightweight repository classes for users, loans, and interactions.
- Provides serialization helpers (`serialize_conversation_state`, `normalize_decimals`, `_sanitize_for_dynamo`) and timestamp utilities.
- Assumes `boto3` is available; there is no local in-memory fallback.

### `whatsapp_messaging.py`
- Thin Meta WhatsApp Cloud API client supporting text, buttons, lists, documents, images, templates, URL buttons, location requests, and selfie prompts.
- Automatically degrades to console logging (“dry-run”) when credentials are absent, aiding local development.

---

## 3. Conversation Journey (Happy Path)
1. **Language selection**: User receives a welcome message and interactive buttons to choose English or Hindi. The choice is persisted on the profile.
2. **Intent selection**: Buttons for “Get Loan” vs “Support”. Support jumps straight into the support journey; “Get Loan” kicks off onboarding.
3. **Onboarding fields** (order defined in `ONBOARDING_SEQUENCE`):
   - Full name → Date of birth → Employment status → Monthly income → Loan purpose → Consent to credit check.
   - Each field has validation helpers (`parse_numeric`, `compute_age_from_dob`, `normalize_boolean`) and both typed-input + button pathways.
4. **Offer generation**: `generate_offers` crafts dummy offers and `present_offers` renders a single rich message with interactive offer-selection buttons.
5. **KYC & document steps**: After offer acceptance the bot issues prompts for KYC completion, selfie upload, bank details, NACH, and agreement signature.
6. **Decision & disbursement**: `run_final_checks_and_disburse` re-validates eligibility using the fallback `DecisionClient`, persists loan records, and sends approval/rejection.
7. **Post-loan support**: `send_post_loan_menu` exposes buttons for viewing/download loan info, repayment instructions, or agent escalation.
8. **Support journey** (available anytime): knowledge-base answers, Bedrock-generated replies, and manual escalation via `escalate_to_agent`.

---

## 4. Feature Highlights
- **Bilingual UX**: Centralized language packs for English/Hindi covering prompts, buttons, and journey texts. Language preference stored per user.
- **Interactive-first design**: Extensive use of WhatsApp interactive buttons/lists (`send_interactive_buttons`, `send_buttons_split`) to keep responses structured.
- **Stateful onboarding**: `ConversationState` objects persisted inside `UserProfile.metadata["conversation_state"]`, allowing users to pause/resume mid-journey.
- **Dummy credit decisioning**: Self-contained `generate_offers` and `DecisionClient` to simulate approvals when the real backend is unavailable.
- **KYC/document placeholders**: Hooks for KYC completion, selfie ingestion, bank detail parsing, NACH initiation, and agreement distribution (with PDF attachment fallbacks).
- **Support automation**: Rule-based KB answers plus optional AWS Bedrock responses, with telemetry recorded via `InteractionStore`.
- **Audit & analytics**: Every inbound/outbound message logs through `record_interaction`; loan decisions are mirrored to Dynamo via `LoanRecordStore`.
- **Deployment flexibility**: Same codebase can run locally with uvicorn or as an AWS Lambda using Mangum.

---

## 5. Configuration & Integrations
- **Environment variables** (non-exhaustive): `META_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `META_VERIFY_TOKEN`, `BACKEND_DECISION_URL`, `BACKEND_DECISION_API_KEY`, `USER_TABLE_NAME`, `INTERACTION_TABLE_NAME`, `LOAN_TABLE_NAME`, `AWS_REGION`, `INACTIVITY_MINUTES`, `BEDROCK_MODEL_ID`, `HUMAN_HANDOFF_QUEUE`.
- **External services**:
  - Meta WhatsApp Cloud API (Graph API).
  - AWS DynamoDB (three tables).
  - AWS Bedrock (anthropic.claude-3-haiku by default) for support responses.
  - Optional backend decisioning service (placeholder `DecisionClient` implemented locally).

---

## 6. Data & Persistence Model
- **UserProfile**: Phone-centric record storing language, lifecycle status, timestamps, and an open-ended metadata dict (offers, chosen offer, bank info, etc.).
- **ConversationState**: Lightweight object (language, journey, answers, flags) serialized into the user profile for resuming flows.
- **LoanRecordStore**: Persists the latest decision snapshot (amount, APR, terms, EMI schedule placeholder). Includes `upsert_from_decision` for idempotent writes.
- **InteractionStore**: Append-only log of inbound/outbound/support/system events with ISO timestamps for auditing.

---

## 7. Operational Notes
- **Logging**: Uses Python’s `logging` module; log level controlled by `LOG_LEVEL`.
- **Error handling**: Most Dynamo/Bedrock interactions are wrapped in try/except with logging; message handling attempts to continue even when DB writes fail.
- **Health endpoint**: `/healthz` reports service readiness plus toggles for dependent systems (messenger enabled, backend decision configured, Dynamo availability).
- **Local dev**: Without Meta tokens the messenger logs payloads (`[dry-run]`). Without DynamoDB/boto3 the process will fail at import-time; mocks are required for offline testing.

---

## 8. Remaining Work / Known Gaps
1. **Hard dependency on boto3/DynamoDB**: `UserProfileStore`, `LoanRecordStore`, and `InteractionStore` are instantiated at import time, causing the entire app to crash locally when Dynamo credentials are absent. Provide an in-memory or file-based fallback for development/unit tests.
2. **Missing persistence for Bedrock toggle**: When `boto3` is unavailable the `BedrockSupportResponder` silently disables support automation without notifying operators. Consider adding health/metrics or feature flags to surface this state.
3. **`send_location_request` bug**: `MetaWhatsAppClient.send_location_request` treats `_post` as returning a `requests.Response`, but `_post` currently returns `None`, so attribute access (`r.status_code`) will raise. Update `_post` to return the response or adjust `send_location_request` to avoid dereferencing.
4. **Document/selfie handling is placeholder**: Selfies and agreements are acknowledged but never stored or verified. Bank details are accepted via plain text without validation/PII safeguards. Future work should integrate secure storage, document OCR, and IFSC validation.
5. **Decision backend integration**: `DecisionClient` always falls back to local dummy logic; `BACKEND_DECISION_URL` and API key are parsed but never used. Implement the actual HTTP call and error-retry strategy.
6. **Offer overflow handling**: `present_offers` sends a single button message even if more than three offers exist, but WhatsApp only allows three buttons; additional offers beyond the first three are silently dropped. Add pagination/carousel or split messages.
7. **Inactivity handling**: `INACTIVITY_MINUTES` is defined but never enforced—stale conversations are never reset. Implement periodic cleanup or auto-reset when users return after long pauses.
8. **Testing & CI**: No automated tests or linting workflows exist. Adding unit tests for conversation routing, Dynamo repositories (with moto/localstack), and WhatsApp payload generation would de-risk future changes.
9. **Security & compliance gaps**: Sensitive data (bank details, KYC state) is stored unencrypted in Dynamo metadata, and there is no explicit masking/auditing. Introduce encryption at rest, access controls, and data retention policies.
10. **Agent handoff plumbing**: `escalate_to_agent` only logs metadata; it does not actually enqueue to a support system (`HUMAN_HANDOFF_QUEUE` is unused outside metadata). Integrate with the real queue/CRM and confirm delivery.

---

## 9. Suggested Next Steps
- Prioritize fixing the WhatsApp client bug and providing a test-friendly persistence layer, enabling local development without AWS dependencies.
- Implement real decision-backend calls and strengthen the post-KYC workflow (selfie/bank verification, document uploads).
- Add automated tests plus CI to guard the critical conversation router and data-access layers.

