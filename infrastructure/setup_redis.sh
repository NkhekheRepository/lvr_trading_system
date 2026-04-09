#!/bin/bash
# Redis setup script for LVR Trading System

set -e

REDIS_VERSION="7.2.0"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"

echo "Setting up Redis $REDIS_VERSION..."

# Install Redis if not present
if ! command -v redis-server &> /dev/null; then
    echo "Installing Redis..."
    apt-get update
    apt-get install -y build-essential tcl
    
    cd /tmp
    wget "http://download.redis.io/redis-$REDIS_VERSION.tar.gz"
    tar xzf "redis-$REDIS_VERSION.tar.gz"
    cd "redis-$REDIS_VERSION"
    make -j$(nproc)
    make install
    cd /
    rm -rf /tmp/redis-$REDIS_VERSION*
fi

# Create Redis config
mkdir -p /etc/redis /var/lib/redis /var/log/redis
chown redis:redis /var/lib/redis /var/log/redis

cat > /etc/redis/redis.conf << EOF
bind 127.0.0.1
port $REDIS_PORT
protected-mode yes
daemonize no
supervised systemd
pidfile /var/run/redis/redis-server.pid
loglevel notice
logfile /var/log/redis/redis-server.log

# Persistence
save 900 1
save 300 100
save 60 10000
stop-writes-on-bgsave-error yes
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
dir /var/lib/redis

# Security
$(if [ -n "$REDIS_PASSWORD" ]; then echo "requirepass $REDIS_PASSWORD"; fi)

# Memory
maxmemory 256mb
maxmemory-policy allkeys-lru

# Append only file
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec
EOF

# Start Redis
redis-server /etc/redis/redis.conf

echo "Redis started on port $REDIS_PORT"

# Test connection
redis-cli ping

echo "Redis setup complete!"
