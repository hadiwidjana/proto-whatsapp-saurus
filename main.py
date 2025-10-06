from flask import Flask, request, jsonify
import logging
import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from models import Database, verify_jwt_token
from services import OpenAIService, WhatsAppAPIService, AutoReplyService
from ai_agent import WhatsAppAIAgent

load_dotenv()

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')
APP_SECRET = os.getenv('WHATSAPP_APP_SECRET', '')

# Initialize database
try:
    db = Database()
    logger.info("Database connection established")
except Exception as e:
    logger.error(f"Failed to initialize database: {str(e)}")
    db = None

# Initialize services
openai_service = None
whatsapp_service = None
auto_reply_service = None
ai_agent = None

try:
    if os.getenv('OPENAI_API_KEY') and os.getenv('WHATSAPP_ACCESS_TOKEN'):
        openai_service = OpenAIService()
        whatsapp_service = WhatsAppAPIService()
        auto_reply_service = AutoReplyService(db, openai_service, whatsapp_service)
        ai_agent = WhatsAppAIAgent(db, whatsapp_service)
        logger.info("AI agent and services initialized")
    else:
        logger.warning("AI agent not initialized - missing required environment variables")
except Exception as e:
    logger.error(f"Failed to initialize AI agent: {str(e)}")

# def verify_signature(payload_body, signature):
#     if not APP_SECRET:
#         return True
#
#     expected_signature = hmac.new(
#         APP_SECRET.encode('utf-8'),
#         payload_body,
#         hashlib.sha256
#     ).hexdigest()
#
#     return hmac.compare_digest(f"sha256={expected_signature}", signature)

@app.route('/', methods=['GET'])
def webhook_verify():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print('WEBHOOK VERIFIED')
        return challenge, 200
    else:
        return '', 403

@app.route('/', methods=['POST'])
def webhook_receive():
    try:
        data = request.get_json()

        if not data:
            logger.warning("No JSON data received in webhook")
            return '', 400

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"\n\nWebhook received {timestamp}\n")
        logger.info(json.dumps(data, indent=2))

        # Store message data in database
        if db and data.get('object') == 'whatsapp_business_account':
            try:
                db.save_message(data)
                logger.info("Message successfully stored in database")

                # Process with AI agent if available
                if ai_agent:
                    try:
                        # Run the async AI agent processing
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        response = loop.run_until_complete(ai_agent.process_message(data))
                        loop.close()

                        if response:
                            logger.info(f"AI agent processed message successfully: {response[:100]}...")
                        else:
                            logger.info("AI agent determined no response needed")
                    except Exception as ai_error:
                        logger.error(f"AI agent processing failed: {str(ai_error)}")

                        # Fallback to basic auto-reply
                        if auto_reply_service:
                            try:
                                auto_reply_service.process_and_reply(data)
                            except Exception as fallback_error:
                                logger.error(f"Fallback auto-reply failed: {str(fallback_error)}")

            except Exception as db_error:
                logger.error(f"Failed to store message in database: {str(db_error)}")

        return '', 200

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return '', 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'whatsapp-webhook'
    }), 200

@app.errorhandler(404)
def not_found(error):
    return '', 404

@app.errorhandler(500)
def internal_error(error):
    return '', 500

@app.route('/api/customers', methods=['GET'])
@verify_jwt_token
def get_customers():
    try:
        if not db:
            return jsonify({'error': 'Database not available'}), 500

        user_email = request.user_email
        user = db.get_user_by_email(user_email)

        if not user:
            return jsonify({'error': 'User not found'}), 404

        if not user.get('is_whatsapp_connected'):
            return jsonify({'error': 'WhatsApp not connected'}), 400

        phone_number_id = user.get('whatsapp_phone_number_id')
        if not phone_number_id:
            return jsonify({'error': 'WhatsApp phone number not configured'}), 400

        limit = request.args.get('limit', 100, type=int)
        if limit > 1000:
            limit = 1000

        customers = db.get_customers_by_phone_number_id(phone_number_id, limit)

        return jsonify({
            'success': True,
            'data': customers,
            'count': len(customers),
            'whatsapp_phone_number': user.get('whatsapp_phone_number'),
            'phone_number_id': phone_number_id
        }), 200

    except Exception as e:
        logger.error(f"Error fetching customers: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/chat-history/<customer_phone>', methods=['GET'])
@verify_jwt_token
def get_chat_history(customer_phone):
    try:
        if not db:
            return jsonify({'error': 'Database not available'}), 500

        user_email = request.user_email
        user = db.get_user_by_email(user_email)

        if not user:
            return jsonify({'error': 'User not found'}), 404

        if not user.get('is_whatsapp_connected'):
            return jsonify({'error': 'WhatsApp not connected'}), 400

        phone_number_id = user.get('whatsapp_phone_number_id')
        if not phone_number_id:
            return jsonify({'error': 'WhatsApp phone number not configured'}), 400

        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        if limit > 200:
            limit = 200
        if limit < 1:
            limit = 1
        if offset < 0:
            offset = 0

        if not customer_phone or len(customer_phone.strip()) == 0:
            return jsonify({'error': 'Customer phone number is required'}), 400

        chat_data = db.get_chat_history(phone_number_id, customer_phone, limit, offset)

        return jsonify({
            'success': True,
            'data': chat_data['messages'],
            'pagination': {
                'total_count': chat_data['total_count'],
                'offset': chat_data['offset'],
                'limit': chat_data['limit'],
                'has_more': chat_data['has_more'],
                'current_page': (chat_data['offset'] // chat_data['limit']) + 1,
                'total_pages': ((chat_data['total_count'] - 1) // chat_data['limit']) + 1 if chat_data['total_count'] > 0 else 1
            },
            'customer_phone': customer_phone,
            'whatsapp_phone_number': user.get('whatsapp_phone_number'),
            'phone_number_id': phone_number_id
        }), 200

    except Exception as e:
        logger.error(f"Error fetching chat history: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))

    if not VERIFY_TOKEN:
        logger.error("WHATSAPP_VERIFY_TOKEN environment variable not set")
        exit(1)

    if not os.getenv('MONGODB_URI'):
        logger.error("MONGODB_URI environment variable not set")
        exit(1)

    logger.info("Starting WhatsApp webhook server...")
    logger.info(f"Verify token configured: Yes")
    logger.info(f"App secret configured: {'Yes' if APP_SECRET else 'No'}")
    logger.info(f"Database configured: {'Yes' if db else 'No'}")
    logger.info(f"Listening on port {port}")

    app.run(host='0.0.0.0', port=port, debug=False)
