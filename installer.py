#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentF Installer — robust macOS bootstrap (loop-proof)
- Venv: ~/Library/Application Support/AgentF/.venv
- Deps: PySide6, packaging, torch, transformers, tokenizers, huggingface-hub, safetensors, accelerate
- Retries package installs; falls back to PyTorch extra index if needed (CPU wheel)
- Verifies imports and MPS availability; checks ai.py/agent.py/weights
- If run directly (no --from-gui), launches gui.py using venv python
"""

from __future__ import annotations
import os, sys, subprocess, pathlib, datetime, shutil, time, errno, platform, json
from typing import Optional, List, Tuple

# GUI (PySide6 is bundled in the app so we can import it before creating the venv)
from PySide6.QtCore import Qt, QThread, QObject, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QProgressBar,
    QPlainTextEdit, QMessageBox
)

# ----------------------------
# Paths & constants
# ----------------------------
APP_NAME    = "AgentF"
HOME        = pathlib.Path.home()
APP_SUPPORT = HOME / "Library" / "Application Support" / APP_NAME
VENV_DIR    = APP_SUPPORT / ".venv"
PY_IN_VENV  = VENV_DIR / "bin" / "python3"
RES_DIR     = pathlib.Path(__file__).resolve().parent
GUI_ENTRY   = RES_DIR / "gui.py"
AI_PY       = RES_DIR / "ai.py"
AGENT_PY    = RES_DIR / "agent.py"
LOCK_FILE   = APP_SUPPORT / ".installer.lock"
FROM_GUI    = "--from-gui" in sys.argv

LOG_DIR     = HOME / "Library" / "Logs" / APP_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH    = LOG_DIR / f"{APP_NAME}_{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}.log"

LIGHTWEIGHT = os.environ.get("AGENTF_LIGHTWEIGHT", "0") == "1"
RETRIES     = 3

# Install set (Torch first; others later)
GUI_DEPS        = ["PySide6>=6.7", "packaging>=24"]  # usually already satisfied in app runtime
RUNTIME_DEPS    = ["transformers>=4.45", "huggingface-hub>=0.24", "safetensors>=0.4", "tokenizers>=0.20", "accelerate>=0.33"]
TORCH_SPEC      = "torch"  # macOS arm64 wheels include MPS; fallback index is CPU wheels only


# ----------------------------
# Logging helpers
# ----------------------------
def log(msg: str):
    print(msg)
    with open(LOG_PATH, "a", encoding="utf-8", buffering=1) as f:
        f.write(msg + "\n")


# ----------------------------
# Locking (single-run)
# ----------------------------
def acquire_lock():
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except OSError as e:
        if e.errno == errno.EEXIST:
            age = time.time() - LOCK_FILE.stat().st_mtime
            # stale lock > 10 min -> remove
            if age > 600:
                LOCK_FILE.unlink(missing_ok=True)
                return acquire_lock()
            log("[installer] Another installer is running; exiting.")
            sys.exit(0)
        raise


def release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ----------------------------
# Shell helpers
# ----------------------------
def arch_info() -> Tuple[str, str]:
    return platform.system(), platform.machine()


def find_python() -> str:
    # Prefer Homebrew Python 3.12, then any 3.12, then python3
    for c in ["/opt/homebrew/bin/python3.12", "/usr/local/bin/python3.12",
              shutil.which("python3.12"), shutil.which("python3")]:
        if c and pathlib.Path(c).exists():
            return c
    # Fall back to the embedded interpreter running this script (rare)
    return sys.executable


def run_stream(cmd: List[str], env: Optional[dict] = None, check: bool = True) -> int:
    env = dict(env or os.environ)
    # keep venv isolated from user/site
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONSAFEPATH", "PYTHONUSERBASE"):
        env.pop(k, None)
    env["PYTHONNOUSERSITE"] = "1"

    log("$ " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    for line in iter(p.stdout.readline, ''):
        if not line:
            break
        log(line.rstrip("\n"))
    rc = p.wait()
    if check and rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)
    return rc


def pip_install(pkgs: List[str], extra_args: Optional[List[str]] = None) -> None:
    args = [str(PY_IN_VENV), "-I", "-m", "pip", "install", "--upgrade", "--no-python-version-warning"]
    if extra_args:
        args += extra_args
    args += pkgs
    run_stream(args)


def pip_install_with_retry(pkgs: List[str]) -> None:
    """
    First try PyPI. If that fails for torch, retry with CPU extra-index.
    """
    try:
        pip_install(pkgs, extra_args=["--no-cache-dir"])
        return
    except subprocess.CalledProcessError:
        log("[installer] pip install failed on default index; evaluating fallback…")

    needs_torch = any(p.split("==")[0].split(">=")[0].strip() in ("torch",) for p in pkgs)
    extra = []
    if needs_torch:
        # Fallback to CPU wheels repo (works universally but without MPS optimizations)
        extra = ["--extra-index-url", "https://download.pytorch.org/whl/cpu"]
        log("[installer] Retrying torch via CPU wheel index (no-MPS fallback).")

    pip_install(pkgs, extra_args=["--no-cache-dir"] + extra)


def venv_python_ok() -> bool:
    return PY_IN_VENV.exists()


def torch_probe() -> dict:
    try:
        code = (
            "import json, sys\n"
            "try:\n"
            " import torch\n"
            " mps = bool(getattr(torch.backends,'mps',None) and torch.backends.mps.is_available())\n"
            " out = {'ok': True, 'ver': torch.__version__, 'mps': mps}\n"
            " print(json.dumps(out))\n"
            "except Exception as e:\n"
            " print(json.dumps({'ok': False, 'err': str(e)}))\n"
        )
        out = subprocess.check_output([str(PY_IN_VENV), "-I", "-c", code], text=True).strip()
        return json.loads(out)
    except Exception as e:
        return {"ok": False, "err": str(e)}


# ----------------------------
# Worker (runs in thread)
# ----------------------------
class InstallerWorker(QObject):
    progress = Signal(int, str)
    done = Signal(bool, str)

    def run(self):
        acquire_lock()
        try:
            sys_name, machine = arch_info()
            log(f"[installer] OS/Arch: {sys_name} {machine}")
            log(f"[installer] Resources: {RES_DIR}")
            log(f"[installer] Log: {LOG_PATH}")

            host_py = find_python()
            log(f"[installer] host python: {host_py}")

            # Step 1: venv
            self.progress.emit(5, "Preparing environment…")
            if not venv_python_ok():
                self.progress.emit(8, "Creating virtual environment…")
                run_stream([host_py, "-I", "-m", "venv", str(VENV_DIR)])
            else:
                log("[installer] Using existing venv")

            # Step 2: toolchain
            self.progress.emit(20, "Upgrading pip/setuptools/wheel…")
            # ensurepip (idempotent), then upgrade
            run_stream([str(PY_IN_VENV), "-I", "-m", "ensurepip", "--upgrade"])
            pip_install(["pip", "setuptools", "wheel"])

            # Step 3: GUI deps (mainly no-op; useful if user runs installer standalone)
            self.progress.emit(30, "Ensuring GUI dependencies…")
            try:
                pip_install(GUI_DEPS)
            except Exception as e:
                log(f"[installer] Non-fatal GUI deps issue: {e}")

            # Step 4: model deps (Torch first)
            if LIGHTWEIGHT:
                log("[installer] AGENTF_LIGHTWEIGHT=1 — skipping torch/LLM deps.")
            else:
                self.progress.emit(55, "Installing PyTorch (this may take a bit)…")
                try:
                    pip_install_with_retry([TORCH_SPEC])
                except Exception as e:
                    log(f"[installer] Torch install failed: {e}")
                    raise

                probe = torch_probe()
                log(f"[torch] probe: {probe}")
                if not probe.get("ok"):
                    raise RuntimeError(f"Torch failed to import: {probe.get('err')}")

                # Remaining deps
                self.progress.emit(75, "Installing Transformers & friends…")
                pip_install_with_retry(RUNTIME_DEPS)

            # Step 5: verify environment
            self.progress.emit(88, "Verifying environment…")
            verify_code = "\n".join([
                "import sys",
                "mods=['PySide6','packaging']",
                "try:\n import transformers, huggingface_hub, safetensors, tokenizers; mods+=['transformers','huggingface-hub','safetensors','tokenizers']\nexcept Exception: pass",
                "try:\n import torch; mods+=['torch']; mps_ok = bool(getattr(torch.backends,'mps',None) and torch.backends.mps.is_available())\n print('Torch', torch.__version__, 'MPS:', mps_ok)\nexcept Exception as e:\n print('Torch import error:', e)",
                "print('OK', ','.join(mods))",
            ])
            try:
                run_stream([str(PY_IN_VENV), "-I", "-c", verify_code])
            except subprocess.CalledProcessError as e:
                log(f"[installer] Verification script failed (non-fatal): {e}")

            # Step 6: sanity checks
            missing = []
            if not AI_PY.exists():    missing.append("ai.py")
            if not AGENT_PY.exists(): missing.append("agent.py")
            weights = None
            for pat in ("qwen*.npz", "*.npz"):
                found = sorted(RES_DIR.glob(pat))
                if found:
                    weights = found[0]
                    break
            if not weights:
                log("[installer] WARNING: no .npz weights found in Resources.")
            log(f"[installer] ai.py: {AI_PY.exists()}  agent.py: {AGENT_PY.exists()}  weights: {weights.name if weights else 'NONE'}")
            if missing:
                log("[installer] WARNING: missing: " + ", ".join(missing))

            self.progress.emit(100, "Installation complete.")
            self.done.emit(True, "Done.")
        except Exception as e:
            log(f"[installer] ERROR: {e}")
            self.done.emit(False, f"Error: {e}")
        finally:
            release_lock()


# ----------------------------
# Minimal GUI shell
# ----------------------------
class InstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} Installer")
        self.resize(760, 480)

        self.title = QLabel(f"Setting up {APP_NAME}…")
        self.title.setStyleSheet("font-size:18px;font-weight:600;")
        self.status = QLabel("Preparing…")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: ui-monospace, Menlo, Monaco, Consolas, 'SF Mono';")

        root = QWidget()
        v = QVBoxLayout(root)
        v.addWidget(self.title)
        v.addWidget(self.status)
        v.addWidget(self.progress)
        v.addWidget(self.log)
        self.setCentralWidget(root)

        self.worker_thread = QThread(self)
        self.worker = InstallerWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker_thread.start()

    def on_progress(self, val: int, txt: str):
        self.progress.setValue(val)
        self.status.setText(txt)
        self.log.appendPlainText(txt)

    def on_done(self, ok: bool, msg: str):
        self.log.appendPlainText(msg)
        if ok and not FROM_GUI and GUI_ENTRY.exists():
            # Launch GUI using venv python
            os.execv(str(PY_IN_VENV), [str(PY_IN_VENV), "-I", str(GUI_ENTRY), "--from-installer"])
        QTimer.singleShot(600, self.close if ok else lambda: None)
        if not ok:
            QMessageBox.warning(self, "Installer error", msg)
        self.worker_thread.quit()


# ----------------------------
# Entrypoint
# ----------------------------
def main():
    log(f"[env] macOS={platform.mac_ver()[0]} arch={platform.machine()} py={sys.version.split()[0]}")
    app = QApplication(sys.argv)
    win = InstallerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
