"""Voice agent process control — psutil discovery, tree kill, dev mode, logs.

Worker hardening (Phase 5.7):
  - Dev stdout/stderr append to ``data/agent_logs/agent_dev.log`` (not discarded).
  - ``AGENT_NUM_IDLE_PROCESSES`` defaults to 1 in dev to limit orphan ``job_proc`` children.
  - LiveKit worker children (``job_proc``, ``agents_inference_process``) ignore SIGTERM;
    cleanup uses SIGKILL via ``python3 agent_process.py kill --force``.
  - Optional ``AGENT_MEMORY_LIMIT_MB``: when set, status reports workers exceeding the cap
    (supervisor should restart the agent if memory stays above limit).
  - Restart policy: crashed agent.py is restarted by LiveKit/supervisor; stale workers
    require explicit ``kill --force`` before ``dev`` restart.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
AGENT_SCRIPT = BASE_DIR / "agent.py"
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"


def _resolve_python() -> str:
    """Prefer project venv — system Python often lacks livekit plugin packages."""
    if VENV_PYTHON.is_file():
        return str(VENV_PYTHON)
    return sys.executable
LOG_DIR = BASE_DIR / "data" / "agent_logs"
DEV_LOG = LOG_DIR / "agent_dev.log"
PROD_LOG = LOG_DIR / "agent_prod.log"
DEV_PID_FILE = LOG_DIR / "agent_dev.pid"
AGENT_PID_FILE = LOG_DIR / "agent.pid"

# LiveKit dev spawns multiprocessing children named job_proc / agents_inference_process.
# Those children ignore SIGTERM — must SIGKILL on cleanup.
WORKER_PROCESS_NAMES = frozenset({"job_proc", "agents_inference_process"})


def _cmdline_joined(cmdline: list[str] | None) -> str:
    return " ".join(cmdline or []).lower()


def _is_agent_cmdline(joined: str) -> bool:
    if "agent.py" not in joined:
        return False
    if "uvicorn" in joined or "ui_server" in joined:
        return False
    return True


def _is_spawn_worker_cmdline(joined: str) -> bool:
    return "spawn_main" in joined and "multiprocessing" in joined


def _proc_cwd(proc) -> str:
    try:
        return str(proc.cwd() or "")
    except Exception:
        return ""


def _is_project_livekit_spawn(proc, *, agent_roots: set[int] | None = None) -> bool:
    """Match LiveKit multiprocessing workers, including orphans after parent exit."""
    joined = _proc_joined(proc)
    if not _is_spawn_worker_cmdline(joined):
        return False

    cwd = _proc_cwd(proc)
    if cwd and str(BASE_DIR) in cwd:
        return True

    if agent_roots and proc.ppid() in agent_roots:
        return True

    try:
        parent = proc.parent()
        depth = 0
        while parent is not None and depth < 10:
            if parent.pid in (agent_roots or set()):
                return True
            if _is_agent_cmdline(_proc_joined(parent)):
                return True
            if _proc_name(parent) in WORKER_PROCESS_NAMES:
                return True
            parent = parent.parent()
            depth += 1
    except Exception:
        return False
    return False


def _psutil_available():
    try:
        import psutil  # noqa: F401

        return True
    except ImportError:
        return False


def _proc_joined(proc) -> str:
    try:
        return _cmdline_joined(proc.cmdline())
    except Exception:
        return ""


def _proc_name(proc) -> str:
    try:
        return str(proc.name() or "")
    except Exception:
        return ""


def _is_agent_worker_process(proc, *, agent_roots: set[int] | None = None) -> bool:
    """Match agent.py parent and LiveKit worker/orphan children."""
    joined = _proc_joined(proc)
    if _is_agent_cmdline(joined):
        return True

    name = _proc_name(proc)
    if name in WORKER_PROCESS_NAMES:
        return True

    return _is_project_livekit_spawn(proc, agent_roots=agent_roots)


def _discover_agent_processes():
    try:
        import psutil
    except ImportError:
        return []

    roots: list = []
    all_procs: list = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "ppid", "create_time", "status"]):
        try:
            joined = _cmdline_joined(proc.info.get("cmdline"))
            if _is_agent_cmdline(joined):
                roots.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    root_ids = {p.pid for p in roots}
    seen: set[int] = set()

    for proc in psutil.process_iter(["pid", "name", "cmdline", "ppid", "create_time", "status"]):
        try:
            if proc.pid in seen:
                continue
            if not _is_agent_worker_process(proc, agent_roots=root_ids):
                continue
            all_procs.append(proc)
            seen.add(proc.pid)
            for child in proc.children(recursive=True):
                if child.pid not in seen:
                    all_procs.append(child)
                    seen.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return all_procs


def _iter_agent_processes():
    return _discover_agent_processes()


def _kill_signal_for_proc(proc, *, force: bool) -> int:
    name = _proc_name(proc)
    if force or name in WORKER_PROCESS_NAMES:
        return signal.SIGKILL
    return signal.SIGTERM


def _kill_process_tree(proc, *, force: bool = False, killed: list[int], errors: list[str]) -> None:
    try:
        import psutil
    except ImportError:
        return

    try:
        children = proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []

    # Deepest children first
    for child in sorted(children, key=lambda p: p.pid, reverse=True):
        try:
            sig = _kill_signal_for_proc(child, force=force)
            if sig == signal.SIGKILL:
                child.kill()
            else:
                child.terminate()
            killed.append(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as exc:
            errors.append(f"pid {child.pid}: {exc}")

    try:
        sig = _kill_signal_for_proc(proc, force=force)
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()
        if proc.pid not in killed:
            killed.append(proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except Exception as exc:
        errors.append(f"pid {proc.pid}: {exc}")


def _kill_dev_process_group(*, force: bool, killed: list[int], errors: list[str]) -> None:
    dev_pid = _read_dev_pid()
    if not dev_pid or not _pid_alive(dev_pid):
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        pgid = os.getpgid(dev_pid)
        os.killpg(pgid, sig)
        killed.append(dev_pid)
    except OSError as exc:
        errors.append(f"dev pgid {dev_pid}: {exc}")
        try:
            os.kill(dev_pid, sig)
            killed.append(dev_pid)
        except OSError as exc2:
            errors.append(f"dev pid {dev_pid}: {exc2}")


def get_detailed_agent_status() -> dict:
    """Running/stopped with PID, uptime, CPU/mem when available."""
    from system_control import get_agent_process_status

    supervisor = get_agent_process_status()
    procs = _iter_agent_processes()
    details: list[dict] = []

    try:
        import psutil
    except ImportError:
        psutil = None  # type: ignore

    for proc in procs:
        try:
            info = {
                "pid": proc.pid,
                "name": _proc_name(proc),
                "cmdline": _proc_joined(proc)[:200],
                "status": proc.status(),
            }
            if psutil:
                with proc.oneshot():
                    info["cpu_percent"] = proc.cpu_percent(interval=0.0)
                    mem = proc.memory_info()
                    info["memory_mb"] = round(mem.rss / (1024 * 1024), 1)
                    created = proc.create_time()
                    info["uptime_sec"] = max(0, int(time.time() - created))
                    info["started_at"] = created
            details.append(info)
        except Exception as exc:
            details.append({"pid": getattr(proc, "pid", None), "error": str(exc)})

    running = len(details) > 0
    state = "running" if running else "stopped"
    if supervisor.get("status") == "running" and not running:
        state = supervisor.get("status", state)

    dev_pid = _read_dev_pid()
    dev_running = dev_pid is not None and _pid_alive(dev_pid)

    memory_limit_mb = 0
    try:
        memory_limit_mb = int(os.environ.get("AGENT_MEMORY_LIMIT_MB", "0") or "0")
    except (TypeError, ValueError):
        memory_limit_mb = 0

    memory_warnings: list[str] = []
    if memory_limit_mb > 0:
        for info in details:
            mem = info.get("memory_mb")
            if mem is not None and float(mem) > memory_limit_mb:
                memory_warnings.append(
                    f"pid {info.get('pid')} using {mem}MB > limit {memory_limit_mb}MB"
                )

    return {
        "state": state,
        "running": running or dev_running,
        "process_count": len(details),
        "processes": details,
        "supervisor": supervisor,
        "dev_mode": {
            "running": dev_running,
            "pid": dev_pid,
            "log_file": str(DEV_LOG),
        },
        "hardening": {
            "memory_limit_mb": memory_limit_mb or None,
            "memory_warnings": memory_warnings,
            "idle_processes": os.environ.get("AGENT_NUM_IDLE_PROCESSES", "1"),
            "restart_hint": "kill --force then agent_process.py dev",
        },
        "cleanup_hint": "python3 agent_process.py kill --force",
    }


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_dev_pid() -> int | None:
    if not DEV_PID_FILE.exists():
        return None
    try:
        return int(DEV_PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _write_dev_pid(pid: int) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DEV_PID_FILE.write_text(str(pid), encoding="utf-8")


def kill_agent_processes(*, force: bool = False) -> dict:
    """Stop agent.py and all LiveKit worker children (including orphans)."""
    killed: list[int] = []
    errors: list[str] = []

    if not _psutil_available():
        return {
            "success": False,
            "killed_pids": [],
            "remaining_pids": [],
            "errors": ["psutil not installed — pip install psutil"],
            "message": "Install psutil for process cleanup",
        }

    import psutil

    procs = _discover_agent_processes()
    root_ids = {p.pid for p in procs if _is_agent_cmdline(_proc_joined(p))}
    roots = [p for p in procs if p.pid in root_ids or p.ppid() not in {x.pid for x in procs}]

    _kill_dev_process_group(force=force, killed=killed, errors=errors)

    for proc in roots:
        _kill_process_tree(proc, force=force, killed=killed, errors=errors)

    # Catch stragglers (orphan job_proc after parent died)
    for proc in _discover_agent_processes():
        if proc.pid in killed:
            continue
        try:
            if force or _proc_name(proc) in WORKER_PROCESS_NAMES:
                proc.kill()
            else:
                proc.terminate()
            killed.append(proc.pid)
        except Exception as exc:
            errors.append(f"straggler {proc.pid}: {exc}")

    if killed:
        time.sleep(0.25 if force else 1.5)

    if not force:
        for proc in _discover_agent_processes():
            try:
                proc.kill()
                if proc.pid not in killed:
                    killed.append(proc.pid)
            except Exception as exc:
                errors.append(f"force pass {proc.pid}: {exc}")
        time.sleep(0.25)

    from system_control import stop_voice_agent

    sup = stop_voice_agent()

    if DEV_PID_FILE.exists():
        try:
            DEV_PID_FILE.unlink()
        except OSError:
            pass

    remaining = [p.pid for p in _discover_agent_processes()]
    deduped_killed = sorted(set(killed))
    return {
        "success": len(remaining) == 0,
        "killed_pids": deduped_killed,
        "remaining_pids": remaining,
        "supervisor_stop": sup,
        "errors": errors,
        "message": (
            "Agent stopped"
            if not remaining
            else f"Some processes remain: {remaining} — run: python3 agent_process.py kill --force"
        ),
    }


def _invalidate_runtime_caches() -> None:
    try:
        from secrets_manager import clear_secrets_cache

        clear_secrets_cache()
    except Exception:
        pass
    try:
        from assistant_settings import touch_reload_signal

        touch_reload_signal()
    except Exception:
        pass
    try:
        from greeting_cache import clear_prewarm_caches
        from worker_prewarm import invalidate_worker_prewarm

        clear_prewarm_caches()
        invalidate_worker_prewarm()
    except Exception:
        pass
    try:
        from ui_server import invalidate_config_cache

        invalidate_config_cache()
    except Exception:
        pass


def start_production_agent() -> dict:
    """Launch agent.py start in background (production / local without supervisord)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    python = _resolve_python()
    args = [python, str(AGENT_SCRIPT), "start"]

    log_fp = open(PROD_LOG, "a", encoding="utf-8")
    log_fp.write(f"\n--- prod start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fp.flush()

    proc = subprocess.Popen(
        args,
        cwd=str(BASE_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "AGENT_NUM_IDLE_PROCESSES": os.environ.get("AGENT_NUM_IDLE_PROCESSES", "1")},
    )
    _write_dev_pid(proc.pid)
    try:
        AGENT_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    return {
        "success": True,
        "pid": proc.pid,
        "command": " ".join(args),
        "log_file": str(PROD_LOG),
        "mode": "direct",
        "message": f"Agent started (pid {proc.pid})",
    }


def restart_agent_fresh() -> dict:
    """
    Kill all agent.py / LiveKit worker PIDs, clear caches, start a clean agent.

    Production: supervisord restart when available.
    Local/dev: direct agent.py start after hard kill.
    """
    _invalidate_runtime_caches()
    kill_result = kill_agent_processes(force=True)

    from system_control import get_agent_process_status, start_voice_agent

    supervisor = get_agent_process_status()
    if supervisor.get("supervisor_available") and supervisor.get("status") not in ("unknown",):
        start_result = start_voice_agent()
        if start_result.get("success"):
            return {
                "success": True,
                "restarted": True,
                "mode": "supervisord",
                "kill": kill_result,
                "start": start_result,
                "message": start_result.get("message") or "Agent restarted via supervisord",
                "status": get_detailed_agent_status(),
            }

    start_result = start_production_agent()
    return {
        "success": bool(start_result.get("success")),
        "restarted": bool(start_result.get("success")),
        "mode": start_result.get("mode", "direct"),
        "kill": kill_result,
        "start": start_result,
        "supervisor": supervisor,
        "message": start_result.get("message") or "Agent restarted",
        "status": get_detailed_agent_status(),
    }


def start_dev_agent(*, reload: bool = True) -> dict:
    """Launch agent.py dev in background; stream logs to DEV_LOG."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    existing = _read_dev_pid()
    if existing and _pid_alive(existing):
        return {
            "success": True,
            "already_running": True,
            "pid": existing,
            "log_file": str(DEV_LOG),
            "message": f"Dev agent already running (pid {existing})",
        }

    kill_agent_processes(force=True)

    python = _resolve_python()
    args = [python, str(AGENT_SCRIPT), "dev"]
    if reload:
        args.append("--reload")

    log_fp = open(DEV_LOG, "a", encoding="utf-8")
    log_fp.write(f"\n--- dev start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fp.flush()

    env = {
        **os.environ,
        # Dev mode: one warm worker — fewer orphans, faster restarts.
        "AGENT_NUM_IDLE_PROCESSES": os.environ.get("AGENT_NUM_IDLE_PROCESSES", "1"),
    }

    proc = subprocess.Popen(
        args,
        cwd=str(BASE_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    _write_dev_pid(proc.pid)

    return {
        "success": True,
        "pid": proc.pid,
        "command": " ".join(args),
        "log_file": str(DEV_LOG),
        "message": f"Dev agent started (pid {proc.pid})",
    }


def tail_agent_log(*, max_bytes: int = 32_000) -> str:
    if not DEV_LOG.exists():
        return ""
    data = DEV_LOG.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Voice agent process control")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show agent PIDs and resource usage")
    kill_p = sub.add_parser("kill", help="Stop agent.py and all worker children")
    kill_p.add_argument("--force", action="store_true", help="SIGKILL immediately (skip grace)")

    sub.add_parser("dev", help="Start agent.py dev in background (kills stale first)")

    args = parser.parse_args()

    if args.cmd == "status":
        import json

        print(json.dumps(get_detailed_agent_status(), indent=2))
        return 0

    if args.cmd == "kill":
        result = kill_agent_processes(force=args.force)
        print(result.get("message") or result)
        if result.get("remaining_pids"):
            print("Remaining:", result["remaining_pids"])
            return 1
        return 0

    if args.cmd == "dev":
        result = start_dev_agent(reload=True)
        print(result.get("message") or result)
        return 0 if result.get("success") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())