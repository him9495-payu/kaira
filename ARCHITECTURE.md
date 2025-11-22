# Architecture – PayU Flowless WhatsApp Chatbot

This document outlines the architectural structure, runtime interactions, and deployment considerations for the Flowless PayU Finance WhatsApp chatbot.

---

## 1. High-Level View
- **Channel**: Meta WhatsApp Cloud API delivers user messages to our FastAPI webhook, and the bot responds via the same API using interactive templates.
- **Application**: `chatbot.py` embeds FastAPI routes, conversation orchestration, and the integrations to persistence, decisioning, and support services.
- **Data Stores**: DynamoDB tables hold user profiles, loan records, and interaction logs (see `db_io.py`).
- **Intelligence**: Optional AWS Bedrock responder (Anthropic Claude 3 Haiku) handles free-form support queries when available.

```
User ⇄ WhatsApp ⇄ FastAPI Webhook ⇄ Conversation Engine
                                  ↘ DynamoDB (profiles/loans/interactions)
                                   ↘ Bedrock (support)
                                   ↘ Decision service (optional)
```

---

## 2. Components

### FastAPI Application (`chatbot.py`)
- Routes: `/webhook` (POST for events, GET for verification) and `/healthz`.
- Core handler `handle_incoming_message` processes WhatsApp payloads, updates `ConversationState`, and dispatches to onboarding/support flows.
- Scheduler-like helpers handle KYC, selfie, bank collection, NACH, agreement, and post-loan menus.

### WhatsApp Integration (`whatsapp_messaging.MetaWhatsAppClient`)
- Abstracts REST calls to `https://graph.facebook.com/{version}/{phone_number_id}/messages`.
- Supports text, interactive buttons, lists, documents, images, templates, and location/selfie prompts.
- Dry-run mode logs payloads when credentials are absent—useful for local dev.

### Persistence (`db_io.py`)
- `UserProfileStore`: stores user metadata, including serialized conversation state, in DynamoDB.
- `LoanRecordStore`: keeps synthetic decision snapshots for downstream systems.
- `InteractionStore`: audit log for all inbound/outbound/system events.
- Helper dataclasses (`UserProfile`, `ConversationState`) define schema; serialization helpers ensure Dynamo-compliant types.

### Decisioning
- `DecisionClient`: placeholder facade that currently routes to the local `generate_offers` dummy model.
- Hooks are present for integrating a real backend via `BACKEND_DECISION_URL` and API key.

### Support Automation
- `BedrockSupportResponder`: wraps AWS Bedrock Runtime (Anthropic model) for bilingual answers; gracefully disabled when boto3 is missing.
- `SUPPORT_KB`: deterministic FAQ fallback before escalating to a human queue hint (`HUMAN_HANDOFF_QUEUE` metadata).

---

## 3. Data Flow & Sequence

### Inbound Message Pipeline
1. **Meta Webhook** posts a JSON event to `/webhook`.
2. `extract_messages` flattens entries into individual WhatsApp message objects.
3. `handle_incoming_message`:
   - Loads/creates `UserProfile`, deserializes `ConversationState`.
   - Records inbound event (`InteractionStore`).
   - Routes:
     - Language selection → `send_language_buttons`.
     - Support intent → `handle_support`.
     - Loan intent → onboarding (guided by `ONBOARDING_SEQUENCE`).
   - Persists updated state back to profile metadata.

### Loan Onboarding Sequence
```
prompt_for_field → receive reply → validate → advance_to_next_field
               ↘ state.answers update ↘ (once complete) handle_onboarding_complete
```
- After onboarding, dummy offers are generated, rendered via WhatsApp buttons, and persisted as `profile.metadata["offers"]`.
- Accepting an offer triggers KYC/selfie/bank/NACH flows sequentially, culminating in `run_final_checks_and_disburse`, which writes loan/disbursement info.

### Support Flow
```
User question → handle_support → (Bedrock answer | KB match | escalation prompt)
                                       ↘ InteractionStore logs for analytics
```

---

## 4. Deployment Topologies

### Container / VM
- Run `python chatbot.py` or `uvicorn chatbot:app`.
- Requires network access to Meta Graph API, DynamoDB, optional Bedrock endpoint.
- Environment variables supply credentials and table names.

### AWS Lambda + API Gateway
- Use `lambda_handler` via Mangum adapter.
- API Gateway handles HTTPS endpoints; Lambda runs the same FastAPI app.
- Ensure IAM roles grant DynamoDB + Bedrock access and secrets (WhatsApp credentials) are available (e.g., via AWS Secrets Manager).

---

## 5. Configuration Inputs
- **WhatsApp**: `META_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `META_VERIFY_TOKEN`.
- **Persistence**: `USER_TABLE_NAME`, `INTERACTION_TABLE_NAME`, `LOAN_TABLE_NAME`, `AWS_REGION`.
- **Decisioning**: `BACKEND_DECISION_URL`, `BACKEND_DECISION_API_KEY`.
- **Support**: `BEDROCK_MODEL_ID`, `HUMAN_HANDOFF_QUEUE`.
- **Operational**: `LOG_LEVEL`, `INACTIVITY_MINUTES`.

Missing or invalid config either disables functionality (messaging dry-run, Bedrock off) or raises during startup (Dynamo stores require boto3).

---

## 6. Observability & Telemetry
- Structured logging via Python’s `logging` (module names: `payu.flowless.chatbot`, `db_io`, `whatsapp_messaging`).
- Interaction logs (Dynamo) capture full conversational breadcrumbs.
- `/healthz` reports readiness plus toggles for messenger availability, decision backend binding, and Dynamo support.
- No metrics/tracing exporters yet—candidates include CloudWatch metrics, OpenTelemetry instrumentation, and structured audit dashboards.

---

## 7. Architectural Gaps (recap)
- DynamoDB hard dependency blocks offline testing—introduce an in-memory adapter or mocking layer.
- Decision API integration remains a stub—needs HTTP client, retries, error surfacing, and schema validation.
- WhatsApp client’s `_post` return value is ignored, causing failures inside `send_location_request`.
- Security/compliance controls (PII encryption, least-privilege IAM, masking) are not enforced.
- No rate limiting, throttling, or spam protection for inbound messages.

Addressing these gaps should be prioritized alongside the enhancements listed in `DOCUMENTATION.md`.

