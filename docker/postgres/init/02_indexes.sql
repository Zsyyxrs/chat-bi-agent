-- chat-bi-agent · 索引设计
-- 覆盖 BI 高频访问路径：
--   1) 客户视角：按 customer_id 切片
--   2) 机构视角：按 branch_id + dt 切片（最常见的 P1 取数）
--   3) 产品视角：按 product_id 聚合
--   4) 时间窗口：按 dt 做范围扫描

-- ---------- 维度表 ----------
CREATE INDEX idx_dim_customer_branch    ON dim_customer (branch_id);
CREATE INDEX idx_dim_customer_tier      ON dim_customer (customer_tier);
CREATE INDEX idx_dim_customer_manager   ON dim_customer (customer_manager_id);
CREATE INDEX idx_dim_customer_open_date ON dim_customer (open_date);

CREATE INDEX idx_dim_account_customer   ON dim_account (customer_id);
CREATE INDEX idx_dim_account_product    ON dim_account (product_id);
CREATE INDEX idx_dim_account_branch     ON dim_account (branch_id);
CREATE INDEX idx_dim_account_status     ON dim_account (status);

CREATE INDEX idx_dim_product_category   ON dim_product (product_category);
CREATE INDEX idx_dim_product_risk       ON dim_product (risk_level);
CREATE INDEX idx_dim_product_expire     ON dim_product (expire_date);

CREATE INDEX idx_dim_branch_parent      ON dim_branch (parent_branch_id);
CREATE INDEX idx_dim_branch_region      ON dim_branch (region);

-- ---------- 事实表（分区表的索引自动下推到每个分区） ----------

-- fct_transaction：按 (customer_id, dt) / (branch_id, dt) / (account_id, dt) 三条主路径
CREATE INDEX idx_fct_txn_customer_dt ON fct_transaction (customer_id, dt);
CREATE INDEX idx_fct_txn_branch_dt   ON fct_transaction (branch_id, dt);
CREATE INDEX idx_fct_txn_account_dt  ON fct_transaction (account_id, dt);
CREATE INDEX idx_fct_txn_product_dt  ON fct_transaction (product_id, dt);
CREATE INDEX idx_fct_txn_type_dt     ON fct_transaction (transaction_type, dt);

-- fct_balance_daily：日终余额最常按 (customer_id, dt) 取最新快照
CREATE INDEX idx_fct_bal_customer_dt ON fct_balance_daily (customer_id, dt);
CREATE INDEX idx_fct_bal_branch_dt   ON fct_balance_daily (branch_id, dt);
CREATE INDEX idx_fct_bal_product_dt  ON fct_balance_daily (product_id, dt);

-- fct_holding
CREATE INDEX idx_fct_hold_customer ON fct_holding (customer_id, snapshot_dt);
CREATE INDEX idx_fct_hold_product  ON fct_holding (product_id, snapshot_dt);
CREATE INDEX idx_fct_hold_branch   ON fct_holding (branch_id, snapshot_dt);

-- fct_risk_event
CREATE INDEX idx_fct_risk_customer ON fct_risk_event (customer_id, dt);
CREATE INDEX idx_fct_risk_type_dt  ON fct_risk_event (event_type, dt);
CREATE INDEX idx_fct_risk_branch   ON fct_risk_event (branch_id, dt);
CREATE INDEX idx_fct_risk_severity ON fct_risk_event (severity, dt);

-- fct_campaign_response
CREATE INDEX idx_fct_camp_customer  ON fct_campaign_response (customer_id, dt);
CREATE INDEX idx_fct_camp_campaign  ON fct_campaign_response (campaign_id, dt);
CREATE INDEX idx_fct_camp_response  ON fct_campaign_response (response_type, dt);
CREATE INDEX idx_fct_camp_product   ON fct_campaign_response (product_id, dt);

-- ---------- 事件埋雷锚定索引 ----------
CREATE INDEX IF NOT EXISTS idx_customer_branch_tier
    ON dim_customer(branch_id, customer_tier);
CREATE INDEX IF NOT EXISTS idx_account_anchor
    ON dim_account(is_event_anchor) WHERE is_event_anchor;
CREATE INDEX IF NOT EXISTS idx_customer_anchor
    ON dim_customer(is_event_anchor) WHERE is_event_anchor;

-- ---------- 写入完成后的初始化记号（健康检查可读） ----------
CREATE TABLE _meta_schema_version (
    version     VARCHAR(16)  PRIMARY KEY,
    description TEXT,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
INSERT INTO _meta_schema_version (version, description) VALUES
    ('0.1.0', '初始 schema：6 域 / 10 张表 / 分区 2025-01 ~ 2026-12');
