import os
import logging
import redis
from rq import Queue, Worker, Connection
from typing import Dict, Any, Optional
import json
from datetime import datetime

logger = logging.getLogger(__name__)

class QueueManager:
    def __init__(self):
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
        self.redis_conn = None
        self.queue = None
        self.initialize_redis()

    def initialize_redis(self):
        """Initialize Redis connection and queue"""
        try:
            self.redis_conn = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )

            # Test connection
            self.redis_conn.ping()

            # Initialize queue
            self.queue = Queue('whatsapp_messages', connection=self.redis_conn)

            logger.info(f"Redis connection established: {self.redis_url}")

        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            self.redis_conn = None
            self.queue = None
        except Exception as e:
            logger.error(f"Error initializing Redis: {str(e)}")
            self.redis_conn = None
            self.queue = None

    def is_available(self) -> bool:
        """Check if Redis queue is available"""
        if not self.redis_conn or not self.queue:
            return False

        try:
            self.redis_conn.ping()
            return True
        except:
            return False

    def enqueue_message(self, message_data: Dict[str, Any], priority: str = 'normal') -> Optional[str]:
        """
        Enqueue a WhatsApp message for processing

        Args:
            message_data: The WhatsApp webhook message data
            priority: 'high', 'normal', or 'low'

        Returns:
            Job ID if successful, None if failed
        """
        if not self.is_available():
            logger.warning("Redis queue not available, cannot enqueue message")
            return None

        try:
            # Add metadata to the message
            enriched_data = {
                'message_data': message_data,
                'enqueued_at': datetime.now().isoformat(),
                'priority': priority,
                'retry_count': 0
            }

            # Determine job timeout based on priority
            timeout_map = {
                'high': 300,    # 5 minutes
                'normal': 180,  # 3 minutes
                'low': 120      # 2 minutes
            }

            job_timeout = timeout_map.get(priority, 180)

            # Enqueue the job
            job = self.queue.enqueue(
                'worker.process_whatsapp_message',
                enriched_data,
                job_timeout=job_timeout,
                retry=3
            )

            logger.info(f"Message enqueued with job ID: {job.id}, priority: {priority}")
            return job.id

        except Exception as e:
            logger.error(f"Failed to enqueue message: {str(e)}")
            return None

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        if not self.is_available():
            return {
                'available': False,
                'error': 'Redis queue not available'
            }

        try:
            stats = {
                'available': True,
                'queued_jobs': len(self.queue),
                'failed_jobs': len(self.queue.failed_job_registry),
                'started_jobs': len(self.queue.started_job_registry),
                'finished_jobs': len(self.queue.finished_job_registry),
                'workers_count': len(Worker.all(connection=self.redis_conn))
            }

            return stats

        except Exception as e:
            logger.error(f"Failed to get queue stats: {str(e)}")
            return {
                'available': False,
                'error': str(e)
            }

    def clear_failed_jobs(self):
        """Clear failed jobs from the queue"""
        if not self.is_available():
            return False

        try:
            self.queue.failed_job_registry.requeue()
            logger.info("Failed jobs cleared and requeued")
            return True
        except Exception as e:
            logger.error(f"Failed to clear failed jobs: {str(e)}")
            return False

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific job"""
        if not self.is_available():
            return None

        try:
            from rq.job import Job
            job = Job.fetch(job_id, connection=self.redis_conn)

            return {
                'id': job.id,
                'status': job.get_status(),
                'created_at': job.created_at.isoformat() if job.created_at else None,
                'started_at': job.started_at.isoformat() if job.started_at else None,
                'ended_at': job.ended_at.isoformat() if job.ended_at else None,
                'result': job.result,
                'exc_info': job.exc_info
            }
        except Exception as e:
            logger.error(f"Failed to get job status: {str(e)}")
            return None

# Global queue manager instance
queue_manager = QueueManager()
