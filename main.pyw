"""Local Agent AI Station desktop entry point and read-only diagnostics."""
import argparse
import json
import logging
from pathlib import Path
import sys
import time

RESTART_WAIT = 'LOCAL_AGENT_STATION_RESTART_WAIT'


def wait_for_previous_instance(timeout=20.0):
    """After a language change, let the previous Station release its instance channel."""
    import os
    value = os.environ.pop(RESTART_WAIT, '').strip()
    if not value.isdigit():
        return
    import psutil
    deadline = time.monotonic() + timeout
    try:
        process = psutil.Process(int(value))
        while process.is_running() and process.status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
            time.sleep(0.2)
    except (psutil.Error, ValueError):
        pass


def main():
    parser = argparse.ArgumentParser(description='Local Agent AI Station (LAAS)')
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--no-tray', action='store_true')
    parser.add_argument('--minimized', action='store_true')
    parser.add_argument('--startup', action='store_true', help='Started by the current-user Windows shortcut')
    parser.add_argument('--no-startup', action='store_true', help='Skip automatic component startup for this session')
    parser.add_argument('--doctor', action='store_true', help='Read-only hardware/runtime diagnostics as JSON')
    parser.add_argument('--output', type=Path, help='Write the diagnostic report to this file')
    parser.add_argument('--data-dir', type=Path, help='Use this persistent Station data directory')
    parser.add_argument('--migrate', action='store_true', help='Preview legacy migration')
    parser.add_argument('--apply', action='store_true', help='Apply the requested migration after preview')
    parser.add_argument('--swap-config', type=Path)
    args = parser.parse_args()
    if args.data_dir:
        import os
        os.environ['LOCAL_AGENT_STATION_HOME'] = str(args.data_dir.expanduser().resolve())
    from src.paths import data_dir, APP_NAME, VERSION
    if args.doctor:
        from src.hardware_topology import topology_engine
        from src.controller import StationController
        from src.services.gpu_mode_client import gpu_service_client
        report = {'application': APP_NAME, 'version': VERSION, 'data_directory': str(data_dir()),
            'hardware': topology_engine.discover_live().to_dict(),
            'agents': StationController().discover_agents(),
            'gpu_helper': gpu_service_client.get_driver_modes()}
        content = json.dumps(report, indent=2, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding='utf-8')
        elif sys.stdout:
            print(content)
        else:
            raise ValueError('Use --doctor --output report.json with the windowed executable')
        return 0
    if args.migrate:
        from src.migration import preview_migration, apply_migration
        plan = preview_migration(swap_config=args.swap_config)
        report = {'preview': plan.summary()}
        if args.apply:
            report['result'] = apply_migration(plan)
        content = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding='utf-8')
        elif sys.stdout:
            print(content)
        else:
            raise ValueError('Use --migrate --output report.json with the windowed executable')
        return 0
    logs = data_dir() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(logs / 'station.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')],
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    wait_for_previous_instance()
    from src.instance import StationInstance
    instance = StationInstance()
    if not instance.acquire():
        return 0
    from src.setup_guard import hold_setup_guard
    hold_setup_guard()
    from src.ui.control_center import ControlCenter
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LocalAgent.AIStation.3')
    app = ControlCenter(start_minimized=args.minimized, no_tray=args.no_tray, skip_startup=args.no_startup)
    instance.listen(lambda: app.events.put(('show', None, None)))
    def report_callback_exception(kind, value, tb):
        logging.error('UI action failed', exc_info=(kind, value, tb))
        from tkinter import messagebox
        messagebox.showerror(APP_NAME, str(value), parent=app)
    app.report_callback_exception = report_callback_exception
    try:
        app.mainloop()
    finally:
        instance.close()
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception('Station startup failed')
        if sys.stderr:
            print(str(exc), file=sys.stderr)
        elif not any(flag in sys.argv for flag in ('--doctor', '--migrate')):
            import ctypes
            from src.paths import data_dir
            from src.i18n import tr
            text = '\n\n'.join([tr('Local Agent AI Station не запустилась.'), str(exc),
                '\n'.join([tr('Настройки: {path}', path=data_dir() / 'config'),
                           tr('Резервные копии: {path}', path=data_dir() / 'config' / 'backups'),
                           tr('Журнал: {path}', path=data_dir() / 'logs' / 'station.log')]),
                tr('Открыть папку настроек?')])
            # MB_YESNO | MB_ICONERROR; IDYES = 6
            if ctypes.windll.user32.MessageBoxW(None, text, tr('Local Agent AI Station — ошибка запуска'), 0x14) == 6:
                import os
                os.startfile(str(data_dir() / 'config'))
        raise SystemExit(1)
