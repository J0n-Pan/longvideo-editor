import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import credentials


def test_no_plaintext_file_when_windows_secure_storage_unavailable(monkeypatch, tmp_path):
    class UnavailableWrite:
        def __call__(self, *args):
            return False
    monkeypatch.setattr(credentials.ctypes, 'WinDLL',
                        lambda *a, **k: SimpleNamespace(CredWriteW=UnavailableWrite()))
    def fail(*args, **kwargs):
        raise OSError(2, 'test profile unavailable')
    monkeypatch.setattr(credentials, 'crypt_data', fail)
    monkeypatch.setattr(credentials, 'encrypted_path', lambda: tmp_path / 'key.dpapi')
    monkeypatch.setattr(credentials, '_SESSION_KEY', '')
    marker = 'local-test-marker-not-a-real-credential'
    mode = credentials.set_key(marker)
    assert '内存' in mode
    assert credentials.get_key() == marker
    assert not list(tmp_path.iterdir())
