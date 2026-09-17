from ..base import AgentRuntimeAdapter, Result, Support
from ...i18n import tr

class MockAdapter(AgentRuntimeAdapter):
    def detect(self):
        return Result(Support.SUPPORTED, tr('Тестовый агент (только для автотестов)'), {'installed': True})
