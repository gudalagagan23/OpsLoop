"""Run the TaskFlow API (port 8080) and its fault-control app (port 9100) in one process,
so planted faults can change in-process behaviour."""

import asyncio
import logging
import os

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
    await asyncio.gather(api.serve(), ctl.serve())


if __name__ == "__main__":
    asyncio.run(main())
