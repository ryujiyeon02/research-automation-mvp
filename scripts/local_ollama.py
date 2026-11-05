"""Install a checksum-pinned official macOS runtime, or serve it on project port 11435.

Reuses ~/.ollama/models; never downloads a model or changes the Homebrew installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

VERSION = "0.40.14"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-darwin.tgz"
SHA256 = "f3bf9a528e908da0da5a2b3a4ad3252a369d62186501dc4880261e8d9023bff5"
ROOT = Path(__file__).resolve().parents[1] / "data" / "ollama_runtime" / f"v{VERSION}"


def install():
    if platform.system() != "Darwin":
        raise RuntimeError("이 설치 도구는 macOS용입니다. 다른 OS는 Ollama 공식 설치본을 사용하세요.")
    receipt = ROOT / "runtime_receipt.json"
    if (receipt.exists() and (ROOT / "ollama").is_file()
            and json.loads(receipt.read_text())["sha256"] == SHA256):
        print(f"Pinned release already installed: {ROOT}")
        return
    if ROOT.exists():
        raise RuntimeError(f"설치 기록을 확인할 수 없는 기존 폴더가 있습니다: {ROOT}")
    ROOT.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT.parent).free < 1_000_000_000:
        raise RuntimeError("실행 파일 설치를 위해 임시 공간을 포함해 1GB 이상 필요합니다.")
    with tempfile.TemporaryDirectory(prefix=".ollama-install-", dir=ROOT.parent) as scratch:
        temporary = Path(scratch)
        archive = temporary / "release.tgz"
        checksum = hashlib.sha256()
        with httpx.stream("GET", URL, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            with archive.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    checksum.update(chunk)
                    output.write(chunk)
        if checksum.hexdigest() != SHA256:
            raise RuntimeError("공식 배포 파일 체크섬이 일치하지 않아 설치하지 않았습니다.")
        extracted = temporary / "runtime"
        extracted.mkdir()
        files, size = 0, 0
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle:
                destination = (extracted / member.name).resolve()
                if not destination.is_relative_to(extracted):
                    raise RuntimeError("배포 파일에 허용되지 않은 경로가 있습니다.")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    destination.chmod(member.mode & 0o777)
                    files += 1
                    size += member.size
                elif member.issym():
                    if not (destination.parent / member.linkname).resolve().is_relative_to(extracted):
                        raise RuntimeError("배포 파일의 링크가 설치 폴더 밖을 가리킵니다.")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(member.linkname)
        if not (extracted / "ollama").is_file():
            raise RuntimeError("배포 파일에 Ollama 실행 파일이 없습니다.")
        (extracted / "runtime_receipt.json").write_text(json.dumps({
            "version": VERSION, "source": URL, "sha256": SHA256,
            "files": files, "extracted_bytes": size,
        }, indent=2))
        extracted.rename(ROOT)
    print(f"Installed official runtime: {ROOT} ({size / 1_000_000:.0f} MB); no model downloaded")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "serve"))
    args = parser.parse_args()
    if args.action == "install":
        install()
    else:
        executable = ROOT / "ollama"
        if not executable.is_file():
            raise RuntimeError("먼저 이 도구의 install 명령을 실행하세요.")
        env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_NUM_PARALLEL": "1",
               "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_CONTEXT_LENGTH": "8192"}
        os.execve(str(executable), [str(executable), "serve"], env)


if __name__ == "__main__":
    main()
