"""The new CPU-corrected execution preserves the original research contract."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cpu_execution_keeps_original_algorithm_and_all_scientific_parameters():
    registration = json.loads((ROOT/'configs/research/discovery/intraday-market-context-cpu-audit-v1.json').read_bytes())
    raw = (ROOT/registration['original_algorithm_spec_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == registration['original_algorithm_spec_sha256']
    original = json.loads(raw)
    for key in ('upstream_sha256', 'upstream_specification_sha256', 'input_compressed_sha256',
            'input_stream_sha256', 'window', 'parameters', 'resources'):
        assert registration[key] == original[key]
    assert registration['output_name'] not in registration['preserved_failed_output_names']


def test_explicit_one_whole_cpu_repair_authorization_preserves_both_failures():
    registration = json.loads((ROOT/'configs/research/discovery/intraday-market-context-cpu-audit-v1.json').read_bytes())
    assert registration['registration_id'] == 'intraday-market-context-cpu-audit-v1'
    assert registration['mode'] == 'one-whole-cpu-corrected-market-context-readiness-audit'
    assert registration['authorization'] == 'one_whole_cpu_corrected_audit'
    assert registration['output_name'] == 'intraday-dq-market-context-readiness-20261025-v1'
    assert registration['previous_failed_run_id'] == '2f0916c1-c92f-413c-af68-663f21c4e6d5'
    assert registration['preserved_failed_run_ids'] == [
        'ed1594af-bc1f-4b26-9098-5f542293601d', '2f0916c1-c92f-413c-af68-663f21c4e6d5']
    assert registration['preserved_failed_output_names'] == [
        'intraday-do-market-context-readiness-20261025-v1',
        'intraday-dp-market-context-readiness-20261025-v1']
    assert type(registration['automatic_retries']) is int and registration['automatic_retries'] == 0
