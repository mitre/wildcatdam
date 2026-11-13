#!/usr/bin/env python3
"""Interactive launcher for the simulator's PLC and HMI."""

import socket
import subprocess
import sys
import time
from pathlib import Path

from simulator import SIM

ROOT = Path(__file__).resolve().parent
TIMEOUT = 10


def choose(title, options):
    """Print a numbered menu and return the key of the chosen option."""
    print(f"\n{title}:")
    for number, (_, label) in enumerate(options, 1):
        print(f"{number}. {label}")
    while True:
        answer = input(f"Select [1-{len(options)}]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print(f"'{answer}' is not one of the options")


def ask(prompt, default):
    return input(f"{prompt} [{default}]: ").strip() or str(default)


def ask_port(default):
    while True:
        answer = ask("PLC port", default)
        if answer.isdigit() and 0 < int(answer) < 65536:
            return answer
        print(f"'{answer}' is not a port number (1-65535)")


def module(name, *args):
    return [sys.executable, "-m", name, *args]


def stop(process):
    """Terminate a process, killing it if it does not exit in time."""
    process.terminate()
    try:
        return process.wait(TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait()


def settle(process):
    """Give a process that also received Ctrl+C time to exit on its own."""
    try:
        return process.wait(TIMEOUT)
    except subprocess.TimeoutExpired:
        return stop(process)


def foreground(command):
    process = subprocess.Popen(command, cwd=ROOT)
    try:
        return process.wait()
    except KeyboardInterrupt:
        settle(process)
        raise


def accepts(host, port):
    address = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        socket.create_connection((address, int(port)), timeout=1).close()
        return True
    except OSError:
        return False


def wait_for_plc(plc, host, port):
    """Wait until the PLC accepts connections. Returns False if it exited."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if plc.poll() is not None:
            return False
        if accepts(host, port):
            return True
        time.sleep(0.2)
    return plc.poll() is None


def run_local(plc_command, hmi_command, host, port):
    if accepts(host, port):
        print(f"Port {port} on {host} is already in use", file=sys.stderr)
        return 1
    plc = subprocess.Popen(plc_command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        if not wait_for_plc(plc, host, port):
            print(f"PLC exited with status {plc.returncode}", file=sys.stderr)
            return plc.returncode or 1
        return foreground(hmi_command)
    except KeyboardInterrupt:
        settle(plc)
        raise
    finally:
        if plc.poll() is None:
            stop(plc)


def main():
    print(f"{SIM['name']} Simulator")

    protocols = SIM["protocols"]
    key = next(iter(protocols))
    protocol_args = []
    if len(protocols) > 1:
        key = choose("Protocol", [(k, p["name"]) for k, p in protocols.items()])
        protocol_args = ["--protocol", key]
    protocol = protocols[key]

    modes = [("local", "Local (PLC + HMI)")] if protocol["local"] else []
    modes += [("plc", "PLC only"), ("hmi", "HMI only")]
    if SIM["test_client"]:
        modes.append(("client", "Test client"))
    mode = choose("Deployment", modes)

    print()
    host = ask("PLC host", protocol["bind"] if mode == "plc" else "127.0.0.1")
    port = ask_port(protocol["port"])
    hmi_args = []
    if mode in ("local", "hmi") and SIM["web_optional"]:
        if input("Web HMI? [y/N]: ").strip().lower() in ("y", "yes"):
            hmi_args.append("--web")

    target = ["--host", host, "--port", port]
    plc_command = module("simulator.plc", *target, *protocol_args)
    hmi_command = module("simulator.hmi", *target, *protocol_args, *hmi_args)

    if mode == "local":
        return run_local(plc_command, hmi_command, host, port)
    if mode == "plc":
        return foreground(plc_command)
    if mode == "hmi":
        return foreground(hmi_command)
    return foreground(module(SIM["test_client"], *target))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
    except EOFError:
        print()
        sys.exit(1)
