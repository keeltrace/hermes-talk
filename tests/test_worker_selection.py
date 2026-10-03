"""Worker selection is driven by host-advertised providers, not a hard-coded enum.

The model may only pick a worker the host advertises, and the provider name that
reaches the request body is resolved from that same advertisement. A worker the
host cannot prove it has is refused; it is never silently swapped for another.
"""

from __future__ import annotations

import pytest

from talk_worker_recipients import (
    DEFAULT_WORKER_LABEL,
    known_worker_labels,
    worker_available,
    worker_labels,
    worker_provider_name,
)


def capabilities(*names, version=1):
    return {
        "features": {
            "linked_child_dispatch": {
                "external_workers": {"version": version, "names": list(names)}
            }
        }
    }


def test_no_advertisement_offers_only_the_builtin_worker():
    assert worker_labels({}) == (DEFAULT_WORKER_LABEL,)
    assert worker_labels(capabilities()) == (DEFAULT_WORKER_LABEL,)


def test_labels_come_from_the_host_advertisement():
    both = worker_labels(capabilities("hermes-talk-codex", "hermes-talk-nerve-ssh"))
    assert set(both) == {DEFAULT_WORKER_LABEL, "codex", "nerve"}
    assert worker_labels(capabilities("hermes-talk-nerve-ssh")) == (
        DEFAULT_WORKER_LABEL,
        "nerve",
    )


def test_a_worker_the_host_does_not_advertise_is_refused():
    caps = capabilities("hermes-talk-codex")
    assert worker_available(caps, "codex") is True
    assert worker_available(caps, "nerve") is False
    with pytest.raises(LookupError):
        worker_provider_name(caps, "nerve")


def test_provider_name_resolves_from_advertisement_not_the_model():
    caps = capabilities("hermes-talk-nerve-ssh")
    assert worker_provider_name(caps, "nerve") == "hermes-talk-nerve-ssh"
    assert worker_provider_name(caps, DEFAULT_WORKER_LABEL) is None


def test_unknown_worker_is_not_in_the_known_set():
    assert "banana" not in known_worker_labels()
    with pytest.raises(LookupError):
        worker_provider_name(capabilities("hermes-talk-codex"), "banana")


def test_malformed_capability_block_fails_closed():
    for bad in (
        {"version": 2, "names": ["hermes-talk-codex"]},
        {"version": 1, "names": "hermes-talk-codex"},
        {"names": ["hermes-talk-codex"]},
        "not-a-dict",
    ):
        block = {"features": {"linked_child_dispatch": {"external_workers": bad}}}
        assert worker_labels(block) == (DEFAULT_WORKER_LABEL,)


def test_unknown_host_provider_names_are_ignored():
    """A provider this build does not understand is not offered as a label."""
    caps = capabilities("some-other-plugin-worker", "hermes-talk-codex")
    assert worker_labels(caps) == (DEFAULT_WORKER_LABEL, "codex")


def test_non_string_advertised_names_are_ignored():
    caps = capabilities("hermes-talk-codex", None, 7, {"a": 1})
    assert worker_labels(caps) == (DEFAULT_WORKER_LABEL, "codex")