"""Regression guard for Sprint 10, task 10.A (Dockerfile portability).

This file does NOT require Docker to be installed -- it checks the
in-repo artifacts that make 10.A verifiable from CI without a
container runtime:

1. ``Dockerfile`` exists, is multi-stage, and contains the key
   directives that prove the design intent (uv builder, non-root
   user, healthcheck, port 8080, ARM64 platform).
2. ``api_port`` default in ``Settings`` is 8080 (not 8000 -- the
   frontend already binds 8000, per ROADMAP-2026-08.md SS2.5).
3. ``SPARK_API_PORT`` env var overrides the default (pydantic-settings
   ``SPARK_`` prefix convention -- guarded so a future rename is
   caught here, not in production).

A full ``docker build`` + ``docker run`` verification is intentionally
out of scope here: the agent requires AWS Bedrock at runtime (AGENTS.md
hard rule #7 is the LOCAL-without-AWS guarantee, NOT container-without-AWS),
so a CI-built image cannot be smoke-tested in this environment without
mocking the entire ``src.api.app`` lifespan. The Dockerfile itself is
verifiable by inspection (this file) and by ``docker build`` in a host
with Docker (manual).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    if not DOCKERFILE.exists():
        pytest.skip(
            "Dockerfile not present (pre-Sprint 10 state or this test "
            "file is being run in isolation before 10.A lands)"
        )
    return DOCKERFILE.read_text(encoding="utf-8")


class TestDockerfileStructure:
    """The Dockerfile MUST be a multi-stage build with the design
    choices documented in src/agent/factory.py and ROADMAP-2026-08.md
    SS10.A. Each test guards one design decision so a future edit that
    silently regresses one of them (e.g. drops the non-root user) is
    caught in CI."""

    def test_uses_multi_stage_build(self, dockerfile_text: str) -> None:
        # Two FROM lines, both with AS <name> -- the canonical
        # multi-stage marker. The image ref can be preceded by flags
        # like `--platform=...`, so we match by the `AS <name>` suffix.
        from_lines = [
            line.strip() for line in dockerfile_text.splitlines() if line.startswith("FROM ")
        ]
        assert len(from_lines) >= 2, (
            f"Dockerfile must be multi-stage (>=2 FROM ... AS ... lines); found: {from_lines!r}"
        )
        assert any(" AS builder" in line for line in from_lines), "missing `AS builder` stage"
        assert any(" AS runtime" in line for line in from_lines), "missing `AS runtime` stage"

    def test_builder_uses_uv_image(self, dockerfile_text: str) -> None:
        """Sprint 10.A uses the official uv image as the builder
        (not pip in a plain python image) -- uv is 10-100x faster and
        the project standard (pyproject.toml + uv.lock)."""
        builder_match = re.search(
            r"^FROM\s+(?:--\S+\s+)*(\S+)\s+AS\s+builder",
            dockerfile_text,
            re.MULTILINE,
        )
        assert builder_match, "builder FROM line not found"
        builder_img = builder_match.group(1)
        assert "uv" in builder_img, f"builder image must be a uv image, got: {builder_img!r}"
        assert "python3.14" in builder_img, (
            f"builder image must pin python3.14, got: {builder_img!r}"
        )

    def test_runtime_uses_python_3_14_slim(self, dockerfile_text: str) -> None:
        """Runtime stage uses python:3.14-slim-bookworm (no `uv` in the
        name -- uv is only the build tool, the runtime is plain CPython
        with the .venv on PATH)."""
        runtime_match = re.search(
            r"^FROM\s+(?:--\S+\s+)*(\S+)\s+AS\s+runtime",
            dockerfile_text,
            re.MULTILINE,
        )
        assert runtime_match, "runtime FROM line not found"
        runtime_img = runtime_match.group(1)
        assert "python:3.14" in runtime_img, (
            f"runtime image must be python:3.14-..., got: {runtime_img!r}"
        )
        assert "slim" in runtime_img, (
            f"runtime image must be `slim` (not full Debian), got: {runtime_img!r}"
        )
        assert "uv" not in runtime_img, (
            f"runtime image must NOT be a uv image (uv is build-only), got: {runtime_img!r}"
        )

    def test_targets_linux_arm64(self, dockerfile_text: str) -> None:
        """ROADMAP 10.B deploys to ECS Fargate on Graviton (ARM64).
        Both stages must declare the platform so multi-arch builds
        produce the right image."""
        for stage in ("builder", "runtime"):
            pattern = rf"^FROM\s+--platform=linux/arm64\s+\S+\s+AS\s+{stage}"
            assert re.search(pattern, dockerfile_text, re.MULTILINE), (
                f"Dockerfile stage `{stage}` is missing "
                "`--platform=linux/arm64` (required for ECS Fargate "
                "Graviton target)"
            )

    def test_creates_non_root_user(self, dockerfile_text: str) -> None:
        """AGENTS.md hard rule: runtime containers must NOT run as root.
        The Dockerfile must `groupadd` + `useradd` and finish with
        `USER spark` (or equivalent non-root)."""
        # `groupadd` / `useradd` may appear on the same RUN line (chained
        # with `&&`), so we match the bare tokens anywhere, not only at
        # line start.
        assert re.search(r"\bgroupadd\b", dockerfile_text), (
            "Dockerfile missing `groupadd` for the non-root user"
        )
        assert re.search(r"\buseradd\b", dockerfile_text), (
            "Dockerfile missing `useradd` for the non-root user"
        )
        assert re.search(r"^\s*USER\s+\S+", dockerfile_text, re.MULTILINE), (
            "Dockerfile must end with `USER <non-root>`"
        )
        # The USER line must not be `USER root` (default) and not
        # `USER 0` (root by UID).
        user_lines = re.findall(r"^\s*USER\s+(\S+)", dockerfile_text, re.MULTILINE)
        assert all(u not in ("root", "0") for u in user_lines), (
            f"Dockerfile runs as root: {user_lines!r}"
        )

    def test_exposes_port_8080_not_8000(self, dockerfile_text: str) -> None:
        """ROADMAP SS10.A note: port 8080 (not 8000 -- frontend already
        binds 8000). EXPOSE must reflect the same port the runtime
        actually serves on."""
        expose_lines = re.findall(r"^\s*EXPOSE\s+(\d+)", dockerfile_text, re.MULTILINE)
        assert expose_lines, "Dockerfile missing `EXPOSE` directive"
        assert "8080" in expose_lines, (
            f"Dockerfile must EXPOSE 8080 (not 8000 -- the frontend "
            f"service already binds 8000 per ROADMAP SS2.5), got: "
            f"{expose_lines!r}"
        )

    def test_has_healthcheck(self, dockerfile_text: str) -> None:
        """ROADMAP 10.A DoD requires a HEALTHCHECK that points at
        /health. The check must use a stdlib-only invocation (no curl
        to keep the runtime image minimal)."""
        assert re.search(r"^\s*HEALTHCHECK\b", dockerfile_text, re.MULTILINE), (
            "Dockerfile missing `HEALTHCHECK` directive"
        )
        assert "/health" in dockerfile_text, (
            "HEALTHCHECK must probe /health (the endpoint defined in src/api/app.py:147)"
        )
        assert "urllib" in dockerfile_text, (
            "HEALTHCHECK should use stdlib urllib (not curl) to keep "
            "the runtime image free of extra packages"
        )

    def test_runs_src_module(self, dockerfile_text: str) -> None:
        """CMD must invoke the standard ``python -m src`` entry point
        (src/__main__.py -> src/api/server.py -> uvicorn.run), not
        something custom that bypasses the standard wiring."""
        cmd_lines = re.findall(r"^\s*CMD\s+(.+?)\s*$", dockerfile_text, re.MULTILINE)
        assert cmd_lines, "Dockerfile missing `CMD` directive"
        assert any('"python", "-m", "src"' in cmd for cmd in cmd_lines), (
            f"Dockerfile CMD must use `python -m src` (the standard "
            f"entry point), got: {cmd_lines!r}"
        )

    def test_runtime_installs_weasyprint_system_libraries(self, dockerfile_text: str) -> None:
        """ADR-019 D11: sin estas bibliotecas, el informe en PDF no se genera.

        Este test existe porque el fallo que previene es INVISIBLE. WeasyPrint
        se importa dentro de la funcion (`src/reports/pdf.py`) a proposito,
        para que a una imagen sin pango no se le caiga el contenedor entero;
        el efecto secundario es que una imagen rota **arranca, conversa y
        recomienda con normalidad**, y nadie se entera hasta que un estudiante
        pide su informe. Esa capa `apt-get` es exactamente el tipo de linea
        que se borra al adelgazar una imagen.
        """
        requeridas = (
            "libpango-1.0-0",
            "libpangoft2-1.0-0",
            "libcairo2",
            "libgdk-pixbuf-2.0-0",
        )
        faltan = [lib for lib in requeridas if lib not in dockerfile_text]
        assert not faltan, (
            f"al Dockerfile le faltan bibliotecas que WeasyPrint carga por FFI: {faltan}. "
            "Sin ellas el agente arranca igual y solo falla al generar un informe en PDF "
            "-- ver src/reports/pdf.py y ADR-019 D11."
        )

    def test_runtime_installs_the_product_typeface(self, dockerfile_text: str) -> None:
        """La web trae Inter de Google Fonts; el contenedor no puede.

        Sin la fuente en la imagen, WeasyPrint no falla: cae a la sustituta
        del sistema y el PDF sale con otra tipografia. Es un fallo silencioso
        y cosmetico, del tipo que nadie reporta y todo el mundo nota.
        """
        assert "fonts-inter" in dockerfile_text, (
            "el Dockerfile no instala `fonts-inter`, la tipografia del producto. "
            "El PDF se renderizaria con la fuente por defecto del sistema sin avisar "
            "-- ver la cabecera de src/reports/report.css."
        )

    def test_apt_layer_cleans_its_package_lists(self, dockerfile_text: str) -> None:
        """Un `apt-get update` sin limpiar deja ~40 MB de indices en la capa."""
        if "apt-get install" not in dockerfile_text:
            pytest.skip("no hay capa apt en el Dockerfile")
        assert "rm -rf /var/lib/apt/lists/*" in dockerfile_text, (
            "la capa apt no borra /var/lib/apt/lists/*, que se queda dentro de la imagen"
        )

    def test_uv_sync_pins_to_lockfile(self, dockerfile_text: str) -> None:
        """Sprint 10.A + ROADMAP 8.7: builds must be reproducible.
        `--frozen` pins to uv.lock and rejects drift."""
        uv_sync_calls = re.findall(r"^\s*uv\s+sync\b.*$", dockerfile_text, re.MULTILINE)
        assert uv_sync_calls, "Dockerfile missing `uv sync`"
        assert all("--frozen" in call for call in uv_sync_calls), (
            f"all `uv sync` calls must use `--frozen` for reproducible "
            f"builds, got: {uv_sync_calls!r}"
        )


class TestApiPortDefault8080:
    """The 8000-vs-8080 port decision is documented in ROADMAP SS10.A
    and in src/config/settings.py. This test guards BOTH ends -- the
    Docker EXPOSE (above) AND the Python default."""

    def test_settings_default_api_port_is_8080(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear SPARK_API_PORT in the process env so pydantic-settings
        # picks up the Settings class default rather than any value
        # inherited from the .env file (the .env shipped with the repo
        # already has SPARK_API_PORT=8080, but a future local .env
        # could regress).
        monkeypatch.delenv("SPARK_API_PORT", raising=False)
        from src.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        assert settings.api_port == 8080, (
            f"api_port default must be 8080 (not 8000 -- the frontend "
            f"already binds 8000 per ROADMAP SS2.5), got: "
            f"{settings.api_port!r}"
        )

    def test_settings_respects_SPARK_API_PORT_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Dockerfile's ENV SPARK_API_PORT=8080 must be overridable
        for local tests (e.g. ``docker run -e SPARK_API_PORT=9090 ...``)
        without rebuilding the image."""
        monkeypatch.setenv("SPARK_API_PORT", "9090")
        from src.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        assert settings.api_port == 9090, (
            f"SPARK_API_PORT env var should override api_port, got: {settings.api_port!r}"
        )
