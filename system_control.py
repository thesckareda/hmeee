"""Restart voice agent worker via supervisord."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SUPERVISOR_PROGRAM = os.environ.get("SUPERVISOR_AGENT_PROGRAM", "agent")


def _supervisor_config_paths() -> list[Path]:
    candidates = [
        os.environ.get("SUPERVISOR_CONFIG", "").strip(),
        "/etc/supervisor/conf.d/supervisord.conf",
        "/etc/supervisor/supervisord.conf",
        str(BASE_DIR / "supervisord.conf"),
    ]
    paths: list[Path] = []
    for item in candidates:
        if not item:
            continue
        path = Path(item)
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def get_agent_process_status() -> dict:
    supervisorctl = shutil.which("supervisorctl")
    if not supervisorctl:
        return {
            "supervisor_available": False,
            "program": SUPERVISOR_PROGRAM,
            "status": "unknown",
            "message": "supervisorctl not found on PATH",
        }

    for conf in _supervisor_config_paths():
        try:
            result = subprocess.run(
                [supervisorctl, "-c", str(conf), "status", SUPERVISOR_PROGRAM],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0 or SUPERVISOR_PROGRAM in output:
                status = "unknown"
                for token in ("RUNNING", "STARTING", "STOPPED", "FATAL", "BACKOFF", "EXITED"):
                    if token in output.upper():
                        status = token.lower()
                        break
                return {
                    "supervisor_available": True,
                    "program": SUPERVISOR_PROGRAM,
                    "status": status,
                    "message": output,
                    "config": str(conf),
                }
        except Exception as exc:
            continue

    return {
        "supervisor_available": bool(supervisorctl),
        "program": SUPERVISOR_PROGRAM,
        "status": "unknown",
        "message": "Could not query supervisord status",
    }


def _supervisorctl_run(action: str, *, timeout: int = 45) -> dict:
    """Run supervisorctl action against the voice agent program."""
    supervisorctl = shutil.which("supervisorctl")
    if not supervisorctl:
        return {
            "success": False,
            "action": action,
            "message": "supervisorctl not installed. Restart agent.py manually.",
            "hint": "supervisord not available",
        }

    errors: list[str] = []
    for conf in _supervisor_config_paths():
        try:
            result = subprocess.run(
                [supervisorctl, "-c", str(conf), action, SUPERVISOR_PROGRAM],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0:
                return {
                    "success": True,
                    "action": action,
                    "message": output or f"{SUPERVISOR_PROGRAM} {action} OK",
                    "config": str(conf),
                }
            errors.append(f"{conf} ({action}): {output or result.returncode}")
        except subprocess.TimeoutExpired:
            errors.append(f"{conf} ({action}): timed out")
        except Exception as exc:
            errors.append(f"{conf} ({action}): {exc}")

    return {
        "success": False,
        "action": action,
        "message": "; ".join(errors) or f"Failed to {action} agent via supervisord",
        "hint": "Check supervisord.conf includes [unix_http_server] and supervisorctl socket",
    }


def stop_voice_agent() -> dict:
    """Stop the agent program under supervisord."""
    return _supervisorctl_run("stop", timeout=30)


def start_voice_agent() -> dict:
    """Start the agent program under supervisord."""
    return _supervisorctl_run("start", timeout=45)


def restart_voice_agent() -> dict:
    """Restart the agent program under supervisord."""
    result = _supervisorctl_run("restart", timeout=45)
    if result.get("success"):
        result["restarted"] = True
        return result
    # Fall back to stop + start when restart is unsupported.
    stop_result = stop_voice_agent()
    start_result = start_voice_agent()
    if start_result.get("success"):
        return {
            "success": True,
            "restarted": True,
            "action": "stop+start",
            "message": start_result.get("message", "agent started"),
            "stop": stop_result,
            "start": start_result,
            "config": start_result.get("config"),
        }
    result["restarted"] = False
    result["stop"] = stop_result
    result["start"] = start_result
    return result


def kill_and_run_voice_agent() -> dict:
    """Hard stop all agent PIDs, then start fresh (supervisord or direct)."""
    from agent_process import restart_agent_fresh

    result = restart_agent_fresh()
    result["action"] = "kill_and_run"
    return result