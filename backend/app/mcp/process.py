import asyncio
import os
import re
import shutil
import socket
import subprocess
import time
from contextlib import AsyncExitStack
import httpx
import psutil
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from app.config import ROOT, DATA, settings
from app.database.store import DEFAULT_WORKSPACE_ID
from app.runtime_settings import runtime_settings

class MCPProcess:
    """One loopback MCP subprocess and long-lived client owner task per conversation."""
    def __init__(self, session_id, workspace_id=DEFAULT_WORKSPACE_ID):
        self.session_id = session_id
        self.workspace_id = workspace_id
        self.process = None
        self.port = None
        self.status = 'closed'
        self.started_at = None
        self.client = None
        self.tools = []
        self.owner = None
        self.ready = asyncio.Event()
        self.shutdown = asyncio.Event()
        self.error = None
        self.lock = asyncio.Lock()
        self.active_tab = None
        self._tab_indices = set()
        session_root = DATA / 'sessions' if workspace_id == DEFAULT_WORKSPACE_ID else DATA / 'workspaces' / workspace_id / 'sessions'
        self.directory = session_root / session_id
        self.directory.mkdir(parents=True, exist_ok=True)

    async def start(self):
        if not runtime_settings.values.playwright_mcp_enabled:
            raise RuntimeError('Playwright MCP is disabled in Settings.')
        if self.owner and not self.owner.done():
            await self.ready.wait()
            if not runtime_settings.values.playwright_mcp_enabled:
                raise RuntimeError('Playwright MCP is disabled in Settings.')
            return
        self.ready.clear()
        self.shutdown.clear()
        self.error = None
        self.active_tab = None
        self._tab_indices.clear()
        self.owner = asyncio.create_task(self._serve())
        await self.ready.wait()
        if not runtime_settings.values.playwright_mcp_enabled:
            await self.stop()
            raise RuntimeError('Playwright MCP is disabled in Settings.')
        if self.error:
            raise RuntimeError(self.error)

    async def _serve(self):
        self.status = 'starting'
        try:
            if not runtime_settings.values.playwright_mcp_enabled:
                raise RuntimeError('Playwright MCP is disabled in Settings.')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.port = sock.getsockname()[1]
            cli = ROOT / 'node_modules' / '@playwright' / 'mcp' / 'cli.js'
            if cli.exists() and settings.playwright_mcp_command == 'npx' and settings.playwright_mcp_package == '@playwright/mcp':
                command = [shutil.which('node') or 'node', str(cli)]
            else:
                executable = shutil.which(settings.playwright_mcp_command)
                if not executable:
                    raise RuntimeError('MCP command not found. Install Node.js and run npm install in browser-agent.')
                command = [executable, '--no-install', settings.playwright_mcp_package]
            command += ['--port', str(self.port), '--host', '127.0.0.1', '--isolated', '--shared-browser-context', '--idle-timeout', '0', '--viewport-size', '1280x800', '--output-dir', str(self.directory), '--output-max-size', '10000000']
            command += ['--allowed-hosts', f'127.0.0.1:{self.port},localhost:{self.port}']
            # Use the Chromium installed with this project's Playwright version.
            browser_path = await asyncio.to_thread(subprocess.check_output, [shutil.which('node') or 'node', '-e', "console.log(require('playwright').chromium.executablePath())"], cwd=ROOT, text=True)
            executable_path = settings.browser_executable_path or browser_path.strip()
            if not __import__('pathlib').Path(executable_path).exists():
                for candidate in [r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']:
                    if __import__('pathlib').Path(candidate).exists():
                        executable_path = candidate
                        break
            command += ['--executable-path', executable_path]
            if settings.headless:
                command.append('--headless')
            safe_env = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in ('API_KEY', 'SECRET', 'TOKEN'))}
            if not runtime_settings.values.playwright_mcp_enabled:
                raise RuntimeError('Playwright MCP is disabled in Settings.')
            self.process = await asyncio.to_thread(subprocess.Popen, command, cwd=self.directory, env=safe_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.started_at = time.time()
            async with httpx.AsyncClient(trust_env=False) as probe:
                for _ in range(100):
                    if self.process.poll() is not None:
                        raise RuntimeError('Playwright MCP exited during startup. Check Node and Chromium installation.')
                    try:
                        response = await probe.get(f'http://127.0.0.1:{self.port}/mcp')
                        if response.status_code == 403:
                            raise RuntimeError('MCP refused localhost connection: ' + response.text[:1000])
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(.1)
                else:
                    raise RuntimeError('Playwright MCP startup timed out.')
            async with AsyncExitStack() as stack:
                def local_client(**kwargs):
                    return httpx.AsyncClient(**kwargs, trust_env=False)
                read, write, _ = await stack.enter_async_context(streamablehttp_client(f'http://127.0.0.1:{self.port}/mcp', httpx_client_factory=local_client))
                self.client = await stack.enter_async_context(ClientSession(read, write))
                await self.client.initialize()
                self.tools = (await self.client.list_tools()).tools
                self.status = 'connected'
                self.ready.set()
                await self.shutdown.wait()
        except Exception as exc:
            def explain(error):
                if isinstance(error, BaseExceptionGroup):
                    return '; '.join(explain(e) for e in error.exceptions)
                return f'{type(error).__name__}: {error}'
            self.error = explain(exc)
            self.status = 'error'
        finally:
            self.ready.set()
            self.client = None
            await asyncio.to_thread(self._kill_tree)
            if self.status != 'error':
                self.status = 'closed'

    def _kill_tree(self):
        if self.process and self.process.poll() is None:
            try:
                parent = psutil.Process(self.process.pid)
                children = parent.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                parent.terminate()
                _, alive = psutil.wait_procs(children + [parent], timeout=3)
                for proc in alive:
                    try:
                        proc.kill()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass

    async def call(self, name, arguments):
        if not runtime_settings.values.playwright_mcp_enabled:
            raise RuntimeError('Playwright MCP is disabled in Settings.')
        async with self.lock:
            if not runtime_settings.values.playwright_mcp_enabled:
                raise RuntimeError('Playwright MCP is disabled in Settings.')
            if not self.client:
                raise RuntimeError('Browser is not connected.')
            # MCP owns the Page objects. Select through MCP so actions, snapshots
            # and screenshots all use the same Page, including delayed popups.
            explicit_tabs = name == 'browser_tabs' and arguments.get('action') in ('select', 'new', 'close')
            await self._sync_active_tab(follow_new=not explicit_tabs)
            captured_tab = self.active_tab
            result = await self._call_tool(name, arguments)
            await self._sync_active_tab(follow_new=not explicit_tabs)
            # A popup can arrive while a preview is being captured. Never return
            # an observation of the old Page after selecting the new one.
            if name in ('browser_snapshot', 'browser_take_screenshot', 'browser_tabs') and not explicit_tabs and captured_tab != self.active_tab:
                result = await self._call_tool(name, arguments)
            return result

    async def _call_tool(self, name, arguments):
        return await self.client.call_tool(name, arguments, read_timeout_seconds=__import__('datetime').timedelta(seconds=90))

    async def _sync_active_tab(self, follow_new=True):
        tabs = await self._call_tool('browser_tabs', {'action': 'list'})
        if tabs.isError:
            raise RuntimeError('Unable to determine the active browser tab.')
        text = '\n'.join(block.text for block in tabs.content if block.type == 'text')
        entries = re.findall(r'^- (\d+):( \(current\))? \[', text, re.MULTILINE)
        indices = {int(index) for index, _ in entries}
        current = next((int(index) for index, selected in entries if selected), None)
        opened = indices - self._tab_indices
        if follow_new and self._tab_indices and opened:
            target = max(opened)
            if target != current:
                selected = await self._call_tool('browser_tabs', {'action': 'select', 'index': target})
                if selected.isError:
                    raise RuntimeError('Unable to select the newly opened browser tab.')
                current = target
        self._tab_indices = indices
        self.active_tab = current

    async def stop(self):
        self.shutdown.set()
        if self.owner:
            await self.owner

    async def restart(self):
        await self.stop()
        await self.start()

    def health_check(self):
        return {'session_id': self.session_id, 'process_id': self.process.pid if self.process else None, 'port': self.port, 'status': self.status, 'started_at': self.started_at}
