-- Hop 6: ownership of the table the write mapping targets.
CREATE TABLE order_events (
    id           BIGSERIAL PRIMARY KEY,
    order_ref    VARCHAR(64) NOT NULL,
    status       VARCHAR(32) NOT NULL,
    recorded_at  TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE INDEX idx_order_events_ref ON order_events (order_ref);
