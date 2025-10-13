import os
import sys
import logging
import asyncio
from typing import Dict, Any
from dotenv import load_dotenv

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from models import Database
from services import WhatsAppAPIService
from ai_agent import WhatsAppAIAgent

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [WORKER] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('worker.log')
    ]
)

logger = logging.getLogger(__name__)

# Initialize services (will be done once per worker process)
db = None
whatsapp_service = None
ai_agent = None

def initialize_services():
    """Initialize database and services for worker"""
    global db, whatsapp_service, ai_agent

    try:
        if not db:
            db = Database()
            logger.info("Worker: Database connection established")

        if not whatsapp_service:
            whatsapp_service = WhatsAppAPIService()
            logger.info("Worker: WhatsApp service initialized")

        if not ai_agent and db and whatsapp_service:
            ai_agent = WhatsAppAIAgent(db, whatsapp_service)
            logger.info("Worker: AI agent initialized")

        return True

    except Exception as e:
        logger.error(f"Worker: Failed to initialize services: {str(e)}")
        return False

def process_whatsapp_message(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a WhatsApp message (this function will be called by RQ workers)

    Args:
        job_data: Dictionary containing message_data and metadata

    Returns:
        Dictionary with processing results
    """
    worker_id = os.getpid()
    logger.info(f"Worker {worker_id}: Starting message processing")

    try:
        # Initialize services if not already done
        if not initialize_services():
            raise Exception("Failed to initialize worker services")

        # Extract message data
        message_data = job_data.get('message_data')
        enqueued_at = job_data.get('enqueued_at')
        priority = job_data.get('priority', 'normal')
        retry_count = job_data.get('retry_count', 0)

        if not message_data:
            raise ValueError("No message data provided")

        logger.info(f"Worker {worker_id}: Processing message with priority {priority}, retry count: {retry_count}")

        # Store message in database first
        if db and message_data.get('object') == 'whatsapp_business_account':
            try:
                db.save_message(message_data)
                logger.info(f"Worker {worker_id}: Message stored in database")
            except Exception as db_error:
                logger.error(f"Worker {worker_id}: Failed to store message: {str(db_error)}")
                # Continue processing even if DB storage fails

        # Process with AI agent
        response = None
        if ai_agent:
            try:
                # Create new event loop for this worker
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                response = loop.run_until_complete(ai_agent.process_message(message_data))

                loop.close()

                if response:
                    logger.info(f"Worker {worker_id}: AI agent processed message successfully")
                else:
                    logger.info(f"Worker {worker_id}: AI agent determined no response needed")

            except Exception as ai_error:
                logger.error(f"Worker {worker_id}: AI agent processing failed: {str(ai_error)}")
                raise ai_error

        # Return processing results
        result = {
            'success': True,
            'worker_id': worker_id,
            'response_sent': bool(response),
            'response_preview': response[:100] if response else None,
            'priority': priority,
            'retry_count': retry_count,
            'enqueued_at': enqueued_at
        }

        logger.info(f"Worker {worker_id}: Message processing completed successfully")
        return result

    except Exception as e:
        error_msg = f"Worker {worker_id}: Error processing message: {str(e)}"
        logger.error(error_msg)

        # Return error result
        return {
            'success': False,
            'error': str(e),
            'worker_id': worker_id,
            'priority': job_data.get('priority', 'normal'),
            'retry_count': job_data.get('retry_count', 0),
            'enqueued_at': job_data.get('enqueued_at')
        }

if __name__ == '__main__':
    """Run worker process"""
    import redis
    from rq import Worker, Connection

    # Initialize Redis connection
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    try:
        redis_conn = redis.from_url(redis_url, decode_responses=True)

        # Test connection
        redis_conn.ping()
        logger.info(f"Worker: Redis connection established: {redis_url}")

        # Initialize services once for this worker
        if initialize_services():
            logger.info("Worker: Services initialized successfully")
        else:
            logger.error("Worker: Failed to initialize services")
            sys.exit(1)

        # Start worker
        with Connection(redis_conn):
            worker = Worker(['whatsapp_messages'])
            logger.info("Worker: Starting to listen for jobs...")
            worker.work(with_scheduler=True)

    except redis.ConnectionError as e:
        logger.error(f"Worker: Failed to connect to Redis: {str(e)}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Worker: Shutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Worker: Unexpected error: {str(e)}")
        sys.exit(1)
