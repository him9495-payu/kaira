# whatsapp_messaging.py
"""
Meta WhatsApp Cloud API helper class with additional interactive helpers.

Provides:
- send_text
- send_interactive_buttons
- send_interactive_list
- send_document
- send_image
- request_selfie
- send_url_button

This class uses the Graph API endpoint:
https://graph.facebook.com/{api_version}/{phone_number_id}/messages
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("whatsapp_messaging")

class MetaWhatsAppClient:
    def __init__(self, token: Optional[str], phone_number_id: Optional[str], api_version: str = "v24.0"):
        self.token = token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages" if phone_number_id else None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.base_url)

    def _post(self, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            logger.info("[dry-run] %s", json.dumps(payload, indent=2, ensure_ascii=False))
            return
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        response = requests.post(self.base_url, json=payload, headers=headers, timeout=10)
        if not response.ok:
            logger.error("WhatsApp send failed - status=%s body=%s", response.status_code, response.text)
            response.raise_for_status()

    def send_text(self, to: str, body: str) -> None:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
        self._post(payload)

    def send_interactive_buttons(self, to: str, body: str, buttons: List[Tuple[str, str]]) -> None:
        # buttons: list of (id, title). Max 3.
        action_buttons = [{"type": "reply", "reply": {"id": bid, "title": title[:20]}} for bid, title in buttons[:3]]
        payload = {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {"type": "button", "body": {"text": body}, "action": {"buttons": action_buttons}}}
        self._post(payload)

    def send_interactive_list(self, to: str, body: str, button_text: str, sections: List[Dict[str, Any]]) -> None:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {"type": "list", "body": {"text": body}, "action": {"button": button_text[:20], "sections": sections}}}
        self._post(payload)

    def send_document(self, to: str, url_or_path: str, filename: str = "document.pdf", caption: Optional[str] = None) -> None:
        # Prefer sending link if looks like a URL, else upload expects file-hosting and Graph API file upload.
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            payload = {"messaging_product": "whatsapp", "to": to, "type": "document", "document": {"link": url_or_path, "filename": filename}}
            if caption:
                payload["document"]["caption"] = caption
            self._post(payload)
            return
        # If local path provided, in many deployments we pre-upload to an accessible URL or use Facebook media upload flow.
        # Here we'll attempt to send as a link fallback message.
        payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": f"Document: {filename} (attachment)"}}
        self._post(payload)

    def send_image(self, to: str, url_or_path: str, caption: Optional[str] = None) -> None:
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            payload = {"messaging_product": "whatsapp", "to": to, "type": "image", "image": {"link": url_or_path}}
            if caption:
                payload["image"]["caption"] = caption
            self._post(payload)
            return
        self._post({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": f"[Image] {caption or ''}"}})

    def request_selfie(self, to: str, body: str) -> None:
        # present a single button prompting the user to send a selfie (they will use camera)
        payload = {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {"type": "button", "body": {"text": body}, "action": {"buttons": [{"type": "reply", "reply": {"id": "SEND_SELFIE", "title": "Send Selfie"}}]}}}
        self._post(payload)

    def send_url_button(self, to: str, body: str, title: str, url: str) -> None:
        # One URL button (button template)
        payload = {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {"type": "button", "body": {"text": body}, "action": {"buttons": [{"type": "url", "url": url, "title": title[:20]}]}}}
        self._post(payload)

    def send_template(self, to: str, template_name: str, language: str = "en_US", components: Optional[List[Dict[str, Any]]] = None) -> None:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "template", "template": {"name": template_name, "language": {"code": language}}}
        if components:
            payload["template"]["components"] = components
        self._post(payload)

    def send_location_request(self, phone: str) -> None:
        """
        Ask user to share their live location with a WhatsApp Location Request button.
        """
        payload = {
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Could you please share your location?"},
                "action": {
                    "buttons": [
                        {
                            "type": "location",
                            "title": "Share Location"
                        }
                    ]
                }
            }
        }

        r = self._post(payload)
        if r.status_code >= 300:
            logger.error(f"Failed sending location request: {r.status_code} {r.text}")
