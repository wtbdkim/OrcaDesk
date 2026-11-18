"""uistate.py: front-end state kept across launches."""

import json

import pytest

from orcamgr import uistate


def test_a_value_is_read_back_after_it_is_put(tmp_path):
    uistate.put("rail.opened", [{"path": "C:/a/b.out"}], root=tmp_path)
    assert uistate.get("rail.opened", root=tmp_path) == [{"path": "C:/a/b.out"}]


def test_keys_are_independent_and_none_removes_one(tmp_path):
    uistate.put("rail.opened", [1], root=tmp_path)
    uistate.put("build.draft", {"name": "x"}, root=tmp_path)
    uistate.put("rail.opened", None, root=tmp_path)
    assert uistate.get("rail.opened", root=tmp_path) is None
    assert uistate.get("build.draft", root=tmp_path) == {"name": "x"}


def test_a_missing_or_unreadable_file_reads_as_empty(tmp_path):
    assert uistate.get("rail.opened", root=tmp_path) is None
    uistate.state_file(tmp_path).write_text("{not json", encoding="utf-8")
    assert uistate.get("rail.opened", root=tmp_path) is None
    # and the next put starts the file over rather than failing on it
    uistate.put("rail.opened", [], root=tmp_path)
    assert json.loads(uistate.state_file(tmp_path).read_text(encoding="utf-8")) == {
        "rail.opened": []}


def test_a_write_leaves_no_temporary_file(tmp_path):
    uistate.put("k", 1, root=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [uistate.FILE_NAME]


@pytest.mark.parametrize("key", ["", "x" * (uistate.MAX_KEY + 1)])
def test_a_bad_key_is_refused(tmp_path, key):
    with pytest.raises(ValueError):
        uistate.put(key, 1, root=tmp_path)
    with pytest.raises(ValueError):
        uistate.get(key, root=tmp_path)


def test_an_oversized_value_is_refused_and_nothing_is_written(tmp_path):
    with pytest.raises(ValueError):
        uistate.put("k", "x" * (uistate.MAX_VALUE_BYTES + 1), root=tmp_path)
    assert not uistate.state_file(tmp_path).exists()


def test_take_returns_the_value_and_removes_it(tmp_path):
    uistate.put("qed.handoff", {"t": 1}, root=tmp_path)
    uistate.put("rail.opened", [1], root=tmp_path)
    assert uistate.take("qed.handoff", root=tmp_path) == {"t": 1}
    assert uistate.get("qed.handoff", root=tmp_path) is None
    assert uistate.take("qed.handoff", root=tmp_path) is None
    assert uistate.get("rail.opened", root=tmp_path) == [1]


def test_concurrent_takes_hand_the_value_to_exactly_one_taker(tmp_path):
    import threading
    for _ in range(20):
        uistate.put("qed.handoff", {"t": 1}, root=tmp_path)
        got, start = [], threading.Barrier(4)

        def taker():
            start.wait()
            got.append(uistate.take("qed.handoff", root=tmp_path))

        ts = [threading.Thread(target=taker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert [v for v in got if v is not None] == [{"t": 1}]
