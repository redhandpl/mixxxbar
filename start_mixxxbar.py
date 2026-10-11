#!/usr/bin/env python3
"""Find the local BUSY Bar and launch the unified Mixxx display."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable

from mixxx_display import main as display_main

USB_BUSYBAR_HOST = "10.0.4.20"
WIFI_INTERFACE = "wlan0"
WIFI_INTERFACE_ADDRESS = "10.26.16.113"
WIFI_BUSYBAR_HOST = "10.26.16.123"


def interface_ipv4_addresses(interface: str) -> set[str]:
    """Return IPv4 addresses assigned to a Linux network interface."""
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "address", "show", "dev", interface],
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("The iproute2 'ip' command is required to inspect wlan0") from exc
    except subprocess.TimeoutExpired:
        return set()

    if result.returncode != 0:
        return set()

    try:
        interfaces = json.loads(result.stdout)
    except json.JSONDecodeError:
        return set()

    return {
        address["local"]
        for device in interfaces
        if device.get("ifname") == interface
        for address in device.get("addr_info", [])
        if address.get("family") == "inet" and isinstance(address.get("local"), str)
    }


def busybar_available(host: str, token: str) -> bool:
    """Check the BUSY API version endpoint using the installed client library."""
    try:
        from busylib import BusyBar, exceptions
    except ImportError as exc:
        raise RuntimeError(
            "Install the project dependencies with: .venv/bin/pip install -r requirements.txt"
        ) from exc

    client = BusyBar(host, token=token, timeout=2, max_retries=0)
    try:
        client.version()
    except (exceptions.BusyBarError, ValueError):
        return False
    finally:
        client.close()
    return True


def select_host(
    manual_host: str | None,
    is_available: Callable[[str], bool],
    interface_addresses: Callable[[str], set[str]],
) -> str | None:
    """Select a manual host or resolve USB first, then the configured Wi-Fi path."""
    if manual_host is not None:
        return manual_host
    if is_available(USB_BUSYBAR_HOST):
        return USB_BUSYBAR_HOST
    if WIFI_INTERFACE_ADDRESS not in interface_addresses(WIFI_INTERFACE):
        return None
    if is_available(WIFI_BUSYBAR_HOST):
        return WIFI_BUSYBAR_HOST
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find BUSY Bar at the USB address or configured wlan0 address, "
            "then start mixxx_display.py."
        )
    )
    parser.add_argument(
        "--host",
        help="use this BUSY Bar host directly, without automatic detection",
    )
    args, app_args = parser.parse_known_args(argv)

    token = os.environ.get("BUSY_API_TOKEN")
    if not token:
        print("BUSY_API_TOKEN is required", file=sys.stderr)
        return 2

    try:
        host = select_host(
            args.host,
            lambda candidate: busybar_available(candidate, token),
            interface_ipv4_addresses,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if host is None:
        print(
            f"BUSY Bar not found at {USB_BUSYBAR_HOST}; Wi-Fi fallback requires "
            f"{WIFI_INTERFACE}={WIFI_INTERFACE_ADDRESS} and a reachable BUSY Bar at "
            f"{WIFI_BUSYBAR_HOST}.",
            file=sys.stderr,
        )
        return 2

    print(f"Starting mixxx_display.py with BUSY Bar at {host}", flush=True)
    return display_main(["--host", host, *app_args])


if __name__ == "__main__":
    raise SystemExit(main())
