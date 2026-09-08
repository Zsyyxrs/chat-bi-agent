# 部署 Runbook

从零到跑通，以及**没跑通时怎么办**。

README 的 Quick Start 给的是顺利路径（五条命令）。这份文档补的是它没覆盖的部分：
前置条件、每步该看到什么、健康检查怎么做、失败了怎么定位。

> 本文所有命令都在 repo 根目录执行，端口、行数、报错文本均为 2026-09-04 实测。

---

## 0. 前置条件

| 项 | 要求 | 怎么确认 |
|---|---|---|
| Docker | 装了 Compose V2（`docker compose`，不是 `docker-compose`） | `docker compose version` |
| 内存 | **给 Docker ≥ 6 GB**。全栈 10 个容器，其中 ClickHouse + MinIO + Redis 都是 Langfuse 的依赖 | Docker Desktop → Settings → Resources |
| 磁盘 | ≥ 5 GB（4 个数据卷 + 镜像） | `docker system df` |
| DashScope API Key | 必填，没有它三条路径全部不可用 | https://dashscope.console.aliyun.com/ |

**只想看 P1/P2/P3 不想要 Langfuse 的**：跳过 Langfuse 全家（见 §5「最小启动」），
省 6 个容器。代价是没有 trace，UI 上 👍/👎 反馈按钮会显示「trace_id 缺失」而不可用。

---

## 1. 从零到跑通

### 1.1 配置文件

```bash
cp .env.example .env
cp config/local.example.yaml config/local.yaml   # 强烈建议，理由见下
```

编辑 `.env`，**这一步只需要填一个值**：

```bash
DASHSCOPE_API_KEY=sk-你的真实key
```

其余（PG 账号密码、端口）`.env.example` 里已是可用默认值，本地跑不用改。
`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` 先留占位符，第 1.4 步再回填。

> **为什么建议建 `config/local.yaml`**：它被 `.gitignore` 排除，全新 clone 没有。
> 缺了不会报错——`config.py` 会静默落到代码里的 `_DEFAULTS`，而 `_DEFAULTS` 的
> `chat_model` 与 `local.example.yaml` 里那个**不是同一个模型**。
> 后果是跑得起来但分数对不上 README，且全程没有任何提示
> （`local.example.yaml` 自己就写着「换模型分数会变，而且不会有任何报错」）。
>
> 想复现 README 的评估成绩，就照抄 `local.example.yaml`；只是想点着玩，缺了也无妨。

### 1.2 起全栈

```bash
docker compose up -d
```

**该看到**：10 个容器创建。首次会构建 `chat-bi-agent:local` 镜像（几分钟）。

```bash
docker compose ps      # 运行中的 9 个
docker compose ps -a   # 加上已退出的一次性 job
```

**该看到**：`chatbi-pg` 与 `chatbi-langfuse-db` 状态是 `Up (healthy)`，其余 `Up`。
`chatbi-minio-bootstrap` 是一次性 job，`Exited (0)` 才是对的——但它**不会出现在
`docker compose ps` 里**（默认只列运行中的），要加 `-a` 才看得到。

Langfuse 首次启动要跑 Postgres + ClickHouse 双份 migration，通常要几分钟才对外服务。
这期间访问 `:3001` 得到 502 或空白页是正常的，不要急着重启——
以 `docker compose logs -f langfuse` 里出现监听日志为准，不要靠掐表。

### 1.3 灌种子数据

```bash
docker compose --profile seed run --rm seed
```

seed 走 profile，`docker compose up` **不会**自动拉起它，必须显式触发。
参数固定为 `--truncate --with-events`（rows 默认 100000、seed 默认 42）。

**该看到**：实测约 40 秒跑完，容器以 0 退出。核对方式见 §2.2。

seed 结束会打印一张 `Table row counts` 表——**别拿它跟 §2.2 对**。那是生成阶段的
内存计数（`dim_counts` / `fact_counts`），而 `--with-events` 的事件传导会在之后
继续增删行，两者本就不一致（实测 seed 打印 `dim_customer 5,000`，库里实际 5230）。
以 §2.2 直接查库的结果为准。

### 1.4 回填 Langfuse API Key

1. 打开 http://localhost:3001
2. 登录 `admin@chatbi.local` / `admin12345`（compose 里 `LANGFUSE_INIT_USER_*` 预置）
3. Settings → API Keys → Create new API keys
4. 把 `pk-lf-...` / `sk-lf-...` 回填进 `.env` 的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
5. `docker compose restart app` —— **必须重启**，`.env` 是 `env_file`，进程启动时读一次

### 1.5 打开应用

http://localhost:8501 —— 三个 tab 各自顶部有「这个 tab 适合问什么？」，里面的示例问题
点一下就填进输入框，都是评测题集里跑过 ground truth 的题面。

**端口一览**

| 服务 | 地址 | 账号 |
|---|---|---|
| Streamlit App | http://localhost:8501 | — |
| Langfuse UI | http://localhost:3001 | `admin@chatbi.local` / `admin12345` |
| pgAdmin | http://localhost:5050 | `admin@local.dev` / `admin` |
| Postgres | `localhost:5433`（容器内 5432） | `chatbi` / `chatbi_dev` |
| MinIO Console | http://localhost:9191 | `minio` / `minio_dev_secret` |

宿主机上**只发布这五个端口**（ClickHouse / Redis / Langfuse worker 只在 compose 内网可达）。
`5433`、`3001`、`9190/9191` 都是刻意避开 `5432` / `3000` / `9000-9001` 的，
防止和机器上已有的 PG、Langfuse、MinIO 撞车。

---

## 2. 健康检查

跑完这四项全绿，才算真的跑通。

### 2.1 容器

```bash
docker compose ps
```
`chatbi-pg`、`chatbi-langfuse-db` 必须 `(healthy)`；`chatbi-minio-bootstrap` 必须 `Exited (0)`。

### 2.2 数据

```bash
docker exec chatbi-pg psql -U chatbi -d chatbi -tAc "
select table_name||' = '||(xpath('/row/c/text()',
  query_to_xml('select count(*) as c from public.'||quote_ident(table_name),false,true,'')))[1]::text::bigint
from information_schema.tables
where table_schema='public' and table_type='BASE TABLE'
  and table_name not like '%_2025%' and table_name not like '%_2026%'
order by 1;"
```

**默认参数（`--rows 100000 --seed 42 --with-events`）下的实测基线**：

```
_meta_schema_version = 1
dim_account = 10380      dim_branch = 50        dim_customer = 5230
dim_date = 730           dim_product = 91
fct_balance_daily = 267753   fct_campaign_response = 159500
fct_holding = 26332          fct_risk_event = 11
fct_transaction = 86000
```

`fct_transaction` 是 86000 不是 100000——`--rows` 是**目标值**不是精确值，别拿它当断言。
另有 48 张分区子表（`fct_transaction_*` / `fct_balance_daily_*`），上面的查询已排除。

**`--seed 42` 是硬要求**：43 个 gold SQL 断言的是具体行数，换种子评估和集成测试全红。

### 2.3 只读角色（Layer 1 安全护栏）

```bash
# 能读
docker exec -e PGPASSWORD=readonly_dev chatbi-pg \
  psql -U chatbi_readonly -d chatbi -h 127.0.0.1 -tAc "select count(*) from dim_branch;"
# 期望：50

# 不能写
docker exec -e PGPASSWORD=readonly_dev chatbi-pg \
  psql -U chatbi_readonly -d chatbi -h 127.0.0.1 -tAc "delete from dim_branch;"
# 期望：ERROR:  permission denied for table dim_branch
```

第二条**必须报错**。如果它成功了，说明 `03_readonly_role.sql` 没执行过（见 §3-E），
Agent 就在拿一个有写权限的连接跑 LLM 生成的 SQL。

### 2.4 端到端

Streamlit P1 tab → 展开「这个 tab 适合问什么？」→ 点第一条示例问题（会自动填进输入框）
→ 执行。

期望：中位约 12 秒出 SQL + 数据表（复杂题到 40 秒以上），底部出现 👍/👎——
后者说明拿到了 trace_id，即 Langfuse 链路是通的。

---

## 3. 常见故障速查

### A. `IsADirectoryError: Is a directory: '/app/config/local.yaml'`

**已根治**（2026-09-04）：compose 现在挂的是 `./config` 和 `./data` 两个**目录**，
不再挂单文件。目录在仓库里必然存在，Docker 不会再凭空造出同名目录。

只有 2026-09-04 之前的 checkout 会撞上这条：那时挂的是被 gitignore 掉的单文件，
Docker 对不存在的 bind 源**不报错**，而是在宿主机建一个同名目录，
于是容器里 `/app/config/local.yaml` 是目录，`config.py` 的 `path.exists()` 判为 True、
`path.open()` 抛 `IsADirectoryError`，import 期就崩，日志里只有这一行。

**如果宿主机上残留了 Docker 建出来的空目录**：

```bash
docker compose down
rmdir config/local.yaml data/example_pool_prod.jsonl 2>/dev/null
git pull                                          # 取到目录挂载的版本
cp config/local.example.yaml config/local.yaml
docker compose up -d
```

### B. 首次构建失败：`dns error / failed to lookup address information: Try again`

**症状**：`docker compose up -d` 在 `RUN uv sync` 那层挂掉，报 `EAI_AGAIN`，
随机卡在某个包上（实测卡在 `setuptools`）。
**原因**：uv 并发下载把 Docker 内嵌 DNS(127.0.0.11) → 宿主机解析器这条转发链
的 UDP 查询打丢。Dockerfile 已把 `UV_CONCURRENT_DOWNLOADS` 降到 4 来缓解，
但网络差时仍会偶发。
**处置**：**直接重试**，层缓存还在，不用清理。实测第二次即通过（约 6 分钟）。

```bash
docker compose build app && docker compose up -d
```
先确认 DNS 本身没坏：`docker run --rm python:3.11-slim getent hosts pypi.org`

### C. `bind: address already in use`

**症状**：`docker compose up` 报某端口被占。
**定位**：`lsof -nP -iTCP:8501 -sTCP:LISTEN`（端口换成报错里那个）。
**处置**：停掉占用进程，或改 `docker-compose.yml` 里该服务的宿主机侧端口（冒号左边）。
本项目已把容易撞的端口都挪开了，实际最常撞的只剩 `8501`（机器上另一个 Streamlit）
和 `5050`（另一个 pgAdmin）。

### D. Langfuse `:3001` 打不开 / 502

**先等**，首启双 migration 要几分钟，`docker compose logs -f langfuse` 能看到进度。
仍不行时：

```bash
docker compose logs langfuse --tail 50
docker compose ps clickhouse redis minio     # 三个依赖必须都 Up
```
最常见是 ClickHouse 因内存不足被 OOM kill（`Exited (137)`）→ 调大 Docker 内存到 6 GB 以上。

### E. Agent 报 `relation "xxx" does not exist`（表明明在）

**这条症状具有欺骗性**：Postgres 把「权限被拒」伪装成「关系不存在」，
所以真正的病因往往是只读角色 `chatbi_readonly` 缺少 SELECT 权限，
而不是表真的没建。

**原因**：`docker/postgres/init/*.sql` **只在数据卷首次初始化时执行一次**。
`pgdata` 卷若是加 `03_readonly_role.sql` 之前建的，它不会补跑。

**处置**：重跑一次 seed 就行——`seed.py` 的 `ensure_readonly_grants()`
会幂等重建角色与授权，它存在的理由正是兜住这一条：

```bash
docker compose --profile seed run --rm seed
```

跑完用 §2.3 复核。真想从头来（会丢数据）才用
`docker compose down -v && docker compose up -d && docker compose --profile seed run --rm seed`。

### F. UI 上没有 👍/👎，显示「Langfuse trace_id 缺失」

**原因**：`.env` 里 Langfuse key 还是占位符，或填完没重启 app。
**处置**：核对 §1.4 第 4、5 步。验证：

```bash
docker exec chatbi-app env | grep LANGFUSE_
```
`LANGFUSE_HOST` 在容器里应当是 `http://langfuse:3000`（**不是** `localhost:3001`——
容器里的 localhost 指它自己；compose 已显式覆盖，`.env` 里那个是给宿主机脚本用的）。

### G. P1 结果正常但 few-shot 从不生效 / caption 里 `pool=0`

**原因**：`data/example_pool_prod.jsonl` 被 `.gitignore` 排除，全新 clone 没有。
**处置**：这个池子是可选增强项，缺了只是不做 few-shot 检索，不影响正确性
（`_build_retriever_if_available()` 返回 None，P1 行为等价 few-shot off）。
想要它就生成一份：

```bash
make bootstrap-pool     # 需要 .env 里的 key 已配好；默认 --source both
```

### H. `Agent 执行失败：... DashScope ...` / 401 / 限流

**处置**：确认 `.env` 里 `DASHSCOPE_API_KEY` 是真 key 且账户有额度；改完 `.env` 后
`docker compose restart app`。侧边栏「本会话用量」计数可以帮你判断是不是问太快了。

### I. 改了源码但 UI 行为没变

`app` 和 `seed` 都设了 `pull_policy: build`，正常情况 `up` 会重建。
如果仍是旧行为：

```bash
docker compose up -d --build app
```

---

## 4. 日常操作

```bash
docker compose logs -f app              # 跟踪应用日志
docker compose restart app              # 改完 .env 后
docker compose stop                     # 停但保留数据
docker compose up -d                    # 起回来
```

**重灌数据**（schema 不变、只想换一批数）：

```bash
docker compose --profile seed run --rm seed
```
`--truncate` 已在 command 里，会先清空再灌。

---

## 4bis. 接入 Claude Desktop（MCP server）

把 P1 精准取数暴露给任何 MCP 客户端。**只暴露 P1**：P2/P3 委托 P1 时不传
`session_props`，子问题走 NL2SQL 兜底时没有行级管控，故不进暴露面。

### 前置

- Postgres 已起、已灌数（`docker compose up -d postgres` + seed）
- 仓库根的 `.env` 已配好 `DASHSCOPE_API_KEY` 与 `PG_*`
  （server 按**绝对路径**读它，与 Claude Desktop 的 cwd 无关）
- 装 MCP 依赖：`uv pip install -e '.[mcp]'`

### 配置

Claude Desktop 的 `claude_desktop_config.json`，一个身份一个条目——
**身份由服务端配置锁定，模型无法在对话里指定**：

```json
{
  "mcpServers": {
    "chatbi-杭州分行": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "chat_bi_agent.mcp_server"],
      "env": {
        "CHATBI_MCP_BRANCH_SCOPE": "BRANCH",
        "CHATBI_MCP_BRANCH_ID": "BR_CITY_0000"
      }
    },
    "chatbi-总行": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "chat_bi_agent.mcp_server"],
      "env": { "CHATBI_MCP_BRANCH_SCOPE": "ALL" }
    }
  }
}
```

`command` 必须是绝对路径的解释器（装了本项目那个环境的），Claude Desktop 不走登录 shell。

### 两个 tool

| tool | 入参 | 说明 |
|---|---|---|
| `query_bank_data` | `question` **仅此一个** | 返回 `{sql, rows, row_count, truncated, route, metric_id, denied, error}`；rows 截断到 200 行，`row_count` 报真值 |
| `list_governed_metrics` | 无 | 受治理指标清单，标出哪些受行级权限管控 |

### 验收（2026-09-08 实测）

同一个问题「统计营销触达响应数」，换身份出不同数：

| 身份配置 | route | 结果 |
|---|---|---|
| `SCOPE=ALL` | `metric` | 159500 |
| `SCOPE=BRANCH` + `BRANCH_ID=BR_CITY_0000` | `metric` | 3831 |
| 两个环境变量都不配 | `metric_denied` | 拒绝，`rows=null` |

两个数与直接打 `fct_campaign_response` 逐字符一致。**第三行是重点**：没配身份不是
「总行视角」，是没有身份——受管控指标在渲染期被拒，且**不回退 NL2SQL**
（回退等于换一条没有行级管控的路把同一个数给出来，2026-09-04 实测过）。

### 故障速查

| 现象 | 原因 |
|---|---|
| 连不上库 / `could not connect ... 5432` | `.env` 没读到。本项目 PG 映射在 **5433**；确认仓库根有 `.env` |
| 所有受治理指标都 `denied` | 环境变量没配或拼错。`SCOPE` 只认 `ALL` / `BRANCH`，其余一律拒绝 |
| `SCOPE=BRANCH` 却全被拒 | 少配了 `CHATBI_MCP_BRANCH_ID`——这是 fail-closed，不是 bug |
| Claude Desktop 里看不到 server | `command` 不是绝对路径，或该解释器没装 `.[mcp]` |

---

## 5. 最小启动（不要 Langfuse）

只跑三条路径、不要观测栈：

```bash
docker compose up -d postgres app
docker compose --profile seed run --rm seed
```

2 个容器代替 10 个。代价：无 trace、UI 反馈按钮不可用、`bootstrap-pool` 的
Langfuse 来源不可用——它默认 `--source both`，此时要显式改用 `--source p1_eval`。

**本地开发**（不进容器跑 Python）：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d postgres            # 只要库
python -m chat_bi_agent.data.seed --port 5433 --truncate --with-events
make streamlit
```

> 宿主机直连时 Postgres 在 **5433**。`.env` 里的 `PG_PORT=5433` 已是对的，但
> `seed` 脚本的默认值是 `5432`——从宿主机跑要显式加 `--port 5433`，
> 否则会连到机器上别的 PG（或直接连不上）。

---

## 6. 卸载与重置

```bash
docker compose down          # 停并删容器，保留数据
docker compose down -v       # 连数据卷一起删 —— 不可逆
```

`down -v` 会删掉四个卷：

| 卷 | 内容 | 删了要做什么 |
|---|---|---|
| `pgdata` | 业务库（10 张表 + 48 张分区） | 重跑 seed（§1.3） |
| `langfusedb` | Langfuse 元数据、**API Key** | 重做 §1.4 建 key 并回填 |
| `clickhousedata` | Langfuse trace 明细 | 历史 trace 全部丢失 |
| `miniodata` | Langfuse 事件对象存储 | 同上 |

删卷后首次 `up` 会重跑 `docker/postgres/init/*.sql`（含只读角色），
这也是 §3-E 的「彻底」解法。

不需要保留镜像时再加：`docker image rm chat-bi-agent:local`。
