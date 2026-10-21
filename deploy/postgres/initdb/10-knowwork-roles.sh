#!/bin/sh
# 首次初始化数据目录时由 postgres 官方镜像执行（/docker-entrypoint-initdb.d），之后不再执行。
#
# 两个连库角色，都不是超级用户、不带 BYPASSRLS、不能建库建角色：
# - knowwork_owner：库与 public 模式的所有者，业务服务启动时用它跑迁移（建表、开行级安全、给 app 授权）；
# - knowwork_app：业务运行账号，只有迁移授予的增删改查与序列权限，不是任何表的所有者，
#   因而关不掉、改不了行级安全，也建不了表。
# 口令由 bootstrap 从主密钥派生写入只读文件，这里经 psql 的反引号读入，不出现在命令行参数里。
set -eu

KNOWWORK_DB_SECRETS_DIR="${KNOWWORK_DB_SECRETS_DIR:-/run/knowwork/db}"
KNOWWORK_DB_NAME="${POSTGRES_DB:-knowwork}"
export KNOWWORK_DB_SECRETS_DIR KNOWWORK_DB_NAME

for secret in owner_password app_password; do
  if [ ! -s "$KNOWWORK_DB_SECRETS_DIR/$secret" ]; then
    echo "knowwork initdb: 缺少口令文件 $secret（bootstrap 是否已运行？）" >&2
    exit 1
  fi
done

psql -v ON_ERROR_STOP=1 --no-psqlrc --username "${POSTGRES_USER:-postgres}" --dbname "$KNOWWORK_DB_NAME" <<'SQL'
\set owner_pw `cat "$KNOWWORK_DB_SECRETS_DIR/owner_password"`
\set app_pw `cat "$KNOWWORK_DB_SECRETS_DIR/app_password"`
\set db_name `printf %s "$KNOWWORK_DB_NAME"`

CREATE ROLE knowwork_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'owner_pw';
CREATE ROLE knowwork_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'app_pw';

ALTER DATABASE :"db_name" OWNER TO knowwork_owner;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO knowwork_app;
ALTER SCHEMA public OWNER TO knowwork_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- 表与序列的权限由迁移授予（KNOWWORK_APP_DB_ROLE），这里只给模式的使用权
GRANT USAGE ON SCHEMA public TO knowwork_app;
SQL
