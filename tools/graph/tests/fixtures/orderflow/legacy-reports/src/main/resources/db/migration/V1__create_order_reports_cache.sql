-- The reporting cache: a nightly ETL job OUTSIDE this repository copies the order
-- chain's event records into it under this component's own name. No config key, no
-- topic, no call joins the two tables — the bridge exists only in the ETL's cron
-- entry, which is exactly the coupling item 79's [[alias]] declaration carries.
CREATE TABLE order_reports_cache (
    id           BIGSERIAL PRIMARY KEY,
    order_ref    VARCHAR(64) NOT NULL,
    status       VARCHAR(32) NOT NULL,
    copied_at    TIMESTAMP   NOT NULL DEFAULT now()
);

CREATE INDEX idx_order_reports_cache_ref ON order_reports_cache (order_ref);
