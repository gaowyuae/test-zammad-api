"""试点所需的只读数据库配置与固定查询接口。"""

from db.config import DbConfig, load_db_config
from db.queries import DbError, Queries

__all__ = ["DbConfig", "DbError", "Queries", "load_db_config"]
