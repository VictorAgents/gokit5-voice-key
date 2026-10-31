# SPDX-License-Identifier: GPL-3.0-or-later
"""Login launcher: wait for Windows audio initialization, never duplicate a receiver."""
import os
import subprocess
import time
from receiver import SingleInstance
from settings import STOP_PATH
from runtime import DATA_ROOT, launch_command
ROOT=DATA_ROOT


def receiver_running():
    try:
        check = SingleInstance()
    except RuntimeError:
        return True
    check.close()
    return False


def main():
    try:
        instance = SingleInstance('Local\\Gokit5LoginLauncher-Community')
    except RuntimeError:
        return
    try:
        if receiver_running():
            return
        STOP_PATH.unlink(missing_ok=True)
        log_path = ROOT/'logs/receiver-autostart.txt'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        failures = 0
        with log_path.open('a', encoding='utf-8') as log:
            while not STOP_PATH.exists():
                if receiver_running():
                    return
                result = subprocess.run(launch_command('--receiver','--stop-file',STOP_PATH), cwd=ROOT, env=env,
                                        creationflags=subprocess.CREATE_NO_WINDOW,
                                        stdout=log, stderr=subprocess.STDOUT)
                if result.returncode == 0 or STOP_PATH.exists():
                    return
                # Audio/network services can become ready well after login.
                # Keep retrying without a two-minute cutoff; explicit Stop wins.
                failures += 1
                end=time.monotonic()+min(30,2**min(failures+1,5))
                while not STOP_PATH.exists() and time.monotonic()<end:
                    time.sleep(.5)
    finally:
        instance.close()


if __name__ == '__main__':
    main()
