"""Launch the two local processes. Stops both on Ctrl+C or child failure."""

import os
import argparse
from pathlib import Path
import shutil
import secrets
import socket
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="Run the local connector application")
    parser.add_argument("--ui", action="store_true", help="Also start the Next.js connection screen")
    args = parser.parse_args()
    environment = dict(os.environ)
    environment.setdefault("ERP_API_TOKEN", secrets.token_urlsafe(32))
    ports = [int(environment.get("ERP_GATEWAY_PORT", "8000")),
             int(environment.get("ERP_CONNECTOR_PORT", "8001"))]
    if args.ui:
        ports.append(int(environment.get("ERP_FRONTEND_PORT", "3000")))
    if len(set(ports)) != len(ports):
        raise SystemExit("Application ports must differ")
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    node = shutil.which("node")
    next_bin = frontend / "node_modules/next/dist/bin/next"
    if args.ui and (not node or not next_bin.is_file()):
        raise SystemExit("Install Node.js and run npm install in frontend before using --ui")
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Port {port} is already in use. Stop its owner or choose a different port.")
    children = []
    try:
        for module, port in (("service", ports[1]), ("gateway", ports[0])):
            child_environment = dict(environment)
            if module == "gateway":
                child_environment = {key: value for key, value in child_environment.items()
                                     if not key.startswith(("DATABRICKS_", "SNOWFLAKE_", "AWS_", "SAP_", "ERP_SAP_")) and key != "BOTO_CONFIG"}
            children.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", f"erp_connectors.{module}:create_app",
                 "--factory", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                env=child_environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        if args.ui:
            ui_environment = {key: value for key, value in environment.items()
                              if not key.startswith(("DATABRICKS_", "SNOWFLAKE_", "AWS_", "SAP_", "ERP_SAP_")) and key != "BOTO_CONFIG"}
            children.append(subprocess.Popen(
                [node, str(next_bin), "dev", "--hostname", "127.0.0.1", "--port", str(ports[2])],
                cwd=frontend, env=ui_environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
            print(f"Connection screen: http://127.0.0.1:{ports[2]}", flush=True)
        print(f"Mode: {environment.get('ERP_CONNECTOR_MODE', 'local-demo')}", flush=True)
        print(f"API documentation: http://127.0.0.1:{ports[0]}/docs", flush=True)
        print(f"Local API token (use Authorize): {environment['ERP_API_TOKEN']}", flush=True)
        print("Wait for both startup messages. Press Ctrl+C here to stop both services.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
