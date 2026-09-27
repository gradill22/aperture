"""TCP relay: the one bridge from aperture-internal to LM Studio on the Docker host.

Listens on :1234 and forwards every connection to a single fixed upstream (LLM_UPSTREAM,
default host.docker.internal:1234). It cannot be used to reach any other destination.
"""

import asyncio
import os

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "1234"))
UP_HOST, _, UP_PORT = os.environ.get("LLM_UPSTREAM", "host.docker.internal:1234").rpartition(":")


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(UP_HOST, int(UP_PORT)), 10)
    except (OSError, TimeoutError) as exc:
        print(f"upstream {UP_HOST}:{UP_PORT} unreachable: {exc!r}", flush=True)
        client_w.close()
        return
    await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))


async def main() -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    print(f"relaying :{LISTEN_PORT} -> {UP_HOST}:{UP_PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
