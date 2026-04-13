#!/usr/bin/env bash
#
# start_trading_system.sh — Trading System Startup Script
#
# Starts daemon agents (executor, portfolio_manager, watcher) with proper logging.
# Screener and supervisor are managed by cron jobs.
# Each daemon agent runs in the background with output logged to ~/trading-system/logs/
#
# Usage:
#   ./start_trading_system.sh              # Start all agents
#   ./start_trading_system.sh --stop       # Stop all agents
#   ./start_trading_system.sh --status     # Check agent status
#   ./start_trading_system.sh --restart    # Stop then start
#
# Prerequisites:
#   - Docker containers running (Redis + TimescaleDB)
#   - Environment variables set in ~/.trading_env
#   - Python packages installed: alpaca-py, redis, psycopg2-binary, numpy, pytz, requests

set -euo pipefail

# ── Configuration ───────────────────────────────────────────

TRADING_DIR="${HOME}/trading-system"
SCRIPTS_DIR="${TRADING_DIR}/scripts"
LOG_DIR="${TRADING_DIR}/logs"
PID_DIR="${TRADING_DIR}/pids"
ENV_FILE="${HOME}/.trading_env"

AGENTS=("executor" "portfolio_manager" "watcher")
DOCKER_CONTAINERS=("trading_redis:redis" "trading_timescaledb:timescaledb" "trading_dashboard:dashboard")

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── Helper Functions ────────────────────────────────────────

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC}  $1"; }

# ── Docker Log Redirectors ───────────────────────────────────

start_docker_log_redirectors() {
    log_step "Starting docker log redirectors..."
    for entry in "${DOCKER_CONTAINERS[@]}"; do
        local container="${entry%%:*}"
        local name="${entry##*:}"
        local log_file="${LOG_DIR}/docker_${name}.log"
        local pid_file="${PID_DIR}/docker_${name}.pid"

        # Kill stale redirector if running
        if [ -f "$pid_file" ]; then
            local stale_pid
            stale_pid=$(cat "$pid_file" 2>/dev/null) && kill "$stale_pid" 2>/dev/null || true
            rm -f "$pid_file"
        fi

        if docker container inspect "$container" > /dev/null 2>&1; then
            nohup sudo docker logs --follow --since "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$container" >> "$log_file" 2>&1 &
            echo $! > "$pid_file"
            log_info "docker log redirector: ${container} → docker_${name}.log"
        else
            log_warn "Container ${container} not running — skipping log redirector"
        fi
    done
}

stop_docker_log_redirectors() {
    for entry in "${DOCKER_CONTAINERS[@]}"; do
        local name="${entry##*:}"
        local pid_file="${PID_DIR}/docker_${name}.pid"
        if [ -f "$pid_file" ]; then
            local stale_pid
            stale_pid=$(cat "$pid_file" 2>/dev/null) && sudo kill "$stale_pid" 2>/dev/null || true
            rm -f "$pid_file"
            log_info "stopped docker log redirector: docker_${name}.log"
        fi
    done
}

# ── Log Tailing ──────────────────────────────────────────────

tail_logs() {
    local DATE_SUFFIX
    DATE_SUFFIX=$(date '+%Y-%m-%d')

    local log_executor="${LOG_DIR}/executor_${DATE_SUFFIX}.log"
    local log_pm="${LOG_DIR}/portfolio_manager_${DATE_SUFFIX}.log"
    local log_watcher="${LOG_DIR}/watcher_${DATE_SUFFIX}.log"
    local log_screener="${LOG_DIR}/screener.log"
    local log_supervisor="${LOG_DIR}/supervisor.log"

    if command -v tmux &>/dev/null; then
        tmux kill-session -t trading-logs 2>/dev/null || true
        tmux new-session -d -s trading-logs -x 220 -y 50

        # Window 1: daemon agents
        tmux send-keys -t trading-logs \
            "tail -f '${log_executor}' 2>/dev/null | sed 's/^/[executor] /'" Enter
        tmux split-window -v -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_pm}' 2>/dev/null | sed 's/^/[portfolio_manager] /'" Enter
        tmux split-window -v -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_watcher}' 2>/dev/null | sed 's/^/[watcher] /'" Enter

        # Window 2: cron agents
        tmux new-window -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_screener}' 2>/dev/null | sed 's/^/[screener] /'" Enter
        tmux split-window -h -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_supervisor}' 2>/dev/null | sed 's/^/[supervisor] /'" Enter

        tmux select-window -t trading-logs:0
        log_info "tmux session 'trading-logs' created. Attaching..."
        tmux attach-session -t trading-logs
    else
        log_info "tmux not found — combined tail:"
        tail -f \
            "${log_executor}" \
            "${log_pm}" \
            "${log_watcher}" \
            "${log_screener}" \
            "${log_supervisor}" 2>/dev/null
    fi
}

check_env() {
    if [ ! -f "$ENV_FILE" ]; then
        log_error "Environment file not found: ${ENV_FILE}"
        echo ""
        echo "Create it with:"
        echo "  cat > ~/.trading_env << 'EOF'"
        echo "  export ALPACA_API_KEY=\"your-paper-key\""
        echo "  export ALPACA_SECRET_KEY=\"your-paper-secret\""
        echo "  export TSDB_PASSWORD=\"your-db-password\""
        echo "  export TELEGRAM_BOT_TOKEN=\"your-bot-token\"    # optional"
        echo "  export TELEGRAM_CHAT_ID=\"your-chat-id\"        # optional"
        echo "  EOF"
        echo "  chmod 600 ~/.trading_env"
        exit 1
    fi

    # shellcheck source=/dev/null
    source "$ENV_FILE"

    if [ -z "${ALPACA_API_KEY:-}" ] || [ -z "${ALPACA_SECRET_KEY:-}" ]; then
        log_error "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in ${ENV_FILE}"
        exit 1
    fi

    if [ -z "${TSDB_PASSWORD:-}" ]; then
        log_warn "TSDB_PASSWORD not set — database logging will fail"
    fi

    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        log_warn "Telegram not configured — notifications will print to console only"
    fi
}

check_infrastructure() {
    log_step "Checking infrastructure..."

    # Check Redis
    if redis-cli ping > /dev/null 2>&1; then
        log_info "Redis: running"
    else
        log_error "Redis not reachable. Run: cd ~/trading-system && sudo docker compose up -d"
        exit 1
    fi

    # Check TimescaleDB
    if PGPASSWORD="${TSDB_PASSWORD:-changeme}" psql -h localhost -U trader -d trading -c "SELECT 1" > /dev/null 2>&1; then
        log_info "TimescaleDB: running"
    else
        log_warn "TimescaleDB not reachable — trade logging will fail"
    fi

    # Check Python dependencies
    if python3 -c "import alpaca, redis, numpy" 2>/dev/null; then
        log_info "Python dependencies: OK"
    else
        log_error "Missing Python packages. Run: python3 -m pip install alpaca-py redis psycopg2-binary numpy pytz requests"
        exit 1
    fi

    # Check that agent scripts exist
    for agent in "${AGENTS[@]}"; do
        if [ ! -f "${TRADING_DIR}/skills/${agent}/${agent}.py" ]; then
            log_error "Agent script not found: ${TRADING_DIR}/skills/${agent}/${agent}.py"
            exit 1
        fi
    done

    log_info "All infrastructure checks passed"
}

# ── Start ───────────────────────────────────────────────────

start_system() {
    echo "════════════════════════════════════════════════════════"
    echo "  TRADING SYSTEM STARTUP"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "════════════════════════════════════════════════════════"

    check_env
    check_infrastructure

    # Create directories
    mkdir -p "$LOG_DIR" "$PID_DIR"

    # Rotate logs (keep last 30 days)
    find "$LOG_DIR" -name "*.log*" -mtime +30 -delete 2>/dev/null || true

    DATE_SUFFIX=$(date '+%Y-%m-%d')

    # Step 1: Run executor startup verification
    log_step "Running executor startup verification..."
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    export PYTHONPATH="${SCRIPTS_DIR}:${PYTHONPATH:-}"
    cd "$TRADING_DIR"

    if ! python3 skills/executor/executor.py --verify 2>&1 | tee "${LOG_DIR}/verify_${DATE_SUFFIX}.log"; then
        log_error "Startup verification failed — aborting"
        exit 1
    fi
    echo ""

    # Step 2: Start agents in order (executor first, then supervisor, then the rest)
    for agent in "${AGENTS[@]}"; do
        # Check if already running
        if [ -f "${PID_DIR}/${agent}.pid" ]; then
            existing_pid=$(cat "${PID_DIR}/${agent}.pid")
            if kill -0 "$existing_pid" 2>/dev/null; then
                log_warn "${agent} already running (PID ${existing_pid}) — skipping"
                continue
            else
                rm -f "${PID_DIR}/${agent}.pid"
            fi
        fi

        log_step "Starting ${agent}..."

        # Start agent in background with logging
        nohup python3 -u "${TRADING_DIR}/skills/${agent}/${agent}.py" --daemon \
            >> "${LOG_DIR}/${agent}_${DATE_SUFFIX}.log" 2>&1 &

        agent_pid=$!
        echo "$agent_pid" > "${PID_DIR}/${agent}.pid"

        # Brief pause to let it initialize
        sleep 2

        # Verify it's still running
        if kill -0 "$agent_pid" 2>/dev/null; then
            log_info "${agent} started (PID ${agent_pid})"
        else
            log_error "${agent} failed to start — check ${LOG_DIR}/${agent}_${DATE_SUFFIX}.log"
        fi
    done

    echo ""
    echo "════════════════════════════════════════════════════════"
    log_info "Daemon agents started. Logs in ${LOG_DIR}/"
    echo ""
    start_docker_log_redirectors
    echo ""
    echo "  Monitor logs:    $0 --logs"
    echo "  Check status:    $0 --status"
    echo "  Stop system:     $0 --stop"
    echo "════════════════════════════════════════════════════════"
}

# ── Stop ────────────────────────────────────────────────────

stop_system() {
    echo "════════════════════════════════════════════════════════"
    echo "  TRADING SYSTEM SHUTDOWN"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "════════════════════════════════════════════════════════"

    # Stop in reverse order (screener/watcher first, executor last)
    for agent in $(echo "${AGENTS[@]}" | tr ' ' '\n' | tac); do
        pid_file="${PID_DIR}/${agent}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                log_step "Stopping ${agent} (PID ${pid})..."
                kill "$pid"
                # Wait up to 10 seconds for graceful shutdown
                for i in $(seq 1 10); do
                    if ! kill -0 "$pid" 2>/dev/null; then
                        break
                    fi
                    sleep 1
                done
                # Force kill if still running
                if kill -0 "$pid" 2>/dev/null; then
                    log_warn "Force killing ${agent}..."
                    kill -9 "$pid" 2>/dev/null || true
                fi
                log_info "${agent} stopped"
            else
                log_warn "${agent} was not running"
            fi
            rm -f "$pid_file"
        else
            log_warn "${agent}: no PID file found"
        fi
    done

    echo ""
    log_info "Daemon agents stopped"
    stop_docker_log_redirectors

    # Note: server-side stop-losses on Alpaca remain active
    echo ""
    log_info "Server-side stop-loss orders remain active on Alpaca"
    log_info "Open positions are protected even with agents offline"
}

# ── Status ──────────────────────────────────────────────────

check_status() {
    echo "════════════════════════════════════════════════════════"
    echo "  TRADING SYSTEM STATUS"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "════════════════════════════════════════════════════════"

    # shellcheck source=/dev/null
    [ -f "$ENV_FILE" ] && source "$ENV_FILE"

    all_running=true

    for agent in "${AGENTS[@]}"; do
        pid_file="${PID_DIR}/${agent}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                # Get memory usage
                mem=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.1f", $1/1024}')
                echo -e "  ${GREEN}●${NC} ${agent}  PID=${pid}  MEM=${mem}MB"
            else
                echo -e "  ${RED}●${NC} ${agent}  DEAD (stale PID ${pid})"
                all_running=false
            fi
        else
            echo -e "  ${RED}●${NC} ${agent}  NOT RUNNING"
            all_running=false
        fi
    done

    echo ""

    # Redis state
    if redis-cli ping > /dev/null 2>&1; then
        equity=$(redis-cli GET trading:simulated_equity 2>/dev/null || echo "N/A")
        drawdown=$(redis-cli GET trading:drawdown 2>/dev/null || echo "N/A")
        status=$(redis-cli GET trading:system_status 2>/dev/null || echo "N/A")
        daily_pnl=$(redis-cli GET trading:daily_pnl 2>/dev/null || echo "N/A")
        positions=$(redis-cli GET trading:positions 2>/dev/null || echo "{}")
        num_positions=$(echo "$positions" | python3 -c "import sys,json; print(len(json.loads(sys.stdin.read() or '{}')))" 2>/dev/null || echo "?")

        echo "  System status:     ${status}"
        echo "  Simulated equity:  \$${equity}"
        echo "  Drawdown:          ${drawdown}%"
        echo "  Daily P&L:         \$${daily_pnl}"
        echo "  Open positions:    ${num_positions}"
    else
        echo "  Redis: NOT REACHABLE"
    fi

    echo ""
    if $all_running; then
        log_info "All daemon agents running"
    else
        log_warn "Some agents are not running"
    fi
}

# ── Main ────────────────────────────────────────────────────

case "${1:-start}" in
    --stop|-stop|stop)
        stop_system
        ;;
    --status|-status|status)
        check_status
        ;;
    --restart|-restart|restart)
        stop_system
        echo ""
        sleep 2
        start_system
        ;;
    --logs|-logs|logs)
        tail_logs
        ;;
    --help|-help|-h)
        echo "Usage: $0 [--start|--stop|--status|--restart|--logs|--help]"
        echo ""
        echo "  --start     Start daemon agents (default)"
        echo "  --stop      Stop daemon agents gracefully"
        echo "  --status    Show agent status and system state"
        echo "  --restart   Stop then start"
        echo "  --logs      Tail agent logs (tmux if available, else combined tail)"
        echo "  --help      Show this help"
        ;;
    *)
        start_system
        ;;
esac

# v1.0.0
