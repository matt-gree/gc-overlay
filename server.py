"""
HTTP + WebSocket server for the GC controller overlay.

Serves the overlay page, broadcasts controller state to connected WebSocket
clients at ~120Hz, and exposes a small JSON API under /api so an external
controller (PRSH) can read state and drive display settings at runtime.

API
---
GET  /api/settings            -> {"settings": {...}, "clients": N}
POST /api/settings            -> apply a partial settings patch, then the same
                                 shape back. Body is either a bare patch
                                 ({"show_gear": false}) or {"settings": {...}}.
GET  /api/state?port=1        -> current controller state for one port
POST /api/calibrate?port=1    -> reset that port's stick centers

Ports in the API are 1-indexed. See overlay_settings.py for the setting keys.
"""

import asyncio
import json
import os

from aiohttp import web

import overlay_settings
from resources import resource_path


STATIC_DIR = resource_path('static')

CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}


@web.middleware
async def api_cors_middleware(request, handler):
    """Allow cross-origin calls to /api so PRSH can drive the overlay from
    its own page or process. The server only binds to 127.0.0.1."""
    if not request.path.startswith('/api/'):
        return await handler(request)
    if request.method == 'OPTIONS':
        return web.Response(status=204, headers=CORS_HEADERS)
    response = await handler(request)
    response.headers.update(CORS_HEADERS)
    return response


async def index_handler(request):
    return web.FileResponse(os.path.join(STATIC_DIR, 'index.html'))


# ── Settings API ──────────────────────────────────────────────────────────

async def push_settings(app, ws, client):
    try:
        await ws.send_str(json.dumps({'type': 'settings', 'settings': client}))
        return True
    except Exception:
        app['ws_clients'].pop(ws, None)
        return False


async def apply_patch(app, patch):
    """Apply a settings patch to the server defaults and every live client."""
    app['settings'].update(patch)
    for ws, client in list(app['ws_clients'].items()):
        client.update(patch)
        await push_settings(app, ws, client)


async def settings_get_handler(request):
    return web.json_response({
        'settings': request.app['settings'],
        'clients': len(request.app['ws_clients']),
    })


async def settings_post_handler(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'body must be JSON'}, status=400)
    if not isinstance(body, dict):
        return web.json_response({'error': 'body must be a JSON object'}, status=400)

    # Accept both a bare patch and {"settings": {...}}.
    nested = body.get('settings')
    source = nested if isinstance(nested, dict) else body

    try:
        patch = overlay_settings.parse(source)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)

    await apply_patch(request.app, patch)
    return web.json_response({
        'settings': request.app['settings'],
        'clients': len(request.app['ws_clients']),
    })


# ── State API ─────────────────────────────────────────────────────────────

def _query_port(request):
    """1-indexed port from ?port=, defaulting to the server's active port."""
    raw = request.query.get('port')
    if raw is None:
        return request.app['settings']['port']
    return overlay_settings.parse({'port': raw})['port']


def _state_payload(app, port):
    adapter = app['adapter']
    state = adapter.get_state(port - 1)
    state['type'] = 'state'
    state['port'] = port
    state['adapter_connected'] = adapter.connected
    # The page's "no data yet" advice differs per transport and it has no
    # other way to know which one is running: memorywatcher connects at game
    # boot and never retries (so the overlay must be up FIRST), while dme
    # polls for the process and re-hooks on its own.
    state['adapter_mode'] = app['mode']
    return state


async def state_get_handler(request):
    try:
        port = _query_port(request)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)
    return web.json_response(_state_payload(request.app, port))


async def calibrate_post_handler(request):
    try:
        port = _query_port(request)
    except ValueError as exc:
        return web.json_response({'error': str(exc)}, status=400)
    request.app['adapter'].calibrate(port - 1)
    return web.json_response({'ok': True, 'port': port})


# ── WebSocket ─────────────────────────────────────────────────────────────

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    app = request.app

    # Effective settings for this client: server defaults, then whatever the
    # page URL asked for. Bad or unrelated query params are ignored.
    client = dict(app['settings'])
    client.update(overlay_settings.parse(request.query, strict=False))
    app['ws_clients'][ws] = client

    if not await push_settings(app, ws, client):
        return ws

    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                if msg.type == web.WSMsgType.ERROR:
                    break
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue

            # A client's settings change is local to that client — it does not
            # move the server defaults or other browser sources.
            patch = data.get('settings')
            if isinstance(patch, dict):
                client.update(overlay_settings.parse(patch, strict=False))

            if data.get('calibrate'):
                app['adapter'].calibrate(client['port'] - 1)
    finally:
        app['ws_clients'].pop(ws, None)
    return ws


async def broadcast_loop(app):
    """Continuously broadcast controller state to all WebSocket clients.

    Each client receives state for its own selected port.
    """
    try:
        while True:
            # Cache states per port to avoid redundant calls
            cached_states = {}
            dead = set()

            for ws, client in list(app['ws_clients'].items()):
                port = client['port']
                if port not in cached_states:
                    cached_states[port] = json.dumps(_state_payload(app, port))

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


def create_app(adapter, settings=None, mode='unknown'):
    """Create the aiohttp application.

    ``settings`` is a partial override of overlay_settings.DEFAULTS, used for
    the CLI flags that seed the server defaults. ``mode`` is the transport
    label already computed for the startup banner ('demo', 'usb',
    'memorywatcher', 'dme'), passed through to clients in the state payload.
    """
    app = web.Application(middlewares=[api_cors_middleware])
    app['adapter'] = adapter
    app['mode'] = mode
    app['settings'] = overlay_settings.defaults()
    app['settings'].update(settings or {})
    app['ws_clients'] = {}  # {WebSocketResponse: settings dict}

    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/api/settings', settings_get_handler)
    app.router.add_post('/api/settings', settings_post_handler)
    app.router.add_get('/api/state', state_get_handler)
    app.router.add_post('/api/calibrate', calibrate_post_handler)
    app.router.add_route('OPTIONS', '/api/{tail:.*}', lambda r: web.Response(status=204))
    app.router.add_static('/static', STATIC_DIR)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app
