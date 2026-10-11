"""The service script's command line (installer/service.py runs it): the
arguments, which folders a command works on, the exit codes and Setup's
mutex around the commands that change the service. The commands
themselves are installer/service.py's Services.

Standard library only: this runs elevated from the admin folder.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from installer.service import Services
from installer.service_checks import is_admin
from installer.service_config import DEFAULT_HOUSEHOLD_PORT, read_config
from installer.service_control import Runner
from installer.service_layout import (
    ADMIN_FOLDER_NAME,
    CONFIG_FILE_NAME,
    ConfigRefused,
    Layout,
    ServiceError,
    _norm,
    program_files,
    refuse_reparse_point,
)
from installer.setup_lock import SETUP_RUNNING_MESSAGE, SetupRunning, default_mutexes, setup_lock

APP_DIR = Path(__file__).resolve().parent.parent


EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_NOT_ADMIN = 3


# The owner's commands that change the service and that Setup doesn't run.
# Setup runs install, stop and uninstall itself while it holds its mutex, so
# those must not wait for it.
SETUP_LOCKED_COMMANDS = ("set-port", "enable-remote", "disable-remote")


def build_services(args) -> Services:
    """From the admin copy (its config.json names the two folders, so
    nothing in the user's environment or the user-writable program folder
    decides where rights are granted) or, for Setup's `install`, from
    Setup's extraction with the folders Setup passes."""
    admin = program_files() / ADMIN_FOLDER_NAME
    config_file = APP_DIR.parent / CONFIG_FILE_NAME
    running_from_admin = config_file.is_file()
    if running_from_admin:
        if _norm(APP_DIR.parent.parent) != _norm(admin):
            raise ServiceError(f"This copy isn't in {admin}; run Setup again.")
        if args.command == "install":
            raise ServiceError("`install` is run by Setup; run Setup again instead.")
        config = read_config(config_file)
        if not config:
            raise ServiceError(f"{config_file} is unreadable; run Setup again.")
        root, data = config["install_root"], config["data_dir"]
    else:
        if args.command != "install":
            raise ServiceError(f"Run this from the installed copy in {admin}\\helper.")
        root, data = args.install_root, args.data_dir
        if not root or not data:
            raise ServiceError("Setup passes --install-root and --data-dir.")
    layout = Layout(root, data, admin)
    layout.check()
    if not running_from_admin:
        # So a failed first install leaves the log Setup points the user at.
        refuse_reparse_point(admin)
        admin.mkdir(exist_ok=True)
    source = None if running_from_admin else APP_DIR.parent.parent
    return Services(layout, Runner(layout.log_file), source=source)


# What `--help` lists (and docs/windows-installer-design.md, "Commands").
COMMANDS_HELP = r"""The Start-menu item "Baihe Studio service" runs these for you. By hand, in an
administrator prompt (status needs no administrator rights):

  "%ProgramFiles%\Baihe Studio Services\helper\python\python.exe" -I -S
      "%ProgramFiles%\Baihe Studio Services\helper\lib\installer\service.py" COMMAND

commands:
  status                    both services, every port Baihe uses, remote access, the firewall rule
  set-port N                move the service to port N (put back if N doesn't answer)
  enable-remote [--household-port N]
                            household access through Caddy (sign-in settings in .env first)
  disable-remote            household access off: Caddy stopped and disabled
  stop                      stop both services
  install [--port N]        (Setup) create or refresh the service and start it; --port
                            only on a fresh install (an update keeps the stored port)
  uninstall                 (the uninstaller) remove both services and the admin folder
"""


def main(argv=None, services=None, admin=None, mutexes=None) -> int:
    parser = argparse.ArgumentParser(description="Baihe Studio's Windows service.",
                                     epilog=COMMANDS_HELP,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--install-root", help="the per-user install folder (Setup)")
    parser.add_argument("--data-dir", help="the data folder (Setup)")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install", help="create or refresh both services and start Baihe Studio's")
    # Checked by install only when it will be used (a bad value is then
    # refused with exit code 2, nothing changed), not by argparse, so the
    # services Setup stopped start again and an update ignores it.
    install.add_argument("--port", help="a fresh install's port (an update keeps the stored one)")
    set_port = sub.add_parser("set-port", help="move the installed service to another port")
    # Checked by set_port (refused with exit code 2, nothing changed), like --port.
    set_port.add_argument("port", help="the new port, 1024 to 65535")
    sub.add_parser("uninstall", help="stop and remove both services and the admin folder")
    sub.add_parser("stop", help="stop both services")
    enable = sub.add_parser("enable-remote", help="turn household access through Caddy on")
    enable.add_argument("--household-port", type=int, default=DEFAULT_HOUSEHOLD_PORT)
    sub.add_parser("disable-remote", help="turn household access off")
    sub.add_parser("status", help="show both services' state")
    args = parser.parse_args(argv)

    if args.command != "status" and not (is_admin() if admin is None else admin):
        print("This needs administrator rights: run it from an administrator Command Prompt "
              "or PowerShell.", file=sys.stderr)
        return EXIT_NOT_ADMIN
    if services is None:
        if os.name != "nt":
            print("Baihe Studio's service exists only on Windows.", file=sys.stderr)
            return EXIT_FAILED
        try:
            services = build_services(args)
        except ServiceError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return EXIT_FAILED
    run = services.run
    if hasattr(run, "log"):
        run.log(f"service.py {args.command}")
    if args.command not in SETUP_LOCKED_COMMANDS:
        return _run_command(args, services)
    try:
        with setup_lock(default_mutexes() if mutexes is None else mutexes):
            return _run_command(args, services)
    except SetupRunning:
        if hasattr(run, "log"):
            run.log(f"refused: {SETUP_RUNNING_MESSAGE}")
        print(SETUP_RUNNING_MESSAGE, file=sys.stderr)
        return EXIT_REFUSED
    except OSError as e:
        if hasattr(run, "log"):
            run.log(f"failed: Setup's mutex: {e}")
        print(f"ERROR: Setup's mutex: {e}", file=sys.stderr)
        return EXIT_FAILED


def _run_command(args, services) -> int:
    run = services.run
    try:
        if args.command == "enable-remote":
            message = services.enable_remote(args.household_port)
        elif args.command == "install":
            message = services.install(args.port)
        elif args.command == "set-port":
            message = services.set_port(args.port)
        else:
            message = getattr(services, args.command.replace("-", "_"))()
    except ConfigRefused as e:
        if hasattr(run, "log"):
            run.log(f"refused: {e}")
        print(str(e), file=sys.stderr)
        return EXIT_REFUSED
    except (ServiceError, OSError, subprocess.SubprocessError) as e:
        if hasattr(run, "log"):
            run.log(f"failed: {e}")
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_FAILED
    if hasattr(run, "log"):
        run.log(message.replace("\n", "; "))
    print(message)
    return 0
