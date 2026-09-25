"""Validate snapshot references without rejecting MCP selector targets."""
import re

REFERENCE = re.compile(r'(?:f\d+)?e\d+')

def valid_refs(arguments, snapshot):
    available = set(re.findall(r'\[ref=([^\]]+)\]', snapshot))
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                field = key.lower()
                if isinstance(item, str):
                    # MCP target fields also accept unique selectors. Only bare
                    # reference tokens must be present in the latest snapshot.
                    is_ref = field.endswith('ref') or (field.endswith('target') and REFERENCE.fullmatch(item))
                    if is_ref and item not in available:
                        raise ValueError('Reference is absent from the current snapshot. Inspect the new snapshot and choose again.')
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(arguments)
