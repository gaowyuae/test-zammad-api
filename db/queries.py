"""为试点提供固定、参数化、独立短事务的只读 PostgreSQL 查询。"""

import os
import re
from datetime import date, datetime, time
from types import TracebackType

import psycopg
import yaml
from psycopg.rows import dict_row

from db.config import DbConfig
from reporting.evidence import EvidenceRecorder
from support.config import _ConfigLoader

_TEMPLATE_COLUMNS = "id, name, options, active, created_by_id"
_TEXT_MODULE_COLUMNS = "id, name, keywords, content, note, active, created_by_id"
_MACRO_COLUMNS = "id, name, perform, active, ux_flow_next_up, created_by_id"

_TICKET_COLUMNS = (
    "id, number, title, group_id, owner_id, customer_id, state_id, priority_id, "
    "article_count, created_by_id, updated_by_id, updated_at"
)
_ARTICLE_COLUMNS = (
    "id, ticket_id, type_id, sender_id, subject, body, content_type, internal, "
    "created_by_id, updated_by_id, updated_at"
)
_USER_COLUMNS = (
    "id, login, firstname, lastname, email, active, organization_id, "
    "created_by_id, updated_by_id, created_at AT TIME ZONE 'UTC' AS created_at, "
    "updated_at AT TIME ZONE 'UTC' AS updated_at"
)
_ACCESS_SQL = """
SELECT rolcanlogin AS can_login,
       (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) AS elevated,
       pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
       EXISTS (
           SELECT 1 FROM (VALUES ('public.tickets'), ('public.ticket_articles'),
                                 ('public.ticket_article_types'), ('public.stores'),
                                 ('public.store_objects')) AS objects(name)
           WHERE pg_catalog.has_table_privilege(
               current_user, objects.name, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
              OR pg_catalog.has_any_column_privilege(
               current_user, objects.name, 'INSERT,UPDATE,REFERENCES')
       ) AS object_write
FROM pg_catalog.pg_roles WHERE rolname = current_user
"""


class DbError(RuntimeError):
    """数据库配置之外的安全错误，不包含驱动消息或连接字符串。"""


def _ticket_id(value: int) -> None:
    """拒绝布尔、字符串及非正 ID，避免意外扩大对象范围。"""
    if type(value) is not int or value <= 0:
        raise DbError("数据库查询需要正整数工单 ID") from None


class Queries:
    """每个用例创建一个实例并通过上下文管理器持有独立连接。"""

    def __init__(self, config: DbConfig, recorder: EvidenceRecorder) -> None:
        """保存非敏感配置和证据器，延迟至进入上下文时读取密码。"""
        self.config = config
        self.recorder = recorder
        self._connection = None

    def __enter__(self) -> "Queries":
        """创建有限超时的默认只读连接，并核实账号实际权限。"""
        if self._connection is not None:
            raise DbError("数据库查询上下文已打开") from None
        password = os.environ.get(self.config.password_env)
        if not password:
            raise DbError("数据库密码环境变量缺失或为空") from None
        self.recorder.redactor.register(password)
        failed = False
        try:
            self._connection = psycopg.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.dbname,
                user=self.config.user,
                password=password,
                connect_timeout=self.config.connect_timeout,
                autocommit=True,
                row_factory=dict_row,
                options=(
                    "-c default_transaction_read_only=on -c search_path=pg_catalog "
                    f"-c statement_timeout={self.config.statement_timeout_ms} "
                    f"-c lock_timeout={self.config.lock_timeout_ms}"
                ),
            )
            access = self._rows(_ACCESS_SQL)
            if len(access) != 1 or access[0] != {
                "can_login": True,
                "elevated": False,
                "schema_create": False,
                "object_write": False,
            }:
                failed = True
            else:
                # 无行探针让服务端验证必需列及 SELECT 授权，不读取无关业务数据。
                for sql in (
                    f"SELECT {_TICKET_COLUMNS} FROM public.tickets WHERE FALSE",
                    f"SELECT {_ARTICLE_COLUMNS} FROM public.ticket_articles WHERE FALSE",
                    "SELECT name FROM public.ticket_article_types WHERE FALSE",
                ):
                    self._rows(sql)
        except Exception:
            failed = True
        finally:
            password = None
        if failed:
            # 在捕获块外创建异常，避免原驱动信息残留于异常链。
            try:
                self.close()
            except DbError:
                pass
            raise DbError("数据库连接、只读权限或必需列核查失败") from None
        self.recorder.record("db-access", {"query": "readonly-account", "result": "ready"})
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """无论断言或查询是否失败，都清理本用例连接。"""
        self.close()

    def close(self) -> None:
        """释放连接并清空引用，关闭失败也不输出原始驱动错误。"""
        connection, self._connection = self._connection, None
        failed = False
        if connection is not None:
            try:
                connection.close()
            except Exception:
                failed = True
        if failed:
            raise DbError("数据库连接关闭失败") from None

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        """执行内部固定 SQL；每次独立读取已提交快照并转换日期时间。"""
        if self._connection is None:
            raise DbError("数据库查询上下文未打开") from None
        failed = False
        rows = []
        try:
            with self._connection.transaction():
                self._connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
                )
                rows = self._connection.execute(sql, params).fetchall()
        except Exception:
            failed = True
        if failed:
            raise DbError("数据库查询失败，无法取得必要证据") from None
        return [
            {
                key: value.isoformat() if isinstance(value, (datetime, date, time)) else value
                for key, value in row.items()
            }
            for row in rows
        ]

    def ticket_number_exists(self, number: str) -> bool:
        """通过精确编号确认空搜索的前置，不读取无关工单内容。"""
        if not isinstance(number, str) or re.fullmatch(r"[0-9]{1,100}", number) is None:
            raise DbError("工单编号必须为有限长度的数字文本") from None
        rows = self._rows(
            "SELECT EXISTS(SELECT 1 FROM public.tickets WHERE number = %s) AS present",
            (number,),
        )
        if len(rows) != 1 or type(rows[0].get("present")) is not bool:
            raise DbError("工单编号存在性证据无效") from None
        self.recorder.record("db-ticket-number", {"number": number, "present": rows[0]["present"]})
        return rows[0]["present"]

    def ticket(self, ticket_id: int) -> dict | None:
        """按工单 ID 返回必要字段快照，不存在时返回 None。"""
        _ticket_id(ticket_id)
        rows = self._rows(
            f"SELECT {_TICKET_COLUMNS} FROM public.tickets WHERE id = %s", (ticket_id,)
        )
        result = rows[0] if rows else None
        self.recorder.record(
            "db-ticket", {"query": "ticket", "ticket_id": ticket_id, "result": result}
        )
        return result

    def check_search_access(self) -> None:
        """基线才校验主次组织最小列和相关只读边界。"""
        self.check_user_access()
        for table in ("organizations", "organizations_users"):
            rows = self._rows(
                "SELECT (pg_catalog.has_table_privilege(current_user,%s,"
                "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
                "pg_catalog.has_any_column_privilege(current_user,%s,'INSERT,UPDATE,REFERENCES')) AS object_write",
                (f"public.{table}", f"public.{table}"),
            )
            if rows != [{"object_write": False}]:
                raise DbError("基线组织证据要求只读账号") from None
        self._rows("SELECT id, shared, active FROM public.organizations WHERE FALSE")
        self._rows("SELECT user_id, organization_id FROM public.organizations_users WHERE FALSE")

    def group_ticket_ids(self, group_id: int) -> list[int]:
        """仅查询专用组工单集合，不把实例总量当作基线规模。"""
        _ticket_id(group_id)
        rows = self._rows(
            "SELECT id FROM public.tickets WHERE group_id = %s ORDER BY id", (group_id,)
        )
        self.recorder.record("db-group-tickets", {"group_id": group_id, "result": rows})
        return [row["id"] for row in rows]

    def user_organizations(self, user_id: int) -> list[dict]:
        """合并主次组织并保留悬挂关联错误，不能静默当作无共享。"""
        _ticket_id(user_id)
        rows = self._rows(
            "WITH linked AS (SELECT organization_id AS id FROM public.users "
            "WHERE id = %s AND organization_id IS NOT NULL UNION SELECT organization_id "
            "FROM public.organizations_users WHERE user_id = %s) "
            "SELECT l.id,o.shared,o.active FROM linked l LEFT JOIN public.organizations o "
            "ON o.id=l.id ORDER BY l.id",
            (user_id, user_id),
        )
        if any(
            not isinstance(row.get("shared"), bool) or not isinstance(row.get("active"), bool)
            for row in rows
        ):
            raise DbError("组织关联证据缺失或格式无效") from None
        self.recorder.record("db-user-organizations", {"user_id": user_id, "result": rows})
        return rows

    def check_user_access(self) -> None:
        """用户场景才探测新列，并拒绝用户或角色关联对象的写权限。"""
        rows = self._rows(
            "SELECT EXISTS (SELECT 1 FROM (VALUES ('public.users'), ('public.roles'), "
            "('public.roles_users')) AS objects(name) WHERE "
            "pg_catalog.has_table_privilege(current_user, objects.name, "
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
            "pg_catalog.has_any_column_privilege(current_user, objects.name, "
            "'INSERT,UPDATE,REFERENCES')) AS object_write"
        )
        if rows != [{"object_write": False}]:
            raise DbError("用户及角色关联查询要求独立只读权限") from None
        for sql in (
            f"SELECT {_USER_COLUMNS} FROM public.users WHERE FALSE",
            "SELECT id, name, active FROM public.roles WHERE FALSE",
            "SELECT user_id, role_id FROM public.roles_users WHERE FALSE",
        ):
            self._rows(sql)
        self.recorder.record("db-user-access", {"result": "ready"})

    def user(self, user_id: int) -> dict | None:
        """按明确用户 ID 返回必要非凭据字段，不存在返回 None。"""
        _ticket_id(user_id)
        rows = self._rows(f"SELECT {_USER_COLUMNS} FROM public.users WHERE id = %s", (user_id,))
        result = rows[0] if rows else None
        self.recorder.record("db-user", {"user_id": user_id, "result": result})
        return result

    def users_by_login(self, login: str) -> list[dict]:
        """按完整合成登录名查询，采用与应用一致的不区分大小写比较。"""
        if not isinstance(login, str) or not login or len(login) > 255:
            raise DbError("用户登录名查询参数无效") from None
        rows = self._rows(
            f"SELECT {_USER_COLUMNS} FROM public.users WHERE lower(login) = lower(%s) ORDER BY id",
            (login,),
        )
        self.recorder.record("db-user-login", {"login": login, "result": rows})
        return rows

    def user_roles(self, user_id: int) -> list[dict]:
        """核对指定用户角色关联，删除后仍能发现残留的关联键。"""
        _ticket_id(user_id)
        rows = self._rows(
            "SELECT ru.role_id AS id, r.name, r.active FROM public.roles_users AS ru "
            "LEFT JOIN public.roles AS r ON r.id = ru.role_id WHERE ru.user_id = %s "
            "ORDER BY ru.role_id",
            (user_id,),
        )
        self.recorder.record("db-user-roles", {"user_id": user_id, "result": rows})
        return rows

    def user_inventory(self) -> dict:
        """不存在用户读取只比较数量及最大 ID，不导出全实例用户资料。"""
        rows = self._rows(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM public.users"
        )
        if (
            len(rows) != 1
            or set(rows[0]) != {"count", "max_id"}
            or any(type(value) is not int or value < 0 for value in rows[0].values())
        ):
            raise DbError("用户数量证据缺失或格式无效") from None
        self.recorder.record("db-user-inventory", rows[0])
        return rows[0]

    def check_permission_access(self) -> None:
        """仅权限用例核验新增关联列及无写权限，不影响旧用例前置。"""
        rows = self._rows(
            "SELECT EXISTS (SELECT 1 FROM (VALUES ('public.groups_users'), "
            "('public.roles_groups'), ('public.permissions_roles'), ('public.permissions')) "
            "AS objects(name) WHERE pg_catalog.has_table_privilege(current_user, objects.name, "
            "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
            "pg_catalog.has_any_column_privilege(current_user, objects.name, "
            "'INSERT,UPDATE,REFERENCES')) AS object_write"
        )
        if rows != [{"object_write": False}]:
            raise DbError("权限关联查询要求独立只读权限") from None
        for sql in (
            "SELECT user_id, group_id, access FROM public.groups_users WHERE FALSE",
            "SELECT role_id, group_id, access FROM public.roles_groups WHERE FALSE",
            "SELECT role_id, permission_id FROM public.permissions_roles WHERE FALSE",
            "SELECT id, name, active FROM public.permissions WHERE FALSE",
        ):
            self._rows(sql)
        self.recorder.record("db-permission-access", {"result": "ready"})

    def check_role_access(self) -> None:
        """角色用例写入前检查已批准列及关联只读权限。"""
        self.check_user_access()
        self.check_permission_access()
        self._rows("SELECT id, name, active, default_at_signup FROM public.roles WHERE FALSE")

    def role(self, role_id: int) -> dict | None:
        """按明确ID读取角色安全属性，不读未授权审计列。"""
        _ticket_id(role_id)
        rows = self._rows(
            "SELECT id, name, active, default_at_signup FROM public.roles WHERE id = %s", (role_id,)
        )
        result = rows[0] if rows else None
        self.recorder.record("db-role", {"role_id": role_id, "result": result})
        return result

    def role_ids(self) -> list[int]:
        """仅获取ID集合用于识别空名称负向请求是否新建对象。"""
        rows = self._rows("SELECT id FROM public.roles ORDER BY id")
        return [row["id"] for row in rows]

    def roles_named(self, name: str) -> list[dict]:
        """大小写不敏感匹配角色名，允许故意为空的负向输入。"""
        if not isinstance(name, str) or len(name) > 255:
            raise DbError("角色名称参数无效") from None
        rows = self._rows(
            "SELECT id, name, active, default_at_signup FROM public.roles "
            "WHERE lower(name) = lower(%s) ORDER BY id",
            (name,),
        )
        self.recorder.record("db-roles-named", {"name": name, "rows": rows})
        return rows

    def role_permissions(self, role_id: int) -> list[dict]:
        """读取单个角色的精确权限集合，包含活跃状态。"""
        _ticket_id(role_id)
        rows = self._rows(
            "SELECT p.id, p.name, p.active FROM public.permissions AS p "
            "JOIN public.permissions_roles AS pr ON pr.permission_id = p.id "
            "WHERE pr.role_id = %s ORDER BY p.id",
            (role_id,),
        )
        self.recorder.record("db-role-permissions", {"role_id": role_id, "rows": rows})
        return rows

    def role_groups(self, role_id: int) -> list[dict]:
        """读取单个角色的组关联，用于确认获准保留时没有继承授权。"""
        _ticket_id(role_id)
        return self._rows(
            "SELECT group_id, access FROM public.roles_groups WHERE role_id = %s "
            "ORDER BY group_id, access",
            (role_id,),
        )

    def role_inventory(self, exclude_id: int | None = None) -> dict:
        """在服务端比较角色及关联，排除本次新对象后证明旧角色未变。"""
        if exclude_id is not None:
            _ticket_id(exclude_id)
        rows = self._rows(
            "WITH selected AS (SELECT id,name,active,default_at_signup FROM public.roles "
            "WHERE (%s::bigint IS NULL OR id <> %s)), "
            "items AS (SELECT 'role' AS kind, id AS rid, "
            "ROW(id,name,active,default_at_signup)::text AS value FROM selected UNION ALL "
            "SELECT 'permission',pr.role_id,ROW(pr.permission_id)::text FROM public.permissions_roles pr "
            "JOIN selected r ON r.id=pr.role_id UNION ALL "
            "SELECT 'group',rg.role_id,ROW(rg.group_id,rg.access)::text FROM public.roles_groups rg "
            "JOIN selected r ON r.id=rg.role_id UNION ALL "
            "SELECT 'user',ru.role_id,ROW(ru.user_id)::text FROM public.roles_users ru "
            "JOIN selected r ON r.id=ru.role_id) "
            "SELECT (SELECT count(*) FROM selected) AS count, "
            "md5(COALESCE(string_agg(ROW(kind,rid,value)::text,'' ORDER BY kind,rid,value),'')) "
            "AS fingerprint FROM items",
            (exclude_id, exclude_id),
        )
        if (
            len(rows) != 1
            or set(rows[0]) != {"count", "fingerprint"}
            or type(rows[0]["count"]) is not int
            or rows[0]["count"] < 0
            or not isinstance(rows[0]["fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", rows[0]["fingerprint"])
        ):
            raise DbError("角色集合摘要证据无效") from None
        self.recorder.record("db-role-inventory", {"exclude_id": exclude_id, **rows[0]})
        return rows[0]

    def _check_configuration_access(self, tables: tuple[str, ...]) -> None:
        """只接收代码内配置表，检查读取账号没有目标写权限。"""
        for table in tables:
            if table not in {
                "templates",
                "text_modules",
                "groups_text_modules",
                "macros",
                "groups_macros",
            }:
                raise DbError("配置查询表不在允许范围") from None
            rows = self._rows(
                "SELECT (pg_catalog.has_table_privilege(current_user,%s,"
                "'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') OR "
                "pg_catalog.has_any_column_privilege(current_user,%s,'INSERT,UPDATE,REFERENCES')) AS object_write",
                (f"public.{table}", f"public.{table}"),
            )
            if rows != [{"object_write": False}]:
                raise DbError("配置对象查询要求独立只读权限") from None

    def check_template_access(self) -> None:
        """仅模板用例需要本表五个已批准列。"""
        self._check_configuration_access(("templates",))
        self._rows(f"SELECT {_TEMPLATE_COLUMNS} FROM public.templates WHERE FALSE")

    def check_text_module_access(self) -> None:
        """仅文本用例需要内容、备注及组关联的最小列。"""
        self._check_configuration_access(("text_modules", "groups_text_modules"))
        self._rows(f"SELECT {_TEXT_MODULE_COLUMNS} FROM public.text_modules WHERE FALSE")
        self._rows("SELECT text_module_id, group_id FROM public.groups_text_modules WHERE FALSE")

    @staticmethod
    def _template_row(row: dict) -> dict:
        """安全解析Rails store文本，不运行标签或吞掉损坏的配置。"""
        return Queries._store_row(row, "options")

    @staticmethod
    def _store_row(row: dict, field: str) -> dict:
        """两种明确store字段共享安全解码，不执行任意YAML对象。"""
        try:
            if field not in {"options", "perform"}:
                raise ValueError
            value = row[field]
            if isinstance(value, str):
                value = yaml.load(value, Loader=_ConfigLoader)
            if not isinstance(value, dict):
                raise ValueError
        except (ValueError, TypeError, KeyError, yaml.YAMLError):
            raise DbError("配置序列化格式无效") from None
        return {**row, field: value}

    def check_macro_access(self) -> None:
        """宏用例才探测动作与组关联的最小只读列。"""
        self._check_configuration_access(("macros", "groups_macros"))
        self._rows(f"SELECT {_MACRO_COLUMNS} FROM public.macros WHERE FALSE")
        self._rows("SELECT macro_id, group_id FROM public.groups_macros WHERE FALSE")

    def macro(self, object_id: int) -> dict | None:
        """按明确ID安全解码宏动作，不读取其他宏内容。"""
        _ticket_id(object_id)
        rows = self._rows(f"SELECT {_MACRO_COLUMNS} FROM public.macros WHERE id = %s", (object_id,))
        value = self._store_row(rows[0], "perform") if rows else None
        self.recorder.record("db-macro", {"id": object_id, "result": value})
        return value

    def macro_ids(self) -> list[int]:
        """只读取宏ID集合，识别新增而不导出现有配置。"""
        return [row["id"] for row in self._rows("SELECT id FROM public.macros ORDER BY id")]

    def macros_named(self, name: str) -> list[dict]:
        """按唯一运行名称定位宏，值以参数绑定。"""
        if not isinstance(name, str) or not name or len(name) > 255:
            raise DbError("宏名称参数无效") from None
        rows = self._rows(
            f"SELECT {_MACRO_COLUMNS} FROM public.macros WHERE name = %s ORDER BY id", (name,)
        )
        return [self._store_row(row, "perform") for row in rows]

    def macro_groups(self, object_id: int) -> list[int]:
        """核对明确宏的组集合，删除后确认关联无残留。"""
        _ticket_id(object_id)
        return [
            row["group_id"]
            for row in self._rows(
                "SELECT group_id FROM public.groups_macros WHERE macro_id = %s ORDER BY group_id",
                (object_id,),
            )
        ]

    def macro_inventory(self, exclude_id: int | None = None) -> dict:
        """宏及组关联整体摘要用于保护既有配置。"""
        return self._configuration_inventory("macro", exclude_id)

    def template(self, object_id: int) -> dict | None:
        """按明确ID读取模板并安全解码预填字段。"""
        _ticket_id(object_id)
        rows = self._rows(
            f"SELECT {_TEMPLATE_COLUMNS} FROM public.templates WHERE id = %s", (object_id,)
        )
        value = self._template_row(rows[0]) if rows else None
        self.recorder.record("db-template", {"id": object_id, "result": value})
        return value

    def template_ids(self) -> list[int]:
        """只读模板ID集合，识别空白名称场景的异常新增。"""
        return [row["id"] for row in self._rows("SELECT id FROM public.templates ORDER BY id")]

    def templates_named(self, name: str) -> list[dict]:
        """精确按原始名称查询，负向空格不被strip改写。"""
        if not isinstance(name, str) or len(name) > 255:
            raise DbError("模板名称参数无效") from None
        rows = self._rows(
            f"SELECT {_TEMPLATE_COLUMNS} FROM public.templates WHERE name = %s ORDER BY id", (name,)
        )
        return [self._template_row(row) for row in rows]

    def text_module(self, object_id: int) -> dict | None:
        """按明确ID读取文本模块的已授权内容及备注。"""
        _ticket_id(object_id)
        rows = self._rows(
            f"SELECT {_TEXT_MODULE_COLUMNS} FROM public.text_modules WHERE id = %s", (object_id,)
        )
        value = rows[0] if rows else None
        self.recorder.record("db-text-module", {"id": object_id, "result": value})
        return value

    def text_module_ids(self) -> list[int]:
        """仅取ID集合定位本轮异常新增，不导出其他文本正文。"""
        return [row["id"] for row in self._rows("SELECT id FROM public.text_modules ORDER BY id")]

    def text_modules_named(self, name: str) -> list[dict]:
        """按本轮唯一名称定位模块，不以搜索索引为空证明未保存。"""
        if not isinstance(name, str) or not name or len(name) > 255:
            raise DbError("文本模块名称参数无效") from None
        return self._rows(
            f"SELECT {_TEXT_MODULE_COLUMNS} FROM public.text_modules WHERE name = %s ORDER BY id",
            (name,),
        )

    def text_module_groups(self, object_id: int) -> list[int]:
        """核对指定文本模块的组ID，删除后还需确认无悬挂关联。"""
        _ticket_id(object_id)
        return [
            row["group_id"]
            for row in self._rows(
                "SELECT group_id FROM public.groups_text_modules WHERE text_module_id = %s ORDER BY group_id",
                (object_id,),
            )
        ]

    def _configuration_inventory(self, kind: str, exclude_id: int | None) -> dict:
        """仅对明确配置计算服务端摘要，不把既有业务内容导出。"""
        if exclude_id is not None:
            _ticket_id(exclude_id)
        if kind == "template":
            table, columns = "templates", _TEMPLATE_COLUMNS
            groups = ""
        elif kind == "text_module":
            table, columns = "text_modules", _TEXT_MODULE_COLUMNS
            groups = (
                " UNION ALL SELECT 'group',g.text_module_id,ROW(g.group_id)::text "
                "FROM public.groups_text_modules g JOIN selected r ON r.id=g.text_module_id"
            )
        elif kind == "macro":
            table, columns = "macros", _MACRO_COLUMNS
            groups = (
                " UNION ALL SELECT 'group',g.macro_id,ROW(g.group_id)::text "
                "FROM public.groups_macros g JOIN selected r ON r.id=g.macro_id"
            )
        else:
            raise DbError("配置摘要类型无效") from None
        rows = self._rows(
            f"WITH selected AS (SELECT {columns} FROM public.{table} "
            "WHERE (%s::bigint IS NULL OR id<>%s)), items AS (SELECT 'object' AS kind,id AS oid,"
            f"ROW({columns})::text AS value FROM selected{groups}) "
            "SELECT (SELECT count(*) FROM selected) AS count, "
            "md5(COALESCE(string_agg(ROW(kind,oid,value)::text,'' ORDER BY kind,oid,value),'')) "
            "AS fingerprint FROM items",
            (exclude_id, exclude_id),
        )
        if (
            len(rows) != 1
            or set(rows[0]) != {"count", "fingerprint"}
            or type(rows[0]["count"]) is not int
            or rows[0]["count"] < 0
            or not isinstance(rows[0]["fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", rows[0]["fingerprint"])
        ):
            raise DbError("配置集合摘要证据无效") from None
        self.recorder.record(f"db-{kind}-inventory", {"exclude_id": exclude_id, **rows[0]})
        return rows[0]

    def template_inventory(self, exclude_id: int | None = None) -> dict:
        """模板修改前后核对既有配置或排除本轮明确ID。"""
        return self._configuration_inventory("template", exclude_id)

    def text_module_inventory(self, exclude_id: int | None = None) -> dict:
        """文本模块及组关联一同摘要，清理不能遗漏组关系。"""
        return self._configuration_inventory("text_module", exclude_id)

    def permission(self, name: str) -> dict | None:
        """参数化读取明确权限名称，不接受查询表达式。"""
        if not isinstance(name, str) or not name or len(name) > 255:
            raise DbError("权限名称参数无效") from None
        rows = self._rows(
            "SELECT id, name, active FROM public.permissions WHERE name = %s", (name,)
        )
        if len(rows) > 1:
            raise DbError("权限名称映射不唯一") from None
        return rows[0] if rows else None

    def user_groups(self, user_id: int) -> list[dict]:
        """读取本条用户的直接组授权，保留每个access值。"""
        _ticket_id(user_id)
        rows = self._rows(
            "SELECT group_id, access FROM public.groups_users WHERE user_id = %s ORDER BY group_id, access",
            (user_id,),
        )
        self.recorder.record("db-user-groups", {"user_id": user_id, "result": rows})
        return rows

    def inherited_groups(self, user_id: int) -> list[dict]:
        """按用户当前角色取得继承组权限，角色活跃状态单独核验。"""
        _ticket_id(user_id)
        rows = self._rows(
            "SELECT rg.role_id, rg.group_id, rg.access FROM public.roles_groups AS rg "
            "JOIN public.roles_users AS ru ON ru.role_id = rg.role_id WHERE ru.user_id = %s "
            "ORDER BY rg.role_id, rg.group_id, rg.access",
            (user_id,),
        )
        self.recorder.record("db-inherited-groups", {"user_id": user_id, "result": rows})
        return rows

    def user_permissions(self, user_id: int) -> list[dict]:
        """读取当前角色关联的真实权限，避免Token范围遮蔽提权。"""
        _ticket_id(user_id)
        rows = self._rows(
            "SELECT DISTINCT p.id, p.name, p.active FROM public.permissions AS p "
            "JOIN public.permissions_roles AS pr ON pr.permission_id = p.id "
            "JOIN public.roles_users AS ru ON ru.role_id = pr.role_id "
            "WHERE ru.user_id = %s ORDER BY p.id",
            (user_id,),
        )
        self.recorder.record("db-user-permissions", {"user_id": user_id, "result": rows})
        return rows

    def role_users(self, role_id: int) -> list[int]:
        """只核查临时管理角色的关联用户ID，不导出其他用户资料。"""
        _ticket_id(role_id)
        return [
            row["user_id"]
            for row in self._rows(
                "SELECT user_id FROM public.roles_users WHERE role_id = %s ORDER BY user_id",
                (role_id,),
            )
        ]

    def other_user_groups(self, user_id: int) -> dict:
        """在服务端比较排除本条U的授权集合，仅返回数量和摘要。"""
        _ticket_id(user_id)
        rows = self._rows(
            "SELECT COUNT(*) AS count, md5(COALESCE(string_agg("
            "ROW(kind, actor_id, group_id, access)::text, '' ORDER BY kind, actor_id, group_id, access), '')) "
            "AS fingerprint FROM (SELECT 'user' AS kind, user_id AS actor_id, group_id, access "
            "FROM public.groups_users WHERE user_id <> %s UNION ALL SELECT 'role', role_id, "
            "group_id, access FROM public.roles_groups UNION ALL SELECT 'membership', user_id, "
            "role_id, '' FROM public.roles_users WHERE user_id <> %s) AS grants",
            (user_id, user_id),
        )
        if (
            len(rows) != 1
            or set(rows[0]) != {"count", "fingerprint"}
            or type(rows[0]["count"]) is not int
            or rows[0]["count"] < 0
            or not isinstance(rows[0]["fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", rows[0]["fingerprint"])
        ):
            raise DbError("其他用户组权限摘要证据无效") from None
        self.recorder.record("db-other-user-groups", rows[0])
        return rows[0]

    def check_pending_access(self) -> None:
        """仅M11在造数据前核验pending_time列权限，不扩展旧用例的必需列。"""
        self._rows("SELECT pending_time FROM public.tickets WHERE FALSE")
        self.recorder.record("db-pending-access", {"query": "pending-access", "result": "ready"})

    def ticket_pending(self, ticket_id: int) -> dict | None:
        """按ID读取状态及挂起时间，明确将Rails的无时区存储值解释为UTC。"""
        _ticket_id(ticket_id)
        rows = self._rows(
            "SELECT id, state_id, pending_time AT TIME ZONE 'UTC' AS pending_time "
            "FROM public.tickets WHERE id = %s",
            (ticket_id,),
        )
        result = rows[0] if rows else None
        self.recorder.record(
            "db-pending", {"query": "ticket-pending", "ticket_id": ticket_id, "result": result}
        )
        return result

    def ticket_inventory(self) -> dict:
        """只返回已授权工单列的服务端摘要，用于核对不存在目标的更新无副作用。"""
        rows = self._rows(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id, "
            f"md5(COALESCE(string_agg(md5(ROW({_TICKET_COLUMNS})::text), '' ORDER BY id), '')) "
            "AS fingerprint FROM public.tickets"
        )
        if len(rows) != 1:
            raise DbError("工单摘要证据缺失") from None
        value = rows[0]
        if (
            set(value) != {"count", "max_id", "fingerprint"}
            or type(value["count"]) is not int
            or value["count"] < 0
            or type(value["max_id"]) is not int
            or value["max_id"] < 0
            or not isinstance(value["fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["fingerprint"])
        ):
            raise DbError("工单摘要证据格式不符") from None
        self.recorder.record("db-inventory", {"query": "ticket-inventory", "result": value})
        return value

    def articles(self, ticket_id: int) -> list[dict]:
        """仅读取指定工单文章，并按 ID 固定顺序返回。"""
        _ticket_id(ticket_id)
        result = self._rows(
            f"SELECT {_ARTICLE_COLUMNS} FROM public.ticket_articles WHERE ticket_id = %s ORDER BY id",
            (ticket_id,),
        )
        self.recorder.record(
            "db-articles", {"query": "articles", "ticket_id": ticket_id, "result": result}
        )
        return result

    def by_marker(self, marker: str) -> dict[str, list[dict]]:
        """按完整 RUN 和 TC 字面标记定位本次工单及文章残留。"""
        if (
            not isinstance(marker, str)
            or len(marker) > 200
            or not re.fullmatch(
                r"RUN_[A-Za-z0-9][A-Za-z0-9_-]*_TC_[0-9]+(?:_[A-Za-z0-9_-]+)?", marker
            )
        ):
            raise DbError("数据库查询需要安全的 RUN 和 TC 标记") from None
        result = {
            "tickets": self._rows(
                f"SELECT {_TICKET_COLUMNS} FROM public.tickets WHERE POSITION(%s IN title) > 0 ORDER BY id",
                (marker,),
            ),
            "articles": self._rows(
                f"SELECT {_ARTICLE_COLUMNS} FROM public.ticket_articles WHERE POSITION(%s IN body) > 0 ORDER BY id",
                (marker,),
            ),
        }
        self.recorder.record(
            "db-marker", {"query": "by-marker", "marker": marker, "result": result}
        )
        return result

    def article_type_exists(self, name: str) -> bool:
        """参数化确认指定文章类型是否存在，不读取整个字典。"""
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise DbError("文章类型名称无效") from None
        rows = self._rows(
            "SELECT EXISTS(SELECT 1 FROM public.ticket_article_types WHERE name = %s) AS present",
            (name,),
        )
        result = rows[0]["present"]
        self.recorder.record(
            "db-article-type", {"query": "article-type-exists", "name": name, "result": result}
        )
        return result

    def ticket_ids_for_creator(self, group_id: int, customer_id: int, creator_id: int) -> list[int]:
        """在专用组、客户及创建人范围对比ID，覆盖无标题且无文章的孤儿工单。"""
        for value in (group_id, customer_id, creator_id):
            _ticket_id(value)
        rows = self._rows(
            "SELECT id FROM public.tickets WHERE group_id = %s AND customer_id = %s "
            "AND created_by_id = %s ORDER BY id",
            (group_id, customer_id, creator_id),
        )
        result = [row["id"] for row in rows]
        self.recorder.record(
            "db-creator-scope",
            {
                "group_id": group_id,
                "customer_id": customer_id,
                "creator_id": creator_id,
                "ticket_ids": result,
            },
        )
        return result

    def check_attachment_access(self) -> None:
        """仅附件用例在造数据前探测已批准的元数据列权限。"""
        self._rows(
            "SELECT id, store_object_id, o_id, size, filename FROM public.stores WHERE FALSE"
        )
        self._rows("SELECT id, name FROM public.store_objects WHERE FALSE")

    def attachments(self, article_id: int) -> list[dict]:
        """参数化读取指定文章附件元数据，不读取文件存储正文。"""
        _ticket_id(article_id)
        rows = self._rows(
            "SELECT s.id, s.o_id AS article_id, s.filename, s.size "
            "FROM public.stores AS s JOIN public.store_objects AS o ON o.id = s.store_object_id "
            "WHERE o.name = 'Ticket::Article' AND s.o_id = %s ORDER BY s.id",
            (str(article_id),),
        )
        result = []
        seen = set()
        for row in rows:
            if (
                set(row) != {"id", "article_id", "filename", "size"}
                or type(row["id"]) is not int
                or row["id"] <= 0
                or row["id"] in seen
                or row["article_id"] != str(article_id)
                or not isinstance(row["filename"], str)
                or not row["filename"]
                or not isinstance(row["size"], str)
                or not re.fullmatch(r"[0-9]{1,20}", row["size"])
            ):
                raise DbError("附件元数据证据无效") from None
            seen.add(row["id"])
            result.append({**row, "article_id": article_id, "size": int(row["size"])})
        self.recorder.record("db-attachments", {"article_id": article_id, "rows": result})
        return result
