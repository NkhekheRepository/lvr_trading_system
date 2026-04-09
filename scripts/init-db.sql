-- Initialize trading_events database

-- Events table for event sourcing
CREATE TABLE IF NOT EXISTS events (
    offset BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    symbol VARCHAR(32),
    timestamp BIGINT NOT NULL,
    sequence BIGINT NOT NULL,
    version INTEGER DEFAULT 1,
    payload JSONB NOT NULL,
    source VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_offset ON events(offset);

-- State snapshots for replay
CREATE TABLE IF NOT EXISTS state_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_id VARCHAR(64) UNIQUE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    offset BIGINT NOT NULL,
    state_type VARCHAR(32) NOT NULL,
    state_data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON state_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_offset ON state_snapshots(offset);

-- Positions table
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(32) UNIQUE NOT NULL,
    quantity DOUBLE PRECISION DEFAULT 0,
    avg_entry_price DOUBLE PRECISION DEFAULT 0,
    unrealized_pnl DOUBLE PRECISION DEFAULT 0,
    realized_pnl DOUBLE PRECISION DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW(),
    version INTEGER DEFAULT 1
);

-- Orders table for tracking
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(64) PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION,
    filled_quantity DOUBLE PRECISION DEFAULT 0,
    avg_fill_price DOUBLE PRECISION DEFAULT 0,
    status VARCHAR(16) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

-- Portfolio snapshots
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    total_value DOUBLE PRECISION NOT NULL,
    cash DOUBLE PRECISION NOT NULL,
    unrealized_pnl DOUBLE PRECISION DEFAULT 0,
    realized_pnl DOUBLE PRECISION DEFAULT 0,
    drawdown_pct DOUBLE PRECISION DEFAULT 0,
    leverage DOUBLE PRECISION DEFAULT 1,
    positions JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_timestamp ON portfolio_snapshots(timestamp);

-- Alerts log
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    alert_id VARCHAR(64) UNIQUE NOT NULL,
    severity VARCHAR(16) NOT NULL,
    category VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    source_module VARCHAR(64),
    trace_id VARCHAR(64),
    acknowledged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

-- Processed events for idempotency
CREATE TABLE IF NOT EXISTS processed_events (
    event_id VARCHAR(64) PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT NOW()
);

-- Retention: Archive old events (run periodically)
CREATE OR REPLACE FUNCTION archive_old_events()
RETURNS void AS $$
BEGIN
    -- Move events older than 90 days to archive
    INSERT INTO events_archive
    SELECT * FROM events
    WHERE timestamp < EXTRACT(EPOCH FROM (NOW() - INTERVAL '90 days'))::BIGINT * 1000;
    
    DELETE FROM events
    WHERE timestamp < EXTRACT(EPOCH FROM (NOW() - INTERVAL '90 days'))::BIGINT * 1000;
END;
$$ LANGUAGE plpgsql;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;
