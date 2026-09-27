import hashlib
import io
import zipfile
from pathlib import PurePosixPath
from defusedxml import ElementTree as ET
from fastapi import HTTPException

MAX_FILE = 30 * 1024 * 1024
MAX_EXPANDED = 120 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate(data: bytes):
    if not data or len(data) > MAX_FILE:
        raise HTTPException(413, "HWPX는 30MB 이하만 허용됩니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if len(names) > 3000 or len(names) != len(set(names)):
                raise ValueError("중복 항목 또는 너무 많은 ZIP 항목")
            if sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise ValueError("압축 해제 한도 초과")
            for i in infos:
                p = PurePosixPath(i.filename)
                if (
                    p.is_absolute()
                    or ".." in p.parts
                    or "\\" in i.filename
                    or ":" in i.filename
                    or i.flag_bits & 1
                ):
                    raise ValueError("안전하지 않은 ZIP 경로/암호화")
                if (
                    i.file_size > MAX_FILE
                    or i.file_size / max(i.compress_size, 1) > 200
                ):
                    raise ValueError("과도한 압축률/항목 크기")
            if not {
                "mimetype",
                "Contents/header.xml",
                "Contents/section0.xml",
                "Contents/content.hpf",
            }.issubset(names):
                raise ValueError("필수 HWPX 구성 누락")
            if z.read("mimetype").strip() != b"application/hwp+zip":
                raise ValueError("HWPX mimetype 불일치")
            roots = {}
            for i in infos:
                raw = z.read(
                    i
                )  # CRC checked, bounded by limits above; never extracted to paths
                if i.filename.endswith((".xml", ".hpf")):
                    roots[i.filename] = ET.fromstring(raw, forbid_dtd=True)
            return names, roots
    except (ValueError, zipfile.BadZipFile, RuntimeError, ET.ParseError) as exc:
        raise HTTPException(422, f"HWPX 검사 실패: {exc}") from exc
    except Exception as exc:
        raise HTTPException(
            422, "HWPX XML 또는 압축파일을 안전하게 읽을 수 없습니다."
        ) from exc


def loss_warnings(before: bytes, after: bytes):
    old_names, old_roots = validate(before)
    new_names, new_roots = validate(after)
    # Conservative guard for resources and unsupported objects. Not a compatibility proof.
    missing = [n for n in old_names if n.startswith("BinData/") and n not in new_names]
    for tag in ("ole", "chart", "video", "equation"):
        count = lambda roots: sum(
            1
            for root in roots.values()
            for e in root.iter()
            if e.tag.split("}")[-1] == tag
        )
        if count(new_roots) < count(old_roots):
            missing.append(tag)
    return missing
