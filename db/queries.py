"""兼容现有调用的查询入口，统一管理会话并委托领域查询。"""

from types import TracebackType

from db.base import DbError, DbSession
from db.config import DbConfig
from db.sql import (
    access,
    articles,
    macros,
    organizations,
    permissions,
    roles,
    templates,
    text_modules,
    tickets,
    users,
)
from reporting.evidence import EvidenceRecorder

__all__ = ["DbError", "Queries"]


class Queries:
    """一个用例使用一个会话，领域模块共享连接与证据器。"""

    def __init__(self, config: DbConfig, recorder: EvidenceRecorder) -> None:
        """保留原入口配置和证据器，延迟建立连接。"""
        self.config = config
        self.recorder = recorder
        self._session = DbSession(config, recorder)

    def __enter__(self) -> "Queries":
        """连接后完成基础只读检查，再向调用方提供查询入口。"""
        self._session.__enter__()
        try:
            access.check_account(self._session)
            tickets.check_access(self._session)
            articles.check_access(self._session)
        except BaseException:
            try:
                self.close()
            except DbError:
                pass
            raise
        self.recorder.record("db-access", {"query": "readonly-account", "result": "ready"})
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """业务断言结束后关闭本用例连接。"""
        self.close()

    def close(self) -> None:
        """释放统一持有的数据库会话。"""
        self._session.close()

    def ticket_number_exists(self, number: str) -> bool:
        """通过精确编号确认空搜索的前置，不读取无关工单内容。"""
        return tickets.ticket_number_exists(self._session, number)

    def ticket(self, ticket_id: int) -> dict | None:
        """按工单 ID 返回必要字段快照，不存在时返回 None。"""
        return tickets.ticket(self._session, ticket_id)

    def group_ticket_ids(self, group_id: int) -> list[int]:
        """仅查询专用组工单集合，不把实例总量当作基线规模。"""
        return tickets.group_ticket_ids(self._session, group_id)

    def check_pending_access(self) -> None:
        """仅M11在造数据前核验pending_time列权限，不扩展旧用例的必需列。"""
        return tickets.check_pending_access(self._session)

    def ticket_pending(self, ticket_id: int) -> dict | None:
        """按ID读取状态及挂起时间，明确将Rails的无时区存储值解释为UTC。"""
        return tickets.ticket_pending(self._session, ticket_id)

    def ticket_inventory(self) -> dict:
        """只返回已授权工单列的服务端摘要，用于核对不存在目标的更新无副作用。"""
        return tickets.ticket_inventory(self._session)

    def by_marker(self, marker: str) -> dict[str, list[dict]]:
        """按完整 RUN 和 TC 字面标记定位本次工单及文章残留。"""
        return tickets.by_marker(self._session, marker)

    def ticket_ids_for_creator(self, group_id: int, customer_id: int, creator_id: int) -> list[int]:
        """在专用组、客户及创建人范围对比ID，覆盖无标题且无文章的孤儿工单。"""
        return tickets.ticket_ids_for_creator(self._session, group_id, customer_id, creator_id)

    def articles(self, ticket_id: int) -> list[dict]:
        """仅读取指定工单文章，并按 ID 固定顺序返回。"""
        return articles.articles(self._session, ticket_id)

    def article_type_exists(self, name: str) -> bool:
        """参数化确认指定文章类型是否存在，不读取整个字典。"""
        return articles.article_type_exists(self._session, name)

    def check_attachment_access(self) -> None:
        """仅附件用例在造数据前探测已批准的元数据列权限。"""
        return articles.check_attachment_access(self._session)

    def attachments(self, article_id: int) -> list[dict]:
        """参数化读取指定文章附件元数据，不读取文件存储正文。"""
        return articles.attachments(self._session, article_id)

    def check_user_access(self) -> None:
        """用户场景才探测新列，并拒绝用户或角色关联对象的写权限。"""
        return users.check_user_access(self._session)

    def user(self, user_id: int) -> dict | None:
        """按明确用户 ID 返回必要非凭据字段，不存在返回 None。"""
        return users.user(self._session, user_id)

    def users_by_login(self, login: str) -> list[dict]:
        """按完整合成登录名查询，采用与应用一致的不区分大小写比较。"""
        return users.users_by_login(self._session, login)

    def user_roles(self, user_id: int) -> list[dict]:
        """核对指定用户角色关联，删除后仍能发现残留的关联键。"""
        return users.user_roles(self._session, user_id)

    def user_inventory(self) -> dict:
        """不存在用户读取只比较数量及最大 ID，不导出全实例用户资料。"""
        return users.user_inventory(self._session)

    def check_search_access(self) -> None:
        """基线才校验主次组织最小列和相关只读边界。"""
        return organizations.check_search_access(self._session)

    def user_organizations(self, user_id: int) -> list[dict]:
        """合并主次组织并保留悬挂关联错误，不能静默当作无共享。"""
        return organizations.user_organizations(self._session, user_id)

    def check_permission_access(self) -> None:
        """仅权限用例核验新增关联列及无写权限，不影响旧用例前置。"""
        return permissions.check_permission_access(self._session)

    def permission(self, name: str) -> dict | None:
        """参数化读取明确权限名称，不接受查询表达式。"""
        return permissions.permission(self._session, name)

    def user_groups(self, user_id: int) -> list[dict]:
        """读取本条用户的直接组授权，保留每个access值。"""
        return permissions.user_groups(self._session, user_id)

    def inherited_groups(self, user_id: int) -> list[dict]:
        """按用户当前角色取得继承组权限，角色活跃状态单独核验。"""
        return permissions.inherited_groups(self._session, user_id)

    def user_permissions(self, user_id: int) -> list[dict]:
        """读取当前角色关联的真实权限，避免Token范围遮蔽提权。"""
        return permissions.user_permissions(self._session, user_id)

    def other_user_groups(self, user_id: int) -> dict:
        """在服务端比较排除本条U的授权集合，仅返回数量和摘要。"""
        return permissions.other_user_groups(self._session, user_id)

    def check_role_access(self) -> None:
        """角色用例写入前检查已批准列及关联只读权限。"""
        return roles.check_role_access(self._session)

    def role(self, role_id: int) -> dict | None:
        """按明确ID读取角色安全属性，不读未授权审计列。"""
        return roles.role(self._session, role_id)

    def role_ids(self) -> list[int]:
        """仅获取ID集合用于识别空名称负向请求是否新建对象。"""
        return roles.role_ids(self._session)

    def roles_named(self, name: str) -> list[dict]:
        """大小写不敏感匹配角色名，允许故意为空的负向输入。"""
        return roles.roles_named(self._session, name)

    def role_permissions(self, role_id: int) -> list[dict]:
        """读取单个角色的精确权限集合，包含活跃状态。"""
        return roles.role_permissions(self._session, role_id)

    def role_groups(self, role_id: int) -> list[dict]:
        """读取单个角色的组关联，用于确认获准保留时没有继承授权。"""
        return roles.role_groups(self._session, role_id)

    def role_inventory(self, exclude_id: int | None = None) -> dict:
        """在服务端比较角色及关联，排除本次新对象后证明旧角色未变。"""
        return roles.role_inventory(self._session, exclude_id)

    def role_users(self, role_id: int) -> list[int]:
        """只核查临时管理角色的关联用户ID，不导出其他用户资料。"""
        return roles.role_users(self._session, role_id)

    def check_template_access(self) -> None:
        """仅模板用例需要本表五个已批准列。"""
        return templates.check_template_access(self._session)

    def template(self, object_id: int) -> dict | None:
        """按明确ID读取模板并安全解码预填字段。"""
        return templates.template(self._session, object_id)

    def template_ids(self) -> list[int]:
        """只读模板ID集合，识别空白名称场景的异常新增。"""
        return templates.template_ids(self._session)

    def templates_named(self, name: str) -> list[dict]:
        """精确按原始名称查询，负向空格不被strip改写。"""
        return templates.templates_named(self._session, name)

    def template_inventory(self, exclude_id: int | None = None) -> dict:
        """模板修改前后核对既有配置或排除本轮明确ID。"""
        return templates.template_inventory(self._session, exclude_id)

    def check_text_module_access(self) -> None:
        """仅文本用例需要内容、备注及组关联的最小列。"""
        return text_modules.check_text_module_access(self._session)

    def text_module(self, object_id: int) -> dict | None:
        """按明确ID读取文本模块的已授权内容及备注。"""
        return text_modules.text_module(self._session, object_id)

    def text_module_ids(self) -> list[int]:
        """仅取ID集合定位本轮异常新增，不导出其他文本正文。"""
        return text_modules.text_module_ids(self._session)

    def text_modules_named(self, name: str) -> list[dict]:
        """按本轮唯一名称定位模块，不以搜索索引为空证明未保存。"""
        return text_modules.text_modules_named(self._session, name)

    def text_module_groups(self, object_id: int) -> list[int]:
        """核对指定文本模块的组ID，删除后还需确认无悬挂关联。"""
        return text_modules.text_module_groups(self._session, object_id)

    def text_module_inventory(self, exclude_id: int | None = None) -> dict:
        """文本模块及组关联一同摘要，清理不能遗漏组关系。"""
        return text_modules.text_module_inventory(self._session, exclude_id)

    def check_macro_access(self) -> None:
        """宏用例才探测动作与组关联的最小只读列。"""
        return macros.check_macro_access(self._session)

    def macro(self, object_id: int) -> dict | None:
        """按明确ID安全解码宏动作，不读取其他宏内容。"""
        return macros.macro(self._session, object_id)

    def macro_ids(self) -> list[int]:
        """只读取宏ID集合，识别新增而不导出现有配置。"""
        return macros.macro_ids(self._session)

    def macros_named(self, name: str) -> list[dict]:
        """按唯一运行名称定位宏，值以参数绑定。"""
        return macros.macros_named(self._session, name)

    def macro_groups(self, object_id: int) -> list[int]:
        """核对明确宏的组集合，删除后确认关联无残留。"""
        return macros.macro_groups(self._session, object_id)

    def macro_inventory(self, exclude_id: int | None = None) -> dict:
        """宏及组关联整体摘要用于保护既有配置。"""
        return macros.macro_inventory(self._session, exclude_id)
