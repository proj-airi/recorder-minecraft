from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from setuptools.build_meta import build_wheel


def main() -> None:
    repository = Path(__file__).resolve().parents[3]
    project = repository / "apps" / "minerec"
    viewer = project / "src" / "minerec" / "viewer_dist"
    if not (viewer / "index.html").is_file():
        raise SystemExit("viewer assets are missing; run 'pixi run build-viewer' first")

    with tempfile.TemporaryDirectory(prefix="minerec-wheel-check-") as temporary_name:
        temporary = Path(temporary_name)
        build_project = temporary / "project"
        wheel_directory = temporary / "wheel"
        installed = temporary / "installed"
        build_project.mkdir()
        wheel_directory.mkdir()
        installed.mkdir()
        shutil.copy2(project / "pyproject.toml", build_project / "pyproject.toml")
        shutil.copytree(project / "src", build_project / "src")

        previous_directory = Path.cwd()
        try:
            os.chdir(build_project)
            wheel_name = build_wheel(str(wheel_directory))
        finally:
            os.chdir(previous_directory)

        wheel_path = wheel_directory / wheel_name
        with zipfile.ZipFile(wheel_path, "r") as wheel:
            names = set(wheel.namelist())
            required = {
                "minerec/viewer_dist/index.html",
                "minerec/serve/viewer/server.py",
            }
            missing = required - names
            if missing:
                raise SystemExit(f"wheel is missing required files: {sorted(missing)}")
            if not any(name.startswith("minerec/viewer_dist/assets/") and name.endswith(".js") for name in names):
                raise SystemExit("wheel is missing the compiled viewer JavaScript")
            wheel.extractall(installed)

        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join((str(installed), environment.get("PYTHONPATH", ""))).rstrip(os.pathsep)
        smoke = (
            "from minerec.serve.viewer.server import ViewerApplication, ViewerHTTPServer; "
            "app = ViewerApplication('wheel-smoke-token'); "
            "server = ViewerHTTPServer(('127.0.0.1', 0), app); "
            "assert server.server_address[1] > 0; "
            "server.server_close(); app.close()"
        )
        subprocess.run(
            [sys.executable, "-c", smoke],
            check=True,
            cwd=temporary,
            env=environment,
        )
        print(f"validated {wheel_name}: packaged viewer assets and loopback startup")


if __name__ == "__main__":
    main()
