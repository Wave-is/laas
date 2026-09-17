from ..base import AgentRuntimeAdapter, Result, Support

class MockAdapter(AgentRuntimeAdapter):
    def detect(self):
        return Result(Support.SUPPORTED, 'Тестовый агент (только для автотестов)', {'installed': True})
