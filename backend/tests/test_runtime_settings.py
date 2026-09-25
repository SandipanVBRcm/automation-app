from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import asyncio
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runtime_settings import RuntimeSettings, RuntimeValues, runtime_settings
from app.sessions.manager import manager
from app.mcp.process import MCPProcess
from app.agent.tools import ToolRegistry
from app.browser.downloads import detect_downloads
from app.file_agent.service import FileService
from app import workspaces


def test_runtime_settings_save_reload_and_validate(monkeypatch):
    path = Path(__file__).parent / f'.runtime-settings-test-{uuid4()}.json'
    monkeypatch.setattr(runtime_settings, 'path', path)
    monkeypatch.setattr(runtime_settings, 'values', RuntimeSettings(path).values)

    try:
        with TestClient(app) as client:
            response = client.patch('/api/settings', json={'max_steps': 75, 'idle_timeout': 1800})
            assert response.status_code == 200
            assert response.json() == {'max_steps': 75, 'idle_timeout': 1800, 'playwright_mcp_enabled': True, 'file_mcp_enabled': True}
            assert client.get('/api/health').json()['max_steps'] == 75
            assert client.get('/api/health').json()['idle_timeout'] == 1800
            assert RuntimeSettings(path).values.model_dump() == response.json()

            class Session:
                closed = 0
                mcp = SimpleNamespace(status='connected')

                async def close(self):
                    self.closed += 1

            session = Session()
            monkeypatch.setattr(manager, 'sessions', {'test-session': session})
            disabled = client.patch('/api/settings', json={
                'max_steps': 75, 'idle_timeout': 1800, 'playwright_mcp_enabled': False,
            })
            assert disabled.status_code == 200
            assert disabled.json()['playwright_mcp_enabled'] is False
            assert session.closed == 1
            assert RuntimeSettings(path).values.playwright_mcp_enabled is False
            manager.sessions.clear()

            file_disabled = client.patch('/api/settings', json={
                'max_steps': 75, 'idle_timeout': 1800,
                'playwright_mcp_enabled': False, 'file_mcp_enabled': False,
            })
            assert file_disabled.status_code == 200
            assert file_disabled.json()['file_mcp_enabled'] is False
            assert RuntimeSettings(path).values.file_mcp_enabled is False
            assert client.get('/api/files/status').status_code == 403
            assert client.get('/api/files').status_code == 403

            invalid = client.patch('/api/settings', json={'max_steps': 0, 'idle_timeout': 90})
            assert invalid.status_code == 422
            assert RuntimeSettings(path).values.model_dump() == file_disabled.json()
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_disabled_playwright_mcp_exposes_no_browser_tools(monkeypatch):
    monkeypatch.setattr(runtime_settings, 'values', RuntimeValues(
        max_steps=50, idle_timeout=3600, playwright_mcp_enabled=False,
    ))
    process = MCPProcess.__new__(MCPProcess)
    with pytest.raises(RuntimeError, match='disabled in Settings'):
        await process.start()
    with pytest.raises(RuntimeError, match='disabled in Settings'):
        await process.call('browser_snapshot', {})

    session = SimpleNamespace(mcp=SimpleNamespace(status='closed', tools=[]), use_knowledge=False)
    names = {tool['name'] for tool in ToolRegistry(session).definitions()}
    assert not any(name.startswith('browser_') for name in names)
    assert 'file_save_screenshot' not in names


@pytest.mark.asyncio
async def test_disabled_file_mcp_blocks_file_tools_and_download_copy(monkeypatch):
    monkeypatch.setattr(runtime_settings, 'values', RuntimeValues(
        max_steps=50, idle_timeout=3600,
        playwright_mcp_enabled=True, file_mcp_enabled=False,
    ))
    session = SimpleNamespace(mcp=SimpleNamespace(status='connected', tools=[
        SimpleNamespace(name='browser_file_upload', description='Upload a file', inputSchema={}),
    ]), files=object(), use_knowledge=False)
    registry = ToolRegistry(session)
    names = {tool['name'] for tool in registry.definitions()}
    assert 'browser_file_upload' not in names
    assert not any(name.startswith('file_') for name in names)
    assert not any(name.startswith('knowledge_') for name in names)
    with pytest.raises(RuntimeError, match='File MCP is disabled'):
        await registry.execute('file_list_directory', {'path': '.'})
    with pytest.raises(RuntimeError, match='File MCP is disabled'):
        await registry.execute('knowledge_create', {'path': 'ai_tools_list.txt', 'content': 'test'})
    with pytest.raises(RuntimeError, match='File MCP is disabled'):
        await registry.execute('browser_file_upload', {'paths': ['report.csv']})
    assert await detect_downloads(session, '- Downloaded file report.csv to "report.csv"') == []


def test_files_from_all_workspaces_save_directly_in_downloads(monkeypatch):
    root = Path(__file__).parent / f'.downloads-test-{uuid4()}'
    service = FileService(root, blocked_names={'Orbit Workspaces'})
    monkeypatch.setattr(workspaces, 'downloads', service)
    workspaces.downloads_for.cache_clear()
    try:
        first = workspaces.downloads_for(str(uuid4()))
        second = workspaces.downloads_for(str(uuid4()))
        assert first is second is service
        asyncio.run(first.execute('create_file', {'path': 'report.txt', 'content': 'saved'}))
        assert root.joinpath('report.txt').read_text() == 'saved'
        assert not root.joinpath('Orbit Workspaces').exists()
    finally:
        root.joinpath('report.txt').unlink(missing_ok=True)
        root.rmdir()
        workspaces.downloads_for.cache_clear()
