#!/usr/bin/env python3
"""Read-only deployment preparation checks; never print configuration or command output."""
import sys
sys.dont_write_bytecode = True

import argparse
import os
from pathlib import Path
import platform
import re
import stat
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import check_env

PROJECT = "hwp-assistant-prod"
VOLUMES = ("hwp-assistant-prod-metadata", "hwp-assistant-prod-documents")
COMMAND_TIMEOUT = 5


def read_command(arguments):
    """Only callers below construct commands; shell execution is never used."""
    try:
        result = subprocess.run(arguments, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=COMMAND_TIMEOUT,
                                check=False, shell=False)
        if result.returncode or len(result.stdout) > 65536:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def diagnose(env_file, *, command=None):
    command = command or read_command
    findings = []

    def add(status, name, message):
        findings.append((status, name, message))

    supported = platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")
    add("PASS" if supported else "BLOCKED", "platform",
        "Linux x86_64 배포 대상입니다." if supported else "Linux x86_64 홈서버에서 다시 실행하세요.")
    uid = getattr(os, "geteuid", lambda: None)()
    add("PASS" if uid not in (None, 0) else "BLOCKED", "runner-user",
        "현재 계정은 비root입니다. 실제 runner 서비스도 이 계정으로 확인하세요."
        if uid not in (None, 0) else "전용 비root runner 서비스 계정으로 다시 진단하세요. root 검사는 해당 계정의 접근권한을 증명하지 않습니다.")

    path = Path(env_file).expanduser()
    try:
        details = path.stat()
        regular = stat.S_ISREG(details.st_mode)
        private = stat.S_IMODE(details.st_mode) == 0o600
        readable = regular and os.access(path, os.R_OK)
    except OSError:
        regular = private = readable = False
        details = None
    add("PASS" if regular else "BLOCKED", "env-file",
        "기존 설정 파일을 확인했습니다." if regular else "설정 파일을 확인할 수 없습니다. 기존 운영 설정의 위치를 먼저 확인하고 재발급하지 마세요.")
    add("PASS" if private else "BLOCKED", "env-mode",
        "설정 파일 권한이 600입니다." if private else "기존 설정 내용과 소유자를 보존하며 권한 600을 확인하세요. 그룹 공개로 해결하지 마세요.")
    owner = details is not None and uid is not None and details.st_uid == uid
    add("PASS" if readable and owner else "BLOCKED", "env-access",
        "현재 계정이 설정 파일을 소유하고 읽을 수 있습니다." if readable and owner
        else "runner 계정의 설정 파일 소유권과 읽기 권한을 확인하세요. 진단기는 소유권을 변경하지 않습니다.")
    if regular and private and readable:
        try:
            check_env(path)
        except Exception:
            add("BLOCKED", "env-validation", "설정 검증이 실패했습니다. 운영 설정을 서버 로컬에서 확인하세요. 값과 예외 내용은 출력하지 않습니다.")
        else:
            add("PASS", "env-validation", "기존 설정의 형식 검증을 통과했습니다. DB 접속 성공을 뜻하지는 않습니다.")

    for name, folder in (("parent-access", path.parent), ("state-access", path.parent / "state"),
                         ("backups-access", path.parent / "backups")):
        try:
            exists = folder.exists()
            usable = folder.is_dir() and os.access(folder, os.R_OK | os.W_OK | os.X_OK)
            owned = name == "parent-access" or (exists and folder.stat().st_uid == uid)
        except OSError:
            exists = usable = owned = False
        if not exists and name != "parent-access":
            add("MANUAL", name, "운영 보조 폴더가 없습니다. 기존 운영 경로를 먼저 확인하세요. 진단기는 폴더를 생성하지 않습니다.")
        else:
            add("PASS" if usable and owned else "BLOCKED", name,
                "현재 계정의 폴더 접근·쓰기 및 필요한 소유권을 확인했습니다."
                if usable and owned else "runner 계정의 폴더 접근·쓰기와 소유권을 확인하세요. 배포·백업은 이 위치를 사용합니다.")

    docker = command(["docker", "info", "--format", "{{.OSType}}"])
    docker_ok = docker == "linux"
    add("PASS" if docker_ok else "BLOCKED", "docker",
        "Linux Docker 엔진 조회에 성공했습니다. Docker 권한은 호스트 관리자에 준합니다."
        if docker_ok else "Docker 명령·현재 계정 접근·Linux 엔진 상태를 확인하세요. 조회 오류 내용은 출력하지 않습니다.")
    compose = command(["docker", "compose", "version", "--short"])
    version = re.match(r"^v?(\d+)\.(\d+)(?:\.|$)", compose or "")
    compose_ok = version is not None and tuple(map(int, version.groups())) >= (2, 24)
    add("PASS" if compose_ok else "BLOCKED", "compose",
        "Docker Compose v2.24 이상을 확인했습니다." if compose_ok else "Docker Compose v2.24 이상의 설치·실행 접근을 확인하세요.")

    if docker_ok:
        for name, volume in zip(("metadata-volume", "documents-volume"), VOLUMES):
            found = command(["docker", "volume", "inspect", "--format", "{{.Name}}", volume])
            add("PASS" if found == volume else "BLOCKED", name,
                "예정된 운영 볼륨을 확인했습니다." if found == volume
                else "예정된 운영 볼륨을 확인할 수 없습니다. 기존 데이터 위치와 인수 여부를 검토하세요. 새 볼륨을 자동 생성하지 않습니다.")
        listing = command(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + PROJECT,
                           "--format", '{{.ID}}|{{.Label "com.docker.compose.service"}}|{{.State}}'])
        services = {name: [] for name in ("db", "api", "web")}
        valid_listing = listing is not None
        for line in (listing or "").splitlines():
            fields = line.split("|")
            if len(fields) != 3 or not re.fullmatch(r"[0-9a-f]{12,64}", fields[0]):
                valid_listing = False
                break
            identifier, service, state = fields
            if service in services:
                services[service].append((identifier, state))
        if not valid_listing:
            add("BLOCKED", "project", "기존 운영 프로젝트 조회를 확인할 수 없습니다. 서버 로컬에서 대상 프로젝트를 확인하세요.")
        else:
            for service, entries in services.items():
                if not entries:
                    add("MANUAL", service + "-service", "예정된 프로젝트의 서비스가 없습니다. 빈 서버인지 다른 프로젝트가 운영 중인지 먼저 확인하세요.")
                elif len(entries) > 1:
                    add("BLOCKED", service + "-service", "같은 서비스의 컨테이너가 여러 개입니다. 실제 운영 대상을 수동으로 확인하세요.")
                else:
                    add("PASS" if entries[0][1] == "running" else "MANUAL", service + "-service",
                        "예정된 프로젝트의 실행 중 컨테이너를 확인했습니다. 건강 상태 검증은 별도입니다."
                        if entries[0][1] == "running" else "예정된 서비스가 실행 중이 아닙니다. 현재 운영 상태를 확인하세요. 진단기는 시작하지 않습니다.")
            for service, target, expected in (("db", "/var/lib/postgresql/data", VOLUMES[0]),
                                              ("api", "/data/files", VOLUMES[1])):
                if len(services[service]) != 1:
                    continue
                template = '{{range .Mounts}}{{if eq .Destination "' + target + '"}}{{.Type}}|{{.Name}}{{end}}{{end}}'
                mount = command(["docker", "inspect", "--format", template, services[service][0][0]])
                add("PASS" if mount == "volume|" + expected else "BLOCKED", service + "-storage",
                    "기존 서비스의 데이터 mount가 예정된 운영 볼륨과 일치합니다." if mount == "volume|" + expected
                    else "기존 서비스의 데이터 mount가 예정된 볼륨과 일치한다고 확인되지 않았습니다. 데이터 인수를 먼저 검토하고 교체하지 마세요.")

    add("MANUAL", "port-owner", "23000 포트의 실제 소유자와 기존 프록시 경로는 서버 관리자가 확인해야 합니다. 이 진단은 포트 점유를 추정하지 않습니다.")
    add("MANUAL", "runner-github", "GitHub의 runner Online·라벨 및 실제 배포 승인 기능을 확인하세요. 이 진단은 등록·승인 설정을 조회하거나 변경하지 않습니다.")
    add("MANUAL", "data-preservation", "기존 설정·DB·문서의 보존과 백업을 확인한 뒤 배포하세요. 초기화·데이터 이전·서비스 교체는 실행하지 않았습니다.")
    return findings


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "BLOCKED arguments: --env-file 경로만 지정하세요. 입력값은 출력하지 않습니다.\n")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--env-file", default="/opt/hwp-assistant/production.env")
    args = parser.parse_args(argv)
    try:
        findings = diagnose(args.env_file)
    except Exception:
        print("BLOCKED diagnosis: 준비 상태를 확인하지 못했습니다. 서버 로컬에서 경로·접근권한을 확인하세요. 예외 내용은 출력하지 않습니다.")
        return 2
    for status, name, message in findings:
        print(f"{status} {name}: {message}")
    return 2 if any(status == "BLOCKED" for status, _, _ in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
