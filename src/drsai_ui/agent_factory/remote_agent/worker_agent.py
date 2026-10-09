import asyncio

from drsai.modules.agents import HepAIWorkerAgent
from loguru import logger


class RemotePauseError(RuntimeError):
    """The worker did not confirm that the agent was paused."""


class ConfirmedPauseWorkerAgent(HepAIWorkerAgent):
    async def pause(self) -> None:
        pause_worker = self._funcs_map.get("pause")
        if pause_worker is None:
            raise RemotePauseError("Remote worker pause function is unavailable.")
        if not self._chat_id:
            raise RemotePauseError("Remote worker chat_id is missing.")

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(pause_worker, chat_id=self._chat_id),
                timeout=60.0,
            )
        except asyncio.TimeoutError as exc:
            raise RemotePauseError(
                "Remote pause timed out after 60 seconds; the worker's pause state is unconfirmed."
            ) from exc
        except Exception as exc:
            raise RemotePauseError(f"Remote pause request failed: {exc}") from exc

        if not isinstance(result, dict) or result.get("status") is not True:
            detail = result.get("message") if isinstance(result, dict) else repr(result)
            raise RemotePauseError(f"Remote worker did not confirm pause: {detail}")

        # Keep consuming the stream until the worker has acknowledged the pause.
        self.is_paused = True
        self._paused.set()
        logger.info(f"Paused {self.name} successfully.")
