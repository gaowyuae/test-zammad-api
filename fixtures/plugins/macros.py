"""宏业务 fixture 注册。"""

import pytest

from fixtures.plugins._configuration import configuration_case


@pytest.fixture
def macro_case(environment_run, request):
    """宏与跨组工单在同一整轮锁下独立准备和清理。"""
    yield from configuration_case(environment_run, request, "macro")
