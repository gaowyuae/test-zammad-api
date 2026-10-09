"""只读复核获准保留的角色，不自动修复历史对象。"""

from fixtures.preflight import PreflightError


def check_retained_roles(run, db) -> None:
    """任何后续业务准备前核对角色隔离状态，漂移则停止。"""
    resources = run.retained_roles
    if not resources:
        return
    db.check_role_access()
    seen = set()
    for item in resources:
        role_id = item["role_id"]
        if role_id in seen:
            raise PreflightError("历史保留角色重复登记，需人工核查") from None
        seen.add(role_id)
        expected = {
            "id": role_id,
            "name": item["name"],
            "active": False,
            "default_at_signup": False,
        }
        if (
            db.role(role_id) != expected
            or db.role_permissions(role_id)
            or db.role_groups(role_id)
            or db.role_users(role_id)
        ):
            raise PreflightError("获准保留角色状态发生变化，需人工核查") from None
    db.recorder.record("retained-roles-checked", {"role_ids": sorted(seen), "result": "isolated"})
