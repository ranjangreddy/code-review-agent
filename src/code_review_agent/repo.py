"""Loading a codebase from a local folder or a public GitHub URL."""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rb", ".php", ".cs",
    ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".sql", ".sh",
    ".prisma", ".vue", ".svelte",
}
SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "out", ".next", ".nuxt", "coverage",
    "venv", ".venv", "env", "__pycache__", ".idea", ".vscode", "vendor", ".gradle", "uploads",
}
SKIP_FILE_PATTERNS = (re.compile(r"\.min\.(js|css)$"), re.compile(r"(^|/)package-lock\.json$"))
MAX_FILE_BYTES = 60_000

GITHUB_URL = re.compile(r"^https?://github\.com/[\w.-]+/[\w.-]+/?$")


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative, forward slashes
    content: str

    @property
    def lines(self) -> int:
        return self.content.count("\n") + 1


def resolve_target(target: str) -> Path:
    """Return a local directory for ``target``, shallow-cloning it first if it's a GitHub URL."""
    if GITHUB_URL.match(target.strip()):
        dest = Path(tempfile.mkdtemp(prefix="code-review-"))
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", target.strip(), str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
        return dest
    path = Path(target).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Not a directory or GitHub repo URL: {target}")
    return path


def load_source_files(root: Path) -> list[SourceFile]:
    """Every reviewable source file under ``root``, skipping dependencies, builds and huge files."""
    files: list[SourceFile] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        rel_str = rel.as_posix()
        if any(p.search(rel_str) for p in SKIP_FILE_PATTERNS):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if content.strip():
            files.append(SourceFile(rel_str, content))
    return files


def with_line_numbers(file: SourceFile) -> str:
    """Numbered code, so reviewers can cite exact lines."""
    numbered = "\n".join(f"{i:>4} | {line}" for i, line in enumerate(file.content.splitlines(), 1))
    return f"### FILE: {file.path}\n{numbered}"


def make_batches(files: list[SourceFile], max_chars: int) -> list[list[SourceFile]]:
    """Greedily pack files into batches that fit one reviewer prompt."""
    batches: list[list[SourceFile]] = []
    current: list[SourceFile] = []
    size = 0
    for f in files:
        cost = len(f.content) + 200
        if current and size + cost > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(f)
        size += cost
    if current:
        batches.append(current)
    return batches
