"""Bundle release uses completed task inputs, recoverable locators and command leases."""

import hashlib
from dataclasses import replace

from nnnotes.core import downloads
from nnnotes.pipeline.contract import Input, Task
from nnnotes.pipeline.depot import Depot
from nnnotes.pipeline.downloads import BundleDownloads


def bundle(depot, data=b"bundle", name="one.bundle", recover=True):
    path = depot.downloads / "bundles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    locators = [{"kind": "download", "path": "bundles/" + name}]
    if recover:
        locators.append({"kind": "catalog", "catalog": "example", "location": name})
    inp = Input("bundle", hashlib.sha256(data).hexdigest(), len(data), name, tuple(locators))
    return path, Task("unity.export", 1, name, inputs=(inp,))


def test_other_leases_protect_files_until_a_later_batch(tmp_path):
    depot = Depot(tmp_path)
    path, task = bundle(depot)
    with downloads.lease(tmp_path, None) as own:
        release = BundleDownloads(depot, own, 0)
        with downloads.lease(tmp_path, None):
            release([task])
            assert path.exists() and release.removed == 0
        release([task])
        assert not path.exists() and release.removed == 1 and release.bytes_removed == 6


def test_only_matching_recoverable_bundle_inputs_are_released(tmp_path):
    depot = Depot(tmp_path)
    changed, task = bundle(depot)
    changed.write_bytes(b"another")
    private, private_task = bundle(depot, name="private.bundle", recover=False)
    other, other_task = bundle(depot, name="other.bundle")
    with downloads.lease(tmp_path, None) as own:
        release = BundleDownloads(depot, own, 0)
        release([task, private_task])
        release([replace(other_task, stage="unity.census")])
        assert changed.exists() and private.exists() and other.exists()
        release([other_task])
        assert not other.exists() and release.removed == 1


def test_missing_lease_and_unlimited_budget_keep_files(tmp_path):
    depot = Depot(tmp_path)
    path, task = bundle(depot)
    BundleDownloads(depot, None, 0)([task])
    BundleDownloads(depot, tmp_path / "absent", 0)([task])
    invalid = tmp_path / "outside.json"
    invalid.write_text("{}")
    BundleDownloads(depot, invalid, 0)([task])
    with downloads.lease(tmp_path, None) as own:
        BundleDownloads(depot, own, None)([task])
    assert path.exists()


def test_cas_recovery_requires_an_explicit_depot_locator(tmp_path):
    depot = Depot(tmp_path)
    path, task = bundle(depot, recover=False)
    inp = task.inputs[0]
    depot.put(path.read_bytes())
    with downloads.lease(tmp_path, None) as own:
        release = BundleDownloads(depot, own, 0)
        release([task])
        assert path.exists()
        task = replace(task, inputs=(replace(inp, locators=(*inp.locators, {"kind": "depot"})),))
        release([task])
        assert not path.exists() and depot.input_bytes(task.inputs[0]) == b"bundle"


def test_released_input_can_be_fetched_for_an_expanded_request(tmp_path):
    fetched = []

    def fetch(inp, locator):
        fetched.append(locator)
        path = tmp_path / "downloads/bundles/one.bundle"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bundle")
        return path

    depot = Depot(tmp_path, fetch=fetch)
    path, task = bundle(depot)
    with downloads.lease(tmp_path, None) as own:
        BundleDownloads(depot, own, 0)([task])
        assert not path.exists()
        assert depot.input_bytes(task.inputs[0]) == b"bundle" and len(fetched) == 1


def test_download_locators_cannot_release_files_outside_the_depot(tmp_path):
    depot = Depot(tmp_path / "depot")
    outside = tmp_path / "bundles/outside"
    outside.parent.mkdir()
    outside.write_bytes(b"bundle")
    _, task = bundle(depot)
    inp = replace(task.inputs[0], locators=({"kind": "download", "path": str(outside)}, {"kind": "catalog"}))
    with downloads.lease(depot.root, None) as own:
        BundleDownloads(depot, own, 0)([replace(task, inputs=(inp,))])
    assert outside.exists()
