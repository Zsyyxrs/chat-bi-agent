-- chat-bi-agent · 行外阶段模拟银行数仓 schema
-- 6 个业务域 / 10 张表 / 中文 COMMENT 直供 NL2SQL Agent 拼 schema prompt
--
-- 命名规约：
--   dim_*   维度表（缓变，业务主键可读 e.g. CUST_000001）
--   fct_*   事实表（事实/流水，大表走分区）
--
-- 分区策略：
--   fct_transaction      按月分区 (RANGE on dt)
--   fct_balance_daily    按月分区 (RANGE on dt)
--   其余事实表 demo 阶段不分区，行内迁移时再加

SET client_encoding = 'UTF8';
SET timezone = 'Asia/Shanghai';

-- =============================================================
-- 1. dim_branch · 机构维度
-- =============================================================
CREATE TABLE dim_branch (
    branch_id        VARCHAR(16)  PRIMARY KEY,
    branch_name      VARCHAR(64)  NOT NULL,
    branch_level     VARCHAR(16)  NOT NULL,
    parent_branch_id VARCHAR(16),
    region           VARCHAR(16),
    province         VARCHAR(32),
    city             VARCHAR(32),
    open_date        DATE,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    create_time      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    update_time      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  dim_branch                  IS '机构维度表：分行/支行树形结构';
COMMENT ON COLUMN dim_branch.branch_id        IS '机构编号，业务主键（如 BR_SH_001）';
COMMENT ON COLUMN dim_branch.branch_name      IS '机构名称（如 上海分行）';
COMMENT ON COLUMN dim_branch.branch_level     IS '机构层级：HEAD 总行 / PROVINCE 省分行 / CITY 城分行 / SUBBRANCH 支行';
COMMENT ON COLUMN dim_branch.parent_branch_id IS '上级机构编号，构成树形结构';
COMMENT ON COLUMN dim_branch.region           IS '大区：华东/华北/华南/华中/西南/西北/东北';
COMMENT ON COLUMN dim_branch.province         IS '所在省份';
COMMENT ON COLUMN dim_branch.city             IS '所在城市';
COMMENT ON COLUMN dim_branch.open_date        IS '机构开业日期';
COMMENT ON COLUMN dim_branch.is_active        IS '是否存续';

-- =============================================================
-- 2. dim_customer · 客户维度
-- =============================================================
CREATE TABLE dim_customer (
    customer_id         VARCHAR(16)  PRIMARY KEY,
    customer_name       VARCHAR(64)  NOT NULL,
    id_no_masked        VARCHAR(32),
    gender              VARCHAR(8),
    birth_date          DATE,
    age                 INT,
    customer_tier       VARCHAR(16)  NOT NULL,
    risk_appetite       VARCHAR(8),
    open_date           DATE         NOT NULL,
    branch_id           VARCHAR(16)  NOT NULL REFERENCES dim_branch(branch_id),
    customer_manager_id VARCHAR(16),
    aum                 NUMERIC(18,2) NOT NULL DEFAULT 0,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    is_event_anchor     BOOLEAN      NOT NULL DEFAULT FALSE,
    create_time         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    update_time         TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  dim_customer                     IS '客户维度表：零售客户基础信息（脱敏）';
COMMENT ON COLUMN dim_customer.customer_id         IS '客户号，业务主键（如 CUST_000001）';
COMMENT ON COLUMN dim_customer.customer_name       IS '客户姓名（演示数据，非真实）';
COMMENT ON COLUMN dim_customer.id_no_masked        IS '身份证号脱敏（保留前4位+后4位）';
COMMENT ON COLUMN dim_customer.gender              IS '性别：M 男 / F 女 / U 未知';
COMMENT ON COLUMN dim_customer.birth_date          IS '出生日期';
COMMENT ON COLUMN dim_customer.age                 IS '年龄（每日批跑刷新）';
COMMENT ON COLUMN dim_customer.customer_tier       IS '客户层级：HIGH_NET_WORTH 私行 / AFFLUENT 财富 / MASS 大众 / BASIC 基础';
COMMENT ON COLUMN dim_customer.risk_appetite       IS '风险偏好：C1 保守 / C2 稳健 / C3 平衡 / C4 进取 / C5 激进';
COMMENT ON COLUMN dim_customer.open_date           IS '客户开户日期（首次建立客户关系）';
COMMENT ON COLUMN dim_customer.branch_id           IS '归属机构编号';
COMMENT ON COLUMN dim_customer.customer_manager_id IS '客户经理工号';
COMMENT ON COLUMN dim_customer.aum                 IS 'AUM 资产管理规模（最新快照，单位：元）';
COMMENT ON COLUMN dim_customer.is_active           IS '是否活跃客户';
COMMENT ON COLUMN dim_customer.is_event_anchor     IS '是否为事件埋雷锚定客户（scenario_anchor 注入，便于追溯）';

-- =============================================================
-- 3. dim_product · 产品维度
-- =============================================================
CREATE TABLE dim_product (
    product_id           VARCHAR(16)  PRIMARY KEY,
    product_name         VARCHAR(64)  NOT NULL,
    product_category     VARCHAR(16)  NOT NULL,
    product_subcategory  VARCHAR(32),
    risk_level           VARCHAR(8),
    term_days            INT,
    expected_return_rate NUMERIC(8,4),
    min_amount           NUMERIC(18,2),
    currency             VARCHAR(8)   NOT NULL DEFAULT 'CNY',
    launch_date          DATE,
    expire_date          DATE,
    is_active            BOOLEAN      NOT NULL DEFAULT TRUE,
    create_time          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    update_time          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  dim_product                      IS '产品维度表：存款/贷款/理财/基金/保险/卡产品';
COMMENT ON COLUMN dim_product.product_id           IS '产品编号，业务主键（如 PROD_FIN_AX90 安鑫90天）';
COMMENT ON COLUMN dim_product.product_name         IS '产品名称';
COMMENT ON COLUMN dim_product.product_category     IS '产品大类：DEPOSIT 存款 / LOAN 贷款 / WEALTH 理财 / FUND 基金 / INSURANCE 保险 / CARD 银行卡';
COMMENT ON COLUMN dim_product.product_subcategory  IS '产品子类（如 活期/定期/T+0/封闭式）';
COMMENT ON COLUMN dim_product.risk_level           IS '风险等级：R1 低 / R2 中低 / R3 中 / R4 中高 / R5 高';
COMMENT ON COLUMN dim_product.term_days            IS '产品期限（天），活期为 NULL';
COMMENT ON COLUMN dim_product.expected_return_rate IS '业绩比较基准/预期年化收益率（小数，如 0.0285 表示 2.85%）';
COMMENT ON COLUMN dim_product.min_amount           IS '起购金额（元）';
COMMENT ON COLUMN dim_product.currency             IS '币种：CNY 人民币 / USD 美元 / HKD 港币 / EUR 欧元';
COMMENT ON COLUMN dim_product.launch_date          IS '产品发售日期';
COMMENT ON COLUMN dim_product.expire_date          IS '产品到期日（封闭式/定期）';
COMMENT ON COLUMN dim_product.is_active            IS '是否在售';

-- =============================================================
-- 4. dim_account · 账户维度
-- =============================================================
CREATE TABLE dim_account (
    account_id     VARCHAR(32)  PRIMARY KEY,
    customer_id    VARCHAR(16)  NOT NULL REFERENCES dim_customer(customer_id),
    account_type   VARCHAR(16)  NOT NULL,
    account_subtype VARCHAR(32),
    currency       VARCHAR(8)   NOT NULL DEFAULT 'CNY',
    product_id     VARCHAR(16)  REFERENCES dim_product(product_id),
    branch_id      VARCHAR(16)  NOT NULL REFERENCES dim_branch(branch_id),
    open_date      DATE         NOT NULL,
    close_date     DATE,
    status         VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
    is_event_anchor      BOOLEAN      NOT NULL DEFAULT FALSE,
    create_time    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    update_time    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  dim_account                  IS '账户维度表：一个客户名下的多个账户（活期/定期/贷款/理财等）';
COMMENT ON COLUMN dim_account.account_id       IS '账号，业务主键（如 6222021234567890001）';
COMMENT ON COLUMN dim_account.customer_id      IS '所属客户号';
COMMENT ON COLUMN dim_account.account_type     IS '账户类型：CURRENT 活期 / SAVING 定期 / LOAN 贷款 / CARD 银行卡 / INVESTMENT 投资';
COMMENT ON COLUMN dim_account.account_subtype  IS '账户子类型';
COMMENT ON COLUMN dim_account.currency         IS '账户币种';
COMMENT ON COLUMN dim_account.product_id       IS '对应产品编号（活期账户可为 NULL）';
COMMENT ON COLUMN dim_account.branch_id        IS '开户机构';
COMMENT ON COLUMN dim_account.open_date        IS '开户日期';
COMMENT ON COLUMN dim_account.close_date       IS '销户日期，未销户为 NULL';
COMMENT ON COLUMN dim_account.status           IS '账户状态：ACTIVE 正常 / FROZEN 冻结 / CLOSED 销户 / DORMANT 睡眠';
COMMENT ON COLUMN dim_account.is_event_anchor  IS '是否为事件埋雷锚定账户（scenario_anchor 注入，便于追溯）';

-- =============================================================
-- 5. dim_date · 时间维度
-- =============================================================
CREATE TABLE dim_date (
    date_id          INT          PRIMARY KEY,   -- 20260528
    full_date        DATE         NOT NULL UNIQUE,
    year             SMALLINT     NOT NULL,
    quarter          SMALLINT     NOT NULL,
    month            SMALLINT     NOT NULL,
    day              SMALLINT     NOT NULL,
    week_of_year     SMALLINT     NOT NULL,
    day_of_week      SMALLINT     NOT NULL,
    is_weekend       BOOLEAN      NOT NULL,
    is_holiday       BOOLEAN      NOT NULL DEFAULT FALSE,
    holiday_name     VARCHAR(32),
    is_month_end     BOOLEAN      NOT NULL,
    is_quarter_end   BOOLEAN      NOT NULL,
    is_year_end      BOOLEAN      NOT NULL,
    fiscal_quarter   VARCHAR(8)
);
COMMENT ON TABLE  dim_date                IS '时间维度表：date_id 形如 20260528，与 fct_*.dt 同型号';
COMMENT ON COLUMN dim_date.date_id        IS '日期主键，INT YYYYMMDD';
COMMENT ON COLUMN dim_date.full_date      IS '完整日期';
COMMENT ON COLUMN dim_date.year           IS '年';
COMMENT ON COLUMN dim_date.quarter        IS '季度（1-4）';
COMMENT ON COLUMN dim_date.month          IS '月（1-12）';
COMMENT ON COLUMN dim_date.day            IS '日（1-31）';
COMMENT ON COLUMN dim_date.week_of_year   IS '一年中的第几周';
COMMENT ON COLUMN dim_date.day_of_week    IS '周几（1=周一, 7=周日）';
COMMENT ON COLUMN dim_date.is_weekend     IS '是否周末';
COMMENT ON COLUMN dim_date.is_holiday     IS '是否法定节假日';
COMMENT ON COLUMN dim_date.holiday_name   IS '节假日名称（如 春节/国庆/中秋）';
COMMENT ON COLUMN dim_date.is_month_end   IS '是否月末（财务/营销月末效应高发期）';
COMMENT ON COLUMN dim_date.is_quarter_end IS '是否季末';
COMMENT ON COLUMN dim_date.is_year_end    IS '是否年末';
COMMENT ON COLUMN dim_date.fiscal_quarter IS '财务季度标识（如 2026Q2）';

-- =============================================================
-- 6. fct_transaction · 交易事实表（按月分区）
-- =============================================================
CREATE TABLE fct_transaction (
    transaction_id     BIGSERIAL    NOT NULL,
    dt                 DATE         NOT NULL,
    transaction_time   TIMESTAMPTZ  NOT NULL,
    account_id         VARCHAR(32)  NOT NULL,
    customer_id        VARCHAR(16)  NOT NULL,
    counter_account_id VARCHAR(32),
    transaction_type   VARCHAR(16)  NOT NULL,
    transaction_channel VARCHAR(16),
    amount             NUMERIC(18,2) NOT NULL,
    currency           VARCHAR(8)   NOT NULL DEFAULT 'CNY',
    balance_after      NUMERIC(18,2),
    branch_id          VARCHAR(16)  NOT NULL,
    product_id         VARCHAR(16),
    description        VARCHAR(255),
    PRIMARY KEY (transaction_id, dt)
) PARTITION BY RANGE (dt);

COMMENT ON TABLE  fct_transaction                     IS '交易流水事实表：账户级笔笔交易，按月分区';
COMMENT ON COLUMN fct_transaction.transaction_id      IS '交易流水号（自增）';
COMMENT ON COLUMN fct_transaction.dt                  IS '交易日期，分区键';
COMMENT ON COLUMN fct_transaction.transaction_time    IS '交易发生时间（毫秒级）';
COMMENT ON COLUMN fct_transaction.account_id          IS '本方账号';
COMMENT ON COLUMN fct_transaction.customer_id         IS '本方客户号（冗余，避免连表）';
COMMENT ON COLUMN fct_transaction.counter_account_id  IS '对方账号';
COMMENT ON COLUMN fct_transaction.transaction_type    IS '交易类型：DEPOSIT 存入 / WITHDRAW 支取 / TRANSFER 转账 / PAYMENT 消费 / INTEREST 利息 / FEE 手续费 / REPAYMENT 还款';
COMMENT ON COLUMN fct_transaction.transaction_channel IS '交易渠道：MOBILE 手机银行 / INTERNET 网上银行 / COUNTER 柜面 / ATM / AGENT 代理 / API';
COMMENT ON COLUMN fct_transaction.amount              IS '交易金额（元，正值）';
COMMENT ON COLUMN fct_transaction.currency            IS '交易币种';
COMMENT ON COLUMN fct_transaction.balance_after       IS '交易后账户余额';
COMMENT ON COLUMN fct_transaction.branch_id           IS '交易归属机构';
COMMENT ON COLUMN fct_transaction.product_id          IS '关联产品（如理财申购）';
COMMENT ON COLUMN fct_transaction.description         IS '交易摘要';

-- 预创建 2025-01 ~ 2026-12 月分区（24 个），覆盖 demo 期间常用窗口
DO $$
DECLARE
    m DATE := DATE '2025-01-01';
    end_m DATE := DATE '2027-01-01';
    part_name TEXT;
BEGIN
    WHILE m < end_m LOOP
        part_name := 'fct_transaction_' || to_char(m, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF fct_transaction FOR VALUES FROM (%L) TO (%L)',
            part_name, m, m + INTERVAL '1 month'
        );
        m := m + INTERVAL '1 month';
    END LOOP;
END $$;

-- =============================================================
-- 7. fct_balance_daily · 日终余额事实表（按月分区）
-- =============================================================
CREATE TABLE fct_balance_daily (
    dt              DATE         NOT NULL,
    account_id      VARCHAR(32)  NOT NULL,
    customer_id     VARCHAR(16)  NOT NULL,
    product_id      VARCHAR(16),
    branch_id       VARCHAR(16)  NOT NULL,
    balance         NUMERIC(18,2) NOT NULL,
    avg_balance_mtd NUMERIC(18,2),
    currency        VARCHAR(8)   NOT NULL DEFAULT 'CNY',
    PRIMARY KEY (dt, account_id)
) PARTITION BY RANGE (dt);

COMMENT ON TABLE  fct_balance_daily                 IS '账户日终余额快照：BI 存款/AUM 类口径的核心底表';
COMMENT ON COLUMN fct_balance_daily.dt              IS '业务日期，分区键';
COMMENT ON COLUMN fct_balance_daily.account_id      IS '账号';
COMMENT ON COLUMN fct_balance_daily.customer_id     IS '客户号（冗余）';
COMMENT ON COLUMN fct_balance_daily.product_id      IS '产品编号';
COMMENT ON COLUMN fct_balance_daily.branch_id       IS '归属机构';
COMMENT ON COLUMN fct_balance_daily.balance         IS '日终余额（元）';
COMMENT ON COLUMN fct_balance_daily.avg_balance_mtd IS '月累计日均余额（Month-To-Date）';
COMMENT ON COLUMN fct_balance_daily.currency        IS '币种';

DO $$
DECLARE
    m DATE := DATE '2025-01-01';
    end_m DATE := DATE '2027-01-01';
    part_name TEXT;
BEGIN
    WHILE m < end_m LOOP
        part_name := 'fct_balance_daily_' || to_char(m, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF fct_balance_daily FOR VALUES FROM (%L) TO (%L)',
            part_name, m, m + INTERVAL '1 month'
        );
        m := m + INTERVAL '1 month';
    END LOOP;
END $$;

-- =============================================================
-- 8. fct_holding · 持仓事实表
-- =============================================================
CREATE TABLE fct_holding (
    snapshot_dt    DATE         NOT NULL,
    customer_id    VARCHAR(16)  NOT NULL,
    account_id     VARCHAR(32)  NOT NULL,
    product_id     VARCHAR(16)  NOT NULL,
    branch_id      VARCHAR(16)  NOT NULL,
    holding_amount NUMERIC(18,2) NOT NULL,
    holding_shares NUMERIC(18,4),
    market_value   NUMERIC(18,2),
    cost_basis     NUMERIC(18,2),
    pnl            NUMERIC(18,2),
    currency       VARCHAR(8)   NOT NULL DEFAULT 'CNY',
    is_event_anchor    BOOLEAN      NOT NULL DEFAULT FALSE,
    PRIMARY KEY (snapshot_dt, account_id, product_id)
);
COMMENT ON TABLE  fct_holding                IS '客户持仓事实表：理财/基金/保险/贵金属当日持仓快照';
COMMENT ON COLUMN fct_holding.snapshot_dt    IS '快照日期';
COMMENT ON COLUMN fct_holding.customer_id    IS '客户号';
COMMENT ON COLUMN fct_holding.account_id     IS '投资账户';
COMMENT ON COLUMN fct_holding.product_id     IS '所持产品';
COMMENT ON COLUMN fct_holding.branch_id      IS '归属机构';
COMMENT ON COLUMN fct_holding.holding_amount IS '持仓本金（元）';
COMMENT ON COLUMN fct_holding.holding_shares IS '持仓份额（基金/理财）';
COMMENT ON COLUMN fct_holding.market_value   IS '持仓市值（元）';
COMMENT ON COLUMN fct_holding.cost_basis     IS '持仓成本（元）';
COMMENT ON COLUMN fct_holding.pnl            IS '浮动盈亏：market_value - cost_basis';
COMMENT ON COLUMN fct_holding.currency       IS '币种';
COMMENT ON COLUMN fct_holding.is_event_anchor IS '是否为事件埋雷锚定持仓行（scenario_anchor 注入，便于追溯）';

-- =============================================================
-- 9. fct_risk_event · 风险事件事实表
-- =============================================================
CREATE TABLE fct_risk_event (
    event_id     BIGSERIAL    PRIMARY KEY,
    event_time   TIMESTAMPTZ  NOT NULL,
    dt           DATE         NOT NULL,
    customer_id  VARCHAR(16)  NOT NULL,
    account_id   VARCHAR(32),
    event_type   VARCHAR(32)  NOT NULL,
    severity     VARCHAR(16)  NOT NULL,
    amount       NUMERIC(18,2),
    status       VARCHAR(16)  NOT NULL DEFAULT 'OPEN',
    branch_id    VARCHAR(16)  NOT NULL,
    description  VARCHAR(255)
);
COMMENT ON TABLE  fct_risk_event             IS '风险事件事实表：逾期/欺诈/反洗钱告警/信用降级等';
COMMENT ON COLUMN fct_risk_event.event_id    IS '事件流水号';
COMMENT ON COLUMN fct_risk_event.event_time  IS '事件发生时间';
COMMENT ON COLUMN fct_risk_event.dt          IS '业务日期';
COMMENT ON COLUMN fct_risk_event.customer_id IS '涉及客户';
COMMENT ON COLUMN fct_risk_event.account_id  IS '涉及账户';
COMMENT ON COLUMN fct_risk_event.event_type  IS '事件类型：OVERDUE 逾期 / FRAUD 欺诈 / AML_ALERT 反洗钱告警 / CREDIT_DOWNGRADE 信用降级 / DISPUTE 争议';
COMMENT ON COLUMN fct_risk_event.severity    IS '严重程度：LOW / MEDIUM / HIGH / CRITICAL';
COMMENT ON COLUMN fct_risk_event.amount      IS '涉及金额（元）';
COMMENT ON COLUMN fct_risk_event.status      IS '处置状态：OPEN 待处理 / INVESTIGATING 调查中 / CLOSED 已关闭 / CONFIRMED 已确认';
COMMENT ON COLUMN fct_risk_event.branch_id   IS '归属机构';
COMMENT ON COLUMN fct_risk_event.description IS '事件描述';

-- =============================================================
-- 10. fct_campaign_response · 营销响应事实表
-- =============================================================
CREATE TABLE fct_campaign_response (
    response_id       BIGSERIAL    PRIMARY KEY,
    campaign_id       VARCHAR(32)  NOT NULL,
    campaign_name     VARCHAR(128),
    customer_id       VARCHAR(16)  NOT NULL,
    touch_time        TIMESTAMPTZ  NOT NULL,
    dt                DATE         NOT NULL,
    channel           VARCHAR(16)  NOT NULL,
    response_type     VARCHAR(16)  NOT NULL,
    conversion_time   TIMESTAMPTZ,
    conversion_amount NUMERIC(18,2),
    product_id        VARCHAR(16),
    branch_id         VARCHAR(16)
);
COMMENT ON TABLE  fct_campaign_response                   IS '营销活动响应事实表：触达→点击→转化的全链路记录';
COMMENT ON COLUMN fct_campaign_response.response_id       IS '响应记录流水号';
COMMENT ON COLUMN fct_campaign_response.campaign_id       IS '营销活动编号';
COMMENT ON COLUMN fct_campaign_response.campaign_name     IS '营销活动名称';
COMMENT ON COLUMN fct_campaign_response.customer_id       IS '被触达客户';
COMMENT ON COLUMN fct_campaign_response.touch_time        IS '触达时间';
COMMENT ON COLUMN fct_campaign_response.dt                IS '触达日期';
COMMENT ON COLUMN fct_campaign_response.channel           IS '触达渠道：SMS 短信 / APP_PUSH 推送 / CALL 外呼 / EMAIL 邮件 / IN_PERSON 网点';
COMMENT ON COLUMN fct_campaign_response.response_type     IS '响应类型：NO_RESPONSE 无响应 / CLICKED 点击 / INTERESTED 感兴趣 / CONVERTED 已转化 / REJECTED 明确拒绝';
COMMENT ON COLUMN fct_campaign_response.conversion_time   IS '转化时间（若已转化）';
COMMENT ON COLUMN fct_campaign_response.conversion_amount IS '转化金额（如理财申购金额）';
COMMENT ON COLUMN fct_campaign_response.product_id        IS '推荐产品';
COMMENT ON COLUMN fct_campaign_response.branch_id         IS '归属机构';
