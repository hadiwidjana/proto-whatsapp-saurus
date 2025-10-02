import os
import requests
import logging
from openai import OpenAI
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    def generate_response(self, conversation_history: List[Dict], contact_name: Optional[str] = None) -> str:
        try:
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful WhatsApp assistant. Keep responses concise and conversational. Respond in a friendly, helpful manner."
                }
            ]

            for msg in reversed(conversation_history):
                role = "user" if msg["message_direction"] == "RECEIVED" else "assistant"
                content = self._extract_message_text(msg["message_content"])
                if content:
                    messages.append({"role": role, "content": content})

            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=150,
                temperature=0.7
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            return "I'm sorry, I'm having trouble processing your message right now. Please try again later."

    def _extract_message_text(self, message_content: Dict) -> str:
        if isinstance(message_content, dict):
            return message_content.get('body', '')
        return str(message_content)

class WhatsAppAPIService:
    def __init__(self):
        self.access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
        self.base_url = f"https://graph.facebook.com/v23.0/{self.phone_number_id}/messages"

    def send_message(self, to_number: str, message_text: str, db=None) -> bool:
        try:
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.access_token}'
            }

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_number,
                "type": "text",
                "text": {
                    "preview_url": False,
                    "body": message_text
                }
            }

            response = requests.post(self.base_url, json=payload, headers=headers)

            if response.status_code == 200:
                logger.info(f"Message sent successfully to {to_number}")

                # Store sent message in database
                if db:
                    try:
                        from models import WhatsAppMessage
                        import time

                        sent_message = WhatsAppMessage(
                            entry_id=f"sent_{int(time.time())}_{to_number}",
                            wa_id=self.phone_number_id,
                            message_id=response.json().get('messages', [{}])[0].get('id', f"sent_{int(time.time())}"),
                            from_number=self.phone_number_id,
                            timestamp=str(int(time.time())),
                            message_type="text",
                            message_content={"body": message_text},
                            message_direction="SENT",
                            contact_name=None,
                            phone_number_id=self.phone_number_id,
                            display_phone_number=self.phone_number_id,
                            raw_webhook_data={"sent_via_api": True, "to": to_number}
                        )

                        db.collection.insert_one(sent_message.to_dict())
                        logger.info(f"Sent message stored in database for {to_number}")

                    except Exception as db_error:
                        logger.error(f"Failed to store sent message: {str(db_error)}")

                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"WhatsApp API error: {str(e)}")
            return False

class AutoReplyService:
    def __init__(self, db, openai_service: OpenAIService, whatsapp_service: WhatsAppAPIService):
        self.db = db
        self.openai_service = openai_service
        self.whatsapp_service = whatsapp_service

    def process_and_reply(self, webhook_data: Dict) -> bool:
        try:
            for entry in webhook_data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        value = change.get('value', {})
                        messages = value.get('messages', [])
                        contacts = value.get('contacts', [])

                        for message in messages:
                            message_direction = self._determine_message_direction(message, value)

                            if message_direction == "RECEIVED" and message.get('type') == 'text':
                                from_number = message.get('from')
                                contact_name = None

                                if contacts:
                                    contact_name = contacts[0].get('profile', {}).get('name')

                                conversation_history = self.db.get_conversation_history(from_number)

                                ai_response = self.openai_service.generate_response(
                                    conversation_history,
                                    contact_name
                                )

                                success = self.whatsapp_service.send_message(from_number, ai_response, self.db)

                                if success:
                                    logger.info(f"Auto-reply sent to {from_number}: {ai_response}")
                                else:
                                    logger.error(f"Failed to send auto-reply to {from_number}")

                                return success

            return True

        except Exception as e:
            logger.error(f"Auto-reply processing error: {str(e)}")
            return False

    def _determine_message_direction(self, message: Dict, value: Dict) -> str:
        metadata = value.get('metadata', {})
        business_phone = metadata.get('display_phone_number', '')
        message_from = message.get('from', '')
        return "SENT" if message_from == business_phone else "RECEIVED"
