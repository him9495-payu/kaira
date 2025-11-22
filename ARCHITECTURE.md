# Architecture – PayU Flowless WhatsApp Chatbot

This document describes how the Flowless WhatsApp chatbot is assembled: the runtime components, how data moves between them, and the engineering work required to reach the long-term vision.

---

## 1. System Snapshot
- **Channel**: Meta WhatsApp Cloud API sends customer messages to our FastAPI webhook; responses go back through the same Graph endpoint.
- **Application core**: `chatbot.py` hosts FastAPI routes, the conversation engine, offer/KYC logic, and decision/disbursement helpers.
- **Persistence**: DynamoDB tables (implemented in `db_io.py`) hold user profiles, loan records, and interaction logs.
- **Intelligence**: Optional AWS Bedrock responder plus a deterministic support knowledge base provide automated answers before any human handoff.

```
Customer → WhatsApp Cloud → FastAPI Webhook
                                 ↘ Conversation Engine
                                    ↘ DynamoDB (profiles / loans / interactions)
                                    ↘ Decision client (local or remote)
                                    ↘ Bedrock responder (optional)
```

---

## 2. Component Responsibilities

| Layer | Description |
| --- | --- |
| **FastAPI app (`chatbot.py`)** | Exposes `/webhook`, `/healthz`, and `lambda_handler`. `handle_incoming_message` orchestrates every turn: it loads the profile, interprets the intent, and delegates to onboarding, support, or post-loan flows. |
| **Conversation helpers** | `prompt_for_field`, `handle_typed_onboarding_input`, KYC/selfie/bank/NACH/agreement functions, and post-loan menu utilities keep the flow modular. |
| **Meta WhatsApp client (`MetaWhatsAppClient`)** | Wraps Graph API calls for text, interactive buttons/lists, templates, documents, images, URL buttons, and location/selfie prompts. Logs payloads in “dry-run” mode when credentials are missing. |
| **Persistence (`db_io.py`)** | `UserProfileStore`, `LoanRecordStore`, and `InteractionStore` wrap DynamoDB tables; dataclasses (`UserProfile`, `ConversationState`) serialize state safely. |
| **Decision client** | `DecisionClient` currently mirrors the local `generate_offers` output but exposes hooks for a remote credit decision service via `BACKEND_DECISION_URL`. |
| **Support automation** | `BedrockSupportResponder` plus the curated `SUPPORT_KB` answer L1 questions; escalation metadata is captured for human queues. |

---

## 3. Data & Control Flows

### Incoming event processing
1. Meta invokes `/webhook` with a batch of messages.
2. `extract_messages` flattens the payload; each message flows into `handle_incoming_message`.
3. The handler loads `UserProfile` + `ConversationState`, records the inbound event, and evaluates:
   - Whether language selection is required.
   - Which journey is active (onboarding, support, post-loan).
   - Which field or action is pending (e.g., KYC, NACH, agreement).
4. The next response is dispatched via `MetaWhatsAppClient`, and the updated `ConversationState` is serialized back into Dynamo.

### Onboarding lifecycle
```
prompt_for_field → WhatsApp reply → validation → advance_to_next_field
                                      ↘ answers stored in state
```
- When the required answers exist, `handle_onboarding_complete` builds a `LoanApplication`, triggers `generate_offers`, and sends interactive offer buttons.
- Selecting an offer persists it on the profile, then kicks off KYC/selfie/bank/NACH/agreement helpers before `run_final_checks_and_disburse` performs the (dummy) final decision and loan persistence.

### Support & post-loan flows
```
question → Bedrock (if enabled) → KB fallback → escalation buttons
             ↘ InteractionStore keeps source + payload metadata
```
- Post-loan menus reuse the same WhatsApp client to expose repayment links, statement downloads, and L1 support/collections actions.

---

## 4. Integrations & Configuration
- **WhatsApp**: `META_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `META_VERIFY_TOKEN`.
- **Persistence**: `USER_TABLE_NAME`, `INTERACTION_TABLE_NAME`, `LOAN_TABLE_NAME`, `AWS_REGION`.
- **Decision backend**: `BACKEND_DECISION_URL`, `BACKEND_DECISION_API_KEY` (currently unused; local decisioning runs instead).
- **Support intelligence**: `BEDROCK_MODEL_ID`, `HUMAN_HANDOFF_QUEUE`.
- **Operational toggles**: `LOG_LEVEL`, `INACTIVITY_MINUTES`.

Failures in WhatsApp config move the client into dry-run logging; failures in boto3/Dynamo access crash startup because the stores are instantiated eagerly.

---

## 5. Deployment Patterns
1. **Container / VM** – run `uvicorn chatbot:app --host 0.0.0.0 --port 8000`; ensure outbound internet to Meta Graph API, DynamoDB, and Bedrock.
2. **AWS Lambda + API Gateway** – `lambda_handler` (Mangum) allows serverless hosting; API Gateway terminates HTTPS, IAM roles grant Dynamo/Bedrock access, and secrets live in environment variables or Secrets Manager.

Both approaches reuse the same Python project; only the hosting substrate differs.

---

## 6. Observability & Operations
- **Logging**: Python loggers per module; `LOG_LEVEL` controls verboseness.
- **Health**: `/healthz` reports messenger enablement, decision-backend binding, and Dynamo availability for readiness probes.
- **Audit trail**: `InteractionStore` records every inbound/outbound/support/system event with timestamps and payload snippets.
- **Gaps**: no outbound retry/backoff, no structured metrics/traces, and `INACTIVITY_MINUTES` is not enforced, so stale sessions persist forever.

---

## 7. Architectural Risks & Backlog
1. **Dynamo dependency** – Provide an in-memory or pluggable store so local dev/tests do not require AWS credentials.
2. **Decision backend** – Implement the real HTTP integration with retries, auth, and schema validation instead of the local fallback.
3. **WhatsApp client bug** – `send_location_request` assumes `_post` returns a `requests.Response`; it currently returns `None`, causing attribute errors.
4. **Data protection** – Bank data, selfies, and agreements are not encrypted or masked; add secure storage and PII handling.
5. **Offer pagination** – WhatsApp only renders three buttons per message; additional offers are silently dropped.
6. **Collections & handoff plumbing** – `escalate_to_agent` writes metadata but does not reach an actual queue/CRM.
7. **Testing & CI** – No automated tests cover the router, stores, or messaging helpers; add regression protection and linting.

Closing these items will align the architecture with the product vision outlined in `DOCUMENTATION.md` and unlock the remaining capabilities for PayU Finance.
