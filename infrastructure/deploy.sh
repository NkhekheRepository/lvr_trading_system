#!/bin/bash
# Deployment script for LVR Trading System on Linux EC2

set -e

echo "=== LVR Trading System Deployment ==="

# Update system
echo "Updating system packages..."
apt-get update -y
apt-get upgrade -y

# Install Python dependencies
echo "Installing Python dependencies..."
apt-get install -y python3 python3-pip python3-venv build-essential

# Install system dependencies
echo "Installing system dependencies..."
apt-get install -y postgresql postgresql-contrib redis-server git

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python packages
echo "Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Setup PostgreSQL
echo "Setting up PostgreSQL..."
sudo -u postgres psql -c "CREATE USER trading_user WITH PASSWORD 'trading_password';"
sudo -u postgres psql -c "CREATE DATABASE trading_system OWNER trading_user;"
sudo -u postgres psql -d trading_system -f infrastructure/setup_db.sql

# Setup Redis
echo "Setting up Redis..."
./infrastructure/setup_redis.sh

# Install systemd service
echo "Installing systemd service..."
cp infrastructure/systemd/lvr-trading.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable lvr-trading

# Create logs directory
mkdir -p logs

echo "=== Deployment Complete ==="
echo ""
echo "To start trading:"
echo "  source venv/bin/activate"
echo "  python app/main.py config/config.yaml"
echo ""
echo "Or use systemd:"
echo "  sudo systemctl start lvr-trading"
echo "  sudo systemctl status lvr-trading"
