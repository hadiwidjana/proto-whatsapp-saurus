# Redis Worker Queue Configuration

This document explains how to configure and use the Redis worker queue system for handling high-volume WhatsApp message traffic.

## Environment Variables

Add these environment variables to your `.env` file:

```env
# Redis Configuration
REDIS_URL=redis://localhost:6379/0

# Optional Redis Configuration
REDIS_MAX_CONNECTIONS=20
REDIS_SOCKET_TIMEOUT=5
REDIS_SOCKET_CONNECT_TIMEOUT=5
REDIS_RETRY_ON_TIMEOUT=true
REDIS_HEALTH_CHECK_INTERVAL=30

# Worker Configuration  
WORKER_CONCURRENCY=3
WORKER_TIMEOUT=180
MAX_RETRIES=3
```

## Quick Start

### 1. Install Redis
```bash
# macOS
brew install redis
brew services start redis

# Ubuntu/Debian
sudo apt-get install redis-server
sudo systemctl start redis-server

# Docker
docker run -d -p 6379:6379 redis:7-alpine
```

### 2. Install Dependencies
```bash
pip install -e .
```

### 3. Start Workers
```bash
# Start 3 workers (default)
./start_workers.sh

# Start specific number of workers
./start_workers.sh 5 start

# Check worker status
./start_workers.sh status
```

### 4. Start Web Application
```bash
python main.py
```

## Architecture

The worker queue system consists of:

1. **Main Application** (`main.py`): Receives webhooks and enqueues messages
2. **Queue Manager** (`queue_manager.py`): Manages Redis connections and job queuing
3. **Worker Process** (`worker.py`): Processes messages from the queue
4. **Management Script** (`start_workers.sh`): Manages worker processes

## Message Priority System

Messages are automatically prioritized based on content:

- **High Priority**: urgent, emergency, order, payment keywords; messages >200 chars
- **Normal Priority**: standard messages
- **Low Priority**: greetings, info requests; messages <20 chars

High priority messages are processed first with longer timeouts.

## Monitoring

### Health Check Endpoint
```bash
curl http://localhost:5000/health
```

Returns queue statistics including:
- Number of queued jobs
- Failed jobs count
- Active workers
- Redis connection status

### Queue Statistics API
```bash
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:5000/api/queue/stats
```

### Clear Failed Jobs
```bash
curl -X POST -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:5000/api/queue/clear-failed
```

## Worker Management

### Start Workers
```bash
./start_workers.sh 3 start    # Start 3 workers
```

### Stop Workers
```bash
./start_workers.sh stop       # Stop all workers
```

### Restart Workers
```bash
./start_workers.sh 5 restart  # Restart with 5 workers
```

### View Worker Status
```bash
./start_workers.sh status
```

### View Worker Logs
```bash
./start_workers.sh logs       # List available logs
./start_workers.sh logs 1     # View worker 1 logs
```

## Docker Deployment

### Using Docker Compose
```bash
# Start all services (Redis + Web + Workers)
docker-compose up -d

# Scale workers
docker-compose up -d --scale worker=5

# View logs
docker-compose logs -f worker
```

### Manual Docker Commands
```bash
# Start Redis
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Build application
docker build -t whatsapp-agent .

# Start web server
docker run -d --name web --link redis:redis -p 5000:5000 \
  -e REDIS_URL=redis://redis:6379/0 whatsapp-agent

# Start workers
docker run -d --name worker1 --link redis:redis \
  -e REDIS_URL=redis://redis:6379/0 whatsapp-agent python worker.py
```

## Production Considerations

### Scaling
- **Horizontal**: Add more worker processes across multiple servers
- **Vertical**: Increase worker concurrency per server
- **Redis**: Use Redis Cluster for high availability

### Monitoring
- Monitor Redis memory usage and connection counts
- Track failed job rates and processing times
- Set up alerts for queue depth thresholds

### Security
- Use Redis AUTH in production
- Configure Redis to bind to specific interfaces
- Use TLS for Redis connections in distributed setups

### Performance Tuning
- Adjust `WORKER_TIMEOUT` based on AI processing times
- Tune `MAX_RETRIES` for reliability vs. performance
- Monitor and adjust worker count based on load

## Troubleshooting

### Redis Connection Issues
```bash
# Test Redis connection
redis-cli ping

# Check Redis logs
redis-cli monitor
```

### Worker Issues
```bash
# Check worker logs
tail -f logs/worker_1.log

# Check worker processes
./start_workers.sh status

# Restart problematic workers
./start_workers.sh restart
```

### Failed Jobs
```bash
# Clear and requeue failed jobs
curl -X POST -H "Authorization: Bearer YOUR_JWT_TOKEN" \
     http://localhost:5000/api/queue/clear-failed
```

### High Memory Usage
- Increase Redis `maxmemory` setting
- Implement job result TTL
- Monitor for memory leaks in worker processes

## Configuration Examples

### High Traffic Setup
```env
WORKER_CONCURRENCY=10
REDIS_MAX_CONNECTIONS=50
WORKER_TIMEOUT=120
MAX_RETRIES=2
```

### Low Latency Setup
```env
WORKER_CONCURRENCY=2
WORKER_TIMEOUT=60
MAX_RETRIES=1
REDIS_SOCKET_TIMEOUT=2
```

### Development Setup
```env
WORKER_CONCURRENCY=1
WORKER_TIMEOUT=300
MAX_RETRIES=3
REDIS_URL=redis://localhost:6379/0
```
