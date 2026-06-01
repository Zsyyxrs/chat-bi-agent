-- chat-bi-agent · 只读角色，供 NL2SQL Agent 执行 SELECT
-- Layer 1 of 2 安全护栏（Layer 2 在 sql_executor.py 的正则白名单）

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbi_readonly') THEN
        CREATE ROLE chatbi_readonly WITH LOGIN PASSWORD 'readonly_dev';
    END IF;
END $$;

GRANT CONNECT ON DATABASE chatbi TO chatbi_readonly;
GRANT USAGE ON SCHEMA public TO chatbi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO chatbi_readonly;

-- 后续新建的表也自动有 SELECT 权限
ALTER DEFAULT PRIVILEGES FOR ROLE chatbi IN SCHEMA public
    GRANT SELECT ON TABLES TO chatbi_readonly;

-- 显式拒绝 DML/DDL（即使授权也无效；多一道防线）
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM chatbi_readonly;
