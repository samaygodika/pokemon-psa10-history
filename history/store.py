#!/usr/bin/env python3
"""The history store outside git (2026-10-17): history/sales/ and history/daily/ (1.2 GB, rewritten by
every nightly) live as one gzip tarball per generation on the GitHub release tagged `store` of this
repo, with a 300-byte pointer asset `store.json` naming the current generation. Everything else in
history/ stays in git. Why, the alternatives and the staging plan: handoff/2026-10-17-store-move-design.md.

    python3 history/store.py pull            # release -> history/sales + history/daily (a no-op when already current)
    python3 history/store.py pull --force    # ... even over local changes (after a git reset, or to drop an unpushed ingest)
    python3 history/store.py push --note "nightly 2026-10-17"
                                             # history/sales + daily -> the next generation. Exit 3 if the release moved
                                             # since the pull: pull --force, re-ingest the run, push again
    python3 history/store.py stage           # git add latest/ and the history files that stay in git (never sales/, daily/)
    python3 history/store.py init            # generation 1 from this checkout's files; refuses if the release has one
    python3 history/store.py status          # the pointer, the local state, the assets held

A generation is `store-<UTC stamp>-g<N>.tar.gz` holding sales/*.csv and daily/*.csv. push uploads the
next one under its own name (never --clobber, which is delete-then-upload), checks that the pointer
still names the generation this checkout pulled (else exit 3 and the upload is deleted), then
clobbers the pointer. Kept on the release: the newest STORE_KEEP generations plus the first of each
month. A pointer that went missing is replaced by the newest complete generation on the release.

Env:  STORE_MODE     release (default): talk to the release | local: pull and push do nothing (offline /
                     scratch runs; `stage` then only works while sales/ and daily/ are still git-tracked)
      STORE_BACKEND  gh (default): the release through the gh CLI (GH_TOKEN on Actions, the user's own
                     login on a laptop) | dir:<path>: a folder laid out like the release (tests, mirrors)
      STORE_REPO     OWNER/REPO (default $GITHUB_REPOSITORY, else samaygodika/pokemon-psa10-history)
      STORE_TAG      the release's tag (store)
      STORE_KEEP     generations kept besides the first of each month (3)
      STORE_DIR      the history folder (this file's); STORE_STATE_DIR: snapshots/store (state.json, the
                     cached tarball)

Exit codes: 0 ok; 1 error; 2 precondition (not pulled, local changes, no store anywhere, local mode
cannot publish); 3 the release moved since the pull."""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STORE_DIRS = ("sales", "daily")           # what the tarball holds, relative to history/
POINTER = "store.json"
ASSET_RX = re.compile(r"^store-(\d{4}-\d{2}-\d{2}T\d{4}Z)-g(\d+)\.tar\.gz$")
DEFAULT_REPO = "samaygodika/pokemon-psa10-history"
GZIP_LEVEL = 1                            # 1.2 GB -> 427 MB in 9 s; level 6 saves 12 % and takes twice as long


class StoreError(Exception):
    code = 1


class Precondition(StoreError):
    code = 2


class Moved(StoreError):
    code = 3


def log(msg):
    print(f"store: {msg}", flush=True)


# --- configuration ------------------------------------------------------------------------------

class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.mode = env.get("STORE_MODE", "release")
        if self.mode not in ("release", "local"):
            raise Precondition(f"STORE_MODE must be release or local, not {self.mode!r}")
        self.backend_spec = env.get("STORE_BACKEND", "gh")
        self.repo = env.get("STORE_REPO") or env.get("GITHUB_REPOSITORY") or DEFAULT_REPO
        self.tag = env.get("STORE_TAG", "store")
        self.keep = int(env.get("STORE_KEEP", "3"))
        self.history = Path(env.get("STORE_DIR") or HERE)
        self.state_dir = Path(env.get("STORE_STATE_DIR") or (ROOT / "snapshots" / "store"))
        self.run_id = env.get("GITHUB_RUN_ID")

    def backend(self):
        if self.backend_spec == "gh":
            return GhBackend(self.repo, self.tag)
        if self.backend_spec.startswith("dir:"):
            return DirBackend(Path(self.backend_spec[4:]))
        raise Precondition(f"STORE_BACKEND must be gh or dir:<path>, not {self.backend_spec!r}")

    def state_path(self):
        return self.state_dir / "state.json"

    def read_state(self):
        p = self.state_path()
        return json.loads(p.read_text()) if p.exists() else None

    def write_state(self, generation, asset, sha256, content):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {"generation": generation, "asset": asset, "sha256": sha256, "content": content,
                 "at": now_iso(), "history": str(self.history)}
        tmp = self.state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1) + "\n")
        os.replace(tmp, self.state_path())
        return state


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stamp_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


# --- backends: the release, or a folder that looks like one --------------------------------------

class GhBackend:
    """The GitHub release, through the gh CLI (preinstalled on Actions runners; GH_TOKEN or the
    user's login). Assets are listed with their upload state, so a crashed upload is never taken
    for a generation."""

    def __init__(self, repo, tag):
        if not shutil.which("gh"):
            raise StoreError("the gh CLI is not installed (or not on PATH): install it, or STORE_MODE=local")
        self.repo, self.tag = repo, tag

    def _gh(self, *args, check=True):
        p = subprocess.run(["gh", *args, "-R", self.repo], capture_output=True, text=True)
        if check and p.returncode != 0:
            raise StoreError(f"gh {' '.join(args[:3])} failed: {p.stderr.strip() or p.stdout.strip()}")
        return p

    def release_exists(self):
        p = self._gh("release", "view", self.tag, "--json", "tagName", check=False)
        if p.returncode == 0:
            return True
        if "not found" in (p.stderr + p.stdout).lower():
            return False
        raise StoreError(f"gh release view failed: {p.stderr.strip()}")

    def ensure_release(self):
        if self.release_exists():
            return
        log(f"creating release {self.tag!r} on {self.repo}")
        self._gh("release", "create", self.tag, "--title", "history store (sales + daily)", "--latest=false",
                 "--notes", "The feed's history store, one gzip tarball of history/sales/*.csv and "
                 "history/daily/*.csv per generation; store.json names the current one. "
                 "Written by history/store.py; see handoff/2026-10-17-store-move-design.md. "
                 "The tag itself means nothing.")

    def assets(self):
        if not self.release_exists():
            return []
        p = self._gh("release", "view", self.tag, "--json", "assets")
        out = []
        for a in json.loads(p.stdout).get("assets", []):
            out.append({"name": a["name"], "size": a.get("size"), "state": a.get("state", "uploaded")})
        return out

    def read_pointer(self):
        if not any(a["name"] == POINTER for a in self.assets()):
            return None
        p = self._gh("release", "download", self.tag, "-p", POINTER, "-O", "-")
        return json.loads(p.stdout)

    def download(self, name, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._gh("release", "download", self.tag, "-p", name, "-O", str(dest), "--clobber")

    def upload(self, path, clobber=False):
        args = ["release", "upload", self.tag, str(path)]
        if clobber:
            args.append("--clobber")
        self._gh(*args)

    def delete(self, name):
        self._gh("release", "delete-asset", self.tag, name, "-y")


class DirBackend:
    """A folder with the same files as the release: store.json and the generations. For the tests,
    and for an offline mirror (STORE_BACKEND=dir:/path)."""

    def __init__(self, path):
        self.dir = Path(path)

    def release_exists(self):
        return self.dir.is_dir()

    def ensure_release(self):
        self.dir.mkdir(parents=True, exist_ok=True)

    def assets(self):
        if not self.dir.is_dir():
            return []
        return [{"name": p.name, "size": p.stat().st_size, "state": "uploaded"}
                for p in sorted(self.dir.iterdir()) if p.is_file() and not p.name.startswith(".")]

    def read_pointer(self):
        p = self.dir / POINTER
        return json.loads(p.read_text()) if p.exists() else None

    def download(self, name, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.dir / name, dest)

    def upload(self, path, clobber=False):
        path = Path(path)
        target = self.dir / path.name
        if target.exists() and not clobber:
            raise StoreError(f"asset {path.name} already exists")
        tmp = self.dir / (".upload-" + path.name)
        shutil.copyfile(path, tmp)
        os.replace(tmp, target)

    def delete(self, name):
        (self.dir / name).unlink()


# --- the files -----------------------------------------------------------------------------------

def store_files(history):
    """[(arcname, path)] of every CSV in the store, in a fixed order."""
    out = []
    for sub in STORE_DIRS:
        out.extend((f"{sub}/{p.name}", p) for p in sorted((history / sub).glob("*.csv")))
    return out


def has_store(history):
    return bool(store_files(history))


def content_hash(history):
    """sha256 over the store's file names and contents, in order: equal hashes = the same store.
    ~1 s per GB. Independent of tar metadata, so a generation can be recognised after extraction."""
    h = hashlib.sha256()
    for arcname, p in store_files(history):
        h.update(arcname.encode() + b"\0")
        fh = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                fh.update(chunk)
        h.update(fh.digest())
    return h.hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_tarball(history, dest):
    files = store_files(history)
    with tarfile.open(dest, "w:gz", compresslevel=GZIP_LEVEL) as tar:
        for arcname, p in files:
            tar.add(p, arcname=arcname, recursive=False)
    counts = {sub: sum(1 for a, _ in files if a.startswith(sub + "/")) for sub in STORE_DIRS}
    return counts


def extract_tarball(tarball, history):
    """Replace history/sales and history/daily with the tarball's: extracted next to them first,
    then swapped in, so a crash never leaves half a store."""
    incoming = history / ".store-incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True)
    kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    with tarfile.open(tarball) as tar:
        members = []
        for m in tar.getmembers():
            parts = m.name.split("/")
            if m.isfile() and len(parts) == 2 and parts[0] in STORE_DIRS and parts[1].endswith(".csv"):
                members.append(m)
        tar.extractall(incoming, members=members, **kw)
    for sub in STORE_DIRS:
        (incoming / sub).mkdir(exist_ok=True)
        old = history / f".store-old-{sub}"
        shutil.rmtree(old, ignore_errors=True)
        if (history / sub).exists():
            os.rename(history / sub, old)
        os.rename(incoming / sub, history / sub)
        shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(incoming, ignore_errors=True)


def parse_asset(name):
    """(generation, stamp) of a generation asset's name, or None."""
    m = ASSET_RX.match(name)
    return (int(m.group(2)), m.group(1)) if m else None


def generation_assets(backend, ignore=None):
    """[(generation, stamp, name)] of the complete generation tarballs on the release, newest first."""
    out = []
    for a in backend.assets():
        g = parse_asset(a["name"])
        if g and a.get("state", "uploaded") == "uploaded" and a["name"] != ignore:
            out.append((g[0], g[1], a["name"]))
    return sorted(out, reverse=True)


def current_pointer(backend, ignore=None):
    """The release's pointer, or one rebuilt from the newest complete generation when the pointer
    asset is missing (a clobber that died), or None when the release holds no generation.
    `ignore` = an asset name not to fall back to (push's own upload, not yet pointed at)."""
    ptr = backend.read_pointer()
    if ptr is not None:
        return ptr
    gens = generation_assets(backend, ignore=ignore)
    if not gens:
        return None
    gen, stamp, name = gens[0]
    log(f"WARNING: {POINTER} is missing on the release; taking the newest complete generation, {name}")
    return {"generation": gen, "asset": name, "sha256": None, "content": None, "built_at": stamp, "fallback": True}


# --- commands -------------------------------------------------------------------------------------

def pull(cfg, force=False):
    if cfg.mode == "local":
        log("STORE_MODE=local: history/sales + daily used as they are")
        return 0
    backend = cfg.backend()
    ptr = current_pointer(backend)
    state = cfg.read_state()
    local = content_hash(cfg.history) if has_store(cfg.history) else None
    if ptr is None:
        if local is None:
            raise Precondition("no store on the release and none in history/: run `history/store.py init` "
                               "from a checkout that holds history/sales and history/daily")
        log("the release has no generation yet: using the checkout's history/sales + daily as they are "
            "(run `history/store.py init` to seed the release)")
        cfg.write_state(0, None, None, local)
        return 0
    gen, asset = ptr["generation"], ptr["asset"]
    if local is not None and ptr.get("content") and local == ptr["content"]:
        cfg.write_state(gen, asset, ptr.get("sha256"), local)
        log(f"up to date at generation {gen} ({asset})")
        return 0
    if state and local is not None and local != state.get("content") and not force:
        raise Precondition(f"history/sales + daily changed since generation {state['generation']} was pulled "
                           "(an ingest not pushed?): push it, or `pull --force` to discard it")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tarball = cfg.state_dir / asset
    if tarball.exists() and ptr.get("sha256") and file_sha256(tarball) == ptr["sha256"]:
        log(f"generation {gen}: using the cached {asset}")
    else:
        log(f"generation {gen}: downloading {asset}")
        backend.download(asset, tarball)
        if ptr.get("sha256"):
            got = file_sha256(tarball)
            if got != ptr["sha256"]:
                tarball.unlink()
                raise StoreError(f"{asset} downloaded with sha256 {got[:12]}, pointer says {ptr['sha256'][:12]}")
    extract_tarball(tarball, cfg.history)
    content = content_hash(cfg.history)
    if ptr.get("content") and content != ptr["content"]:
        raise StoreError(f"the extracted store does not hash as the pointer says ({content[:12]} vs {ptr['content'][:12]})")
    cfg.write_state(gen, asset, ptr.get("sha256"), content)
    for p in cfg.state_dir.glob("store-*.tar.gz"):
        if p.name != asset:
            p.unlink()
    n = len(store_files(cfg.history))
    log(f"generation {gen} extracted into {cfg.history} ({n} files)" + (" [forced]" if force else ""))
    return 0


def push(cfg, note=""):
    if cfg.mode == "local":
        log("STORE_MODE=local: nothing pushed")
        return 0
    state = cfg.read_state()
    if state is None:
        raise Precondition(f"history/sales + daily were not pulled in this checkout ({cfg.state_path()} is missing): "
                           "run `history/store.py pull` before ingesting")
    if not has_store(cfg.history):
        raise Precondition(f"nothing to push: no CSV files under {cfg.history}/sales or /daily")
    local = content_hash(cfg.history)
    backend = cfg.backend()
    ptr = current_pointer(backend)
    remote_gen = ptr["generation"] if ptr else 0
    if ptr and ptr.get("content") == local:
        cfg.write_state(remote_gen, ptr["asset"], ptr.get("sha256"), local)
        log(f"unchanged since generation {remote_gen}: nothing to push")
        return 0
    if state["generation"] >= 1 and local == state.get("content"):
        # This checkout never changed the store since it pulled: nothing of ours to push, whatever the
        # release has done meanwhile (2026-10-21: the eBay verify, which runs beside the nightly and the
        # refreshes since it got its own concurrency group and never touches the store, died here with
        # exit 3 whenever a generation landed during its 15 minutes, and its day's answers stayed in
        # the artifact). A changed local store on a moved release is still refused below.
        log(f"unchanged since the pull of generation {state['generation']} (the release is at {remote_gen}): nothing to push")
        return 0
    if remote_gen != state["generation"]:
        raise Moved(f"the release is at generation {remote_gen}, this checkout pulled {state['generation']}: "
                    "`pull --force`, re-ingest the run, push again")
    gen = remote_gen + 1
    name = f"store-{stamp_now()}-g{gen}.tar.gz"
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    tarball = cfg.state_dir / name
    log(f"generation {gen}: packing {name}")
    counts = make_tarball(cfg.history, tarball)
    sha, size = file_sha256(tarball), tarball.stat().st_size
    backend.ensure_release()
    log(f"generation {gen}: uploading {size / 1e6:.0f} MB")
    backend.upload(tarball)
    again = current_pointer(backend, ignore=name)
    if (again["generation"] if again else 0) != remote_gen:
        backend.delete(name)
        tarball.unlink()
        raise Moved(f"the release moved to generation {again['generation']} during the upload: "
                    "`pull --force`, re-ingest the run, push again")
    pointer = {"generation": gen, "asset": name, "sha256": sha, "bytes": size, "content": local,
               "built_at": now_iso(), "note": note or "", "built_by": cfg.run_id or socket.gethostname(),
               "previous": ptr["asset"] if ptr else None, "files": counts}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / POINTER
        p.write_text(json.dumps(pointer, indent=1) + "\n")
        backend.upload(p, clobber=True)
    cfg.write_state(gen, name, sha, local)
    for p in cfg.state_dir.glob("store-*.tar.gz"):
        if p.name != name:
            p.unlink()
    log(f"generation {gen} is current ({counts['sales']} sales + {counts['daily']} daily files, {size / 1e6:.0f} MB)")
    prune(cfg, backend, current=name)
    return 0


def prune(cfg, backend, current):
    """Delete generations beyond the newest STORE_KEEP, except the first of each month."""
    gens = generation_assets(backend)
    keep = {name for _, _, name in gens[:cfg.keep]} | {current}
    first_of_month = {}
    for gen, stamp, name in gens:
        month = stamp[:7]
        if month not in first_of_month or gen < first_of_month[month][0]:
            first_of_month[month] = (gen, name)
    keep |= {name for _, name in first_of_month.values()}
    for gen, stamp, name in gens:
        if name in keep:
            continue
        try:
            backend.delete(name)
            log(f"pruned generation {gen} ({name})")
        except StoreError as e:
            log(f"WARNING: could not prune {name}: {e}")


def init(cfg, note=""):
    if cfg.mode == "local":
        raise Precondition("init needs STORE_MODE=release")
    backend = cfg.backend()
    ptr = current_pointer(backend)
    if ptr is not None:
        raise Precondition(f"the release already holds generation {ptr['generation']} ({ptr['asset']}): use pull / push")
    if not has_store(cfg.history):
        raise Precondition(f"no CSV files under {cfg.history}/sales or /daily to seed the release with")
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *[str(cfg.history / s) for s in STORE_DIRS]],
                           capture_output=True, text=True, cwd=ROOT)
    if dirty.returncode == 0 and dirty.stdout.strip():
        log(f"WARNING: {len(dirty.stdout.splitlines())} store file(s) differ from the git checkout; seeding with the files as they are")
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    cfg.write_state(0, None, None, content_hash(cfg.history))
    return push(cfg, note=note or f"init from {head or 'this checkout'} on {socket.gethostname()}")


def stage(cfg):
    """git add latest/ and the history files that stay in git. In local mode the store must still be
    git-tracked (before the migration commit), else latest/ would be published from a store nobody
    else can see."""
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", str(cfg.history / STORE_DIRS[0])],
                             capture_output=True, text=True, cwd=ROOT).returncode == 0
    if cfg.mode == "local":
        if not tracked:
            raise Precondition("STORE_MODE=local and history/sales is not in git: committing would publish latest/ "
                               "built from a store nobody else can see. Use STORE_MODE=release (or NO_COMMIT=1).")
        subprocess.run(["git", "add", "history", "latest"], check=True, cwd=ROOT)
        log("local mode: history/ (store included, still git-tracked) and latest/ staged")
        return 0
    if cfg.read_state() is None:
        raise Precondition("history/sales + daily were not pulled in this checkout: nothing may be committed on top of them")
    subprocess.run(["git", "add", "latest"], check=True, cwd=ROOT)
    # history/sales and history/daily are gitignored since the untrack commit, so a plain add skips
    # them; naming them as exclude pathspecs made git exit 1 ("paths are ignored", 2026-10-20 refresh
    # 39350660318). On a checkout that still tracks them, the reset keeps their changes out.
    subprocess.run(["git", "add", "--", "history"], check=True, cwd=ROOT)
    if tracked:
        subprocess.run(["git", "reset", "-q", "--", *[f"history/{sub}" for sub in STORE_DIRS]], check=True, cwd=ROOT)
    return 0


def status(cfg):
    print(f"mode {cfg.mode}, backend {cfg.backend_spec}, repo {cfg.repo}, tag {cfg.tag}, history {cfg.history}")
    state = cfg.read_state()
    local = content_hash(cfg.history) if has_store(cfg.history) else None
    n = len(store_files(cfg.history))
    print(f"local: {n} files, content {local[:12] if local else '-'}; "
          f"state: generation {state['generation']} ({state['asset']}) pulled {state['at']}, content {state['content'][:12]}"
          if state else f"local: {n} files, content {local[:12] if local else '-'}; state: never pulled here")
    if cfg.mode == "local":
        return 0
    backend = cfg.backend()
    ptr = current_pointer(backend)
    if ptr is None:
        print("release: no generation yet")
        return 0
    print(f"release: generation {ptr['generation']} = {ptr['asset']}, {ptr.get('bytes', 0) / 1e6:.0f} MB, "
          f"built {ptr.get('built_at')} by {ptr.get('built_by')}: {ptr.get('note')}")
    same = "the same as" if local and local == ptr.get("content") else "DIFFERENT from"
    print(f"local store is {same} the release's current generation")
    for gen, stamp, name in generation_assets(backend):
        size = next((a["size"] for a in backend.assets() if a["name"] == name), None)
        print(f"  g{gen:<4} {name}  {(size or 0) / 1e6:.0f} MB")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="release -> history/sales + history/daily")
    p.add_argument("--force", action="store_true", help="overwrite local changes made since the last pull")
    p = sub.add_parser("push", help="history/sales + history/daily -> the next generation on the release")
    p.add_argument("--note", default="", help="what this generation is (the run's commit message)")
    p = sub.add_parser("init", help="seed the release with this checkout's store as generation 1")
    p.add_argument("--note", default="")
    sub.add_parser("stage", help="git add latest/ and the history files that stay in git")
    sub.add_parser("status", help="pointer, local state and the generations held")
    args = ap.parse_args(argv)
    try:
        cfg = Config()
        if args.cmd == "pull":
            return pull(cfg, force=args.force)
        if args.cmd == "push":
            return push(cfg, note=args.note)
        if args.cmd == "init":
            return init(cfg, note=args.note)
        if args.cmd == "stage":
            return stage(cfg)
        return status(cfg)
    except StoreError as e:
        print(f"store: {e}", file=sys.stderr, flush=True)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
