import asyncio
from app.browser.references import valid_refs
import json
import re
import time
import uuid
from pathlib import Path
from jsonschema import validate
from app.agent.providers import Decision, decide, append_result, compact_messages
from app.browser.state import observe, result_text
from app.config import settings
from app.runtime_settings import runtime_settings
from app.database.store import store, now
from app.services.security import redact
from app.agent.tools import ToolRegistry

SYSTEM = Path(__file__).with_name('system.md').read_text()
CONFIRM = {'name': 'request_confirmation', 'description': 'Request permission for the exact next high-impact action.', 'parameters': {'type': 'object', 'properties': {'description': {'type': 'string'}}, 'required': ['description'], 'additionalProperties': False}}
CLOSE = re.compile(r'^\s*(?:please\s+)?(?:close (?:the )?browser|exit browser|terminate browser)[.!]?\s*$', re.I)
RISK = re.compile(r'\b(delete|remove|purchase|buy|pay|checkout|send|submit|publish|transfer|save changes|confirm order)\b', re.I)
TOOL_RESULT_LIMITS = {'agent': 32000}
DEFAULT_TOOL_RESULT_LIMIT = 8000

def is_nonknowledge_request(message):
    """Bypass automatic retrieval for explicit operations, not mixed RAG tasks."""
    text = re.sub(r'^\s*(?:(?:please|can you|could you|would you)\s+)+', '', message.lower()).strip()
    if re.search(r'\b(rag|knowledge|uploaded|records?|patient|demographics?|treatment|policy|policies|documents?|medical|reports?|charts?|according to|internal)\b', text):
        return False
    if re.fullmatch(r'(hi|hello|hey|thanks|thank you|good morning|good evening)[!. ]*', text):
        return True
    return bool(re.match(
        r'^(?:open|launch|visit|navigate|go to|browse|click|scroll|type|press|refresh|reload|switch|close|take a screenshot|capture a screenshot|'
        r'(?:search|look up|find).*(?:google|bing|web|internet)|'
        r'(?:create|rename|move|delete|list) (?:a |the |my )?(?:file|folder|directory))\b', text))


def cited_sources(text, sources):
    numbers = {int(value) for value in re.findall(r'\[Source\s+(\d+)\]', text, re.I)}
    return [{**source, 'source_number': index} for index, source in enumerate(sources, 1) if index in numbers]


def tools_for(session):
    return ToolRegistry(session).definitions() + [CONFIRM]


async def confirmation(session, description):
    session.confirmation = asyncio.get_running_loop().create_future()
    session.pending_confirmation = {'id': str(uuid.uuid4()), 'description': redact(description)}
    await session.emit({'type': 'confirmation', **session.pending_confirmation})
    try:
        return await session.confirmation
    finally:
        session.confirmation = None
        session.pending_confirmation = None
        await session.emit({'type': 'confirmation_cleared'})

async def run(session, message, provider, use_knowledge=None, knowledge_domain_id=None):
    run_id = str(uuid.uuid4())
    store.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?)', (run_id, session.session_id, 'running', now(), None, None))
    session.activity, session.partial = [], ''
    session.active_skills = {}
    session.use_knowledge = use_knowledge
    session.knowledge_domain_id = knowledge_domain_id
    session.rag_sources = []
    session.rag_debug = None
    session.rag_context = ''
    await session.emit({'type': 'status', 'status': 'running'})
    final_status, error = 'completed', None
    async def say(text, role='assistant', include_rag=False):
        metadata = None
        sources = cited_sources(text, session.rag_sources) if include_rag else []
        if sources:
            metadata = {
                'sources': sources,
                'retrieval': {
                    'source_count': len({item['document_id'] for item in sources}),
                    'chunk_count': len(sources),
                    'domain_id': session.knowledge_domain_id,
                },
            }
            if settings.debug_rag and session.rag_debug:
                metadata['rag_debug'] = session.rag_debug
        await session.emit({'type': 'message', 'message': store.message(session.session_id, role, text, metadata)})
    try:
        if CLOSE.fullmatch(message):
            await session.mcp.stop()
            session.url, session.tabs, session.screenshot = '', '', 0
            await session.emit({'type': 'browser', **session.browser_state()})
            await say('Browser closed. Your conversation is saved.')
            return
        selected_skill = session.skills.resolve_command(message)
        if selected_skill:
            session.active_skills[selected_skill.metadata.name] = selected_skill
            if not session.knowledge_domain_id and selected_skill.metadata.knowledge_domain:
                from app.rag.service import rag_service
                domains = await rag_service.list_domains(session.workspace_id)
                requested = selected_skill.metadata.knowledge_domain.lower()
                match = next((item for item in domains if item['id'] == requested or item['name'].lower() == requested), None)
                if match:
                    session.knowledge_domain_id = match['id']
                    session.use_knowledge = True
            await session.emit({'type': 'activity', 'id': str(uuid.uuid4()), 'category': 'skill', 'name': 'Skill loaded', 'description': selected_skill.metadata.display_name, 'status': 'success'})
        history = store.conversation(session.session_id)['messages'][-30:]
        messages = [{'role': m['role'] if m['role'] in ('user', 'assistant') else 'assistant', 'content': m['content']} for m in history]
        tools = tools_for(session)
        executor = ToolRegistry(session, provider)
        schemas = {t['name']: t['parameters'] for t in tools}
        failures = {}
        approved = False
        # A selected domain scopes knowledge tasks; it must not turn a browser
        # operation or greeting into a document search.
        nonknowledge = is_nonknowledge_request(message)
        if nonknowledge:
            session.use_knowledge = False
            tools = tools_for(session)
            schemas = {t['name']: t['parameters'] for t in tools}
        retrieve_first = session.use_knowledge is not False and (
            session.use_knowledge is True or bool(session.knowledge_domain_id)
        ) and not nonknowledge
        query = message
        alternate_queries = []
        if re.match(r'^\s*(?:yes|no|please|continue|go ahead|more|all|their|his|her|what about|and)\b', message, re.I) and len(message.split()) <= 8:
            previous = [m['content'] for m in history if m['role'] == 'user' and m['content'] != message]
            if previous:
                query = previous[-1] + '\nFollow-up: ' + message
                alternate_queries = [previous[-1][:4000]]
        for _ in range(runtime_settings.values.max_steps):
            session.last_activity = time.time()
            snapshot = session.snapshot
            discovered = session.skills.discover()
            catalog = json.dumps([s['metadata'] for s in discovered['skills']])
            skills_context = '\n\n'.join(s.markdown for s in session.active_skills.values())
            context = SYSTEM + '\n\nAVAILABLE SKILLS:\n' + catalog + '\n\nACTIVE JOB INSTRUCTIONS (subordinate to tool security and user consent):\n' + skills_context + '\n\nCURRENT BROWSER OBSERVATION:\n' + snapshot[-45000:]
            if session.use_knowledge is False:
                context += '\nKNOWLEDGE MODE: Off. Do not use workspace retrieval.'
            elif session.knowledge_domain_id:
                context += '\nKNOWLEDGE MODE: The user selected a specific knowledge domain. Search it for record and document questions before asking for details. Do not use public web search for private records.'
            elif session.use_knowledge is True:
                context += '\nKNOWLEDGE MODE: Search all knowledge in the active workspace for this request.'
            else:
                context += '\nKNOWLEDGE MODE: Auto. Use rag_search for questions about uploaded documents, internal records, named patients/customers, policies, and workspace facts before asking for clarification.'
            if not runtime_settings.values.file_mcp_enabled:
                context += '\nFILE MCP: Disabled in Settings. File and workspace-note tools are unavailable. Do not claim to create or save files or notes. Ask the user to enable File MCP before creating a file.'
            context += '\n' + session.rag_context
            if retrieve_first:
                retrieve_first = False
                decision = Decision('', [{'id': 'rag-' + str(uuid.uuid4()), 'name': 'rag_search', 'arguments': {'query': query[:4000], 'queries': alternate_queries}}])
            else:
                decision = await decide(provider, context, compact_messages(messages), tools, session.emit)
            if not decision.calls:
                await say(decision.text or 'Task complete.', include_rag=True)
                break
            if len(decision.calls) != 1:
                raise RuntimeError('Model returned multiple simultaneous actions. Retry with a model supporting sequential tool use.')
            call = decision.calls[0]
            name, args = call['name'], call['arguments']
            if name not in schemas:
                raise ValueError('Model requested an unavailable tool.')
            validate(args, schemas[name])
            if decision.text:
                await say(decision.text)
            if name == 'request_confirmation':
                approved = await confirmation(session, args['description'])
                append_result(messages, decision, 'Approved for the next action only.' if approved else 'Denied. Do not perform this action.', provider)
                continue
            action_id, started = str(uuid.uuid4()), time.monotonic()
            category = executor.category(name)
            activity = {'type': 'activity', 'id': action_id, 'category': category, 'name': name.removeprefix(category + '_').replace('_', ' ').title(), 'description': redact(str(args.get('element') or args.get('url') or args.get('path') or args.get('destination') or args.get('name') or {'browser': 'Using the current browser state', 'agent': 'Parallel subtasks', 'knowledge': 'Workspace knowledge', 'skill': 'Skill operation'}.get(category, 'Downloads operation'))), 'status': 'running'}
            await session.emit(activity)
            store.execute('INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?)', (action_id, run_id, name, 'running', now(), None, None))
            result, tool_status = '', 'success'
            signature = name + json.dumps(args, sort_keys=True)
            try:
                if failures.get(signature, 0) > settings.max_tool_retries:
                    raise RuntimeError('Retry limit reached for this action. Choose a different approach.')
                if category == 'browser':
                    valid_refs(args, snapshot)
                risky = executor.needs_confirmation(name, args) or (category == 'browser' and (RISK.search(json.dumps(args)) or name in ('browser_evaluate', 'browser_file_upload')))
                if risky and not approved:
                    approved = await confirmation(session, f"Allow {activity['name']}: {activity['description']}?")
                    if not approved:
                        raise ValueError('User declined the action. Do not perform it.')
                approved = False
                result = await executor.execute(name, args)
            except asyncio.CancelledError:
                tool_status = 'cancelled'
                result = 'Cancelled by user.'
                raise
            except Exception as exc:
                tool_status = 'failed'
                result = redact(str(exc))
                failures[signature] = failures.get(signature, 0) + 1
            finally:
                activity.update(status=tool_status, duration=round(time.monotonic() - started, 2), details=redact(result[:2000]) if tool_status != 'success' else f'{category.title()} action completed.')
                store.execute('UPDATE tool_calls SET status=?,completed_at=?,error=? WHERE id=?', (tool_status, now(), redact(result) if tool_status == 'failed' else None, action_id))
                await session.emit(activity)
            fresh = snapshot
            if category == 'browser' and session.mcp.status == 'connected':
                fresh = await observe(session, screenshot=True)
            tools = tools_for(session)
            schemas = {t['name']: t['parameters'] for t in tools}
            result_limit = TOOL_RESULT_LIMITS.get(category, DEFAULT_TOOL_RESULT_LIMIT)
            append_result(messages, decision, redact(result[:result_limit]) + '\nFRESH OBSERVATION:\n' + fresh[-12000:], provider)
        else:
            final_status = 'limit_reached'
            await say('Maximum agent steps reached. The browser remains open; send a follow-up to continue.')
    except asyncio.CancelledError:
        final_status = 'cancelled'
        await say('Task stopped. Browser session remains open.')
    except Exception as exc:
        final_status, error = 'failed', redact(str(exc))
        await say(error, 'error')
    finally:
        for skill in session.active_skills.values():
            await session.emit({'type': 'activity', 'id': str(uuid.uuid4()), 'category': 'skill', 'name': 'Skill ' + ('completed' if final_status == 'completed' else final_status.replace('_', ' ')), 'description': skill.metadata.display_name, 'status': 'success' if final_status == 'completed' else 'cancelled' if final_status == 'cancelled' else 'failed', 'details': error or 'See task results above.'})
        store.execute('UPDATE agent_runs SET status=?,completed_at=?,error=? WHERE id=?', (final_status, now(), error, run_id))
        session.last_activity = time.time()
        await session.emit({'type': 'status', 'status': 'idle'})


