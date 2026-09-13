from ..base import AgentRuntimeAdapter, Result, Support

class MockAdapter(AgentRuntimeAdapter):
    def detect(self):
        return Result(Support.SUPPORTED, 'Explicit test fixture', {'installed': True})
