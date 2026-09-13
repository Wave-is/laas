"""Real OpenAI-compatible backend qualification, with small deterministic probes."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import time
import requests

def qualify_model(model, *, timeout=240, vision=False, agents=None):
    endpoint = model.endpoint.rstrip('/') + '/chat/completions'
    report = {'model': model.id, 'endpoint': model.endpoint, 'checks': {}, 'started_utc': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
    common = {'model': model.backend_model_id, 'temperature': 0, 'max_tokens': 96,
        'chat_template_kwargs': {'enable_thinking': False}}
    def request(payload):
        start = time.monotonic()
        r = requests.post(endpoint, json={**common, **payload}, timeout=timeout)
        r.raise_for_status()
        return r.json(), round(time.monotonic()-start,3)
    def run(name, action):
        try:
            report['checks'][name] = action()
        except Exception as exc:
            report['checks'][name] = {'passed': False, 'error': str(exc)}
    def text_probe():
        data, elapsed=request({'messages':[{'role':'user','content':'Reply exactly STATION_OK.'}]})
        message=data['choices'][0]['message'].get('content') or ''
        return {'passed':'STATION_OK' in message, 'reply':message, 'seconds':elapsed, 'usage':data.get('usage')}
    def stream_probe(marker):
        start=time.monotonic(); content=''; chunks=0; done=False
        with requests.post(endpoint,json={**common,'stream':True,'messages':[{'role':'user','content':'Reply exactly '+marker+'.'}]},stream=True,timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith(b'data: '): continue
                raw=line[6:]
                if raw==b'[DONE]': done=True; break
                event=json.loads(raw)
                if event.get('choices'):
                    content+=event['choices'][0].get('delta',{}).get('content') or ''
                    chunks+=1
        return {'passed':marker in content and done and chunks>0,'reply':content,'chunks':chunks,'done':done,'seconds':round(time.monotonic()-start,3)}
    def concurrent_probe():
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(stream_probe,['STATION_ALPHA','STATION_BETA']))
        return {'passed':all(r['passed'] for r in results),'streams':results,'semantics':'Both simultaneous requests completed; latency may include backend queueing.'}
    def tools_probe():
        tool={'type':'function','function':{'name':'station_add','description':'Add two integers.', 'parameters':{'type':'object','properties':{'a':{'type':'integer'},'b':{'type':'integer'}},'required':['a','b'],'additionalProperties':False}}}
        data,elapsed=request({'messages':[{'role':'user','content':'Use station_add to add 17 and 25.'}], 'tools':[tool], 'tool_choice':{'type':'function','function':{'name':'station_add'}}})
        calls=data['choices'][0]['message'].get('tool_calls',[])
        args=json.loads(calls[0]['function']['arguments']) if calls else {}
        valid=bool(calls) and calls[0]['function']['name']=='station_add' and args=={'a':17,'b':25}
        return {'passed':valid,'tool_calls':calls,'seconds':elapsed}
    def vision_probe():
        from PIL import Image, ImageDraw
        fixture=Image.new('RGB',(256,256),'#f00000')
        buf=io.BytesIO();fixture.save(buf,format='PNG')
        url='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
        data,elapsed=request({'messages':[{'role':'user','content':[{'type':'text','text':'What is the dominant color of this image? Reply with one English color word.'},{'type':'image_url','image_url':{'url':url}}]}]})
        message=data['choices'][0]['message'].get('content') or ''
        return {'passed':'red' in message.lower(),'reply':message,'seconds':elapsed}
    run('text',text_probe)
    run('stream',lambda:stream_probe('STATION_STREAM'))
    run('concurrent_streams',concurrent_probe)
    run('tool_calling',tools_probe)
    if vision: run('vision',vision_probe)
    for adapter in agents or []:
        def agent_probe(adapter=adapter):
            from .paths import data_dir
            workspace=data_dir()/'qualification-workspace';workspace.mkdir(parents=True,exist_ok=True)
            result=adapter.smoke(model,str(workspace),timeout=timeout)
            return {'passed':result.ok,**result.to_dict()}
        run('agent:'+adapter.id,agent_probe)
    report['passed']=all(check.get('passed',False) for check in report['checks'].values())
    return report
