"""Entry point of the stand-alone OD Studio app (built by packaging/build.py).

Starts the program and opens its window in the web browser.  If OD Studio is already
running, it only opens that window again, so a second copy never competes for the
serial port.
"""
import os
import sys
import urllib.request
import webbrowser

PORT = 8250


def already_running() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=0.5) as r:
            return b"OD Studio" in r.read(2000)
    except Exception:
        return False


def main() -> int:
    # an app started from Finder has no terminal to print to
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    args = [a for a in sys.argv[1:] if not a.startswith("-psn")]   # old macOS process id
    if not args:
        if already_running():
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
            return 0
        args = ["gui", "--http-port", str(PORT)]
    from odstudio.cli import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
