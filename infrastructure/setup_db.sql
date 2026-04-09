-- LVR Trading System - PostgreSQL Schema
-- Run as: psql -U postgres -d trading_system -f setup_db.sql

-- Create database if not exists
-- CREATE DATABASE trading_system;

-- Positions table (authoritative state)
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE,
    quantity DECIMAL(18,8) NOT NULL DEFAULT 0,
    entry_price DECIMAL(18,8) NOT NULL DEFAULT 0,
    current_price DECIMAL(18,8) NOT NULL DEFAULT 0,
    unrealized_pnl DECIMAL(18,8) NOT NULL DEFAULT 0,
    realized_pnl DECIMAL(18,8) NOT NULL DEFAULT 0,
    entry_timestamp BIGINT,
    updated_at TIMESTAMP DEFAULT NOW(),
    version INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);

-- Event log (append-only)
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    trace_id VARCHAR(64),
    symbol VARCHAR(20),
    order_id VARCHAR(64),
    payload JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- Orders table
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(64) PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    quantity DECIMAL(18,8) NOT NULL,
    filled_qty DECIMAL(18,8) DEFAULT 0,
    price DECIMAL(18,8),
    avg_fill_price DECIMAL(18,8) DEFAULT 0,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

-- Fills table
CREATE TABLE IF NOT EXISTS fills (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    quantity DECIMAL(18,8) NOT NULL,
    price DECIMAL(18,8) NOT NULL,
    fee DECIMAL(18,8) DEFAULT 0,
    slippage DECIMAL(18,8) DEFAULT 0,
    latency_ms DECIMAL(10,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_symbol ON fills(symbol);

-- Portfolio snapshots
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id BIGSERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    capital DECIMAL(18,8) NOT NULL,
    exposure DECIMAL(18,8) NOT NULL,
    leverage DECIMAL(8,4) NOT NULL,
    realized_pnl DECIMAL(18,8) NOT NULL,
    unrealized_pnl DECIMAL(18,8) NOT NULL,
    drawdown DECIMAL(8,6) NOT NULL,
    positions JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_ts ON portfolio_snapshots(timestamp);

-- Risk state
CREATE TABLE IF NOT EXISTS risk_state (
    id SERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    leverage DECIMAL(8,4) NOT NULL,
    drawdown DECIMAL(8,6) NOT NULL,
    daily_loss DECIMAL(8,6) NOT NULL,
    consecutive_losses INTEGER DEFAULT 0,
    protection_level INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Alerts log
CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    alert_id VARCHAR(64) UNIQUE NOT NULL,
    severity VARCHAR(20) NOT NULL,
    category VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    source_module VARCHAR(50),
    trace_id VARCHAR(64),
    acknowledged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

-- Metrics snapshots
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id BIGSERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    fill_rate DECIMAL(6,4),
    avg_slippage DECIMAL(18,8),
    edge_error DECIMAL(18,8),
    latency_ms DECIMAL(10,2),
    drawdown DECIMAL(8,6),
    protection_level INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Function to update timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
DROP TRIGGER IF EXISTS update_positions_updated_at ON positions;
CREATE TRIGGER update_positions_updated_at
    BEFORE UPDATE ON positions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_orders_updated_at ON orders;
CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
