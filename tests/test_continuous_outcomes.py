"""Chunked reconciliation (#3): outcomes stream in with the runs instead of only at the end."""
from engine.runner import chunk_sizes


def test_off_by_default_is_one_chunk():
    # every<=0 keeps the old behaviour exactly: run everything, reconcile once at the end.
    assert chunk_sizes(40, 0) == [40]
    assert chunk_sizes(40, -1) == [40]


def test_splits_evenly():
    assert chunk_sizes(40, 10) == [10, 10, 10, 10]
    assert chunk_sizes(8, 4) == [4, 4]


def test_remainder_becomes_its_own_chunk_and_is_never_dropped():
    # The tail must still reconcile; dropping it would leave the last runs permanently pending.
    assert chunk_sizes(10, 4) == [4, 4, 2]
    assert sum(chunk_sizes(10, 4)) == 10


def test_every_larger_than_count_is_one_chunk():
    assert chunk_sizes(5, 50) == [5]


def test_every_equal_to_count_is_one_chunk():
    assert chunk_sizes(8, 8) == [8]


def test_every_run_reconciles_when_every_is_one():
    assert chunk_sizes(3, 1) == [1, 1, 1]


def test_empty_batch():
    assert chunk_sizes(0, 5) == []
    assert chunk_sizes(-3, 5) == []


def test_total_always_preserved():
    # Whatever the split, the sim must run exactly the requested number of items.
    for count in range(0, 25):
        for every in range(0, 8):
            assert sum(chunk_sizes(count, every)) == max(0, count)
