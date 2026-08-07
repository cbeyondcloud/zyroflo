-- Creates the table this worker owns.
CREATE TABLE restock_events (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A table this worker only reads. Named here, created elsewhere:
-- CREATE TABLE pantry_items (sku TEXT PRIMARY KEY);
