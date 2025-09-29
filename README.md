# WhatsApp Webhook Server

A secure Python Flask server for receiving WhatsApp webhook notifications.

## Features

- Webhook verification (GET requests)
- Message reception and logging (POST requests)
- Signature verification for security
- Comprehensive logging to both file and console
- Health check endpoint
- Secure handling of sensitive data

## Setup

1. Install dependencies:
```bash
pip install -e .
```

2. Set environment variables:
```bash
export WHATSAPP_VERIFY_TOKEN="your_webhook_verify_token"
export WHATSAPP_APP_SECRET="your_app_secret"  # Optional but recommended
```

3. Run the server:
```bash
python main.py
```

The server will start on `http://localhost:5000`

## Endpoints

- `GET /webhook` - Webhook verification endpoint
- `POST /webhook` - Webhook message receiver
- `GET /health` - Health check endpoint

## Configuration

- **WHATSAPP_VERIFY_TOKEN**: Required for webhook verification
- **WHATSAPP_APP_SECRET**: Optional, used for signature verification

## Logs

All webhook activity is logged to:
- Console output
- `whatsapp_webhook.log` file

The logging follows secure practices and doesn't expose sensitive message content.
