-- Initialize TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create events table
CREATE TABLE IF NOT EXISTS events_raw (
    ts TIMESTAMPTZ NOT NULL,
    event_id UUID NOT NULL,
    source TEXT NOT NULL,
    metric DOUBLE PRECISION,
    status TEXT,
    user_id TEXT,
    attributes JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Convert to hypertable
SELECT create_hypertable('events_raw', 'ts', if_not_exists => TRUE);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_events_source_ts ON events_raw (source, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_id ON events_raw (user_id);
CREATE INDEX IF NOT EXISTS idx_events_status ON events_raw (status);
CREATE INDEX IF NOT EXISTS idx_events_attributes ON events_raw USING GIN (attributes);

-- Create aggregated metrics table
CREATE TABLE IF NOT EXISTS metrics_1min (
    ts TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    count_events BIGINT,
    avg_metric DOUBLE PRECISION,
    p95_metric DOUBLE PRECISION,
    p99_metric DOUBLE PRECISION,
    error_rate DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Convert to hypertable
SELECT create_hypertable('metrics_1min', 'ts', if_not_exists => TRUE);

-- Create index for metrics table
CREATE INDEX IF NOT EXISTS idx_metrics_source_ts ON metrics_1min (source, ts DESC);

-- Create anomalies table
CREATE TABLE IF NOT EXISTS anomalies (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    value DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    z_score DOUBLE PRECISION,
    description TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for anomalies
CREATE INDEX IF NOT EXISTS idx_anomalies_ts ON anomalies (ts DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_source ON anomalies (source);
CREATE INDEX IF NOT EXISTS idx_anomalies_resolved ON anomalies (resolved);

-- Create retention policies (optional - keep data for 30 days by default)
-- SELECT add_retention_policy('events_raw', INTERVAL '30 days');
-- SELECT add_retention_policy('metrics_1min', INTERVAL '90 days');

-- Create some sample views for common queries
CREATE OR REPLACE VIEW events_last_hour AS
SELECT *
FROM events_raw
WHERE ts >= NOW() - INTERVAL '1 hour'
ORDER BY ts DESC;

CREATE OR REPLACE VIEW metrics_last_24h AS
SELECT *
FROM metrics_1min
WHERE ts >= NOW() - INTERVAL '24 hours'
ORDER BY ts DESC;

CREATE OR REPLACE VIEW active_anomalies AS
SELECT *
FROM anomalies
WHERE resolved = FALSE
ORDER BY ts DESC;

-- Grant permissions (adjust as needed for production)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO admin;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO admin;
