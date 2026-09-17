"""Opt-in, ordered startup. No driver changes or implicit agent configuration writes."""
from copy import deepcopy
from .storage import ConfigurationError

DEFAULT_STARTUP = {'enabled': False, 'minimized': False, 'delay_seconds': 0,
                   'model_id': 'none', 'frontend_ids': [], 'service_ids': [],
                   'stop_on_error': True}


def startup_settings(value=None):
    if value is not None and not isinstance(value, dict):
        raise ConfigurationError('Настройки автозапуска должны быть объектом.')
    result = {**deepcopy(DEFAULT_STARTUP), **deepcopy(value or {})}
    for key in ('enabled', 'minimized', 'stop_on_error'):
        if type(result[key]) is not bool:
            raise ConfigurationError(f'{key}: требуется галочка да/нет.')
    if type(result['delay_seconds']) is not int or not 0 <= result['delay_seconds'] <= 300:
        raise ConfigurationError('Задержка запуска: целое число от 0 до 300 секунд.')
    if not isinstance(result['model_id'], str) or not result['model_id']:
        raise ConfigurationError('Выберите модель или «Без модели».')
    for key in ('frontend_ids', 'service_ids'):
        ids = result[key]
        if not isinstance(ids, list) or any(not isinstance(id, str) or not id for id in ids) or len(ids) != len(set(ids)):
            raise ConfigurationError('Выбор компонентов должен содержать уникальные идентификаторы.')
    return result


def startup_steps(settings):
    settings = startup_settings(settings)
    if not settings['enabled']:
        return []
    return ([('service', id) for id in settings['service_ids']] +
            ([('model', settings['model_id'])] if settings['model_id'] != 'none' else []) +
            [('frontend', id) for id in settings['frontend_ids']])


class StartupRunner:
    def __init__(self, controller, services, models, profiles):
        self.controller, self.services = controller, services
        self.models, self.profiles = models, profiles
        self.started = False

    def run(self, settings, cancelled, progress=lambda message: None):
        settings = startup_settings(settings)
        if self.started:
            return {'Success': True, 'Message': 'Автозапуск уже обработан.', 'Steps': []}
        self.started = True
        results = []
        steps = startup_steps(settings)
        for kind, id in steps:
            if cancelled.is_set():
                return {'Success': False, 'Message': 'Автозапуск отменён. Уже запущенные компоненты продолжают работать.', 'Steps': results}
            label = id
            try:
                if kind == 'service':
                    profile = self.services.profiles().get(id)
                    if not profile or profile.get('type', 'local') != 'local':
                        raise ValueError('Локальная служба не найдена. Проверьте настройки запуска.')
                    label = profile.get('name', id)
                    progress('Запуск службы: ' + label)
                    result = self.services.start(id)
                elif kind == 'model':
                    model = self.profiles.model_profiles.get(id)
                    if not model:
                        raise ValueError('Модель удалена или недоступна. Проверьте настройки запуска.')
                    label = model.name
                    progress('Загрузка модели: ' + label)
                    result = self.models.apply_model_profile_only(id)
                else:
                    frontend = self.controller.frontends.get(id)
                    if not frontend or frontend.get('status') != 'INSTALLED':
                        raise ValueError('Интерфейс агента не установлен или недоступен.')
                    label = frontend['name']
                    progress('Запуск агента: ' + label)
                    if not self.controller.frontend_status(id)['running']:
                        # Auto-start uses a previously reviewed binding; never silently rewrites it.
                        model = self.profiles.model_profiles.get(settings['model_id'])
                        adapter = self.controller.adapters[frontend['runtime_id']]
                        if self.controller.model_binding_state(adapter.id, model) == 'NEEDS_REVIEW':
                            raise ValueError(f'{frontend["name"]} ещё не настроен на модель «{model.name}». '
                                'Один раз нажмите «Открыть» на странице «Станция» и подтвердите изменения — '
                                'после этого автозапуск будет работать.')
                    result = self.controller.launch_frontend(id, remember=False)
                if not isinstance(result, dict):
                    raise ValueError('Компонент не вернул результат запуска.')
            except Exception as exc:
                result = {'Success': False, 'Message': str(exc)}
            results.append({'kind': kind, 'id': id, 'name': label, **result})
            if not result.get('Success') and settings['stop_on_error']:
                break
        failed = [row for row in results if not row.get('Success')]
        message = ('Автозапуск: ' + failed[0]['name'] + ' — ' + failed[0].get('Message', 'Ошибка') if failed else
                   'Автозапуск завершён: ' + ', '.join(row['name'] for row in results) + '.' if results else 'Автозапуск компонентов выключен.')
        return {'Success': not failed, 'Message': message, 'Steps': results}
