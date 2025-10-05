from pymongo import MongoClient
from datetime import datetime, timezone
import os
import logging
import jwt
from functools import wraps
from flask import request, jsonify

logger = logging.getLogger(__name__)

class WhatsAppMessage:
    def __init__(self, entry_id, wa_id, message_id, from_number, timestamp, message_type,
                 message_content, message_direction="RECEIVED", contact_name=None, phone_number_id=None,
                 display_phone_number=None, raw_webhook_data=None):
        self.entry_id = entry_id
        self.wa_id = wa_id
        self.message_id = message_id
        self.from_number = from_number
        self.timestamp = timestamp
        self.message_type = message_type
        self.message_content = message_content
        self.message_direction = message_direction
        self.contact_name = contact_name
        self.phone_number_id = phone_number_id
        self.display_phone_number = display_phone_number
        self.created_at = datetime.now(timezone.utc)
        self.raw_webhook_data = raw_webhook_data

    def to_dict(self):
        return {
            "_id": self.entry_id,
            "wa_id": self.wa_id,
            "message_id": self.message_id,
            "from_number": self.from_number,
            "timestamp": self.timestamp,
            "message_type": self.message_type,
            "message_content": self.message_content,
            "message_direction": self.message_direction,
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

        # Extract database name from URI or use default
        db_name = os.getenv('MONGODB_DATABASE', 'whatsapp_saurus')
        self.db = self.client[db_name]
        self.collection = self.db.whatsapp_messages
        self.users_collection = self.db.users

        self._create_indexes()

    def _create_indexes(self):
        try:
            self.collection.create_index("message_id")
            self.collection.create_index("from_number")
            self.collection.create_index("message_direction")
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
                            wa_id = None
                            if contacts:
                                contact_name = contacts[0].get('profile', {}).get('name')
                                wa_id = contacts[0].get('wa_id')

                            business_phone = metadata.get('display_phone_number', '')
                            phone_number_id = metadata.get('phone_number_id', '')
                            message_id = message.get('id')
                            message_from = message.get('from', '')
                            message_direction = "SENT" if message_from == business_phone else "RECEIVED"

                            # Generate new ID format based on message direction
                            timestamp = message.get('timestamp', str(int(datetime.now(timezone.utc).timestamp())))

                            if message_direction == "RECEIVED":
                                entry_id = f"received_{phone_number_id}_{timestamp}"
                            else:
                                entry_id = f"sent_{phone_number_id}_{timestamp}"

                            wa_message = WhatsAppMessage(
                                entry_id=entry_id,
                                wa_id=wa_id,
                                message_id=message_id,
                                from_number=message.get('from'),
                                timestamp=timestamp,
                                message_type=message.get('type'),
                                message_content=message.get(message.get('type', 'text'), {}),
                                message_direction=message_direction,
                                contact_name=contact_name,
                                phone_number_id=metadata.get('phone_number_id'),
                                display_phone_number=metadata.get('display_phone_number'),
                                raw_webhook_data=message_data
                            )

                            self.collection.replace_one(
                                {"_id": wa_message.entry_id},
                                wa_message.to_dict(),
                                upsert=True
                            )

            return True
        except Exception as e:
            logger.error(f"Database save error: {str(e)}")
            raise e

    def close(self):
        if hasattr(self, 'client'):
            self.client.close()

    def get_conversation_history(self, from_number, limit=10):
        logger.info(f"Retrieving conversation history for contact, limit: {limit}")
        try:
            messages = self.collection.find(
                {"from_number": from_number},
                {"message_content": 1, "message_direction": 1, "timestamp": 1, "_id": 0}
            ).sort("timestamp", -1).limit(limit)

            message_list = list(messages)
            logger.info(f"Successfully retrieved {len(message_list)} messages from conversation history")
            return message_list
        except Exception as e:
            logger.error(f"Error retrieving conversation history: {str(e)}")
            return []

    def get_user_by_email(self, email):
        try:
            user = self.users_collection.find_one({"email": email})
            return user
        except Exception as e:
            logger.error(f"Error retrieving user by email: {str(e)}")
            return None

    def get_customers_by_phone_number_id(self, phone_number_id, limit=100):
        try:
            pipeline = [
                {
                    "$match": {
                        "phone_number_id": phone_number_id
                    }
                },
                {
                    "$group": {
                        "_id": "$from_number",
                        "contact_name": {"$last": "$contact_name"},
                        "last_message_timestamp": {"$max": "$timestamp"},
                        "last_message_content": {"$last": "$message_content"},
                        "message_count": {"$sum": 1}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "phone_number": "$_id",
                        "contact_name": 1,
                        "last_message_timestamp": 1,
                        "last_message_content": 1,
                        "message_count": 1
                    }
                },
                {
                    "$sort": {"last_message_timestamp": -1}
                },
                {
                    "$limit": limit
                }
            ]

            customers = list(self.collection.aggregate(pipeline))
            logger.info(f"Retrieved {len(customers)} customers for phone_number_id: {phone_number_id}")
            return customers
        except Exception as e:
            logger.error(f"Error retrieving customers: {str(e)}")
            return []

    def get_chat_history(self, phone_number_id, customer_phone, limit=50, offset=0):
        try:
            pipeline = [
                {
                    "$match": {
                        "phone_number_id": phone_number_id,
                        "$or": [
                            {"from_number": customer_phone},
                            {"$and": [
                                {"message_direction": "SENT"},
                                {"$expr": {"$ne": ["$from_number", "$display_phone_number"]}}
                            ]}
                        ]
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "message_id": 1,
                        "from_number": 1,
                        "timestamp": 1,
                        "message_type": 1,
                        "message_content": 1,
                        "message_direction": 1,
                        "contact_name": 1,
                        "created_at": 1
                    }
                },
                {
                    "$sort": {"timestamp": -1}
                },
                {
                    "$skip": offset
                },
                {
                    "$limit": limit
                }
            ]

            messages = list(self.collection.aggregate(pipeline))

            total_count_pipeline = [
                {
                    "$match": {
                        "phone_number_id": phone_number_id,
                        "$or": [
                            {"from_number": customer_phone},
                            {"$and": [
                                {"message_direction": "SENT"},
                                {"$expr": {"$ne": ["$from_number", "$display_phone_number"]}}
                            ]}
                        ]
                    }
                },
                {
                    "$count": "total"
                }
            ]

            total_result = list(self.collection.aggregate(total_count_pipeline))
            total_count = total_result[0]["total"] if total_result else 0

            logger.info(f"Retrieved {len(messages)} messages (offset: {offset}, limit: {limit}) out of {total_count} total messages")

            return {
                "messages": messages,
                "total_count": total_count,
                "offset": offset,
                "limit": limit,
                "has_more": (offset + len(messages)) < total_count
            }

        except Exception as e:
            logger.error(f"Error retrieving chat history: {str(e)}")
            return {
                "messages": [],
                "total_count": 0,
                "offset": offset,
                "limit": limit,
                "has_more": False
            }

def verify_jwt_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')

        if not token:
            return jsonify({'error': 'Token is missing'}), 401

        if token.startswith('Bearer '):
            token = token[7:]

        try:
            decoded_token = jwt.decode(
                token,
                options={"verify_signature": False}
            )

            email = decoded_token.get('sub')
            if not email:
                return jsonify({'error': 'Invalid token format'}), 401

            request.user_email = email

        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401

        return f(*args, **kwargs)

    return decorated_function
