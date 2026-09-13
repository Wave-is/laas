"""Optional Aider CLI integration using its documented OpenAI-compatible endpoint."""
import os
import shutil
from pathlib import Path
from ..base import AgentRuntimeAdapter,Result,Support,probe,unsupported
from ...supervisor import supervisor

class AiderAdapter(AgentRuntimeAdapter):
    def detect(self):
        exe=self.settings.get('executable') or shutil.which('aider')
        self.command=[exe] if exe else []
        self.frontends=[{'id':'aider-terminal','runtime_id':self.id,'name':'Aider Terminal','type':'terminal','optional':True,'status':'INSTALLED' if exe else 'NOT INSTALLED'}]
        if not exe:return unsupported('Aider is optional and is not installed')
        try:
            self.version=probe(self.command+['--version']);self.help_text=probe(self.command+['--help'])
            return Result(Support.SUPPORTED,'Aider CLI detected',{'version':self.version})
        except Exception as exc:return Result(Support.DEGRADED,str(exc))
    def get_config_locations(self,workspace=None):
        return Result(Support.SUPPORTED,data={'user':str(Path.home()/'.aider.conf.yml')})
    def start(self,workspace=None,model=None):
        if not self.command:return unsupported('Aider is not installed')
        if not model:return unsupported('Select a Station model before opening Aider')
        if '--model' not in self.help_text:return unsupported('Installed model selection flag was not confirmed')
        args=self.command+['--model','openai/'+(model.backend_model_id)]
        env=dict(OPENAI_API_BASE=model.endpoint,OPENAI_API_KEY='local-station')
        try:return Result(Support.SUPPORTED,data=supervisor.start('agent:'+self.id,args,cwd=workspace,env=env,visible=True))
        except Exception as exc:return Result(Support.ERROR,str(exc))
