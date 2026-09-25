import pytest
from app.agent.runner import valid_refs
from app.agent.parallel import _valid_refs

@pytest.mark.parametrize('validate', [valid_refs, _valid_refs])
def test_selectors_and_current_references(validate):
    snapshot = '- link "Result" [ref=e12]\n- button "Go" [ref=f2e9]'
    validate({'target': 'e12'}, snapshot)
    validate({'target': 'f2e9'}, snapshot)
    validate({'target': 'a[href="https://example.com"]'}, snapshot)
    validate({'target': 'text=Result'}, snapshot)
    validate({'startTarget': '#source', 'endTarget': '#destination'}, snapshot)
    validate({'fields': [{'target': 'input[name="query"]', 'value': 'tools'}]}, snapshot)

@pytest.mark.parametrize('validate', [valid_refs, _valid_refs])
@pytest.mark.parametrize('args', [
    {'target': 'e999'}, {'ref': 'e999'}, {'target': 'f2e99'},
    {'fields': [{'target': 'e999'}]}, {'startTarget': 'e12', 'endTarget': 'e999'},
])
def test_stale_references_remain_rejected(validate, args):
    with pytest.raises(ValueError, match='Reference is absent'):
        validate(args, '- link "Result" [ref=e12]')
