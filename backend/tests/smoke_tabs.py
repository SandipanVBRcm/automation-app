"""Real MCP regression: popup selection, screenshots, subsequent actions and closing tabs."""
import asyncio
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app.config import settings
from app.sessions.manager import BrowserSession
from app.browser.state import observe, result_text
from app.browser.references import valid_refs

class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        if self.path == '/popup':
            html = '<title>Popup</title><body style="background:blue"><h1>Popup</h1><button onclick="document.querySelector(\'h1\').textContent=\'Popup action done\'">Work here</button>'
        else:
            html = '<title>Original</title><body style="background:red"><h1>Original</h1><a href="/popup" target="_blank">Open popup</a>'
        self.wfile.write(html.encode())
    def log_message(self, *args):
        pass

async def main():
    settings.headless = True
    server = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = BrowserSession('test-tabs-' + str(uuid.uuid4()))
    try:
        await session.mcp.start()
        await session.mcp.call('browser_navigate', {'url': f'http://127.0.0.1:{server.server_port}/'})
        original = await observe(session)
        image = session.mcp.directory / 'screenshots/latest.png'
        original_image = image.read_bytes()
        async def click(label, snapshot):
            ref = re.search(r'(?:link|button) "' + label + r'" \[ref=([^\]]+)\]', snapshot).group(1)
            schema = next(t.inputSchema for t in session.mcp.tools if t.name == 'browser_click')
            key = 'target' if 'target' in schema['properties'] else 'ref'
            result = await session.mcp.call('browser_click', {key: ref, 'element': label})
            assert not result.isError, result_text(result)
        await click('Open popup', original)
        popup = await observe(session)
        assert session.mcp.active_tab == 1
        assert session.url.endswith('/popup')
        assert 'button "Work here"' in popup
        assert image.read_bytes() != original_image
        # Compare capture against the selected Page directly, bypassing tracking.
        import base64
        direct = await session.mcp._call_tool('browser_take_screenshot', {'type': 'png'})
        assert image.read_bytes() == base64.b64decode(next(b.data for b in direct.content if b.type == 'image'))
        args = {'target': 'button', 'element': 'Work here'}
        valid_refs(args, popup)
        result = await session.mcp.call('browser_click', args)
        assert not result.isError, result_text(result)
        assert 'Popup action done' in await observe(session)
        await session.mcp.call('browser_tabs', {'action': 'select', 'index': 0})
        assert 'Original' in await observe(session)
        assert session.mcp.active_tab == 0
        assert image.read_bytes() == original_image
        await session.mcp.call('browser_tabs', {'action': 'select', 'index': 1})
        assert 'Popup action done' in await observe(session)
        await session.mcp.call('browser_tabs', {'action': 'close', 'index': 1})
        assert 'Original' in await observe(session)
        assert session.mcp.active_tab == 0
        print('PASS: popup screenshot, subsequent action, explicit switches, active-tab closure')
    finally:
        await session.close()
        server.shutdown()

if __name__ == '__main__':
    asyncio.run(main())
