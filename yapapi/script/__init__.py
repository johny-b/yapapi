"""Stuff."""

from typing import Callable, Iterable, Optional, Dict, List, Any, Awaitable

from yapapi.ctx import WorkContext
from yapapi.script.capture import CaptureContext
from yapapi.script.work import (
    _Deploy,
    _ReceiveBytes,
    _ReceiveFile,
    _ReceiveJson,
    _Run,
    _SendBytes,
    _SendFile,
    _SendJson,
    _Start,
    _Terminate,
    StorageEvent,
    Work,
)
from yapapi.storage import StorageProvider, DOWNLOAD_BYTES_LIMIT_DEFAULT
from yapapi.work import BatchCommand


class Script:
    def __init__(
        self,
        storage: StorageProvider,
        emitter: Optional[Callable[[StorageEvent], None]],
        implicit_init: bool = True,
    ):
        self._pending_steps: List[Work] = []
        self._storage = storage
        self._emitter = emitter
        if implicit_init:
            self.deploy()
            self.start()

    async def _evaluate(self, context: WorkContext) -> List[BatchCommand]:
        batch_script: List[BatchCommand] = []
        for step in self._pending_steps:
            batch_cmd = await step.evaluate(context)
            batch_script.append(batch_script)
        return batch_script

    def deploy(self):
        """Schedule a Deploy command."""
        self._pending_steps.append(_Deploy())

    def start(self, *args: str):
        """Schedule a Start command."""
        self._pending_steps.append(_Start(*args))

    def terminate(self):
        """Schedule a Terminate command."""
        self._pending_steps.append(_Terminate())

    def send_json(self, json_path: str, data: dict):
        """Schedule sending JSON data to the provider.

        :param json_path: remote (provider) path
        :param data: dictionary representing JSON data
        :return: None
        """
        self._pending_steps.append(_SendJson(self._storage, data, json_path))

    def send_bytes(self, dst_path: str, data: bytes):
        """Schedule sending bytes data to the provider.

        :param dst_path: remote (provider) path
        :param data: bytes to send
        :return: None
        """
        self._pending_steps.append(_SendBytes(self._storage, data, dst_path))

    def send_file(self, src_path: str, dst_path: str):
        """Schedule sending file to the provider.

        :param src_path: local (requestor) path
        :param dst_path: remote (provider) path
        :return: None
        """
        self._pending_steps.append(_SendFile(self._storage, src_path, dst_path))

    def run(
        self,
        cmd: str,
        *args: Iterable[str],
        env: Optional[Dict[str, str]] = None,
    ):
        """Schedule running a command.

        :param cmd: command to run on the provider, e.g. /my/dir/run.sh
        :param args: command arguments, e.g. "input1.txt", "output1.txt"
        :param env: optional dictionary with environmental variables
        :return: None
        """
        stdout = CaptureContext.build(mode="stream")
        stderr = CaptureContext.build(mode="stream")

        self._pending_steps.append(_Run(cmd, *args, env=env, stdout=stdout, stderr=stderr))

    def download_file(self, src_path: str, dst_path: str):
        """Schedule downloading remote file from the provider.

        :param src_path: remote (provider) path
        :param dst_path: local (requestor) path
        :return: None
        """
        self._pending_steps.append(_ReceiveFile(self._storage, src_path, dst_path, self._emitter))

    def download_bytes(
        self,
        src_path: str,
        on_download: Callable[[bytes], Awaitable],
        limit: int = DOWNLOAD_BYTES_LIMIT_DEFAULT,
    ):
        """Schedule downloading a remote file as bytes
        :param src_path: remote (provider) path
        :param on_download: the callable to run on the received data
        :param limit: the maximum length of the expected byte string
        :return None
        """
        self._pending_steps.append(
            _ReceiveBytes(self._storage, src_path, on_download, limit, self._emitter)
        )

    def download_json(
        self,
        src_path: str,
        on_download: Callable[[Any], Awaitable],
        limit: int = DOWNLOAD_BYTES_LIMIT_DEFAULT,
    ):
        """Schedule downloading a remote file as JSON
        :param src_path: remote (provider) path
        :param on_download: the callable to run on the received JSON data
        :param limit: the maximum length of the expected remote file
        :return None
        """
        self._pending_steps.append(
            _ReceiveJson(self._storage, src_path, on_download, limit, self._emitter)
        )
