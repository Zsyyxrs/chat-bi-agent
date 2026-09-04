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

### 1.1 配置文件（两个，缺一个就起不来）

```bash
cp .env.example .env
cp config/local.example.yaml config/local.yaml   # ← README 没写这步，但必须做
```

编辑 `.env`，**这一步只需要填一个值**：

```bash
DASHSCOPE_API_KEY=sk-你的真实key
```

其余（PG 账号密码、端口）`.env.example` 里已是可用默认值，本地跑不用改。
`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` 先留占位符，第 1.4 步再回填。

> **为什么 `config/local.yaml` 必须先建**：它被 `.gitignore` 的 `config/local.yaml` 一条排除，全新 clone 没有这个文件，
> 而 `docker-compose.yml` 的 `app` 和 `seed` 服务都 bind-mount 了它。Docker 遇到不存在的
> bind 源会在宿主机**建一个同名目录**，容器里 `/app/config/local.yaml` 于是是个目录，
> `config.py` 的 `path.exists()` 判为 True、`path.open()` 直接抛
> `IsADirectoryError: [Errno 21] Is a directory: '/app/config/local.yaml'`——
> 容器 import 期就崩，日志里只有这一行。
>
> 同理 `data/example_pool_prod.jsonl`（被 `.gitignore` 的 `data/example_pool*.jsonl` 排除）也被 `app` 挂载。它是 few-shot 池，
> 全新 clone 同样没有。见 §3-F 的处理方式。

### 1.2 起全栈

```bash
docker compose up -d
```

**该看到**：10 个容器创建。首次会构建 `chat-bi-agent:local` 镜像（几分钟）。

```bash
docker compose ps
```

**该看到**：`chatbi-pg` 与 `chatbi-langfuse-db` 状态是 `Up (healthy)`，
其余 `Up`。`chatbi-minio-bootstrap` 是一次性 job，`Exited (0)` 才是对的。

Langfuse 首次启动要跑 Postgres + ClickHouse 双份 migration，通常要几分钟才对外服务。
这期间访问 `:3001` 得到 502 或空白页是正常的，不要急着重启——
以 `docker compose logs -f langfuse` 里出现监听日志为准，不要靠掐表。

### 1.3 灌种子数据

```bash
docker compose --profile seed run --rm seed
```

seed 走 profile，`docker compose up` **不会**自动拉起它，必须显式触发。
参数固定为 `--truncate --with-events`（rows 默认 100000、seed 默认 42）。

**该看到**：跑完 2–5 分钟，容器以 0 退出。核对方式见 §2.2。

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

第二条**必须报错**。如果它成功了，说明 `03_readonly_role.sql` 没执行过（见 §3-D），
Agent 就在拿一个有写权限的连接跑 LLM 生成的 SQL。

### 2.4 端到端

Streamlit P1 tab → 点示例问题「杭州分行（BR_CITY_0000）在 2026 年 2 月末……」→ 执行。
期望 10 秒内出 SQL + 数据表；底部出现 👍/👎（说明 trace_id 拿到了，Langfuse 通了）。

---

## 3. 常见故障速查

### A. `IsADirectoryError: Is a directory: '/app/config/local.yaml'`

**症状**：`chatbi-app` 反复重启，`docker compose logs app` 只有这一行。
**原因**：漏了 `cp config/local.example.yaml config/local.yaml`，Docker 把缺失的 bind 源建成了目录。
**处置**：

```bash
docker compose down
rmdir config/local.yaml                          # 删掉 Docker 建的空目录
cp config/local.example.yaml config/local.yaml   # 建成真文件
docker compose up -d
```

### B. `bind: address already in use`

**症状**：`docker compose up` 报某端口被占。
**定位**：`lsof -nP -iTCP:8501 -sTCP:LISTEN`（端口换成报错里那个）。
**处置**：停掉占用进程，或改 `docker-compose.yml` 里该服务的宿主机侧端口（冒号左边）。
本项目已把容易撞的端口都挪开了，实际最常撞的只剩 `8501`（机器上另一个 Streamlit）
和 `5050`（另一个 pgAdmin）。

### C. Langfuse `:3001` 打不开 / 502

**先等**，首启双 migration 要几分钟，`docker compose logs -f langfuse` 能看到进度。
仍不行时：

```bash
docker compose logs langfuse --tail 50
docker compose ps clickhouse redis minio     # 三个依赖必须都 Up
```
最常见是 ClickHouse 因内存不足被 OOM kill（`Exited (137)`）→ 调大 Docker 内存到 6 GB 以上。

### D. Agent 报 `permission denied` 或只读角色不存在

**原因**：`docker/postgres/init/*.sql` **只在数据卷首次初始化时执行一次**。
如果 `pgdata` 卷是之前建的（比如加 `03_readonly_role.sql` 之前），它不会补跑。
**处置**（二选一）：

```bash
# 轻量：只补跑角色脚本
docker exec -i chatbi-pg psql -U chatbi -d chatbi < docker/postgres/init/03_readonly_role.sql

# 彻底：删卷重来（会丢数据，要重跑 seed）
docker compose down -v && docker compose up -d && docker compose --profile seed run --rm seed
```

### E. UI 上没有 👍/👎，显示「Langfuse trace_id 缺失」

**原因**：`.env` 里 Langfuse key 还是占位符，或填完没重启 app。
**处置**：核对 §1.4 第 4、5 步。验证：

```bash
docker exec chatbi-app env | grep LANGFUSE_
```
`LANGFUSE_HOST` 在容器里应当是 `http://langfuse:3000`（**不是** `localhost:3001`——
容器里的 localhost 指它自己；compose 已显式覆盖，`.env` 里那个是给宿主机脚本用的）。

### F. P1 结果正常但 few-shot 从不生效 / caption 里 `pool=0`

**原因**：`data/example_pool_prod.jsonl` 被 `.gitignore` 排除，全新 clone 没有，
Docker 又把它建成了目录（同 §3-A）。
**处置**：这个池子是可选增强项，不影响正确性。要么忽略，要么生成它：

```bash
rmdir data/example_pool_prod.jsonl 2>/dev/null   # 如果 Docker 建成了目录
make bootstrap-pool                              # 需要 .env 里的 key 已配好
```

### G. `Agent 执行失败：... DashScope ...` / 401 / 限流

**处置**：确认 `.env` 里 `DASHSCOPE_API_KEY` 是真 key 且账户有额度；改完 `.env` 后
`docker compose restart app`。侧边栏「本会话用量」计数可以帮你判断是不是问太快了。

### H. 改了源码但 UI 行为没变

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
这也是 §3-D 的「彻底」解法。

不需要保留镜像时再加：`docker image rm chat-bi-agent:local`。
