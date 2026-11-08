"""Invented byte packets only; never open the registered native environment."""

import builtins
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from research import intraday_context_numerical_receipt as audit


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(value):
    return sha256(encode(value)).hexdigest()


def publish(packet):
    packet["publication"]["independent_verification"]["numerical_environment"] = {
        "before": deepcopy(packet["before"]), "after": deepcopy(packet["after"])}
    packet["publication_raw"] = encode(packet["publication"])
    packet["publication_sha256"] = sha256(packet["publication_raw"]).hexdigest()


def check(packet):
    return audit.verify_numerical_receipt(packet["manifest_raw"], packet["proof_raw"], packet["publication_raw"],
        expected_environment=packet["environment"], expected_publication_sha256=packet["publication_sha256"],
        expected_binding=packet["binding"], before=packet["before"], after=packet["after"])


@pytest.fixture
def packet(monkeypatch):
    # Only this invented fixture replaces immutable evidence pins. Production
    # has no alternate-environment, alternate-count or authentication bypass.
    files = {name + "/__init__.py": char * 64
             for name, char in (("numpy", "a"), ("scipy", "b"), ("lightgbm", "c"))}
    files.update({"numpy.libs/libinvented.so": "d" * 64, "scipy.libs/libinvented.so": "e" * 64,
                  "lightgbm/lib/lib_lightgbm.so": "f" * 64})
    files.update({f"numpy/invented-{index:04d}.py": "a" * 64 for index in range(2383)})
    environment = audit._environment()
    mapped = {environment["site_packages"] + "/" + name: pin for name, pin in files.items() if ".so" in name}
    mapped["/usr/lib/invented-system.so"] = "1" * 64
    manifest = {"status": "installed_not_qualified", "site_packages": environment["site_packages"],
        "runtime": "/usr/bin/python3.12", "thread_environment": environment["thread_environment"],
        "files": files, "model_fits": 0, "actual_market_inputs": 0, "wheels": {"invented": True}}
    proof = {"status": "invented_import_only_pass", "versions": environment["versions"],
             "model_fits": 0, "market_inputs": 0, "mapped_libraries": mapped, "cpu_self": 1.5}
    manifest_raw = encode(manifest)
    # Original import proofs use json.dumps(sort_keys=True), including spaces.
    proof_raw = (json.dumps(proof, sort_keys=True) + "\n").encode()
    monkeypatch.setattr(audit, "_MANIFEST_SHA256", sha256(manifest_raw).hexdigest())
    monkeypatch.setattr(audit, "_IMPORT_PROOF_SHA256", sha256(proof_raw).hexdigest())
    environment = audit._environment()
    before = {"registered": environment, "environment_sha256": digest(environment),
        "manifest_sha256": environment["manifest_sha256"], "import_proof_sha256": environment["import_proof_sha256"],
        "installed_file_count": len(files), "installed_file_map_sha256": digest(files), "mapped_libraries": mapped,
        "mapped_libraries_sha256": digest(mapped), "versions": environment["versions"], "runtime_sha256": "2" * 64,
        "thread_environment": environment["thread_environment"], "bytecode_writes": False}
    after = {"verified": True, "environment_sha256": before["environment_sha256"],
        "installed_file_count": len(files), "installed_file_map_sha256": before["installed_file_map_sha256"],
        "mapped_libraries_sha256": before["mapped_libraries_sha256"],
        "full_installed_roster_rehashed": True, "mapped_binary_closure_unchanged": True}
    registration = "configs/research/discovery/intraday-context-forecast-v1.json"
    sources = {registration: "3" * 64, "python/research/intraday_ed_numerical.py": "4" * 64,
               "python/research/intraday_context_numerical_receipt.py": "5" * 64}
    binding = {"unit_id": "EI-FORECAST-1", "invented_only": True, "source_manifest_sha256": digest(sources),
        "registration_path": registration, "registration_sha256": sources[registration],
        "runtime_sha256": "2" * 64, "output_sha256": "6" * 64}
    identity = {"runtime_sha256": binding["runtime_sha256"], "source_manifest_sha256": digest(sources),
                "execution_host": "local_wsl_only"}
    publication = {"status": "completed", "unit_id": binding["unit_id"], "invented_only": True,
        "output_sha256": binding["output_sha256"], "execution_host": "local_wsl_only",
        "resource_profile": "ei-local-model-cpu1800-as4096-v1", "full_bridge": True,
        "local_pipe_test": False, "verification_complete": True, "source_hashes": sources,
        "local_identity": identity, "remote_identity": deepcopy(identity), "independent_verification": {"verified": True}}
    result = {"manifest": manifest, "proof": proof, "manifest_raw": manifest_raw, "proof_raw": proof_raw,
        "environment": environment, "before": deepcopy(before), "after": deepcopy(after),
        "binding": binding, "publication": publication}
    publish(result)
    return result


def authenticate_invented(packet, monkeypatch):
    """Model an externally pinned alternative original solely for mutation tests."""
    packet["manifest_raw"], packet["proof_raw"] = encode(packet["manifest"]), encode(packet["proof"])
    monkeypatch.setattr(audit, "_MANIFEST_SHA256", sha256(packet["manifest_raw"]).hexdigest())
    monkeypatch.setattr(audit, "_IMPORT_PROOF_SHA256", sha256(packet["proof_raw"]).hexdigest())
    packet["environment"] = audit._environment()
    packet["before"].update(registered=packet["environment"], environment_sha256=digest(packet["environment"]),
        manifest_sha256=audit._MANIFEST_SHA256, import_proof_sha256=audit._IMPORT_PROOF_SHA256)
    packet["after"]["environment_sha256"] = packet["before"]["environment_sha256"]
    publish(packet)


def test_fixed_contract_matches_existing_ed_registration_without_native_access():
    from research import intraday_ed_numerical

    assert audit._environment() == intraday_ed_numerical.ENVIRONMENT
    assert audit._FILES == intraday_ed_numerical.INSTALLED_FILE_COUNT


def test_valid_saved_proof_is_bounded_to_recorded_claims_and_has_no_io_or_ml_import(packet, monkeypatch):
    original = deepcopy(packet)
    imported = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in ("numpy", "scipy", "lightgbm")
        return imported(name, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("verifier opened a file"))
        guard.setattr(builtins, "__import__", guarded_import)
        result = check(packet)
    assert packet == original
    assert result["verified"] is True and result["saved_receipt_only"] is True
    assert result["installed_file_count"] == 2389
    assert result["publication_sha256"] == packet["publication_sha256"]
    assert result["source_manifest_sha256"] == packet["binding"]["source_manifest_sha256"]
    assert result["native_files_read"] is result["numerical_libraries_imported"] is result["historical_process_reobserved"] is False


@pytest.mark.parametrize("name", ["manifest_raw", "proof_raw", "publication_raw"])
def test_changed_original_bytes_cannot_reuse_external_pin(packet, name):
    packet[name] += b" "
    with pytest.raises(ValueError, match="external pin"):
        check(packet)


def test_self_rehashed_environment_replacement_cannot_change_fixed_registered_pin(packet):
    packet["manifest"]["files"]["numpy/__init__.py"] = "0" * 64
    packet["manifest_raw"] = encode(packet["manifest"])
    packet["environment"]["manifest_sha256"] = sha256(packet["manifest_raw"]).hexdigest()
    packet["before"].update(registered=packet["environment"], environment_sha256=digest(packet["environment"]),
                           manifest_sha256=packet["environment"]["manifest_sha256"])
    packet["after"]["environment_sha256"] = packet["before"]["environment_sha256"]
    publish(packet)
    with pytest.raises(ValueError, match="fixed registered"):
        check(packet)


def test_self_rehashed_installed_map_cannot_replace_original_manifest(packet):
    changed = {**packet["manifest"]["files"], "numpy/__init__.py": "0" * 64}
    packet["before"]["installed_file_map_sha256"] = digest(changed)
    packet["after"]["installed_file_map_sha256"] = digest(changed)
    publish(packet)
    with pytest.raises(ValueError, match="authenticated originals"):
        check(packet)


@pytest.mark.parametrize("change", ["pin", "extra", "missing"])
def test_self_rehashed_mapped_libraries_still_require_original_allowed_closure(packet, change):
    mapped = packet["before"]["mapped_libraries"]
    if change == "pin":
        mapped["/usr/lib/invented-system.so"] = "0" * 64
    elif change == "extra":
        mapped["/invented/unregistered.so"] = "0" * 64
    else:
        del mapped["/usr/lib/invented-system.so"]
    packet["before"]["mapped_libraries_sha256"] = digest(mapped)
    packet["after"]["mapped_libraries_sha256"] = digest(mapped)
    publish(packet)
    with pytest.raises(ValueError, match="permitted closure"):
        check(packet)


def test_wrong_original_manifest_rejected_even_when_its_bytes_are_authenticated(packet, monkeypatch):
    packet["manifest"]["files"]["numpy/__init__.py"] = "0" * 64
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="authenticated originals"):
        check(packet)


def test_conflicting_original_native_binary_pins_rejected(packet, monkeypatch):
    packet["manifest"]["files"]["lightgbm/lib/lib_lightgbm.so"] = "0" * 64
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="binary pins conflict"):
        check(packet)


def test_original_proof_cannot_invent_an_unlisted_library_inside_the_install(packet, monkeypatch):
    path = packet["environment"]["site_packages"] + "/unlisted.so"
    packet["proof"]["mapped_libraries"][path] = "0" * 64
    packet["before"]["mapped_libraries"][path] = "0" * 64
    packet["before"]["mapped_libraries_sha256"] = digest(packet["before"]["mapped_libraries"])
    packet["after"]["mapped_libraries_sha256"] = packet["before"]["mapped_libraries_sha256"]
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="binary pins conflict"):
        check(packet)


def test_installed_native_libraries_beyond_the_required_import_map_remain_permitted(packet, monkeypatch):
    del packet["proof"]["mapped_libraries"][packet["environment"]["site_packages"] + "/scipy.libs/libinvented.so"]
    authenticate_invented(packet, monkeypatch)
    assert check(packet)["verified"] is True


@pytest.mark.parametrize("change", ["file_count", "entrypoint", "version", "runtime", "threads"])
def test_authenticated_original_still_requires_fixed_install_contract(packet, monkeypatch, change):
    if change == "file_count":
        del packet["manifest"]["files"]["numpy/invented-0000.py"]
    elif change == "entrypoint":
        packet["manifest"]["files"]["renamed.py"] = packet["manifest"]["files"].pop("scipy/__init__.py")
    elif change == "version":
        packet["proof"]["versions"] = {**packet["proof"]["versions"], "numpy": "0.0"}
    elif change == "runtime":
        packet["manifest"]["runtime"] = "/usr/bin/python3.13"
    else:
        packet["manifest"]["thread_environment"] = {**packet["manifest"]["thread_environment"], "OMP_NUM_THREADS": "2"}
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="roster/count|preparation differs"):
        check(packet)


@pytest.mark.parametrize("path", ["../outside.py", "numpy/../outside.py", "numpy//x.py", "numpy/./x.py",
                                  "/numpy/x.py", "numpy\\x.py", "numpy/\x00x.py"])
def test_authenticated_manifest_cannot_contain_unsafe_relative_paths(packet, monkeypatch, path):
    packet["manifest"]["files"][path] = packet["manifest"]["files"].pop("numpy/invented-0000.py")
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="pin map"):
        check(packet)


@pytest.mark.parametrize("path", ["relative.so", "//usr/lib/x.so", "/usr/../x.so", "/usr//x.so",
                                  "/usr/./x.so", "/usr/\nx.so", "/usr/x.so (deleted)"])
def test_authenticated_proof_cannot_contain_unsafe_mapped_paths(packet, monkeypatch, path):
    packet["proof"]["mapped_libraries"][path] = packet["proof"]["mapped_libraries"].pop("/usr/lib/invented-system.so")
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="pin map|mapped library path"):
        check(packet)


@pytest.mark.parametrize("scope,field,value", [
    ("before", "installed_file_count", 2389.0), ("before", "installed_file_count", True),
    ("before", "bytecode_writes", 0), ("after", "verified", 1),
    ("after", "full_installed_roster_rehashed", 1), ("after", "mapped_binary_closure_unchanged", 1),
    ("publication", "invented_only", 1), ("publication", "full_bridge", 1),
    ("publication", "verification_complete", 1), ("binding", "invented_only", 1)])
def test_numeric_boolean_and_integer_float_substitutions_are_rejected(packet, scope, field, value):
    packet[scope][field] = value
    publish(packet)
    with pytest.raises(ValueError):
        check(packet)


@pytest.mark.parametrize("scope,field", [("manifest", "model_fits"), ("manifest", "actual_market_inputs"),
                                         ("proof", "model_fits"), ("proof", "market_inputs")])
def test_original_preparation_zero_is_not_false(packet, monkeypatch, scope, field):
    packet[scope][field] = False
    authenticate_invented(packet, monkeypatch)
    with pytest.raises(ValueError, match="preparation differs"):
        check(packet)


@pytest.mark.parametrize("field", ["installed_file_map_sha256", "mapped_libraries_sha256", "environment_sha256"])
def test_after_receipt_must_match_verified_before(field, packet):
    packet["after"][field] = "0" * 64
    publish(packet)
    with pytest.raises(ValueError, match="after-verification"):
        check(packet)


@pytest.mark.parametrize("scope", ["before", "after", "binding"])
@pytest.mark.parametrize("change", ["extra", "missing"])
def test_closed_receipt_and_binding_schemas(packet, scope, change):
    if change == "extra":
        packet[scope]["extra"] = True
    else:
        del packet[scope][next(iter(packet[scope]))]
    publish(packet)
    with pytest.raises(ValueError, match="missing/extra"):
        check(packet)


def test_receipt_must_be_the_pinned_publication_member(packet):
    packet["after"]["verified"] = False
    with pytest.raises(ValueError, match="original publication members"):
        check(packet)


@pytest.mark.parametrize("change", ["source", "registration", "runtime_before", "runtime_local", "runtime_remote", "output", "unit", "profile"])
def test_publication_source_output_and_interpreter_binding(packet, change):
    if change == "source":
        packet["publication"]["source_hashes"]["python/research/intraday_ed_numerical.py"] = "0" * 64
    elif change == "registration":
        packet["binding"]["registration_sha256"] = "0" * 64
    elif change == "runtime_before":
        packet["before"]["runtime_sha256"] = "0" * 64
    elif change.startswith("runtime_"):
        packet["publication"][change.removeprefix("runtime_") + "_identity"]["runtime_sha256"] = "0" * 64
    elif change == "output":
        packet["publication"]["output_sha256"] = "0" * 64
    elif change == "unit":
        packet["publication"]["unit_id"] = "EI-FORECAST-2"
    else:
        packet["publication"]["resource_profile"] = "ef-local-sequence-cpu1800-as4096-v1"
    publish(packet)
    with pytest.raises(ValueError):
        check(packet)


@pytest.mark.parametrize("scope", ["manifest", "proof", "publication"])
@pytest.mark.parametrize("change", ["duplicate", "nan", "infinite_exponent"])
def test_original_json_rejects_duplicate_and_nonfinite_fields(packet, monkeypatch, scope, change):
    raw_key = {"manifest": "manifest_raw", "proof": "proof_raw", "publication": "publication_raw"}[scope]
    field = {"duplicate": b'"probe":0,"probe":1,', "nan": b'"probe":NaN,',
             "infinite_exponent": b'"probe":1e999,'}[change]
    packet[raw_key] = b"{" + field + packet[raw_key][1:]
    pin = sha256(packet[raw_key]).hexdigest()
    if scope == "publication":
        packet["publication_sha256"] = pin
    else:
        name = "_MANIFEST_SHA256" if scope == "manifest" else "_IMPORT_PROOF_SHA256"
        monkeypatch.setattr(audit, name, pin)
        packet["environment"] = audit._environment()
    with pytest.raises(ValueError, match="duplicate JSON|nonfinite JSON"):
        check(packet)


@pytest.mark.parametrize("raw", [b"", b"x" * (1024**2 + 1), bytearray(b"{}\n")])
def test_original_byte_documents_are_immutable_and_bounded(packet, raw):
    packet["proof_raw"] = raw
    with pytest.raises(ValueError, match="bounded original"):
        check(packet)


def test_publication_encoding_is_canonical_even_with_a_matching_external_pin(packet):
    packet["publication_raw"] = (json.dumps(packet["publication"], sort_keys=True) + "\n").encode()
    packet["publication_sha256"] = sha256(packet["publication_raw"]).hexdigest()
    with pytest.raises(ValueError, match="noncanonical publication"):
        check(packet)
