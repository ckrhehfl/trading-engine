"""The corrected whole audit preserves its original scientific registration."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = ROOT/'configs/research/discovery/intraday-market-context-corrected-audit-v1.json'


def test_corrected_audit_preserves_algorithm_bytes_and_scientific_scope():
    registration = json.loads(REGISTRATION.read_bytes())
    source = ROOT/registration['original_algorithm_spec_path']
    raw = source.read_bytes()
    original = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == registration['original_algorithm_spec_sha256']
    for field in ('upstream_sha256', 'upstream_specification_sha256',
            'input_compressed_sha256', 'input_stream_sha256', 'window', 'parameters', 'resources'):
        assert registration[field] == original[field]
    assert registration['preserved_previous_output_name'] == original['output_name']


def test_execution_identity_is_new_exclusive_once_and_not_a_verifier_resume():
    registration = json.loads(REGISTRATION.read_bytes())
    assert registration['registration_id'] == 'intraday-market-context-corrected-audit-v1'
    assert registration['mode'] == 'one-whole-corrected-market-context-readiness-audit'
    assert registration['authorization'] == 'one_whole_corrected_audit'
    assert registration['output_name'] == 'intraday-dp-market-context-readiness-20261025-v1'
    assert registration['output_name'] != registration['preserved_previous_output_name']
    assert registration['previous_failed_run_id'] == 'ed1594af-bc1f-4b26-9098-5f542293601d'
    assert type(registration['automatic_retries']) is int
    assert registration['automatic_retries'] == 0
    assert registration['resources']['automatic_retries'] == 0
