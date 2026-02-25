"""
HTTP + WebSocket server for the GC controller overlay.

Serves the overlay HTML page and broadcasts controller state
to connected WebSocket clients at ~120Hz.
"""

import asyncio
import json
import os

from aiohttp import web


STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')


async def index_handler(request):
    return web.FileResponse(os.path.join(STATIC_DIR, 'index.html'))


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    app = request.app

    app['ws_clients'].add(ws)
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if 'port' in data:
                        port = int(data['port'])
                        if 0 <= port <= 3:
                            app['active_port'] = port
                    if data.get('calibrate'):
                        app['adapter'].calibrate(app['active_port'])
                except (json.JSONDecodeError, ValueError):
                    pass
            elif msg.type == web.WSMsgType.ERROR:
                break
    finally:
        app['ws_clients'].discard(ws)
    return ws


async def broadcast_loop(app):
    """Continuously broadcast controller state to all WebSocket clients."""
    adapter = app['adapter']
    try:
        while True:
            port = app['active_port']
            state = adapter.get_state(port)
            state['port'] = port
            state['adapter_connected'] = adapter.connected

            msg = json.dumps(state)

            dead = set()
            for ws in app['ws_clients']:
                try:
                    await ws.send_str(msg)
                except Exception:
                    dead.add(ws)
            app['ws_clients'] -= dead

            await asyncio.sleep(1 / 120)
    except asyncio.CancelledError:
        pass


async def on_startup(app):
    app['broadcast_task'] = asyncio.create_task(broadcast_loop(app))


async def on_cleanup(app):
    app['broadcast_task'].cancel()
    await app['broadcast_task']

    for ws in set(app['ws_clients']):
        await ws.close()


def create_app(adapter, port=0):
    """Create the aiohttp application."""
    app = web.Application()
    app['adapter'] = adapter
    app['active_port'] = port
    app['ws_clients'] = set()

    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_static('/static', STATIC_DIR)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app
