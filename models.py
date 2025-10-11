import os
import logging
import jwt
from datetime import datetime, timezone
from functools import wraps
from flask import request, jsonify
from pymongo import MongoClient
from bson import ObjectId
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017/'))
        self.db = self.client[os.getenv('DATABASE_NAME', 'whatsapp_saurus')]
        self.collection = self.db.messages
        self.users_collection = self.db.users
        self.business_collection = self.db.business_details
        self.balance_history_collection = self.db.balance_history

        self._create_indexes()

    def _create_indexes(self):
        try:
            self.collection.create_index("message_id")
            self.collection.create_index("from_number")
            self.collection.create_index("message_direction")
            self.collection.create_index("created_at")
            self.collection.create_index([("phone_number_id", 1), ("from_number", 1)])

            self.users_collection.create_index("email", unique=True)
            self.users_collection.create_index("whatsapp_phone_number_id")

            self.business_collection.create_index("user_id")

            self.balance_history_collection.create_index("user_id")
            self.balance_history_collection.create_index("created_at")
            self.balance_history_collection.create_index([("user_id", 1), ("created_at", -1)])
        except Exception as e:
            logger.warning(f"Failed to create indexes: {str(e)}")

    def save_message(self, message_data: Dict[str, Any]) -> bool:
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
                            timestamp = message.get('timestamp', '')
                            message_type = message.get('type', '')

                            message_text = ''
                            if message_type == 'text':
                                message_text = message.get('text', {}).get('body', '')

                            document = {
                                'message_id': message_id,
                                'phone_number_id': phone_number_id,
                                'business_phone': business_phone,
                                'from_number': message_from,
                                'wa_id': wa_id,
                                'contact_name': contact_name,
                                'message_text': message_text,
                                'message_type': message_type,
                                'message_direction': 'incoming',
                                'timestamp': timestamp,
                                'created_at': datetime.now(timezone.utc),
                                'raw_data': message
                            }

                            existing = self.collection.find_one({'message_id': message_id})
                            if not existing:
                                self.collection.insert_one(document)
                                logger.info(f"Message {message_id} saved successfully")
                            else:
                                logger.info(f"Message {message_id} already exists")
            return True
        except Exception as e:
            logger.error(f"Error saving message: {str(e)}")
            return False

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        try:
            return self.users_collection.find_one({'email': email})
        except Exception as e:
            logger.error(f"Error getting user by email: {str(e)}")
            return None

    def get_user_by_phone_number_id(self, phone_number_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.users_collection.find_one({'whatsapp_phone_number_id': phone_number_id})
        except Exception as e:
            logger.error(f"Error getting user by phone number ID: {str(e)}")
            return None

    def get_business_details(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            business_details = self.business_collection.find_one({'user_id': ObjectId(user_id)})
            if business_details:
                # Convert ObjectId to string for JSON serialization
                if '_id' in business_details:
                    business_details['_id'] = str(business_details['_id'])
                if 'user_id' in business_details:
                    business_details['user_id'] = str(business_details['user_id'])
            return business_details
        except Exception as e:
            logger.error(f"Error getting business details: {str(e)}")
            return None

    def get_customers_by_phone_number_id(self, phone_number_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            pipeline = [
                {'$match': {'phone_number_id': phone_number_id, 'message_direction': 'incoming'}},
                {'$group': {
                    '_id': '$from_number',
                    'contact_name': {'$last': '$contact_name'},
                    'last_message': {'$last': '$message_text'},
                    'last_message_time': {'$last': '$created_at'},
                    'message_count': {'$sum': 1}
                }},
                {'$sort': {'last_message_time': -1}},
                {'$limit': limit},
                {'$project': {
                    'phone_number': '$_id',
                    'contact_name': 1,
                    'last_message': 1,
                    'last_message_time': 1,
                    'message_count': 1,
                    '_id': 0
                }}
            ]
            return list(self.collection.aggregate(pipeline))
        except Exception as e:
            logger.error(f"Error getting customers: {str(e)}")
            return []

    def get_chat_history(self, phone_number_id: str, customer_phone: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        try:
            # Get total count
            total_count = self.collection.count_documents({
                'phone_number_id': phone_number_id,
                '$or': [
                    {'from_number': customer_phone},
                    {'to_number': customer_phone}
                ]
            })

            # Get messages with pagination
            messages = list(self.collection.find({
                'phone_number_id': phone_number_id,
                '$or': [
                    {'from_number': customer_phone},
                    {'to_number': customer_phone}
                ]
            }).sort('created_at', -1).skip(offset).limit(limit))

            # Convert ObjectId to string for JSON serialization
            for message in messages:
                if '_id' in message:
                    message['_id'] = str(message['_id'])

            return {
                'messages': messages,
                'total_count': total_count,
                'offset': offset,
                'limit': limit,
                'has_more': (offset + limit) < total_count
            }
        except Exception as e:
            logger.error(f"Error getting chat history: {str(e)}")
            return {
                'messages': [],
                'total_count': 0,
                'offset': offset,
                'limit': limit,
                'has_more': False
            }

    def save_outgoing_message(self, phone_number_id: str, to_number: str, message_text: str, message_id: str = None, cost_amount: int = 0, cost_reason: str = "AI response") -> bool:
        try:
            document = {
                'message_id': message_id or f"out_{datetime.now().timestamp()}",
                'phone_number_id': phone_number_id,
                'to_number': to_number,
                'message_text': message_text,
                'message_type': 'text',
                'message_direction': 'outgoing',
                'created_at': datetime.now(timezone.utc),
                'ai_generated': True,
                'cost_amount': cost_amount,
                'cost_reason': cost_reason,
                'billing_processed': True
            }

            self.collection.insert_one(document)
            logger.info(f"Outgoing message saved successfully with cost: {cost_amount} rupiah")
            return True
        except Exception as e:
            logger.error(f"Error saving outgoing message: {str(e)}")
            return False

    def get_user_balance(self, user_id: str) -> int:
        """Get current user balance from balance_history"""
        try:
            user_object_id = ObjectId(user_id)

            # Get the latest balance entry from balance_history
            latest_entry = self.balance_history_collection.find_one(
                {"user_id": user_object_id},
                sort=[("created_at", -1)]
            )

            return latest_entry.get("balance", 0) if latest_entry else 0
        except Exception as e:
            logger.error(f"Error getting user balance: {str(e)}")
            return 0

    def deduct_user_balance(self, user_id: str, amount: int, reason: str = "AI response") -> Dict[str, Any]:
        """
        Deduct balance from user account using balance_history table
        Returns: {"success": bool, "new_balance": int, "message": str}
        """
        try:
            user_object_id = ObjectId(user_id)

            # Verify user exists
            user = self.users_collection.find_one({"_id": user_object_id})
            if not user:
                return {"success": False, "new_balance": 0, "message": "User not found"}

            # Get current balance from balance_history
            current_balance = self.get_user_balance(user_id)

            # Check if user has sufficient balance
            if current_balance < amount:
                logger.warning(f"Insufficient balance for user {user_id}: {current_balance} < {amount}")
                return {
                    "success": False,
                    "new_balance": current_balance,
                    "message": f"Insufficient balance. Current: {current_balance}, Required: {amount}"
                }

            # Calculate new balance after deduction
            new_balance = current_balance - amount

            # Create balance history entry
            balance_entry = {
                "user_id": user_object_id,
                "event": "deduction",
                "amount": amount,
                "balance": new_balance,
                "created_at": datetime.now(timezone.utc)
            }

            # Insert the balance history record
            result = self.balance_history_collection.insert_one(balance_entry)

            if result.inserted_id:
                logger.info(f"Balance deducted for user {user_id}: -{amount} (new balance: {new_balance})")
                return {
                    "success": True,
                    "new_balance": new_balance,
                    "message": f"Balance deducted: {amount}. New balance: {new_balance}"
                }
            else:
                return {"success": False, "new_balance": current_balance, "message": "Failed to record balance deduction"}

        except Exception as e:
            logger.error(f"Error deducting balance: {str(e)}")
            return {"success": False, "new_balance": 0, "message": f"Error: {str(e)}"}

    def add_user_balance(self, user_id: str, amount: int, reason: str = "Top up") -> Dict[str, Any]:
        """
        Add balance to user account using balance_history table
        Returns: {"success": bool, "new_balance": int, "message": str}
        """
        try:
            user_object_id = ObjectId(user_id)

            # Verify user exists
            user = self.users_collection.find_one({"_id": user_object_id})
            if not user:
                return {"success": False, "new_balance": 0, "message": "User not found"}

            # Get current balance from balance_history
            current_balance = self.get_user_balance(user_id)

            # Calculate new balance after addition
            new_balance = current_balance + amount

            # Create balance history entry
            balance_entry = {
                "user_id": user_object_id,
                "event": "addition",
                "amount": amount,
                "balance": new_balance,
                "created_at": datetime.now(timezone.utc)
            }

            # Insert the balance history record
            result = self.balance_history_collection.insert_one(balance_entry)

            if result.inserted_id:
                logger.info(f"Balance added for user {user_id}: +{amount} (new balance: {new_balance})")
                return {
                    "success": True,
                    "new_balance": new_balance,
                    "message": f"Balance added: {amount}. New balance: {new_balance}"
                }
            else:
                return {"success": False, "new_balance": current_balance, "message": "Failed to record balance addition"}

        except Exception as e:
            logger.error(f"Error adding balance: {str(e)}")
            return {"success": False, "new_balance": 0, "message": f"Error: {str(e)}"}

    def get_balance_history(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Get balance history for a user with pagination"""
        try:
            user_object_id = ObjectId(user_id)

            history = list(self.balance_history_collection.find(
                {"user_id": user_object_id}
            ).sort("created_at", -1).skip(offset).limit(limit))

            # Convert ObjectId to string for JSON serialization
            for entry in history:
                if '_id' in entry:
                    entry['_id'] = str(entry['_id'])
                if 'user_id' in entry:
                    entry['user_id'] = str(entry['user_id'])

            return history
        except Exception as e:
            logger.error(f"Error getting balance history: {str(e)}")
            return []

def verify_jwt_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')

        if not token:
            return jsonify({'error': 'No token provided'}), 401

        if token.startswith('Bearer '):
            token = token[7:]

        try:
            payload = jwt.decode(
                token,
                os.getenv('JWT_SECRET_KEY', 'your-secret-key'),
                algorithms=['HS256']
            )
            # Extract email from 'sub' field (standard JWT claim) or 'email' field
            request.user_email = payload.get('sub') or payload.get('email')
            request.user_id = payload.get('user_id')

            if not request.user_email:
                return jsonify({'error': 'Invalid token: no email found'}), 401

        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401

        return f(*args, **kwargs)

    return decorated_function
