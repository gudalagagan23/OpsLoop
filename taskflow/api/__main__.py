"""Run the TaskFlow API (port 8080) and its fault-control app (port 9100) in one process,
so planted faults can change in-process behaviour."""

import asyncio
import logging
import os
import sys

import uvicorn

from taskflow.api.main import create_app
from taskflow.faults.registry import create_control_app


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    control = create_control_app(app.state.registry)
    api = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",  # noqa: S104 - container port, published by compose
            port=int(os.environ.get("PORT", "8080")),
            log_level="warning",
            access_log=False,
        )
    )
    ctl = uvicorn.Server(
        uvicorn.Config(
            control,
            host="0.0.0.0",  # noqa: S104 - compose binds the host side to 127.0.0.1
            port=int(os.environ.get("CONTROL_PORT", "9100")),
            log_level="warning",
            access_log=False,
        )
    )
    # If either server stops (e.g. the API failed to start because the database was not
    # reachable yet), stop both and exit non-zero so Docker restarts the container.
    tasks = {asyncio.create_task(api.serve()), asyncio.create_task(ctl.serve())}
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    api.should_exit = ctl.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
