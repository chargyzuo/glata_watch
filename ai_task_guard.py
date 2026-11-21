"""AI 任务幂等保护与并发安全存储，只锁同一任务，不串行不同工单。"""
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path


class TaskBusy(RuntimeError):
    pass


class StateError(RuntimeError):
    pass


def task_key(ticket_no=None, transfer_time=None, chat_id=None, since=None):
    # 时间参与键，避免把复用同一值班号群的新问题当成旧任务。
    identity = [str(ticket_no or chat_id or ""), str(transfer_time or since or "")]
    return "ai_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]


def digest(key):
    return hashlib.sha256(str(key).encode()).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        raise StateError("AI 状态无法读取或已损坏，未清空覆盖；请检查本机文件") from None
    if not isinstance(data, dict):
        raise StateError("AI 状态必须为 JSON 对象，未覆盖")
    return data


def merge_json(path, updates):
    """先加锁再读当前值，只合并本次更新；原子替换防止其他工单会话丢失。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(path)
        data.update(updates)
        atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


@contextlib.contextmanager
def task_lock(directory, key):
    path = Path(directory) / (".ai_task_" + digest(key) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TaskBusy("同一轮工单仍有 AI 进程运行，本次不重复调用") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def receipt_path(directory, key):
    return Path(directory) / (".ai_done_" + digest(key) + ".json")


def completed_report(directory, key):
    receipt = read_json(receipt_path(directory, key))
    if not receipt:
        return None
    name = receipt.get("report_name") or ""
    # 本地状态不能使调用方读/写到报告目录之外。
    if Path(name).name != name or not name.endswith("_summary.md"):
        raise StateError("AI 完成凭据中的报告路径无效，未重新调用")
    path = Path(directory) / name
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        raise StateError("AI 已完成但报告缺失，请先检查本机产物；未自动重跑") from None
    if hashlib.sha256(content).hexdigest() != receipt.get("sha256"):
        raise StateError("AI 完成报告校验不一致，未自动重跑")
    return str(path)


def record_completed(directory, key, report):
    report = Path(report)
    atomic_text(receipt_path(directory, key), json.dumps({
        "report_name": report.name,
        "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    }, ensure_ascii=False) + "\n")
