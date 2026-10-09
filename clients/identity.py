"""从指定环境变量读取凭据，不在对象展示中包含秘密。"""

import os
import re
from dataclasses import dataclass, field


class IdentityError(ValueError):
    """身份输入错误，仅包含固定安全消息。"""


@dataclass(frozen=True)
class Identity:
    """安全身份代号与隐藏于展示的 Token。"""

    alias: str
    token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """阻止直接构造绕过别名和凭据校验。"""
        if not isinstance(self.alias, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", self.alias
        ):
            raise IdentityError("身份代号无效") from None
        if self.token is not None and (
            not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9._~-]+", self.token)
        ):
            raise IdentityError("身份凭据无效") from None

    @classmethod
    def from_env(cls, alias: str, env_name: str) -> "Identity":
        """只从明确指定的环境变量读取有效 Token。"""
        if not isinstance(env_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
            raise IdentityError("凭据环境变量名称无效") from None
        token = os.environ.get(env_name)
        if not token:
            raise IdentityError("身份凭据缺失") from None
        return cls(alias, token)

    @classmethod
    def anonymous(cls) -> "Identity":
        """创建明确无认证的匿名身份。"""
        return cls("anonymous")

    @property
    def authorization(self) -> str | None:
        """返回发送使用的认证头，调用者不得直接输出。"""
        return None if self.token is None else f"Token token={self.token}"
