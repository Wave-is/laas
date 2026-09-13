"""OpenAI-compatible provider records shared by Pi and OpenClaw adapters."""
import hashlib
import re


def provider_id(model):
    slug = re.sub('[^a-z0-9-]', '-', model.id.lower()).strip('-')[:40] or 'model'
    return 'local-agent-station-' + slug + '-' + hashlib.sha256(model.id.encode()).hexdigest()[:8]


def provider_record(model):
    return {
        'baseUrl': model.endpoint, 'api': 'openai-completions', 'apiKey': 'local-station',
        'models': [{
            'id': model.backend_model_id, 'name': model.name,
            'reasoning': False,
            'input': ['text', 'image'] if model.vision and model.qualified else ['text'],
            'contextWindow': model.context,
            'maxTokens': min(4096, max(1, model.context // 4)),
            'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
            'compat': {'supportsDeveloperRole': False, 'supportsStore': False,
                       'supportsReasoningEffort': False, 'maxTokensField': 'max_tokens'},
        }],
    }


def enabled_models(models):
    return [m for m in models if m.id != 'none' and m.status != 'disabled']
