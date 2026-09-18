"""Persist progress without replacing previous runs."""
import json
import os
import tempfile
from pathlib import Path


class PersistenceError(RuntimeError):
    pass


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ResultStore:
    def __init__(self, path):
        self.path = Path(path)
        self.txt_path = self.path.with_suffix('.txt')
        if self.path == self.txt_path:
            raise ValueError('结果文件请使用 .json 扩展名，不能与 TXT 文件重名')
        self.records = []
        if self.path.exists():
            self.records = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if not isinstance(self.records, list) or not all(isinstance(r, dict) for r in self.records):
                raise ValueError('现有结果文件格式错误，停止运行以免覆盖')
        elif self.txt_path.exists():
            raise ValueError('存在同名 TXT，请换一个输出文件名以免覆盖')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, result):
        try:
            self._save(result)
        except (OSError, TypeError, ValueError) as exc:
            raise PersistenceError('结果保存失败，已停止后续注册：' + str(exc)) from exc

    def _save(self, result):
        record = dict(result)
        for i, old in enumerate(self.records):
            if record.get('attempt_id') and old.get('attempt_id') == record['attempt_id']:
                self.records[i] = record
                break
        else:
            self.records.append(record)
        atomic_write(self.path, json.dumps(self.records, ensure_ascii=False, indent=2))
        lines = ['# 邮箱 | 密码 | 状态 | token（包含凭据，请妥善保管）']
        lines.extend(' | '.join(str(r.get(k) or '') for k in ('email', 'password', 'status', 'token'))
                     for r in self.records)
        atomic_write(self.txt_path, '\n'.join(lines) + '\n')
