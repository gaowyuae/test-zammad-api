"""模板业务 fixture 注册。"""

import pytest

from fixtures.plugins._configuration import configuration_case


@pytest.fixture
def template_case(environment_run, request):
    """模板用例独立会话和资源，在同一整轮锁内完成。"""
    yield from configuration_case(environment_run, request, "template")
