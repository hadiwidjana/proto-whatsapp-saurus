# Deploying WhatsApp AI Agent to Render

This guide walks you through deploying your WhatsApp AI Agent with Redis worker queue to Render.

## Architecture on Render

Your application will be deployed as 3 separate services:

1. **Web Service** - Handles webhook requests (main.py)
2. **Worker Service** - Processes messages in background (worker.py) 
3. **Redis Service** - Message queue and caching

## Deployment Options

### Option 1: Blueprint Deployment (Recommended)

Use the `render.yaml` file for automated deployment:

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Add Redis worker queue system"
   git push origin main
   ```

2. **Create Render Blueprint**
   - Go to [Render Dashboard](https://dashboard.render.com)
   - Click "New" → "Blueprint"
   - Connect your GitHub repository
   - Render will automatically detect `render.yaml`
   - Click "Apply" to deploy all services

### Option 2: Manual Service Creation

Create each service individually:

#### 1. Create Redis Service
- Service Type: **Redis**
- Name: `whatsapp-redis`
- Plan: **Starter** (free)
- Region: Choose closest to your users

#### 2. Create Web Service
- Service Type: **Web Service**
- Name: `whatsapp-web`
- Environment: **Python**
- Build Command: `pip install -e .`
- Start Command: `python main.py`
- Plan: **Starter** (free)

#### 3. Create Worker Service
- Service Type: **Background Worker**
- Name: `whatsapp-worker`
- Environment: **Python**
- Build Command: `pip install -e .`
- Start Command: `python worker.py`
- Plan: **Starter** (free)
- Instances: **2** (for redundancy)

## Environment Variables

Set these in your Render service dashboard:

### Required Variables
```env
WHATSAPP_VERIFY_TOKEN=your_webhook_verify_token
WHATSAPP_ACCESS_TOKEN=your_whatsapp_access_token
WHATSAPP_APP_SECRET=your_app_secret
OPENAI_API_KEY=your_openai_api_key
MONGODB_URI=your_mongodb_connection_string
JWT_SECRET_KEY=your_jwt_secret_key
```

### Automatically Set by Render
- `REDIS_URL` - Automatically linked from Redis service
- `PORT` - Set by Render (usually 10000)

### Optional Configuration
```env
LOG_LEVEL=INFO
WORKER_TIMEOUT=180
MAX_RETRIES=3
WORKER_CONCURRENCY=2
```

## Pre-Deployment Checklist

Run the deployment preparation script:

```bash
./deploy_to_render.sh
```

This script will:
- ✅ Check required environment variables
- ✅ Verify file structure
- ✅ Create example environment file
- ✅ Validate Python compatibility

## Deployment Steps

### 1. Prepare Repository
```bash
# Ensure all files are committed
git add .
git commit -m "Prepare for Render deployment"
git push origin main
```

### 2. Deploy to Render

**Option A: Blueprint Deployment**
1. Go to Render Dashboard
2. Click "New" → "Blueprint"
3. Connect your GitHub repo
4. Click "Apply"

**Option B: Manual Deployment**
1. Create Redis service first
2. Create Web service (link to Redis)
3. Create Worker service (link to Redis)

### 3. Configure Environment Variables

In each service dashboard:
1. Go to "Environment" tab
2. Add all required variables
3. Save changes (triggers redeploy)

### 4. Configure WhatsApp Webhook

Set your webhook URL in Meta Developer Console:
```
https://your-app-name.onrender.com
```

## Monitoring & Health Checks

### Health Check Endpoint
```
GET https://your-app-name.onrender.com/health
```

Returns:
```json
{
  "status": "healthy",
  "timestamp": "2025-10-13T...",
  "service": "whatsapp-webhook",
  "queue": {
    "available": true,
    "queued_jobs": 0,
    "failed_jobs": 0,
    "workers_count": 2
  }
}
```

### Queue Management APIs

**Get Queue Statistics:**
```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     https://your-app-name.onrender.com/api/queue/stats
```

**Clear Failed Jobs:**
```bash
curl -X POST -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     https://your-app-name.onrender.com/api/queue/clear-failed
```

## Scaling on Render

### Worker Scaling
- Go to Worker service dashboard
- Increase "Instance Count" under "Settings"
- Higher traffic = more workers needed

### Vertical Scaling
- Upgrade service plans for more CPU/memory
- Starter → Standard → Pro plans available

### Redis Scaling
- Starter Redis: 25MB (good for development)
- Standard Redis: 100MB-1GB (production ready)

## Troubleshooting

### Common Issues

**1. Worker Not Starting**
- Check worker service logs
- Verify REDIS_URL environment variable
- Ensure Redis service is healthy

**2. Queue Not Processing**
- Check Redis service status
- Verify worker instances are running
- Check for failed jobs in queue stats

**3. Webhook Timeouts**
- Ensure workers are processing messages
- Check queue depth in health endpoint
- Scale workers if queue is backing up

### Debugging Commands

**View Service Logs:**
```bash
# Via Render dashboard
Go to service → "Logs" tab

# Or use Render CLI
render logs --service whatsapp-web
render logs --service whatsapp-worker
```

**Check Service Status:**
```bash
render services list
render service status whatsapp-web
```

## Cost Optimization

### Free Tier Usage
- Redis Starter: Free (25MB)
- Web Service Starter: Free (750 hours/month)
- Worker Starter: Free (750 hours/month)

### Production Recommendations
- Redis Standard: $7/month (100MB)
- Web Service Standard: $7/month
- Worker Standard: $7/month per instance

## Security Considerations

1. **Environment Variables**
   - Never commit secrets to repository
   - Use Render's encrypted environment variables

2. **Webhook Security**
   - Verify webhook signatures (WHATSAPP_APP_SECRET)
   - Use HTTPS endpoints only

3. **Database Security**
   - Use MongoDB Atlas with IP allowlisting
   - Enable authentication and SSL

4. **Redis Security**
   - Render Redis is automatically secured
   - Uses internal networking between services

## Performance Tips

1. **Worker Optimization**
   - Start with 2 workers, scale based on load
   - Monitor queue depth and processing times

2. **Caching**
   - Redis automatically handles caching
   - Consider implementing result caching for AI responses

3. **Database Optimization**
   - Use database indexes for frequent queries
   - Consider read replicas for high traffic

## Post-Deployment Verification

1. **Test Webhook:**
   ```bash
   curl https://your-app-name.onrender.com/health
   ```

2. **Send Test Message:**
   - Send WhatsApp message to your number
   - Check logs for processing confirmation

3. **Monitor Queue:**
   - Check queue stats API
   - Verify workers are processing messages

Your WhatsApp AI Agent with Redis worker queue is now ready for production on Render! 🚀
