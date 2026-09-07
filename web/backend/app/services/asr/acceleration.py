"""What this machine can actually accelerate transcription with, and what is stopping it.

Three facts have to line up before a model runs on a GPU, and when they do not the failure surfaces
at the worst possible moment — the user presses record, the model tries to load, and an exception
arrives with no indication of which of the three is wrong:

1. **a GPU the kernel can see** — an ``amdgpu`` or NVIDIA device with a compute node;
2. **a CTranslate2 built for it** — the wheel on PyPI is CPU-and-CUDA only, so an AMD machine needs
   the ROCm build from the project's GitHub releases;
3. **that build's runtime libraries present and loadable** — `libhiprand`, `librocrand`, and the
   rest, which a base ROCm install does not always pull in.

Every one of those has been observed failing on the development machine, and each looks identical
from the application: no GPU option, or a load error. So this module checks all three and reports
which one is missing along with the command that fixes it.

**It never imports the GPU stack to find out.** Everything here is filesystem inspection and one
already-imported module's own capability report, so it is cheap enough to run at startup and safe
on a machine where the ROCm libraries would abort the process rather than raise.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ... import branding, paths

logger = logging.getLogger(__name__)

#: Where the kernel exposes the AMD compute node. Its absence means no ROCm, whatever is installed.
KFD_NODE = Path("/dev/kfd")

#: Where DRM cards appear, for spotting a GPU that has no compute node.
DRM_DIR = Path("/sys/class/drm")

#: Shared libraries the ROCm build of CTranslate2 links against. `hiprand` and `rocrand` are the two
#: a base ROCm install commonly omits — they are what a plain `dnf install rocm` leaves out.
ROCM_LIBRARIES = (
    "libamdhip64.so.7",
    "libhipblas.so.3",
    "librocblas.so.5",
    "libhiprand.so.1",
    "librocrand.so.1",
)

#: The release archive carrying the ROCm build, by CTranslate2 version.
ROCM_WHEEL_URL = "https://github.com/OpenNMT/CTranslate2/releases/download/v{version}/rocm-python-wheels-Linux.zip"

#: Where a previously downloaded wheel is kept. ``uv sync`` reinstalls CTranslate2 from PyPI
#: whenever it touches the package, silently replacing the ROCm build with the CPU/CUDA one — so
#: this is not a rare recovery path, it is the one that gets used.
KEPT_WHEEL_DIR = paths.WHEELS_DIR


@dataclass
class Acceleration:
    """What is available, what is missing, and what to do about it."""

    #: ``cuda``, ``rocm``, or ``none`` — what the *hardware* offers.
    hardware: str = "none"
    #: Human name of the device, when one could be read.
    device_name: str = ""
    #: Whether the installed CTranslate2 can actually use it.
    usable: bool = False
    #: Precisions the GPU path offers. Empty when it is unusable.
    precisions: list[str] = field(default_factory=list)
    #: One line saying where things stand.
    summary: str = ""
    #: Shell commands that would fix it, in order. Empty when nothing needs fixing.
    remedy: list[str] = field(default_factory=list)
    #: Libraries that are required and could not be found.
    missing_libraries: list[str] = field(default_factory=list)

    @property
    def gpu_present(self) -> bool:
        """Whether the machine has a GPU at all, usable or not."""
        return self.hardware != "none"

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/health``."""
        return {
            "hardware": self.hardware,
            "device_name": self.device_name,
            "usable": self.usable,
            "precisions": list(self.precisions),
            "summary": self.summary,
            "remedy": list(self.remedy),
            "missing_libraries": list(self.missing_libraries),
        }


def detect() -> Acceleration:
    """Inspect the machine and report where GPU acceleration stands."""
    hardware, name = _detect_hardware()

    if not _installed("ctranslate2"):
        return Acceleration(
            hardware=hardware,
            device_name=name,
            summary="Real transcription is not installed, so nothing runs on a GPU yet.",
            remedy=["uv sync"],
        )

    usable, precisions = _ctranslate2_gpu_support()

    if usable:
        return Acceleration(
            hardware=hardware or "cuda",
            device_name=name,
            usable=True,
            precisions=precisions,
            summary=f"GPU acceleration is available on {name or hardware}.",
        )

    if hardware == "none":
        return Acceleration(
            summary="No GPU detected. Transcription will run on the CPU.",
        )

    # A GPU exists and CTranslate2 cannot drive it. Which of the two reasons applies decides the
    # remedy entirely, so it is worth distinguishing rather than saying "GPU unavailable".
    missing = _missing_rocm_libraries() if hardware == "rocm" else []
    if hardware == "rocm":
        return Acceleration(
            hardware=hardware,
            device_name=name,
            missing_libraries=missing,
            summary=(
                f"{name or 'An AMD GPU'} is present but the installed CTranslate2 cannot use it. "
                + (
                    f"Missing runtime {'libraries' if len(missing) > 1 else 'library'}: "
                    f"{', '.join(missing)}."
                    if missing
                    else (
                        "The CPU/CUDA build from PyPI is installed rather than the ROCm build, "
                        "which is what a `uv sync` touching CTranslate2 leaves behind."
                    )
                )
            ),
            remedy=_rocm_remedy(missing),
        )

    return Acceleration(
        hardware=hardware,
        device_name=name,
        summary=(
            f"{name or 'A GPU'} is present but the installed CTranslate2 was not built for it."
        ),
        remedy=["uv pip install --reinstall --force-reinstall ctranslate2"],
    )


# -- hardware ---------------------------------------------------------------------------


def _detect_hardware() -> tuple[str, str]:
    """What kind of GPU the kernel is exposing, without importing any GPU library."""
    if Path("/proc/driver/nvidia/version").exists():
        return "cuda", _nvidia_name()

    if KFD_NODE.exists():
        return "rocm", _amd_name()

    # A DRM card with an amdgpu driver but no /dev/kfd: the GPU is there for display but the
    # compute stack is not loaded, which is a different problem with a different fix.
    if _amdgpu_present():
        return "rocm", _amd_name()

    return "none", ""


def _amdgpu_present() -> bool:
    try:
        return any(
            (card / "device" / "driver").resolve().name == "amdgpu"
            for card in DRM_DIR.glob("card*")
            if (card / "device" / "driver").exists()
        )
    except OSError:
        return False


def _amd_name() -> str:
    """Read the marketing name from sysfs, falling back to the gfx target."""
    for card in sorted(DRM_DIR.glob("card*")):
        product = card / "device" / "product_name"
        try:
            if product.is_file():
                name = product.read_text(encoding="utf-8").strip()
                if name:
                    return name
        except OSError:
            continue

    for node in sorted(Path("/sys/class/kfd/kfd/topology/nodes").glob("*/properties")):
        try:
            text = node.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"gfx_target_version\s+(\d+)", text)
        if match and match.group(1) != "0":
            raw = int(match.group(1))
            return f"AMD GPU (gfx{raw // 10000}{(raw // 100) % 100:x}{raw % 100:x})"
    return "AMD GPU"


def _nvidia_name() -> str:
    try:
        text = Path("/proc/driver/nvidia/gpus").glob("*/information")
        for info in text:
            for line in info.read_text(encoding="utf-8").splitlines():
                if line.startswith("Model:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "NVIDIA GPU"


# -- the installed CTranslate2 -----------------------------------------------------------


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _ctranslate2_gpu_support() -> tuple[bool, list[str]]:
    """Ask CTranslate2 whether it can see a GPU, tolerating a build that cannot load at all."""
    try:
        import ctranslate2
    except Exception as exc:  # noqa: BLE001 - a ROCm build with missing libs raises ImportError here
        logger.debug("CTranslate2 could not be imported: %s", exc)
        return False, []

    try:
        if int(ctranslate2.get_cuda_device_count()) < 1:
            return False, []
        return True, sorted(ctranslate2.get_supported_compute_types("cuda"))
    except Exception as exc:  # noqa: BLE001 - probing must never take the startup path down
        logger.debug("CTranslate2 GPU probe failed: %s", exc)
        return False, []


def _missing_rocm_libraries() -> list[str]:
    """Which of the ROCm runtime libraries cannot be found by the dynamic loader."""
    import ctypes

    missing: list[str] = []
    for name in ROCM_LIBRARIES:
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return missing


def _rocm_remedy(missing: list[str]) -> list[str]:
    """The commands that would make an AMD GPU usable, in the order they should be run."""
    steps: list[str] = []

    if missing:
        packages = sorted({_package_for(lib) for lib in missing} - {""})
        if packages:
            steps.append(
                f"sudo dnf install {' '.join(packages)}"
                "   # or your distribution's equivalent ROCm packages"
            )

    version = _ctranslate2_version()

    kept = _kept_wheel(version)
    if kept is not None:
        # The common case by far: the wheel was installed once and a later sync replaced it. One
        # command restores it. If this wheel turns out not to be the ROCm build, the next start
        # reports the same thing again rather than claiming success — so guessing here is safe.
        steps.append(f"uv pip install --reinstall {_display_path(kept)}")
        return steps

    steps.extend(
        [
            f"curl -LO {ROCM_WHEEL_URL.format(version=version)}",
            # The trailing dash after the second tag matters: without it the glob also matches
            # the free-threaded `cp314t` wheel, and uv refuses two conflicting URLs for one
            # package rather than picking one.
            "unzip -j rocm-python-wheels-Linux.zip "
            f"'*cp{_python_tag()}-cp{_python_tag()}-manylinux*x86_64.whl'",
            f"uv pip install --reinstall ./ctranslate2-{version}"
            f"-cp{_python_tag()}-cp{_python_tag()}-*.whl",
            # Keeping it turns the three steps above into the one step below, next time.
            f"mkdir -p {_display_path(KEPT_WHEEL_DIR)} && mv ./ctranslate2-{version}"
            f"-cp{_python_tag()}-cp{_python_tag()}-*.whl {_display_path(KEPT_WHEEL_DIR)}/",
        ]
    )
    return steps


def _kept_wheel(version: str) -> Path | None:
    """A previously downloaded wheel matching this CTranslate2 and this Python, if one was kept.

    Matched on version and interpreter tag because a wheel for either a different CTranslate2 or a
    different Python will not install, and offering a command that fails is worse than offering the
    download. The build itself is not inspected: telling a ROCm wheel from a CUDA one means reading
    the ELF headers of a forty-megabyte compressed library, which is far too much work for something
    that runs on every startup.
    """
    tag = _python_tag()
    try:
        candidates = sorted(KEPT_WHEEL_DIR.glob(f"ctranslate2-{version}-cp{tag}-cp{tag}-*.whl"))
    except OSError:
        return None
    return candidates[0] if candidates else None


# -- putting the ROCm build back -----------------------------------------------------------

#: Set to ``1`` to stop the launcher restoring the kept wheel, for anyone who wants the environment
#: left exactly as the lockfile describes it.
REPAIR_OFF = branding.env_var("NO_GPU_REPAIR")
LEGACY_REPAIR_OFF = branding.legacy_env_var("NO_GPU_REPAIR")


#: Models small enough to have been measured working with int8 on a ROCm GPU here.
ROCM_INT8_SAFE_MODELS = frozenset({"tiny", "tiny.en", "base", "base.en"})

#: Set to "1" to load a combination this refuses. For hardware where it is not actually broken.
UNSAFE_OVERRIDE = branding.env_var("ALLOW_UNSAFE_COMPUTE")


def unsafe_combination(device: str, precision: str, model: str) -> str:
    """Why this (device, precision, model) must not be loaded here, or empty if it may be.

    **There is one entry and it earned its place by killing the process.** On this machine — an AMD
    gfx1151 through ROCm — `small` at `int8` on the GPU does not raise, does not return bad results
    and does not fall back. It aborts the process with *"Memory access fault by GPU node-1"*, taking
    the server and any recording in progress with it. `tiny` and `base` at int8 are fine, and
    `small` at float16 is fine, so it is the combination rather than any one part.

    A guard from a single machine would normally be too broad. Two things justify it. The failure is
    not catchable — no `try` can recover a GPU fault — so the only place to stop it is before the
    load. And it is reachable from the **shipped defaults**: `model="small"`, `device="auto"`,
    `precision="int8"` resolves to exactly this on any AMD machine. Overridable by environment for
    anyone whose hardware does not have the fault.
    """
    if os.environ.get(UNSAFE_OVERRIDE) == "1":
        return ""
    if precision != "int8" or device not in ("cuda", "auto"):
        return ""
    if _detect_hardware()[0] != "rocm":
        return ""
    if model.split("/")[-1].lower() in ROCM_INT8_SAFE_MODELS:
        return ""
    return (
        f"Loading {model} at int8 on an AMD GPU crashes the process with a GPU memory fault "
        "rather than failing cleanly, so it is refused here. Use float16 on the GPU, or int8 on "
        "the CPU — which was measured faster on this hardware anyway."
    )


def repair_kept_wheel() -> str | None:
    """Reinstall the kept ROCm wheel when the environment manager has replaced it.

    ``uv run app.py`` synchronises the environment against the lockfile before Python starts, and
    the lockfile says *PyPI* — so every single launch silently swaps the ROCm build for the CPU/CUDA
    one. Documenting a recovery command does not help when the thing that breaks it is the command
    used to start the application. The launcher therefore puts it back.

    Deliberately narrow. It fires only when all of the following hold, and does nothing otherwise:

    * the machine has an AMD GPU;
    * a wheel matching the installed CTranslate2 version and this interpreter is kept on disk;
    * the installed CTranslate2 did *not* come from that wheel.

    That last check is why this is safe to run on every start. A wheel installed from a file records
    its origin in ``direct_url.json``; one installed from an index does not. Reading it costs a
    single small file, and after a successful repair the condition is false — so this cannot loop.

    Returns the message to print, or ``None`` when there was nothing to do.
    """
    # The deprecated name is still honoured: someone who switched the repair off did so because it
    # broke something for them, and silently switching it back on during a rename would break it
    # again (D-038).
    if "1" in (os.environ.get(REPAIR_OFF), os.environ.get(LEGACY_REPAIR_OFF)):
        return None
    if _detect_hardware()[0] != "rocm":
        return None

    version = _installed_version()
    wheel = _kept_wheel(version) if version else None
    if wheel is None or _installed_from(wheel):
        return None

    executable = shutil.which("uv")
    if executable is None:
        return None

    import subprocess

    logger.info("Restoring the ROCm build of CTranslate2 from %s", wheel)
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable, path from our own directory
            [executable, "pip", "install", "--quiet", "--reinstall", str(wheel)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Could not restore the ROCm build of CTranslate2: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return f"Could not restore the ROCm build of CTranslate2 from {wheel.name}" + (
            f": {detail[-1]}" if detail else "."
        )
    return f"Restored the ROCm build of CTranslate2 from {wheel.name} (uv had replaced it)."


def _installed_version() -> str:
    """The installed CTranslate2's version, read from its metadata rather than by importing it."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ctranslate2")
    except PackageNotFoundError:
        return ""


def _installed_from(wheel: Path) -> bool:
    """Whether the installed CTranslate2 was installed from ``wheel``.

    ``direct_url.json`` is written by the installer for anything installed from a file or URL and
    omitted for anything resolved from an index, which makes its absence a reliable signal that the
    PyPI build is what is present.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        raw = distribution("ctranslate2").read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return False
    if not raw:
        return False
    try:
        url = str(json.loads(raw).get("url", ""))
    except ValueError:
        return False
    return url.endswith(wheel.name)


def _display_path(path: Path) -> str:
    """A path as the user would type it — relative to the repository when it lies inside it."""
    try:
        return f"./{path.relative_to(paths.REPO_ROOT)}"
    except ValueError:
        return str(path)


#: Which package ships each library. Fedora names; other distributions differ, which the remedy
#: says out loud rather than pretending to know every packaging scheme.
_LIBRARY_PACKAGES = {
    "libhiprand.so.1": "hiprand",
    "librocrand.so.1": "rocrand",
    "libhipblas.so.3": "hipblas",
    "librocblas.so.5": "rocblas",
    "libamdhip64.so.7": "rocm-hip",
}


def _package_for(library: str) -> str:
    return _LIBRARY_PACKAGES.get(library, "")


def _ctranslate2_version() -> str:
    try:
        import ctranslate2

        return str(ctranslate2.__version__)
    except Exception:  # noqa: BLE001 - a build that will not import still has a version on disk
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("ctranslate2")
        except PackageNotFoundError:
            return "4.8.1"


def _python_tag() -> str:
    import sys

    return f"{sys.version_info.major}{sys.version_info.minor}"


def describe_lines() -> list[str]:
    """The acceleration report as lines for the startup banner."""
    report = detect()
    lines = [report.summary]
    if report.usable and report.precisions:
        best = "float16" if "float16" in report.precisions else report.precisions[0]
        lines.append(f"Set Run on = GPU and Precision = {best} in Settings → Transcription.")
    lines.extend(f"  {step}" for step in report.remedy)
    return lines


def gpu_env_hint() -> str:
    """Where a container's pip-installed ROCm lives, when that is why libraries are missing.

    TheRock's ``rocm-sdk`` wheels put the runtime inside site-packages rather than on the system
    library path. PyTorch finds it through its own RPATH; CTranslate2 has none, so it needs the
    directory on ``LD_LIBRARY_PATH`` and fails with a bare "cannot open shared object file"
    otherwise — which reads like a missing install rather than a missing path.
    """
    import site

    for base in site.getsitepackages():
        for candidate in ("_rocm_sdk_core/lib", "_rocm_sdk_libraries_gfx1151/lib"):
            path = Path(base) / candidate
            if path.is_dir():
                return os.pathsep.join(
                    str(Path(base) / part)
                    for part in ("_rocm_sdk_core/lib", "_rocm_sdk_libraries_gfx1151/lib")
                    if (Path(base) / part).is_dir()
                )
    return ""
