#!/bin/bash

# Render deployment preparation script
# This script helps prepare your application for Render deployment

echo "🚀 Preparing WhatsApp AI Agent for Render deployment..."

# Check if required environment variables are set
echo "📋 Checking required environment variables..."

required_vars=(
    "WHATSAPP_VERIFY_TOKEN"
    "WHATSAPP_ACCESS_TOKEN"
    "WHATSAPP_APP_SECRET"
    "OPENAI_API_KEY"
    "MONGODB_URI"
    "JWT_SECRET_KEY"
)

missing_vars=()

for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        missing_vars+=("$var")
    else
        echo "✅ $var is set"
    fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
    echo ""
    echo "❌ Missing required environment variables:"
    for var in "${missing_vars[@]}"; do
        echo "   - $var"
    done
    echo ""
    echo "💡 Set these in your Render service environment variables before deployment."
    echo ""
fi

# Create a sample environment file for reference
echo "📝 Creating sample environment file..."
cat > .env.render.example << EOF
# Required Environment Variables for Render Deployment
# Copy these to your Render service environment variables

# WhatsApp Configuration
WHATSAPP_VERIFY_TOKEN=your_webhook_verify_token
WHATSAPP_ACCESS_TOKEN=your_whatsapp_access_token
WHATSAPP_APP_SECRET=your_app_secret

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Database Configuration
MONGODB_URI=your_mongodb_connection_string

# JWT Configuration
JWT_SECRET_KEY=your_jwt_secret_key

# Optional Configuration
LOG_LEVEL=INFO
WORKER_TIMEOUT=180
MAX_RETRIES=3

# Render will automatically set these:
# REDIS_URL=automatically_set_by_render
# PORT=automatically_set_by_render
EOF

echo "✅ Created .env.render.example with required variables"

# Check Python version compatibility
echo ""
echo "🐍 Checking Python compatibility..."
python_version=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
if [[ "$python_version" =~ ^3\.(9|10|11|12)$ ]]; then
    echo "✅ Python $python_version is compatible with Render"
else
    echo "⚠️  Python $python_version - ensure Render supports this version"
fi

# Check if all required files exist
echo ""
echo "📁 Checking deployment files..."
files_to_check=(
    "main.py"
    "worker.py"
    "queue_manager.py"
    "pyproject.toml"
    "Procfile"
    "render.yaml"
)

for file in "${files_to_check[@]}"; do
    if [ -f "$file" ]; then
        echo "✅ $file exists"
    else
        echo "❌ $file missing"
    fi
done

echo ""
echo "🎯 Deployment Checklist:"
echo "========================"
echo ""
echo "1. 📋 Environment Variables:"
echo "   - Go to your Render service dashboard"
echo "   - Add all variables from .env.render.example"
echo "   - REDIS_URL and PORT are set automatically"
echo ""
echo "2. 🔧 Service Configuration:"
echo "   - Web Service: Handles webhook requests"
echo "   - Worker Service: Processes messages in background"
echo "   - Redis Service: Message queue and caching"
echo ""
echo "3. 🌐 Webhook Configuration:"
echo "   - Set webhook URL: https://your-app-name.onrender.com"
echo "   - Use the WHATSAPP_VERIFY_TOKEN for verification"
echo ""
echo "4. 📊 Monitoring:"
echo "   - Health check: https://your-app-name.onrender.com/health"
echo "   - Queue stats: https://your-app-name.onrender.com/api/queue/stats"
echo ""
echo "🚀 Ready for deployment!"
echo ""
echo "Next steps:"
echo "1. Push your code to GitHub"
echo "2. Connect your repository to Render"
echo "3. Deploy using render.yaml or create services manually"
echo "4. Set environment variables in Render dashboard"
echo "5. Configure your WhatsApp webhook URL"
