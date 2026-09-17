"""Optional Aider CLI integration using its documented OpenAI-compatible endpoint."""
import os
import shutil
from pathlib import Path
from ..base import AgentRuntimeAdapter,Result,Support,probe,unsupported
from ...supervisor import supervisor
from ...i18n import tr

class AiderAdapter(AgentRuntimeAdapter):
    def detect(self):
        exe=self.settings.get('executable') or shutil.which('aider')
        self.command=[exe] if exe else []
        self.frontends=[{'id':'aider-terminal','runtime_id':self.id,'name':tr('Aider — терминал'),'type':'terminal','optional':True,'status':'INSTALLED' if exe else 'NOT INSTALLED'}]
        if not exe:return unsupported(tr('Aider не установлен (необязательный агент). Чтобы использовать его, установите Aider и нажмите «Найти агенты заново».'))
        try:
            self.version=probe(self.command+['--version']);self.help_text=probe(self.command+['--help'])
            return Result(Support.SUPPORTED,tr('Aider найден'),{'version':self.version})
        except Exception as exc:return Result(Support.DEGRADED,tr('Aider найден, но не отвечает на проверку версии: {error}',error=exc))
    def get_config_locations(self,workspace=None):
        return Result(Support.SUPPORTED,data={'user':str(Path.home()/'.aider.conf.yml')})
    def start(self,workspace=None,model=None):
        if not self.command:return unsupported(tr('Aider не установлен. Установите его и нажмите «Найти агенты заново».'))
        if not model:return unsupported(tr('Сначала выберите модель в Station, затем откройте Aider.'))
        if '--model' not in self.help_text:return unsupported(tr('Эта версия Aider не поддерживает выбор модели при запуске (--model). Обновите Aider.'))
        args=self.command+['--model','openai/'+(model.backend_model_id)]
        env=dict(OPENAI_API_BASE=model.endpoint,OPENAI_API_KEY='local-station')
        try:return Result(Support.SUPPORTED,data=supervisor.start('agent:'+self.id,args,cwd=workspace,env=env,visible=True))
        except Exception as exc:return Result(Support.ERROR,str(exc))
