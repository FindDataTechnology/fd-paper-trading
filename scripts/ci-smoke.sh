#!/usr/bin/env bash
# 出片冒烟（fd-ci image-build.yml 的 smoke_script）：构建后、推 tag 前跑。
#
# 为什么需要：本镜像的 entrypoint.sh 是 `set -euo pipefail` + `alembic upgrade head`
# + exec 服务。 **迁移跑不动 ⇒ 容器启动即退出**。而 image.yml 此前没配烟雾，
# 「构建 success」只等于 docker build 过了。2026-11-26 同族缺陷在
# fd-erp-ledger-mcp 真实发生过：镜像能构建，生产 CrashLoop 33 小时。
#
# 验什么（照 entrypoint.sh 的真实顺序）：
#   1. 入口可执行 —— `--help` 不炸；
#   2. 一次性 Postgres 起来 → 用镜像**真的跑一次 entrypoint**（含 alembic upgrade）
#      → 等 /health 应答。迁移失败、缺依赖、启动期崩，都会在这里红。
#   3. 顺手断言迁移真的落库（alembic_version 非空），否则「服务起来了但库是空的」也能混过去。
set -euo pipefail

: "${IMG:?IMG 未设置（应由 fd-ci image-build.yml 注入）}"

NET="smokenet-$$"
PG="smokepg-$$"
APP="smokeapp-$$"
cleanup() { docker rm -f "$APP" "$PG" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# 注意：本镜像的 console script 只装在 /app/.venv 里（Dockerfile `uv sync
# --no-install-project` + `uv sync`），**不在 PATH 上**——2026-11-26 第一次跑
# 就红在 `exec: "fd-paper-trading": executable file not found`。所以第 1 步不能
# 调它；要验的本来也不是「命令存在」，而是「entrypoint 能不能跑通 alembic 并起服务」，
# 那正是第 2/3 步用默认 ENTRYPOINT 做的事。
echo "== 1. 镜像的入口脚本在 =="
docker run --rm --entrypoint sh "$IMG" -c 'test -x /app/entrypoint.sh && echo entrypoint-ok'

echo "== 2. 一次性 Postgres =="
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=smoke -e POSTGRES_PASSWORD=smoke -e POSTGRES_DB=paper \
  postgres:16-alpine >/dev/null
for i in $(seq 1 30); do
  docker exec "$PG" pg_isready -U smoke -d paper >/dev/null 2>&1 && break
  [ "$i" = 30 ] && { echo "FAIL: Postgres 30s 未就绪" >&2; exit 1; }
  sleep 1
done
DBURL="postgresql+asyncpg://smoke:smoke@$PG:5432/paper"

echo "== 3. 用镜像真跑 entrypoint（含 alembic upgrade head）=="
docker run -d --name "$APP" --network "$NET" -p 18080:8000 \
  -e DATABASE_URL="$DBURL" "$IMG" >/dev/null

for i in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:18080/health >/tmp/h.json 2>/dev/null; then
    echo "health OK after ${i}s:"; head -c 160 /tmp/h.json; echo
    echo "== 4. 迁移真的落库 =="
    n=$(docker exec "$PG" psql -U smoke -d paper -tAc 'select count(*) from alembic_version')
    [ "${n:-0}" -ge 1 ] || { echo "FAIL: alembic_version 为空 —— 服务起来了但迁移没落库" >&2; exit 1; }
    echo "alembic_version 行数=$n"
    exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$APP")" != "true" ]; then
    echo "FAIL: 容器启动后即退出（很可能是 alembic 迁移失败），日志：" >&2
    docker logs "$APP" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "FAIL: 60s 内 /health 不应答，日志：" >&2
docker logs "$APP" >&2 || true
exit 1
