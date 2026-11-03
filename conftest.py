"""集中注册项目 pytest 插件。"""

pytest_plugins = [
    "fixtures.plugins.options",
    "fixtures.plugins.environment",
    "fixtures.plugins.tickets",
    "fixtures.plugins.users",
    "fixtures.plugins.user_permissions",
    "fixtures.plugins.roles",
    "fixtures.plugins.templates",
    "fixtures.plugins.text_modules",
    "fixtures.plugins.macros",
    "fixtures.plugins.search",
]
