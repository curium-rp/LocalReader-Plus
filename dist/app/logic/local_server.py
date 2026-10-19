"""Loopback selection and URLs shared by the reader and FalcoType launchers."""

import socket
import sys


def pick_host(embedded: bool) -> str:
    if not embedded or sys.platform == "darwin":
        return "127.0.0.1"
    return "127.77.77.77"


def bind_server_socket(host: str) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((host, 0))
    except BaseException:
        listener.close()
        raise
    return listener


def server_url(host: str, port: int, embedded: bool = False) -> str:
    suffix = "?embedded=1" if embedded else ""
    # Both apps serve their index at /, not at a separate /reader route.
    return f"http://{host}:{port}/{suffix}"
