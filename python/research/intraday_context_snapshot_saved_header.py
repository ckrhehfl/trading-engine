"""Byte-compatible EI roster header for bounded audits of saved snapshots."""

from hashlib import sha256


def stream_roster_header(snapshot_module, groups: dict) -> dict:
    """Hash the original canonical roster array without buffering the array."""
    digest = sha256(b"[")
    memberships = 0
    for rows in groups.values():
        for row in rows:
            if memberships:
                digest.update(b",")
            encoded = snapshot_module.wire.encode(snapshot_module._target(row))
            digest.update(encoded[:-1])  # The wire encoder appends one LF to each value.
            memberships += 1
    digest.update(b"]\n")
    return {"schema": snapshot_module.FEATURE_SCHEMA, "projection": snapshot_module.FEATURE_PROJECTION,
            "questions": snapshot_module.SCHEME_ID, "roster_sha256": digest.hexdigest(),
            "coordinates": len(groups), "memberships": memberships}
