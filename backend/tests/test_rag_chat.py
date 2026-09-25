import pytest
from app.agent import runner
from app.agent.providers import Decision
from app.database.store import Store
from app.sessions.manager import BrowserSession
from app.rag.models import RetrievalResult
from app.rag.service import rag_service

@pytest.fixture
def chat(monkeypatch):
    db = Store(':memory:')
    cid = db.create()['id']
    session = BrowserSession(cid)
    monkeypatch.setattr(runner, 'store', db)
    yield session, db
    db.db.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('domain', ['selected-domain', None])
async def test_selected_knowledge_searches_before_model_and_cites(chat, monkeypatch, domain):
    session, db = chat
    db.message(session.session_id, 'user', 'Show Morgan Test demographic information')
    calls = []
    async def query(workspace, question, selected, **kwargs):
        calls.append((workspace, question, selected))
        return RetrievalResult([{'id': 'chunk'}], [{'chunk_id': 'chunk', 'document_id': 'doc', 'file_name': 'synthetic.csv'}], '[Source 1: synthetic.csv] Morgan Test lives in Example City.', 'query')
    async def decide(provider, context, messages, tools, emit):
        assert calls and calls[0][2] == domain
        assert 'Morgan Test lives in Example City' in context
        return Decision('Morgan Test lives in Example City. [Source 1]', [])
    monkeypatch.setattr(rag_service, 'query', query)
    monkeypatch.setattr(runner, 'decide', decide)
    await runner.run(session, 'Show Morgan Test demographic information', 'azure', True, domain)
    assert len(calls) == 1
    assert db.conversation(session.session_id)['messages'][-1]['sources'][0]['chunk_id'] == 'chunk'
    assert session.mcp.status == 'closed'
    assert db.rows('SELECT status FROM agent_runs')[0]['status'] == 'completed'

@pytest.mark.asyncio
async def test_off_never_searches(chat, monkeypatch):
    session, db = chat
    async def query(*args, **kwargs):
        raise AssertionError('Knowledge Off must not search')
    async def decide(provider, context, messages, tools, emit):
        assert 'rag_search' not in {t['name'] for t in tools}
        return Decision('Knowledge is off.', [])
    monkeypatch.setattr(rag_service, 'query', query)
    monkeypatch.setattr(runner, 'decide', decide)
    await runner.run(session, 'Show a record', 'azure', False, 'selected-domain')
    assert db.rows('SELECT status FROM agent_runs')[0]['status'] == 'completed'

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_empty_and_failed_retrieval_are_distinct(chat, monkeypatch, failure):
    session, db = chat
    async def query(*args, **kwargs):
        if failure:
            raise RuntimeError('private diagnostic must not reach model')
        return RetrievalResult([], [], '', 'query')
    async def decide(provider, context, messages, tools, emit):
        assert ('Retrieval failed' if failure else 'No sufficiently relevant source') in context
        assert 'private diagnostic' not in context
        return Decision('Knowledge service unavailable.' if failure else 'No matching record was found.', [])
    monkeypatch.setattr(rag_service, 'query', query)
    monkeypatch.setattr(runner, 'decide', decide)
    await runner.run(session, 'Show an unknown record', 'azure', True)
    assert not session.rag_sources
    assert db.rows('SELECT status FROM agent_runs')[0]['status'] == 'completed'

@pytest.mark.asyncio
async def test_short_followup_keeps_record_query(chat, monkeypatch):
    session, db = chat
    db.message(session.session_id, 'user', 'Show Morgan Test demographics')
    db.message(session.session_id, 'assistant', 'Which details?')
    db.message(session.session_id, 'user', 'yes')
    async def query(workspace, question, selected, **kwargs):
        assert 'Morgan Test' in question and 'yes' in question
        return RetrievalResult([], [], '', 'query')
    async def decide(*args):
        return Decision('No matching record.', [])
    monkeypatch.setattr(rag_service, 'query', query)
    monkeypatch.setattr(runner, 'decide', decide)
    await runner.run(session, 'yes', 'azure', True)
    assert db.rows('SELECT status FROM tool_calls')[0]['status'] == 'success'


@pytest.mark.asyncio
async def test_new_person_query_does_not_reuse_previous_person(chat, monkeypatch):
    session, db = chat
    db.message(session.session_id, 'user', 'Show Morgan Test demographics')
    db.message(session.session_id, 'user', 'Alex Example demographics')
    async def query(workspace, question, selected, **kwargs):
        assert question == 'Alex Example demographics'
        assert kwargs.get('queries') == []
        return RetrievalResult([], [], '', 'query')
    async def decide(*args):
        return Decision('No matching record.', [])
    monkeypatch.setattr(rag_service, 'query', query)
    monkeypatch.setattr(runner, 'decide', decide)
    await runner.run(session, 'Alex Example demographics', 'azure', True)
    assert db.rows('SELECT status FROM tool_calls')[0]['status'] == 'success'


@pytest.mark.parametrize('message', ['Open Google and find the patient record in uploaded documents', 'Create a report using our knowledge', 'Show Morgan Test demographics'])
def test_mixed_knowledge_requests_keep_rag(message):
    assert not runner.is_nonknowledge_request(message)

@pytest.mark.asyncio
@pytest.mark.parametrize('reply,expected', [('Done.', []), ('Matched [Source 2].', ['second']), ('Invalid [Source 99].', [])])
async def test_only_cited_sources_persist(chat, monkeypatch, reply, expected):
    session, db = chat
    async def query(*args, **kwargs):
        sources = [{'chunk_id': key, 'document_id': key, 'file_name': key + '.txt'} for key in ['first', 'second']]
        return RetrievalResult([{'id':'first'}, {'id':'second'}], sources, '[Source 1] First. [Source 2] Second.', 'query')
    async def decide(*args):
        return Decision(reply, [])
    monkeypatch.setattr(rag_service, 'query', query)
    monkeypatch.setattr(runner, 'decide', decide)
    await runner.run(session, 'Show the record', 'azure', True)
    response = db.conversation(session.session_id)['messages'][-1]
    assert [s['chunk_id'] for s in response.get('sources', [])] == expected
    if expected:
        assert response['sources'][0]['source_number'] == 2
        assert response['retrieval']['chunk_count'] == 1
    else:
        assert not response.get('retrieval')
