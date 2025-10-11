import os
import logging
import requests
from typing import Dict, Any, Optional
from openai import OpenAI
import resend

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    def generate_response(self, prompt: str, context: str = "", max_tokens: int = 150) -> Optional[str]:
        try:
            messages = [
                {"role": "system", "content": f"You are a helpful customer service assistant. {context}"},
                {"role": "user", "content": prompt}
            ]

            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )

            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating OpenAI response: {str(e)}")
            return None

class WhatsAppAPIService:
    def __init__(self):
        self.access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.base_url = "https://graph.facebook.com/v18.0"

    def send_message(self, phone_number_id: str, to_number: str, message_text: str) -> bool:
        try:
            url = f"{self.base_url}/{phone_number_id}/messages"

            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }

            payload = {
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "text",
                "text": {
                    "body": message_text
                }
            }

            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                logger.info(f"Message sent successfully to {to_number}")
                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {str(e)}")
            return False

    def send_template_message(self, phone_number_id: str, to_number: str, template_name: str,
                            language_code: str = "en", parameters: list = None) -> bool:
        try:
            url = f"{self.base_url}/{phone_number_id}/messages"

            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }

            template_data = {
                "name": template_name,
                "language": {
                    "code": language_code
                }
            }

            if parameters:
                template_data["components"] = [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": param} for param in parameters]
                    }
                ]

            payload = {
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "template",
                "template": template_data
            }

            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                logger.info(f"Template message sent successfully to {to_number}")
                return True
            else:
                logger.error(f"Failed to send template message: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending WhatsApp template message: {str(e)}")
            return False

class EmailService:
    def __init__(self):
        self.resend_api_key = os.getenv('RESEND_API_KEY')
        if self.resend_api_key:
            resend.api_key = self.resend_api_key

    def send_order_notification(self, business_details: Dict[str, Any], customer_phone: str,
                              order_details: str, customer_message: str) -> bool:
        """Send order/reservation notification email to business owner"""
        try:
            if not self.resend_api_key:
                logger.warning("Resend API key not configured")
                return False

            escalation_settings = business_details.get('escalation_settings', {})
            business_email = escalation_settings.get('email')
            business_name = business_details.get('business_name', 'Your Business')

            if not business_email:
                logger.warning("No business email configured for notifications")
                return False

            subject = f"New Order/Reservation Request - {business_name}"

            html_content = f"""
            <h2>New Order/Reservation Request</h2>
            <p><strong>Business:</strong> {business_name}</p>
            <p><strong>Customer Phone:</strong> {customer_phone}</p>
            <p><strong>Order Details:</strong></p>
            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 10px 0;">
                {order_details.replace('\n', '<br>')}
            </div>
            <p><strong>Original Customer Message:</strong></p>
            <div style="background-color: #e3f2fd; padding: 15px; border-radius: 5px; margin: 10px 0;">
                {customer_message}
            </div>
            <p>Please contact the customer to confirm the order/reservation details.</p>
            <hr>
            <p><small>This notification was sent automatically by Protosaurus.</small></p>
            """

            params = {
                "from": "support@protosaurus.id",
                "to": [business_email],
                "subject": subject,
                "html": html_content,
            }

            email = resend.Emails.send(params)
            logger.info(f"Order notification email sent successfully to {business_email}")
            return True

        except Exception as e:
            logger.error(f"Error sending order notification email: {str(e)}")
            return False

    def send_whatsapp_notification(self, whatsapp_service, phone_number_id: str,
                                 business_phone: str, customer_phone: str,
                                 order_details: str) -> bool:
        """Send WhatsApp notification to business owner"""
        try:
            notification_message = f"""🔔 New Order/Reservation Request

Customer: {customer_phone}

Order Details:
{order_details}

Please contact the customer to confirm the details.

- Protosaurus Auto-Notification"""

            success = whatsapp_service.send_message(phone_number_id, business_phone, notification_message)
            if success:
                logger.info(f"Order notification WhatsApp sent to {business_phone}")
            return success

        except Exception as e:
            logger.error(f"Error sending WhatsApp notification: {str(e)}")
            return False

class AutoReplyService:
    def __init__(self, db, openai_service, whatsapp_service):
        self.db = db
        self.openai_service = openai_service
        self.whatsapp_service = whatsapp_service

    def should_auto_reply(self, message_text: str) -> bool:
        """Determine if message should get an auto-reply (basic implementation)"""
        try:
            # Simple keyword-based auto-reply logic
            auto_reply_keywords = [
                'hello', 'hi', 'hey', 'hours', 'open', 'closed',
                'location', 'address', 'phone', 'contact', 'help'
            ]

            message_lower = message_text.lower()
            return any(keyword in message_lower for keyword in auto_reply_keywords)
        except Exception as e:
            logger.error(f"Error checking auto-reply: {str(e)}")
            return False

    def generate_auto_reply(self, message_text: str, business_context: str = "") -> str:
        """Generate an appropriate auto-reply"""
        try:
            if not business_context:
                business_context = "You are a helpful customer service assistant."

            prompt = f"Generate a brief, helpful response to this customer message: {message_text}"

            response = self.openai_service.generate_response(prompt, business_context)

            if not response:
                return "Thank you for your message! We'll get back to you shortly."

            return response
        except Exception as e:
            logger.error(f"Error generating auto-reply: {str(e)}")
            return "Thank you for your message! We'll get back to you shortly."

    def process_and_reply(self, message_data: Dict[str, Any]) -> bool:
        """Process incoming message and send auto-reply if appropriate"""
        try:
            # Extract message details
            for entry in message_data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        value = change.get('value', {})
                        metadata = value.get('metadata', {})
                        messages = value.get('messages', [])

                        phone_number_id = metadata.get('phone_number_id', '')

                        for message in messages:
                            if message.get('type') == 'text':
                                message_text = message.get('text', {}).get('body', '')
                                from_number = message.get('from', '')

                                # Check if should auto-reply
                                if self.should_auto_reply(message_text):
                                    # Get user and business context
                                    user = self.db.get_user_by_phone_number_id(phone_number_id)
                                    if user:
                                        business_details = self.db.get_business_details(str(user.get('_id')))
                                        business_context = ""

                                        if business_details:
                                            business_name = business_details.get('business_name', '')
                                            description = business_details.get('description', '')
                                            business_context = f"Business: {business_name}. {description}"

                                        # Generate and send reply
                                        reply_text = self.generate_auto_reply(message_text, business_context)

                                        success = self.whatsapp_service.send_message(
                                            phone_number_id, from_number, reply_text
                                        )

                                        if success:
                                            # Save outgoing message
                                            self.db.save_outgoing_message(
                                                phone_number_id, from_number, reply_text
                                            )
                                            logger.info(f"Auto-reply sent to {from_number}")

                                        return success

            return True
        except Exception as e:
            logger.error(f"Error in auto-reply processing: {str(e)}")
            return False
