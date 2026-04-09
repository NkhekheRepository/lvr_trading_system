# Deployment Guide

Complete guide for deploying the LVR Trading System on Linux EC2.

## Prerequisites

- Linux EC2 instance (Ubuntu 20.04+ recommended)
- Python 3.10+
- 2GB RAM minimum (4GB recommended)
- 20GB SSD minimum

---

## Quick Deployment

```bash
# Clone repository
git clone https://github.com/NkhekheRepository/lvr_trading_system.git
cd lvr_trading_system

# Run deployment script
chmod +x infrastructure/deploy.sh
sudo ./infrastructure/deploy.sh
```

---

## Manual Deployment

### Step 1: System Dependencies

```bash
# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install dependencies
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    postgresql \
    postgresql-contrib \
    redis-server \
    git \
    build-essential
```

### Step 2: Python Environment

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### Step 3: PostgreSQL Setup

```bash
# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database and user
sudo -u postgres psql << EOF
CREATE USER trading_user WITH PASSWORD 'your_secure_password';
CREATE DATABASE trading_system OWNER trading_user;
GRANT ALL PRIVILEGES ON DATABASE trading_system TO trading_user;
EOF

# Run schema
psql -U trading_user -d trading_system -f infrastructure/setup_db.sql

# Configure authentication
sudo nano /etc/postgresql/14/main/pg_hba.conf
# Change: local all all peer
# To: local all all md5

sudo systemctl restart postgresql
```

### Step 4: Redis Setup

```bash
# Start Redis
sudo systemctl start redis-server
sudo systemctl enable redis-server

# Verify
redis-cli ping
# Should return: PONG

# Optional: Configure Redis
sudo nano /etc/redis/redis.conf
# Set: maxmemory 256mb
# Set: maxmemory-policy allkeys-lru
```

### Step 5: Configuration

```bash
# Copy environment file
cp .env.example .env

# Edit with your settings
nano .env
```

**Required Environment Variables:**

```bash
# Execution Mode
LVR_EXECUTION_MODE=SIM

# Database
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=trading_system
PG_USER=trading_user
PG_PASSWORD=your_secure_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

### Step 6: Systemd Service

```bash
# Copy service file
sudo cp infrastructure/systemd/lvr-trading.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable service
sudo systemctl enable lvr-trading

# Create logs directory
mkdir -p logs
chmod 755 logs
```

---

## Running the System

### Option 1: Direct Execution

```bash
# Activate environment
source venv/bin/activate

# Run in simulation mode
python app/main.py config/config.yaml

# Run in paper trading mode
export LVR_EXECUTION_MODE=PAPER
python app/main.py config/config.yaml

# Run with custom config
python app/main.py /path/to/config.yaml
```

### Option 2: Systemd Service

```bash
# Start
sudo systemctl start lvr-trading

# Stop
sudo systemctl stop lvr-trading

# Status
sudo systemctl status lvr-trading

# View logs
sudo journalctl -u lvr-trading -f
```

### Option 3: Screen/Tmux

```bash
# Create screen session
screen -S trading

# Run
source venv/bin/activate
python app/main.py

# Detach: Ctrl+A, D

# Reattach
screen -r trading
```

---

## EC2-Specific Setup

### Security Groups

**Inbound Rules:**

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | Your IP | SSH |
| 5432 | TCP | 127.0.0.1 | PostgreSQL (local only) |
| 6379 | TCP | 127.0.0.1 | Redis (local only) |

**Important:** Never expose PostgreSQL or Redis to public internet.

### Instance Sizing

| Workload | Instance Type | RAM | CPU |
|----------|--------------|-----|-----|
| Development/Backtesting | t3.medium | 4GB | 2 |
| Production (light) | t3.large | 8GB | 2 |
| Production (heavy) | t3.xlarge | 16GB | 4 |

### Storage

- **Root volume:** 20GB minimum
- **Data volume:** 50GB+ for tick data storage
- Use SSD (gp3) for performance

### Monitoring

```bash
# Set up CloudWatch monitoring
sudo apt-get install -y amazon-cloudwatch-agent

# Configure
sudo nano /opt/aws/amazon-cloudwatch-agent/bin/config.json
```

---

## Binance API Setup

### Paper Trading (Testnet)

1. Create Binance testnet account: https://testnet.binancefuture.com
2. Generate API keys
3. Set in `.env`:
```bash
BINANCE_API_KEY=your_testnet_api_key
BINANCE_API_SECRET=your_testnet_secret
BINANCE_TESTNET=true
```

### Live Trading

1. Create Binance Futures account
2. Generate API keys with trading permission
3. Set in `.env`:
```bash
BINANCE_API_KEY=your_live_api_key
BINANCE_API_SECRET=your_live_secret
BINANCE_TESTNET=false
```

**Security Warning:** Live trading requires explicit confirmation:
```bash
export LVR_LIVE_CONFIRMED=true
```

---

## PostgreSQL Maintenance

### Backup

```bash
# Daily backup script
#!/bin/bash
DATE=$(date +%Y%m%d)
pg_dump -U trading_user trading_system > /backups/trading_$DATE.sql
```

### Restore

```bash
# Restore from backup
psql -U trading_user -d trading_system < /backups/trading_20240101.sql
```

### Connection Pooling

For high-frequency trading, use PgBouncer:

```bash
sudo apt-get install -y pgbouncer

sudo nano /etc/pgbouncer/pgbouncer.ini
# [databases]
# trading_system = host=127.0.0.1 port=5432 dbname=trading_system

# [pgbouncer]
# pool_mode = transaction
# max_client_conn = 100
# default_pool_size = 20

sudo systemctl restart pgbouncer
```

---

## Redis Configuration

### Persistence

```conf
# /etc/redis/redis.conf

# RDB snapshots
save 900 1
save 300 100
save 60 10000

# AOF
appendonly yes
appendfsync everysec

# Memory
maxmemory 256mb
maxmemory-policy allkeys-lru
```

### Cluster Mode (Optional)

For high availability:

```bash
# Install Redis Cluster
redis-cli --cluster create 127.0.0.1:7000 127.0.0.1:7001 127.0.0.1:7002
```

---

## Health Checks

### PostgreSQL

```bash
psql -U trading_user -d trading_system -c "SELECT 1;"
```

### Redis

```bash
redis-cli ping
# Should return: PONG
```

### System

```bash
# Check service status
sudo systemctl status lvr-trading

# Check logs
sudo journalctl -u lvr-trading --since "1 hour ago"

# Check resource usage
htop
```

---

## Troubleshooting

### PostgreSQL Connection Failed

```bash
# Check status
sudo systemctl status postgresql

# Check logs
sudo journalctl -u postgresql --since "1 hour ago"

# Verify credentials
psql -U trading_user -d trading_system -c "SELECT current_user;"
```

### Redis Connection Failed

```bash
# Check status
sudo systemctl status redis-server

# Check memory
redis-cli info memory

# Clear cache if needed
redis-cli FLUSHALL
```

### Import Errors

```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall

# Check Python version
python3 --version  # Should be 3.10+
```

### Permission Errors

```bash
# Fix ownership
sudo chown -R ubuntu:ubuntu /home/ubuntu/lvr_trading_system

# Fix logs directory
mkdir -p logs
chmod 755 logs
```

---

## Production Checklist

- [ ] PostgreSQL with regular backups
- [ ] Redis with persistence enabled
- [ ] Systemd service installed and enabled
- [ ] CloudWatch monitoring configured
- [ ] Log rotation configured
- [ ] Security groups properly restricted
- [ ] API keys secured in .env
- [ ] Live trading confirmation explicitly set
- [ ] Alert webhooks configured
- [ ] Rollback plan documented

---

## Scaling

### Horizontal Scaling

For multiple trading instances:

```
┌─────────────────────────────────────────┐
│              Load Balancer               │
└─────────────────────────────────────────┘
         │              │
    ┌────┴────┐    ┌────┴────┐
    │ Instance│    │ Instance│
    │    1    │    │    2    │
    └────┬────┘    └────┬────┘
         │              │
         └──────┬───────┘
                │
         ┌─────┴─────┐
         │ PostgreSQL │
         │   (Shared) │
         └───────────┘
```

### Database Connection Pooling

Use PgBouncer to handle multiple connections efficiently.

### Redis Pub/Sub

For real-time state synchronization:
```python
# Example
redis.publish('trading:events', json.dumps(event))
```
