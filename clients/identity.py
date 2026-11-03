"""从指定环境变量读取凭据，不在对象展示中包含秘密。"""

import os
from dataclasses import dataclass, field


class IdentityError(ValueError):
    """身份输入错误，仅包含固定安全消息。"""


@dataclass(frozen=True)
class Identity:
    """身份代号与隐藏于展示的 Token。"""

    alias: str
    token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, alias: str, env_name: str) -> "Identity":
        """从指定环境变量读取非空 Token。"""
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
