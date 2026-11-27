"""Locate or prepare the pinned Windows tools used by the private 104319 importer."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


PYTHON_REPO = "https://github.com/leafall903/ORPHEUS.git"
PYTHON_COMMIT = "cedec602c41ee14f158ea9fdd9f16d60e4f51f3d"
OWLIB_REPO = "https://github.com/overtools/OWLib.git"
OWLIB_COMMIT = "7afe5f52706e7002ed8c2717299cd60846f3fa3a"
SUBMODULES = ("TACTLib", "revorbstd")
PROJECT = Path("DataTool/DataTool.csproj")
BUILT_TOOL = Path("DataTool/bin/Release/net6.0/DataTool.exe")


def _program(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"Install {name} and make it available on PATH.")
    return path


def _build_sdk() -> str:
    dotnet = _program("dotnet")
    sdks = subprocess.check_output([dotnet, "--list-sdks"], text=True)
    if not any(int(major) >= 8 for major in re.findall(r"^(\d+)\.", sdks, re.MULTILINE)):
        raise RuntimeError(
            "Install the x64 .NET 8 SDK (or newer) and put its dotnet.exe first on PATH. "
            "The host on PATH reports no suitable SDK. DataTool also runs on the x64 .NET 6 runtime."
        )
    return dotnet


def _msbuild_environment() -> dict[str, str]:
    # MSBuild's Win32 Exec may drop empty environment values. Git then sees an
    # incomplete GIT_CONFIG_COUNT array while describing the local source tree.
    # Fetch/submodule commands continue to inherit the caller's full Git config.
    return {key: value for key, value in os.environ.items()
            if not re.fullmatch(r"GIT_CONFIG_(COUNT|(?:KEY|VALUE)_\d+)", key, re.IGNORECASE)}


def _git(folder: Path, *args: str) -> str:
    return subprocess.check_output(
        [_program("git"), "--no-optional-locks", "-C", str(folder), *args],
        # Windows may drop empty Git config values from the process environment
        # when it is restored. Pass the complete key/value block to the child.
        env=os.environ.copy(), text=True, encoding="utf-8",
    ).strip()


def _origin_matches(value: str, upstream: str) -> bool:
    expected = upstream.removeprefix("https://github.com/").removesuffix(".git").casefold()
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if value.startswith(prefix):
            return value[len(prefix):].rstrip("/").removesuffix(".git").casefold() == expected
    return False


def _verify(folder: Path, upstream: str, commit: str) -> None:
    if not folder.is_dir() or not (folder / ".git").exists():
        raise RuntimeError(f"Expected a Git checkout in {folder}")
    if not _origin_matches(_git(folder, "remote", "get-url", "origin"), upstream):
        raise RuntimeError(f"{folder} must use {upstream} as its Git origin.")
    if _git(folder, "rev-parse", "HEAD") != commit:
        raise RuntimeError(f"Expected {upstream} at {commit} in {folder}.")
    if _git(folder, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=untracked"):
        raise RuntimeError(f"Tracked files in {folder} differ from the pinned sources.")


def _cache_dir(cache: Path) -> Path:
    path = cache.expanduser().resolve()
    if any((p / ".git").exists() or (p / ".build.info").is_file() for p in (path, *path.parents)):
        raise RuntimeError("Choose a tool cache outside Git checkouts and game installations.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _checkout(cache: Path, name: str, upstream: str, commit: str) -> Path:
    cache = _cache_dir(cache)
    target = cache / f"{name}-{commit[:12]}"
    if not target.exists():
        print(f"Fetching {upstream} at {commit[:12]} into the user cache.", flush=True)
        with tempfile.TemporaryDirectory(dir=cache, prefix=f".{name}-") as tmp:
            stage = Path(tmp) / "source"
            stage.mkdir()
            _git(stage, "init", "--quiet")
            _git(stage, "remote", "add", "origin", upstream)
            _git(stage, "config", "remote.origin.promisor", "true")
            _git(stage, "config", "remote.origin.partialclonefilter", "blob:none")
            # No extracted data is needed from either repository. In particular
            # OWLib's optional prepackaged keyring is outside this offline import.
            patterns = ["/*", "!*.keyring"] if upstream == OWLIB_REPO else [
                "/src/", "/ow174/", "/tools/", "/pyproject.toml", "/requirements.txt", "/LICENSE",
            ]
            _git(stage, "sparse-checkout", "set", "--no-cone", "--", *patterns)
            _git(stage, "fetch", "--quiet", "--depth=1", "--filter=blob:none", "origin", commit)
            _git(stage, "checkout", "--quiet", "--detach", commit)
            if upstream == OWLIB_REPO:
                _git(stage, "submodule", "update", "--init", "--depth=1", "--", *SUBMODULES)
            _verify(stage, upstream, commit)
            try:
                stage.rename(target)
            except FileExistsError:
                pass  # A concurrent setup published its complete checkout first.
    _verify(target, upstream, commit)
    return target


def _configured(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


def _verify_tool(exe: Path) -> None:
    if exe.name.casefold() != "datatool.exe" or not exe.is_file():
        raise RuntimeError(f"Expected a built DataTool.exe at {exe}")
    for name in ("DataTool.dll", "TankLib.dll", "TACTLib.dll", "Static/GUIDNames.csv"):
        if not (exe.parent / name).is_file():
            raise RuntimeError(f"DataTool installation lacks {name}: {exe.parent}")
    version = subprocess.check_output(
        [_program("powershell"), "-NoProfile", "-NonInteractive", "-Command",
         "[Diagnostics.FileVersionInfo]::GetVersionInfo($env:ORPHEUS_TOOL_ASSEMBLY).ProductVersion"],
        env={**os.environ, "ORPHEUS_TOOL_ASSEMBLY": str(exe.with_suffix(".dll"))}, text=True,
    ).strip()
    if not re.fullmatch(r"1\.74\.0\.0\+[^+]+\+" + OWLIB_COMMIT[:8], version):
        raise RuntimeError(f"DataTool must be built from OWLib {OWLIB_COMMIT[:12]}; found {version!r}.")


@contextmanager
def _build_lock(cache: Path):
    import msvcrt

    with (cache / ".datatool-build.lock").open("a+b") as stream:
        stream.seek(0)
        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if error.errno != errno.EACCES:
                    raise
                time.sleep(0.25)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _build(cache: Path, source: Path) -> Path:
    target = cache / f"datatool-{OWLIB_COMMIT[:12]}"
    exe = target / "DataTool.exe"
    if not target.exists():
        dotnet = _build_sdk()
        print("Building DataTool. Git, the x64 .NET 8 SDK, .NET 6 runtime and NuGet access are required.", flush=True)
        _git(source, "submodule", "update", "--init", "--depth=1", "--", *SUBMODULES)
        with tempfile.TemporaryDirectory(dir=cache, prefix=".datatool-") as tmp:
            stage = Path(tmp)
            targets = stage / "OfflineExport.targets"
            targets.write_text(
                '<Project><ItemGroup Condition="\'$(MSBuildProjectName)\' == \'TankLib\'">'
                '<Content Remove="Overwatch.keyring" /></ItemGroup></Project>', encoding="utf-8",
            )
            output = stage / "output"
            subprocess.run(
                [dotnet, "build", str(source / PROJECT), "--no-incremental", "-c", "Release", "-o", str(output),
                 f"-p:CustomAfterMicrosoftCommonTargets={targets}"], cwd=source,
                env=_msbuild_environment(), check=True,
            )
            _verify_tool(output / "DataTool.exe")
            output.rename(target)
    return exe


def extraction_tools(cache: Path) -> tuple[Path, Path, Path]:
    """Return the Python checkout, OWLib checkout and DataTool executable."""
    if os.name != "nt":
        raise RuntimeError("The local 104319 extraction tools require Windows.")
    reference = _configured("ORPHEUS_PYTHON_TOOLS")
    if reference is None:
        reference = _checkout(cache, "python", PYTHON_REPO, PYTHON_COMMIT)
    else:
        _verify(reference, PYTHON_REPO, PYTHON_COMMIT)
    owlib = _configured("ORPHEUS_OWLIB")
    if owlib is None:
        owlib = _checkout(cache, "owlib", OWLIB_REPO, OWLIB_COMMIT)
    else:
        _verify(owlib, OWLIB_REPO, OWLIB_COMMIT)
    if not (reference / "tools/prepare_client_catalogs.py").is_file() or not (owlib / PROJECT).is_file():
        raise RuntimeError("The pinned checkout is missing its extraction sources.")
    tool = _configured("ORPHEUS_DATATOOL")
    if tool is None:
        tool = owlib / BUILT_TOOL
        if not tool.is_file():
            # MSBuild writes obj files beside its projects. Only build in our own checkout.
            source = _checkout(cache, "owlib", OWLIB_REPO, OWLIB_COMMIT)
            cache = _cache_dir(cache)
            with _build_lock(cache):
                tool = _build(cache, source)
    _verify_tool(tool)
    return reference, owlib, tool
