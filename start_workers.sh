#!/bin/bash

# Worker management script for WhatsApp AI Agent
# Usage: ./start_workers.sh [number_of_workers] [action]
# Actions: start, stop, restart, status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_SCRIPT="$SCRIPT_DIR/worker.py"
PIDFILE_DIR="$SCRIPT_DIR/pids"
LOG_DIR="$SCRIPT_DIR/logs"

# Default number of workers
DEFAULT_WORKERS=3

# Create directories if they don't exist
mkdir -p "$PIDFILE_DIR"
mkdir -p "$LOG_DIR"

# Function to start workers
start_workers() {
    local num_workers=${1:-$DEFAULT_WORKERS}
    echo "Starting $num_workers worker processes..."

    for i in $(seq 1 $num_workers); do
        local pidfile="$PIDFILE_DIR/worker_$i.pid"
        local logfile="$LOG_DIR/worker_$i.log"

        if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "Worker $i is already running (PID: $(cat "$pidfile"))"
            continue
        fi

        echo "Starting worker $i..."
        nohup python3 "$WORKER_SCRIPT" > "$logfile" 2>&1 &
        local worker_pid=$!
        echo $worker_pid > "$pidfile"
        echo "Worker $i started with PID: $worker_pid"
    done

    echo "All workers started!"
}

# Function to stop workers
stop_workers() {
    echo "Stopping all worker processes..."

    for pidfile in "$PIDFILE_DIR"/worker_*.pid; do
        if [ -f "$pidfile" ]; then
            local pid=$(cat "$pidfile")
            local worker_num=$(basename "$pidfile" .pid | sed 's/worker_//')

            if kill -0 "$pid" 2>/dev/null; then
                echo "Stopping worker $worker_num (PID: $pid)..."
                kill "$pid"

                # Wait for process to stop
                local timeout=10
                while kill -0 "$pid" 2>/dev/null && [ $timeout -gt 0 ]; do
                    sleep 1
                    timeout=$((timeout - 1))
                done

                if kill -0 "$pid" 2>/dev/null; then
                    echo "Force killing worker $worker_num..."
                    kill -9 "$pid"
                fi

                echo "Worker $worker_num stopped"
            else
                echo "Worker $worker_num was not running"
            fi

            rm -f "$pidfile"
        fi
    done

    echo "All workers stopped!"
}

# Function to show worker status
show_status() {
    echo "Worker Status:"
    echo "=============="

    local running_count=0

    for pidfile in "$PIDFILE_DIR"/worker_*.pid; do
        if [ -f "$pidfile" ]; then
            local pid=$(cat "$pidfile")
            local worker_num=$(basename "$pidfile" .pid | sed 's/worker_//')

            if kill -0 "$pid" 2>/dev/null; then
                echo "Worker $worker_num: RUNNING (PID: $pid)"
                running_count=$((running_count + 1))
            else
                echo "Worker $worker_num: STOPPED (stale PID file)"
                rm -f "$pidfile"
            fi
        fi
    done

    if [ $running_count -eq 0 ]; then
        echo "No workers are currently running"
    else
        echo ""
        echo "Total running workers: $running_count"
    fi

    # Show Redis connection status
    echo ""
    echo "Redis Status:"
    echo "============="
    if python3 -c "import redis; r=redis.from_url('${REDIS_URL:-redis://localhost:6379/0}'); r.ping(); print('Redis: CONNECTED')" 2>/dev/null; then
        echo "Redis: CONNECTED"
    else
        echo "Redis: DISCONNECTED"
    fi
}

# Function to restart workers
restart_workers() {
    local num_workers=${1:-$DEFAULT_WORKERS}
    echo "Restarting workers..."
    stop_workers
    sleep 2
    start_workers "$num_workers"
}

# Function to show logs
show_logs() {
    local worker_num=${1:-""}

    if [ -n "$worker_num" ]; then
        local logfile="$LOG_DIR/worker_$worker_num.log"
        if [ -f "$logfile" ]; then
            echo "Showing logs for worker $worker_num:"
            tail -f "$logfile"
        else
            echo "Log file for worker $worker_num not found"
        fi
    else
        echo "Available log files:"
        for logfile in "$LOG_DIR"/worker_*.log; do
            if [ -f "$logfile" ]; then
                local worker_num=$(basename "$logfile" .log | sed 's/worker_//')
                echo "  Worker $worker_num: $logfile"
            fi
        done
        echo ""
        echo "Usage: $0 logs [worker_number]"
    fi
}

# Main script logic
case "${2:-start}" in
    start)
        start_workers "${1:-$DEFAULT_WORKERS}"
        ;;
    stop)
        stop_workers
        ;;
    restart)
        restart_workers "${1:-$DEFAULT_WORKERS}"
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "$1"
        ;;
    *)
        echo "Usage: $0 [number_of_workers] [start|stop|restart|status|logs]"
        echo ""
        echo "Examples:"
        echo "  $0 3 start     # Start 3 workers"
        echo "  $0 stop        # Stop all workers"
        echo "  $0 restart     # Restart workers with default count"
        echo "  $0 status      # Show worker status"
        echo "  $0 logs 1      # Show logs for worker 1"
        echo "  $0 logs        # List available log files"
        exit 1
        ;;
esac
