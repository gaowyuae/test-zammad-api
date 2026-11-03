"""文本模块业务 fixture 注册。"""

import pytest

from fixtures.plugins._configuration import configuration_case


@pytest.fixture
def text_module_case(environment_run, request):
    """文本模块用例按指定身份读取，维护身份负责清理。"""
    yield from configuration_case(environment_run, request, "text_module")
