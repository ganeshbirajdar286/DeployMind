import ast
import os
import sys
from pathlib import Path
from typing import Set, List, Dict, Tuple

# Built-in Python standard library modules (Python 3.10 - 3.14)
STD_LIB_MODULES = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "bdb", "binascii",
    "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath",
    "cmd", "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy", "copyreg",
    "cProfile", "crypt", "csv", "ctypes", "curses", "dataclasses", "datetime",
    "dbm", "decimal", "difflib", "dis", "distutils", "doctest", "email",
    "encodings", "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt", "getpass",
    "gettext", "glob", "graphlib", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect", "io",
    "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache", "locale",
    "logging", "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
    "mmap", "modulefinder", "msilib", "msvcrt", "multiprocessing", "netrc",
    "nis", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev",
    "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile", "pstats",
    "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random",
    "re", "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched",
    "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal",
    "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
    "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl", "stat",
    "statistics", "string", "stringprep", "struct", "subprocess", "sunau",
    "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib",
    "tempfile", "termios", "test", "textwrap", "threading", "time", "timeit",
    "tkinter", "token", "tokenize", "trace", "traceback", "tracemalloc", "tty",
    "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest",
    "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
    "zipimport", "zlib", "_thread"
}

# Mapping from Python import names to actual PyPI package names
IMPORT_TO_PYPI = {
    "whisper": "openai-whisper",
    "yt_dlp": "yt-dlp",
    "cv2": "opencv-python-headless",
    "PIL": "pillow",
    "dotenv": "python-dotenv",
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "jwt": "pyjwt",
    "telegram": "python-telegram-bot",
    "google.genai": "google-genai",
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "pydub": "pydub",
    "moviepy": "moviepy",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
}

# System tools required by specific Python packages
PACKAGE_TO_SYSTEM_DEPS = {
    "whisper": ["ffmpeg"],
    "openai-whisper": ["ffmpeg"],
    "pydub": ["ffmpeg"],
    "moviepy": ["ffmpeg"],
    "librosa": ["ffmpeg", "libsndfile1"],
    "torchaudio": ["ffmpeg"],
    "cv2": ["libgl1", "libglib2.0-0"],
    "psycopg2": ["libpq-dev"],
}


def scan_python_imports(directory: Path) -> Tuple[Set[str], Set[str]]:
    """Scans all Python files using AST to detect imported packages and system dependencies."""
    directory = Path(directory).resolve()
    imported_modules: Set[str] = set()
    system_deps: Set[str] = set()

    for py_file in directory.rglob("*.py"):
        # Ignore virtualenvs and caches
        if any(ignored in str(py_file) for ignored in [".venv", "venv", "__pycache__", "site-packages", "node_modules"]):
            continue

        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0]
                        imported_modules.add(top_pkg)
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        top_pkg = node.module.split(".")[0]
                        imported_modules.add(top_pkg)
        except Exception:
            continue

    # Identify local project directories and files to exclude from third-party PyPI packages
    local_modules: Set[str] = set()
    search_dirs = [directory]
    if directory.parent and directory.parent.exists() and directory.parent != directory:
        search_dirs.append(directory.parent)
    for s_dir in search_dirs:
        try:
            for item in s_dir.iterdir():
                if item.is_dir() and not item.name.startswith((".", "_")):
                    local_modules.add(item.name.lower())
                elif item.is_file() and item.suffix == ".py":
                    local_modules.add(item.stem.lower())
        except Exception:
            pass

    # Filter out standard library modules and local project packages
    third_party_imports = {
        pkg for pkg in imported_modules
        if pkg not in STD_LIB_MODULES and pkg.lower() not in local_modules
    }

    # Map to PyPI packages and system requirements
    pypi_packages: Set[str] = set()
    for mod in third_party_imports:
        pypi_name = IMPORT_TO_PYPI.get(mod, mod)
        pypi_packages.add(pypi_name)

        # Check for system dependencies like ffmpeg
        if mod in PACKAGE_TO_SYSTEM_DEPS:
            system_deps.update(PACKAGE_TO_SYSTEM_DEPS[mod])
        if pypi_name in PACKAGE_TO_SYSTEM_DEPS:
            system_deps.update(PACKAGE_TO_SYSTEM_DEPS[pypi_name])

    # Check for frameworks or features in content that imply extra requirements
    for py_file in directory.rglob("*.py"):
        if any(ignored in str(py_file) for ignored in [".venv", "venv", "__pycache__", "site-packages", "node_modules"]):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if "ImageField" in content and "pillow" not in pypi_packages:
                pypi_packages.add("pillow")
            if ("django.db.backends.postgresql" in content or "psycopg2" in content) and "psycopg2-binary" not in pypi_packages:
                pypi_packages.add("psycopg2-binary")
                system_deps.add("libpq-dev")
        except Exception:
            continue

    return pypi_packages, system_deps
