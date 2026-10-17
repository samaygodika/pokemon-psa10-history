#!/usr/bin/env python3
"""Checks for history/store.py (2026-10-17) with the dir backend: init seeds generation 1, a second
checkout pulls it byte for byte, an ingest pushes generation 2 and the first checkout picks it up,
a push on a stale pull exits 3 and pull refuses to drop local changes without --force, old
generations are pruned except the first of each month, a missing pointer falls back to the newest
complete generation, local mode is a no-op, and push without a pull is refused.

    python3 history/test_store.py

Plain asserts, no pytest."""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store  # noqa: E402


def run(env, *argv):
    """store.main under this env; (exit code, stdout+stderr)."""
    saved = dict(os.environ)
    os.environ.update(env)
    for k in ("STORE_MODE", "STORE_BACKEND", "STORE_DIR", "STORE_STATE_DIR", "STORE_KEEP", "GITHUB_RUN_ID"):
        if k not in env:
            os.environ.pop(k, None)
    out = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(out):
            code = store.main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return code, out.getvalue()


def checkout(root, name, files=None):
    """A history folder + its own state dir, with the given store files."""
    hist, state = root / name / "history", root / name / "snapshots" / "store"
    for sub in store.STORE_DIRS:
        (hist / sub).mkdir(parents=True)
    for rel, text in (files or {}).items():
        (hist / rel).write_text(text)
    return {"STORE_BACKEND": f"dir:{root / 'release'}", "STORE_DIR": str(hist), "STORE_STATE_DIR": str(state), "STORE_KEEP": "2"}


def files_of(env):
    hist = Path(env["STORE_DIR"])
    return {a: p.read_text() for a, p in store.store_files(hist)}


SEED = {"sales/2026-09.csv": "asset_id,date,price\nA,2026-09-01,10\n",
        "sales/2026-10.csv": "asset_id,date,price\nA,2026-10-01,12\n",
        "daily/2026-10-15.csv": "asset_id,pop_at_grade\nA,5\n"}


def test_init_pull_push_cycle():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        a = checkout(root, "a", SEED)
        b = checkout(root, "b")

        code, out = run(b, "pull")                       # nothing anywhere yet
        assert code == 2 and "no store on the release" in out, out

        code, out = run(a, "init")
        assert code == 0 and "generation 1 is current" in out, out
        ptr = json.loads((root / "release" / store.POINTER).read_text())
        assert ptr["generation"] == 1 and ptr["files"] == {"sales": 2, "daily": 1}, ptr
        assert (root / "release" / ptr["asset"]).exists()
        code, out = run(a, "init")
        assert code == 2 and "already holds generation 1" in out, out

        code, out = run(b, "pull")
        assert code == 0 and "generation 1 extracted" in out, out
        assert files_of(b) == SEED
        code, out = run(b, "pull")
        assert code == 0 and "up to date at generation 1" in out, out
        code, out = run(b, "push")
        assert code == 0 and "unchanged since generation 1" in out, out

        # b ingests (a new month file, an edited one), pushes generation 2; a picks it up
        hb = Path(b["STORE_DIR"])
        (hb / "sales" / "2026-10.csv").write_text("asset_id,date,price\nA,2026-10-01,12\nB,2026-10-02,99\n")
        (hb / "sales" / "2026-11.csv").write_text("asset_id,date,price\nA,2026-11-01,13\n")
        (hb / "daily" / "2026-10-16.csv").write_text("asset_id,pop_at_grade\nA,6\n")
        code, out = run(b, "push", "--note", "nightly 2026-10-16")
        assert code == 0 and "generation 2 is current (3 sales + 2 daily files" in out, out
        ptr = json.loads((root / "release" / store.POINTER).read_text())
        assert ptr["generation"] == 2 and ptr["note"] == "nightly 2026-10-16" and ptr["previous"].endswith("-g1.tar.gz"), ptr
        code, out = run(a, "pull")
        assert code == 0 and "generation 2 extracted" in out, out
        assert files_of(a) == files_of(b)
        assert json.loads((Path(a["STORE_STATE_DIR"]) / "state.json").read_text())["generation"] == 2

        # a month file that vanished upstream vanishes on pull too (the store is replaced wholesale)
        (hb / "sales" / "2026-11.csv").unlink()
        code, out = run(b, "push")
        assert code == 0 and "generation 3" in out, out
        code, out = run(a, "pull")
        assert code == 0 and "sales/2026-11.csv" not in files_of(a)


def test_moved_and_local_changes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        a = checkout(root, "a", SEED)
        b = checkout(root, "b")
        assert run(a, "init")[0] == 0
        assert run(b, "pull")[0] == 0
        # both at generation 1; b pushes 2; a's push on top of 1 must not win
        (Path(b["STORE_DIR"]) / "daily" / "2026-10-16.csv").write_text("asset_id,pop_at_grade\nA,6\n")
        assert run(b, "push")[0] == 0
        (Path(a["STORE_DIR"]) / "daily" / "2026-10-16.csv").write_text("asset_id,pop_at_grade\nA,7\n")
        code, out = run(a, "push")
        assert code == 3 and "release is at generation 2, this checkout pulled 1" in out, out
        assets = sorted(p.name for p in (root / "release").iterdir())
        assert sum(n.endswith("-g2.tar.gz") for n in assets) == 1 and not any("-g3" in n for n in assets), assets
        # pull refuses to drop a's unpushed ingest, --force takes generation 2, then the re-ingest pushes 3
        code, out = run(a, "pull")
        assert code == 2 and "changed since generation 1 was pulled" in out, out
        code, out = run(a, "pull", "--force")
        assert code == 0 and "generation 2 extracted" in out and "[forced]" in out, out
        assert files_of(a)["daily/2026-10-16.csv"] == "asset_id,pop_at_grade\nA,6\n"
        (Path(a["STORE_DIR"]) / "daily" / "2026-10-16.csv").write_text("asset_id,pop_at_grade\nA,7\n")
        code, out = run(a, "push")
        assert code == 0 and "generation 3 is current" in out, out


def test_prune_keeps_recent_and_first_of_month():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        a = checkout(root, "a", SEED)          # STORE_KEEP=2
        assert run(a, "init")[0] == 0
        hist = Path(a["STORE_DIR"])
        for i in range(2, 6):
            (hist / "daily" / "2026-10-16.csv").write_text(f"asset_id,pop_at_grade\nA,{i}\n")
            code, out = run(a, "push")
            assert code == 0 and f"generation {i} is current" in out, out
        gens = sorted(store.parse_asset(p.name)[0] for p in (root / "release").glob("store-*.tar.gz"))
        assert gens == [1, 4, 5], gens          # newest 2 + the first of this month


def test_missing_pointer_falls_back_to_newest_generation():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        a = checkout(root, "a", SEED)
        b = checkout(root, "b")
        assert run(a, "init")[0] == 0
        (Path(a["STORE_DIR"]) / "daily" / "2026-10-16.csv").write_text("asset_id,pop_at_grade\nA,6\n")
        assert run(a, "push")[0] == 0
        (root / "release" / store.POINTER).unlink()
        code, out = run(b, "pull")
        assert code == 0 and "store.json is missing" in out and "generation 2 extracted" in out, out
        assert files_of(b) == files_of(a)
        # the next push rebuilds the pointer as generation 3 on top of 2
        (Path(b["STORE_DIR"]) / "daily" / "2026-10-16.csv").write_text("asset_id,pop_at_grade\nA,8\n")
        code, out = run(b, "push")
        assert code == 0 and "generation 3 is current" in out, out
        assert json.loads((root / "release" / store.POINTER).read_text())["generation"] == 3


def test_local_mode_and_preconditions():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        a = checkout(root, "a", SEED)
        code, out = run(dict(a, STORE_MODE="local"), "pull")
        assert code == 0 and "STORE_MODE=local" in out, out
        code, out = run(dict(a, STORE_MODE="local"), "push")
        assert code == 0 and "nothing pushed" in out, out
        code, out = run(a, "push")                       # never pulled here
        assert code == 2 and "were not pulled" in out, out
        code, out = run(a, "pull")                       # no release yet: the checkout's files stand, state = generation 0
        assert code == 0 and "no generation yet" in out, out
        code, out = run(a, "push", "--note", "first")
        assert code == 0 and "generation 1 is current" in out, out
        code, out = run(a, "status")
        assert code == 0 and "generation 1" in out and "the same as the release" in out, out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
