from pymongo import MongoClient
from datetime import datetime, timezone
import os
import logging

logger = logging.getLogger(__name__)

class WhatsAppMessage:
    def __init__(self, wa_id, message_id, from_number, timestamp, message_type,
                 message_content, contact_name=None, phone_number_id=None,
                 display_phone_number=None, raw_webhook_data=None):
        self.wa_id = wa_id
        self.message_id = message_id
        self.from_number = from_number
        self.timestamp = timestamp
        self.message_type = message_type
        self.message_content = message_content
        self.contact_name = contact_name
        self.phone_number_id = phone_number_id
        self.display_phone_number = display_phone_number
        self.created_at = datetime.now(timezone.utc)
        self.raw_webhook_data = raw_webhook_data

    def to_dict(self):
        return {
            "_id": self.wa_id,
            "message_id": self.message_id,
            "from_number": self.from_number,
            "timestamp": self.timestamp,
            "message_type": self.message_type,
            "message_content": self.message_content,
            "contact_name": self.contact_name,
            "phone_number_id": self.phone_number_id,
            "display_phone_number": self.display_phone_number,
            "created_at": self.created_at,
            "raw_webhook_data": self.raw_webhook_data
        }

class Database:
    def __init__(self):
        self.mongo_uri = os.getenv('MONGODB_URI')
        if not self.mongo_uri:
            raise ValueError("MONGODB_URI environment variable not set")

        self.client = MongoClient(self.mongo_uri)
        self.db = self.client.get_default_database()
        self.collection = self.db.whatsapp_messages

        self._create_indexes()

    def _create_indexes(self):
        try:
            self.collection.create_index("message_id")
            self.collection.create_index("from_number")
            self.collection.create_index("created_at")
        except Exception as e:
            logger.warning(f"Failed to create indexes: {str(e)}")

    def save_message(self, message_data):
        try:
            for entry in message_data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        value = change.get('value', {})
                        metadata = value.get('metadata', {})
                        contacts = value.get('contacts', [])
                        messages = value.get('messages', [])

                        for message in messages:
                            contact_name = None
                            if contacts:
                                contact_name = contacts[0].get('profile', {}).get('name')

                            wa_message = WhatsAppMessage(
                                wa_id=message.get('from'),
                                message_id=message.get('id'),
                                from_number=message.get('from'),
                                timestamp=message.get('timestamp'),
                                message_type=message.get('type'),
                                message_content=message.get(message.get('type', 'text'), {}),
                                contact_name=contact_name,
                                phone_number_id=metadata.get('phone_number_id'),
                                display_phone_number=metadata.get('display_phone_number'),
                                raw_webhook_data=message_data
                            )

                            self.collection.replace_one(
                                {"_id": wa_message.wa_id},
                                wa_message.to_dict(),
                                upsert=True
                            )

            return True
        except Exception as e:
            logger.error(f"Database save error: {str(e)}")
            raise e

    def close(self):
        if self.client:
            self.client.close()
