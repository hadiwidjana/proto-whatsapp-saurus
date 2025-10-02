from flask import Flask, request, jsonify
import logging
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from models import Database
from services import OpenAIService, WhatsAppAPIService, AutoReplyService

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

try:
    if os.getenv('OPENAI_API_KEY') and os.getenv('WHATSAPP_ACCESS_TOKEN'):
        openai_service = OpenAIService()
        whatsapp_service = WhatsAppAPIService()
        auto_reply_service = AutoReplyService(db, openai_service, whatsapp_service)
        logger.info("Auto-reply services initialized")
    else:
        logger.warning("Auto-reply services not initialized - missing required environment variables")
except Exception as e:
    logger.error(f"Failed to initialize auto-reply services: {str(e)}")

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
        # signature = request.headers.get('X-Hub-Signature-256', '')
        # payload = request.get_data()

        # if APP_SECRET and not verify_signature(payload, signature):
        #     logger.warning("Invalid signature in webhook request")
        #     return '', 401

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

                # Process auto-reply if services are available
                if auto_reply_service:
                    try:
                        auto_reply_service.process_and_reply(data)
                    except Exception as reply_error:
                        logger.error(f"Auto-reply processing failed: {str(reply_error)}")

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
