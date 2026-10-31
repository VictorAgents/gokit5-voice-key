# SPDX-License-Identifier: GPL-3.0-or-later
"""Single executable entry point, including esptool's isolated worker process."""
import sys
import runpy


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else '--installer'
    sys.argv = [sys.argv[0], *sys.argv[2:]]
    if getattr(sys,'frozen',False) and sys.platform=='win32' and action not in ('--esptool','--self-test','--cable-probe'):
        import ctypes
        ctypes.WinDLL('kernel32').FreeConsole()
    if action in ('--audio-setup','--cable-probe'):
        from cable_setup import main as audio_main
        if action=='--cable-probe':sys.argv.append('--cable-probe')
        audio_main()
    elif action == '--esptool':
        import esptool
        esptool._main()
    elif action == '--self-test':
        import json
        from runtime import VERSION
        from flasher import load_bundle
        from runtime import DATA_ROOT
        import esptool,sounddevice,numpy
        # Import all GUI/worker entry points as well as their data dependencies.
        # PyInstaller may omit an invalid hidden import without failing its build.
        import control_panel,installer,setup_wifi,run_receiver,cable_setup,audio_assets,battery_status,device_options
        frozen=bool(getattr(sys,'frozen',False))
        if frozen:
            from pathlib import Path
            embedded=Path(sys._MEIPASS).resolve()
            if not all(Path(m.__file__).resolve().is_relative_to(embedded) for m in (esptool,sounddevice,numpy,control_panel,installer,setup_wifi,run_receiver,cable_setup,audio_assets,battery_status,device_options)):
                raise RuntimeError('A dependency was loaded outside the bundled application')
        result=json.dumps({'version': VERSION, 'bundle_valid': bool(load_bundle()), 'gui_started': False,'frozen':frozen,
                           'esptool':esptool.__version__,'sounddevice':sounddevice.__version__,'numpy':numpy.__version__})
        print(result)
    elif action in ('--receiver', '--bootstrap', '--panel', '--wifi'):
        modules = {'--receiver': 'receiver', '--bootstrap': 'run_receiver', '--panel': 'control_panel', '--wifi': 'setup_wifi'}
        if action in ('--panel','--wifi'):
            from runtime import active_id
            if not active_id():
                from installer import main
                main()
                return
        runpy.run_module(modules[action], run_name='__main__')
    else:
        from installer import main
        main()


if __name__ == '__main__':
    main()
