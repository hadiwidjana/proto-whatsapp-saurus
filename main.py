from flask import Flask, request, jsonify
import logging
import hashlib
import hmac
import os
import json
from datetime import datetime
from dotenv import load_dotenv

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
        print(f"\n\nWebhook received {timestamp}\n")
        print(json.dumps(data, indent=2))

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

    logger.info("Starting WhatsApp webhook server...")
    logger.info(f"Verify token configured: Yes")
    logger.info(f"App secret configured: {'Yes' if APP_SECRET else 'No'}")
    logger.info(f"Listening on port {port}")

    app.run(host='0.0.0.0', port=port, debug=False)
