"""What every convert stage shares: identity, provenance, logs, isolated workers.

A stage (tier0.py, convert.py) declares what it is -- its code, the data files
and packages it depends on, its settings, anything else that changes its
output -- and `run_stage` does the rest:

  run id      a hash of the stage's code (syntax trees, docstrings removed),
              its data files, the installed versions of its packages and
              everything they require, the Python version, its settings and
              its extra identity (model weights, an input run). Same inputs,
              same folder; change one and the next run lands beside it.
  run folder  scripts/convert/runs/<stage>-<sig>/: env.json says what the id stands for
              and holds the full `pip freeze`; code/ keeps every distinct
              version of the code and data files that wrote into the run.
  sessions    sessions/<stamp>.log, one readable line per document, and
              sessions/<stamp>.jsonl, the same as events, with session start
              and end: argv, host, user, git commit, uncommitted files.
  workers     one document at a time per worker process, so a crash or hang
              costs that document only. It is recorded as failed with a reason
              and a fresh worker takes over. A stage's workers can load models
              once (`init_worker`), and start one at a time, each once the last
              is ready and the stage's gate (e.g. free GPU memory) allows.
  resuming    a document already recorded in this run is skipped unless its PDF
              changed; --retry-failed redoes failures.
"""
from __future__ import annotations

import ast
import collections
import datetime
import getpass
import gzip
import hashlib
import importlib
import importlib.metadata
import json
import multiprocessing as mp
import multiprocessing.connection
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_SOURCE = REPO / "source"
DEFAULT_RUNS = HERE / "runs"
DEFAULT_OUTPUT = REPO / "output"
HASH_CHUNK = 1 << 20


# --------------------------------------------------------------------------
# small helpers

def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def r(x, n=2):
    return None if x is None else round(float(x), n)


def clip(text, n):
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= n else text[:n] + f"...[+{len(text) - n} chars]"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _atomic(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write_bytes_atomic(path: Path, data: bytes) -> None:
    _atomic(path, lambda f: f.write(data))


def write_json_atomic(path: Path, obj) -> None:
    _atomic(path, lambda f: f.write(json.dumps(obj, indent=2, ensure_ascii=False, default=str).encode()))


def write_json_gz_atomic(path: Path, obj) -> None:
    def w(f):
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(obj, ensure_ascii=False, default=str).encode())
    _atomic(path, w)


def write_jsonl_gz_atomic(path: Path, rows) -> None:
    def w(f):
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            for row in rows:
                gz.write(json.dumps(row, ensure_ascii=False, default=str).encode() + b"\n")
    _atomic(path, w)


def read_json_gz(path: Path):
    with gzip.open(path) as f:
        return json.load(f)


# --------------------------------------------------------------------------
# identity

def code_ast(path: Path) -> str:
    """A file's syntax tree without docstrings: what actually executes."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def installed_packages() -> dict[str, str]:
    return dict(sorted(((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                        if d.metadata["Name"]), key=lambda kv: kv[0].lower()))


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_closure(names: list[str]) -> dict[str, str]:
    """The installed versions of these packages and of everything they require."""
    installed = {_canon(d.metadata["Name"]): d for d in importlib.metadata.distributions() if d.metadata["Name"]}
    seen: dict[str, str] = {}
    stack = [_canon(n) for n in names]
    while stack:
        n = stack.pop()
        d = installed.get(n)
        if d is None or d.metadata["Name"] in seen:
            continue
        seen[d.metadata["Name"]] = d.version
        for req in d.requires or []:
            if "extra ==" in req:
                continue
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
            if m:
                stack.append(_canon(m.group(1)))
    return dict(sorted(seen.items(), key=lambda kv: kv[0].lower()))


def git_state() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True,
                                  text=True, timeout=10).stdout
        except Exception:
            return None
    return {"commit": (git("rev-parse", "HEAD") or "").strip() or None,
            "dirty_files_in_convert": [l[3:] for l in (git("status", "--porcelain", "--", str(HERE)) or "").splitlines() if l]}


def base_environment() -> dict:
    return {
        "python": sys.version,
        "python_implementation": platform.python_implementation(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "packages": installed_packages(),
    }


# --------------------------------------------------------------------------
# a stage

class Stage:
    """What a stage is. Subclasses fill in the class attributes and the hooks."""
    name = "stage"
    code_files: list[Path] = []          # their syntax trees enter the run id
    data_files: list[Path] = []          # their bytes enter the run id
    packages: list[str] = []             # these and their requirements enter the run id
    settings: dict = {}
    worker_module = ""                   # module with process(path, doc_id, meta, ctx) -> record
    min_start_gap_s = 0.0

    def extra_identity(self) -> dict:
        """Anything else that changes the output: model weights, an input run."""
        return {}

    def environment(self) -> dict:
        return base_environment()

    def doc_out(self, output: Path, doc_id: str, sig: str) -> Path:
        return output / doc_id / "convert" / f"{self.name}-{sig}" / f"{doc_id}.{self.name}.json.gz"

    def describe(self, ev: dict) -> str:
        return describe_basic(ev)

    def worker_meta(self) -> dict:
        """Anything else a worker needs to know, passed with every document."""
        return {}

    def gate(self) -> str | None:
        """None when another worker may start now; otherwise why not yet."""
        return None

    def header_lines(self, env: dict) -> list[str]:
        return []

    def progress_note(self, out: Path) -> str:
        """Where in the document a worker was, for a failure it could not report itself."""
        return ""

    def telemetry_fields(self) -> list[str]:
        return []

    def telemetry_values(self) -> list:
        return []

    # ---- identity
    def signature(self) -> tuple[str, dict]:
        parts = {
            "stage": self.name,
            "code_ast_sha256": {p.name: sha256_bytes(code_ast(p).encode()) for p in self.code_files},
            "data_sha256": {p.name: sha256_file(p) for p in self.data_files},
            "packages": package_closure(self.packages),
            "python": platform.python_version(),
            "settings": self.settings,
            "extra": self.extra_identity(),
        }
        blob = json.dumps(parts, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:8], parts

    def prepare_run(self, runs: Path) -> tuple[Path, str, dict, dict]:
        env = self.environment()
        sig, parts = self.signature()
        run_dir = runs / f"{self.name}-{sig}"
        (run_dir / "code").mkdir(parents=True, exist_ok=True)
        code = {}
        # One copy per distinct file: a comment edit keeps the run, and the copy
        # a record's code_sha256 names is still here to read.
        for p in self.code_files + self.data_files:
            b = p.read_bytes()
            sha = sha256_bytes(b)
            code[p.name] = sha
            copy = run_dir / "code" / f"{p.stem}.{sha[:12]}{p.suffix}"
            if not copy.exists():
                write_bytes_atomic(copy, b)
        if not (run_dir / "env.json").exists():
            write_json_atomic(run_dir / "env.json", {"stage": self.name, "signature": sig,
                                                     "created_at": utcnow(), "signature_parts": parts,
                                                     "environment": env})
        return run_dir, sig, env, code


def describe_basic(ev: dict) -> str:
    s = ev.get("summary") or {}
    bits = [f"{ev['status']:<7}", f"{ev['doc_id']:<12}", f"{s.get('pages', '-'):>5}p",
            f"{ev.get('seconds') or 0:7.2f}s"]
    if ev.get("reasons"):
        bits.append("why=" + ",".join(ev["reasons"]))
    if ev.get("error"):
        bits.append("error=" + clip(ev["error"], 200))
    return "  ".join(bits)


def event_from_record(rec: dict) -> dict:
    f = rec.get("file") or {}
    return {"event": "doc", "doc_id": rec["doc_id"], "status": rec["status"],
            "reasons": rec.get("reasons", []), "seconds": rec.get("seconds"),
            "size": f.get("size"), "mtime_ns": f.get("mtime_ns"), "sha256": f.get("sha256"),
            "summary": rec.get("summary"), "failed_pages": rec.get("failed_pages"),
            "error": (rec.get("error") or {}).get("error"), "at": rec.get("finished_at")}


def failure_record(path: Path, doc_id: str, meta: dict, reason: str, detail: str, started: float) -> dict:
    """Written when a worker died, hung or raised before writing its own record."""
    try:
        st = path.stat()
        file = {"path": str(path), "name": path.name, "size": st.st_size,
                "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(path)}
    except OSError as e:
        file = {"path": str(path), "error": repr(e)}
    return {"doc_id": doc_id, **meta, "file": file, "status": "failed", "reasons": [reason],
            "error": {"step": "worker", "error": detail}, "finished_at": utcnow(),
            "seconds": r(time.monotonic() - started, 3)}


# --------------------------------------------------------------------------
# workers

def worker_main(conn, module_name: str, meta: dict):
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # the parent decides when to stop
    mod = importlib.import_module(module_name)
    try:
        ctx = mod.init_worker(meta) if hasattr(mod, "init_worker") else None
    except Exception as e:
        conn.send({"ready": False, "error": repr(e), "trace": traceback.format_exc()})
        return
    conn.send({"ready": True, "pid": os.getpid()})
    while True:
        msg = conn.recv()
        if msg is None:
            return
        path, doc_id, out, attempt = msg
        started = time.monotonic()
        try:
            rec = mod.process(Path(path), doc_id, dict(meta, out=out, attempt=attempt), ctx)
        except Exception as e:   # a bug in the stage, not a property of the PDF: say so
            rec = failure_record(Path(path), doc_id, meta, "internal_error", repr(e), started)
            rec["error"]["trace"] = traceback.format_exc()
        write_json_gz_atomic(Path(out), rec)
        ev = event_from_record(rec)
        del rec
        release_memory()
        conn.send(ev)


class Worker:
    def __init__(self, ctx, module_name, meta, ready_timeout):
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=worker_main, args=(child, module_name, meta), daemon=True)
        self.proc.start()
        child.close()
        self.job = None       # (path, doc_id, out, started)
        self.done = 0
        self.ready = None
        if self.conn.poll(ready_timeout):
            try:
                self.ready = self.conn.recv()
            except (EOFError, OSError):
                self.ready = {"ready": False, "error": exit_detail(self.proc.exitcode)}
        else:
            self.ready = {"ready": False, "error": f"not ready after {ready_timeout}s"}

    def send(self, path, doc_id, out, attempt=1):
        self.job = (path, doc_id, out, time.monotonic())
        self.conn.send((str(path), doc_id, str(out), attempt))

    def stop(self, kill=False):
        if kill:
            self.proc.kill()
        else:
            try:
                self.conn.send(None)
            except (BrokenPipeError, OSError):
                pass
        self.proc.join(timeout=10)
        if self.proc.is_alive():
            self.proc.kill()
            self.proc.join()


def exit_detail(code) -> str:
    if code is not None and code < 0:
        try:
            return f"worker killed by {signal.Signals(-code).name}"
        except ValueError:
            pass
    return f"worker exited with code {code}"


# --------------------------------------------------------------------------
# memory: what the machine has left, what each worker holds

def meminfo_gb() -> dict:
    """MemAvailable and swap in use, from /proc/meminfo."""
    vals = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                vals[k] = int(v.split()[0]) / 1048576
    except OSError:
        return {}
    return {"available": r(vals.get("MemAvailable"), 2), "total": r(vals.get("MemTotal"), 2),
            "swap_used": r((vals.get("SwapTotal", 0) - vals.get("SwapFree", 0)), 2)}


def rss_gb(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1048576
    except OSError:
        return None
    return None


def release_memory() -> None:
    """Hand freed memory back to the system, not just to Python's allocator."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


# --------------------------------------------------------------------------
# sessions and the index

class Session:
    def __init__(self, run_dir: Path):
        self.stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        d = run_dir / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        self.log = open(d / f"{self.stamp}.log", "a", buffering=1)
        self.events = open(d / f"{self.stamp}.jsonl", "a", buffering=1)

    def say(self, line: str):
        line = f"{utcnow()}  {line}"
        print(line, flush=True)
        self.log.write(line + "\n")

    def event(self, ev: dict):
        self.events.write(json.dumps(ev, default=str) + "\n")

    def close(self):
        self.log.close()
        self.events.close()


def load_index(run_dir: Path) -> dict[str, dict]:
    """Latest event per document, from every session of this run."""
    idx = {}
    for f in sorted((run_dir / "sessions").glob("*.jsonl")):
        with open(f) as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue       # a line cut short by a kill
                if ev.get("event") == "doc":
                    idx[ev["doc_id"]] = ev
    return idx


def find_pdfs(inputs: list[str]) -> list[Path]:
    paths = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            paths += sorted(q for q in p.iterdir() if q.suffix.lower() == ".pdf" and q.is_file())
        elif p.is_file():
            paths.append(p)
        else:
            raise SystemExit(f"not found: {p}")
    return list(dict.fromkeys(p.resolve() for p in paths))


def runs_of(runs: Path, stage: str) -> list[Path]:
    return sorted((d for d in runs.glob(f"{stage}-*") if (d / "env.json").exists()),
                  key=lambda d: d.stat().st_mtime) if runs.exists() else []


# --------------------------------------------------------------------------
# running a stage

def add_run_arguments(p) -> None:
    p.add_argument("inputs", nargs="*", help="PDF files or directories (default: source/)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--timeout", type=float, default=600, help="seconds per document before it is failed")
    p.add_argument("--recycle-after", type=int, default=200, help="replace each worker after this many documents")
    p.add_argument("--retry-failed", action="store_true", help="redo documents whose last attempt failed")
    p.add_argument("--limit", type=int, help="do at most N documents this session")
    p.add_argument("--ready-timeout", type=float, default=600, help="seconds a worker may take to load")
    p.add_argument("--max-worker-ram-gb", type=float, default=8.0,
                   help="replace a worker after its document if it holds more than this")
    p.add_argument("--min-free-ram-gb", type=float, default=6.0,
                   help="start no worker while less than this is available")
    p.add_argument("--kill-worker-ram-gb", type=float, default=12.0,
                   help="stop a worker holding more than this mid-document; its document is retried")
    p.add_argument("--critical-free-ram-gb", type=float, default=3.0,
                   help="below this, stop the largest worker; its document is retried later")
    p.add_argument("--max-attempts", type=int, default=2,
                   help="attempts per document when it is stopped for memory, a crash or a timeout")


def run_stage(stage: Stage, args, filter_todo=None) -> int:
    runs, output = Path(args.runs), Path(args.output)
    run_dir, sig, env, code = stage.prepare_run(runs)
    pdfs = find_pdfs(args.inputs or [str(DEFAULT_SOURCE)])
    index = load_index(run_dir)

    todo, skipped = [], collections.Counter()
    for p in pdfs:
        ev = index.get(p.stem)
        out = stage.doc_out(output, p.stem, sig)
        if ev and out.exists():
            st = p.stat()
            unchanged = ev.get("size") == st.st_size and ev.get("mtime_ns") == st.st_mtime_ns
            if unchanged and not (args.retry_failed and ev["status"] == "failed"):
                skipped[ev["status"]] += 1
                continue
        todo.append(p)
    if filter_todo:
        todo, not_ready = filter_todo(todo)
        skipped.update(not_ready)
    if args.limit:
        todo = todo[:args.limit]

    sess = Session(run_dir)
    meta = {"stage": stage.name, "signature": sig, "session": sess.stamp, "code_sha256": code,
            "settings": stage.settings, "output": str(output), "run_dir": str(run_dir),
            **stage.worker_meta()}
    git = git_state()
    sess.event({"event": "session_start", "at": utcnow(), "stage": stage.name, "signature": sig,
                "argv": sys.argv, "cwd": os.getcwd(), "host": socket.gethostname(), "user": getpass.getuser(),
                "git": git, "code_sha256": code, "found": len(pdfs), "todo": len(todo),
                "skipped": dict(skipped), "workers": args.workers, "timeout_s": args.timeout})
    sess.say(f"{stage.name} run {sig}  python {platform.python_version()}")
    for line in stage.header_lines(env):
        sess.say(line)
    sess.say(f"run      {run_dir}")
    sess.say(f"records  {stage.doc_out(output, '<id>', sig)}")
    sess.say(f"git      {git['commit'] and git['commit'][:12]}"
             + (f"  uncommitted in scripts/convert/: {', '.join(git['dirty_files_in_convert'])}" if git["dirty_files_in_convert"] else ""))
    sess.say(f"found {len(pdfs)} PDFs; {len(todo)} to do; skipped: "
             + (", ".join(f"{k} {v}" for k, v in skipped.items()) or "none"))

    tele = open(run_dir / "sessions" / f"{sess.stamp}.telemetry.csv", "a", buffering=1)
    tele.write("at,available_gb,swap_used_gb,workers,worker_rss_gb,busy_docs," + ",".join(stage.telemetry_fields()) + "\n")
    last_tele = 0.0
    attempts = collections.Counter()

    ctx = mp.get_context("spawn")
    workers: list[Worker] = []
    queue = collections.deque(todo)
    counts, reasons_count = collections.Counter(), collections.Counter()
    pages_done = 0
    t0 = time.monotonic()
    interrupts = 0
    last_start = 0.0
    gate_said = None

    def on_sigint(signum, frame):
        nonlocal interrupts
        interrupts += 1
        if interrupts == 1:
            sess.say("interrupt: finishing documents in progress, starting no more (Ctrl-C again to stop them)")
        else:
            sess.say("interrupt: stopping workers now")
    signal.signal(signal.SIGINT, on_sigint)

    def record(ev):
        nonlocal pages_done
        sess.event(ev)
        counts[ev["status"]] += 1
        for rsn in ev.get("reasons") or []:
            reasons_count[rsn] += 1
        pages_done += (ev.get("summary") or {}).get("pages", 0)
        sess.say(stage.describe(ev))

    def dispatch(w: Worker):
        if queue and not interrupts:
            p = queue.popleft()
            attempts[p.stem] += 1
            w.send(p, p.stem, stage.doc_out(output, p.stem, sig), attempts[p.stem])
            sess.event({"event": "doc_started", "at": utcnow(), "doc_id": p.stem, "pid": w.proc.pid,
                        "attempt": attempts[p.stem], "memory": meminfo_gb()})

    def start_worker() -> bool:
        """Start one more worker if the stage allows it now."""
        nonlocal last_start, gate_said
        if time.monotonic() - last_start < stage.min_start_gap_s:
            return False
        mem = meminfo_gb()
        why = None
        if mem.get("available") is not None and mem["available"] < args.min_free_ram_gb:
            why = f"{mem['available']:.1f} GB RAM available, need {args.min_free_ram_gb}"
        why = why or stage.gate()
        if why:
            if why != gate_said:
                sess.say(f"not starting another worker yet: {why}")
                gate_said = why
            return False
        gate_said = None
        last_start = time.monotonic()
        w = Worker(ctx, stage.worker_module, meta, args.ready_timeout)
        if not (w.ready or {}).get("ready"):
            detail = (w.ready or {}).get("error")
            sess.say(f"worker failed to start: {detail}")
            sess.event({"event": "worker_failed_to_start", "at": utcnow(), "error": detail,
                        "trace": (w.ready or {}).get("trace")})
            w.stop(kill=True)
            raise SystemExit(f"{stage.name}: a worker could not start ({detail}); see the session log")
        sess.event({"event": "worker_started", "at": utcnow(), "pid": w.ready.get("pid")})
        workers.append(w)
        dispatch(w)
        return True

    def replace(w: Worker, reason: str, detail: str):
        path, doc_id, out, started = w.job
        w.stop(kill=True)
        where = stage.progress_note(Path(out))
        if where:
            detail = f"{detail}; {where}"
        if reason in ("memory_pressure", "worker_crashed", "timeout") and attempts[doc_id] < args.max_attempts \
                and not interrupts:
            # Try again at the end, when fewer large documents may be in flight.
            sess.say(f"{reason:<7}  {doc_id:<12}  {detail}; will retry (attempt {attempts[doc_id] + 1} of {args.max_attempts})")
            sess.event({"event": "doc_retry_later", "at": utcnow(), "doc_id": doc_id, "reason": reason,
                        "detail": detail, "attempt": attempts[doc_id]})
            queue.append(Path(path))
        else:
            rec = failure_record(Path(path), doc_id, meta, reason, detail, started)
            rec["attempts"] = attempts[doc_id]
            write_json_gz_atomic(Path(out), rec)
            record(event_from_record(rec))
        workers.remove(w)

    if queue:
        start_worker()
    last_progress = time.monotonic()
    while any(w.job for w in workers) or (queue and not interrupts):
        if interrupts >= 2:
            for w in list(workers):
                if w.job:
                    replace(w, "interrupted", "stopped by a second Ctrl-C")
            break
        if queue and not interrupts and len(workers) < min(args.workers, len(queue) + len(workers)):
            start_worker()
        for w in [w for w in workers if w.job]:
            held = rss_gb(w.proc.pid) or 0
            if held > args.kill_worker_ram_gb:
                detail = f"worker held {held:.1f} GB (limit {args.kill_worker_ram_gb})"
                sess.say(f"memory: {w.job[1]}: {detail}; stopping it")
                replace(w, "memory_pressure", detail)
        mem = meminfo_gb()
        if mem.get("available") is not None and mem["available"] < args.critical_free_ram_gb:
            busy_now = [w for w in workers if w.job]
            if busy_now:
                big = max(busy_now, key=lambda w: rss_gb(w.proc.pid) or 0)
                detail = (f"{mem['available']:.1f} GB RAM available (critical {args.critical_free_ram_gb}); "
                          f"this worker held {rss_gb(big.proc.pid) or 0:.1f} GB")
                sess.say(f"memory: {detail}; stopping it")
                replace(big, "memory_pressure", detail)
        if time.monotonic() - last_tele >= 30:
            last_tele = time.monotonic()
            rss = [rss_gb(w.proc.pid) or 0 for w in workers]
            tele.write(",".join(str(x) for x in [utcnow(), mem.get("available"), mem.get("swap_used"), len(workers),
                                                 "|".join(f"{x:.2f}" for x in rss),
                                                 "|".join(w.job[1] for w in workers if w.job),
                                                 *stage.telemetry_values()]) + "\n")
        busy = [w for w in workers if w.job]
        if not busy:
            time.sleep(1.0)
            continue
        ready = mp.connection.wait([w.conn for w in busy] + [w.proc.sentinel for w in busy], timeout=1.0)
        for w in busy:
            if w.conn in ready:
                try:
                    ev = w.conn.recv()
                except (EOFError, OSError):
                    w.proc.join(timeout=5)
                    replace(w, "worker_crashed", exit_detail(w.proc.exitcode))
                    continue
                w.job = None
                w.done += 1
                record(ev)
                held = rss_gb(w.proc.pid) or 0
                if w.done >= args.recycle_after or ev["status"] == "failed" or held > args.max_worker_ram_gb:
                    # A worker that failed a document may be in a bad state, and one that
                    # kept what its largest document needed holds memory: a fresh one takes over.
                    if held > args.max_worker_ram_gb:
                        sess.event({"event": "worker_recycled", "at": utcnow(), "pid": w.proc.pid,
                                    "rss_gb": r(held, 2), "why": "max_worker_ram_gb"})
                    w.stop()
                    workers.remove(w)
                else:
                    dispatch(w)
            elif w.proc.sentinel in ready:
                replace(w, "worker_crashed", exit_detail(w.proc.exitcode))
            elif time.monotonic() - w.job[3] > args.timeout:
                replace(w, "timeout", f"no result after {args.timeout}s")
        if time.monotonic() - last_progress > 60:
            last_progress = time.monotonic()
            el = time.monotonic() - t0
            sess.say(f"progress: {sum(counts.values())}/{len(todo)} docs, {pages_done} pages, "
                     f"{pages_done / el:.1f} pages/s, {len(queue)} queued, {len(workers)} workers")

    for w in workers:
        w.stop()
    tele.close()

    el = time.monotonic() - t0
    sess.event({"event": "session_end", "at": utcnow(), "counts": dict(counts),
                "reasons": dict(reasons_count), "pages": pages_done, "seconds": r(el, 1),
                "left": len(queue), "interrupted": bool(interrupts)})
    sess.say(f"done: {sum(counts.values())} docs ({', '.join(f'{k} {v}' for k, v in counts.items()) or 'none'}), "
             f"{pages_done} pages in {el:.0f}s ({pages_done / el if el else 0:.1f} pages/s); {len(queue)} left")
    if reasons_count:
        sess.say("reasons: " + ", ".join(f"{k} {v}" for k, v in reasons_count.most_common()))
    sess.close()
    if interrupts:
        return 130
    return 1 if counts["failed"] else 0
