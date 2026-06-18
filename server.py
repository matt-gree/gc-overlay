"""
HTTP + WebSocket server for the GC controller overlay.

Serves the overlay HTML page and broadcasts controller state
to connected WebSocket clients at ~120Hz.
"""

import asyncio
import json
import os

from aiohttp import web

from resources import resource_path


STATIC_DIR = resource_path('static')


async def index_handler(request):
    return web.FileResponse(os.path.join(STATIC_DIR, 'index.html'))


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    app = request.app

    # Per-client port: check ?port= query param, fall back to app default
    initial_port = app['active_port']
    port_param = request.query.get('port')
    if port_param is not None:
        try:
            p = int(port_param)
            if 0 <= p <= 3:
                initial_port = p
        except ValueError:
            pass

    app['ws_clients'][ws] = initial_port
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if 'port' in data:
                        port = int(data['port'])
                        if 0 <= port <= 3:
                            app['ws_clients'][ws] = port
                    if data.get('calibrate'):
                        app['adapter'].calibrate(app['ws_clients'].get(ws, 0))
                except (json.JSONDecodeError, ValueError):
                    pass
            elif msg.type == web.WSMsgType.ERROR:
                break
    finally:
        app['ws_clients'].pop(ws, None)
    return ws


async def broadcast_loop(app):
    """Continuously broadcast controller state to all WebSocket clients.

    Each client receives state for its own selected port.
    """
    adapter = app['adapter']
    try:
        while True:
            # Cache states per port to avoid redundant calls
            cached_states = {}
            dead = set()

            for ws, port in list(app['ws_clients'].items()):
                if port not in cached_states:
                    state = adapter.get_state(port)
                    state['port'] = port
                    state['adapter_connected'] = adapter.connected
                    cached_states[port] = json.dumps(state)

                try:
                    await ws.send_str(cached_states[port])
                except Exception:
                    dead.add(ws)

            for ws in dead:
                app['ws_clients'].pop(ws, None)

            await asyncio.sleep(1 / 120)
    except asyncio.CancelledError:
        pass


async def on_startup(app):
    app['broadcast_task'] = asyncio.create_task(broadcast_loop(app))


async def on_cleanup(app):
    app['broadcast_task'].cancel()
    await app['broadcast_task']

    for ws in list(app['ws_clients']):
        await ws.close()


def create_app(adapter, port=0):
    """Create the aiohttp application."""
    app = web.Application()
    app['adapter'] = adapter
    app['active_port'] = port
    app['ws_clients'] = {}  # {WebSocketResponse: port_index}

    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_static('/static', STATIC_DIR)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app
