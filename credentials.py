"""Windows Credential Manager with user-scope DPAPI fallback; no plaintext files."""
import ctypes
from ctypes import wintypes
import getpass
import os
import sys
import warnings

TARGET = ('LongVideoEditor.DashScope' if getattr(sys, 'frozen', False)
          else 'Codex.LongVideoEditor.DashScope.xia')
_SESSION_KEY = ''


class DATA_BLOB(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt_data(raw, decrypt=False):
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    src, dest = DATA_BLOB(len(raw), buf), DATA_BLOB()
    crypt = ctypes.WinDLL('Crypt32.dll', use_last_error=True)
    kernel = ctypes.WinDLL('Kernel32.dll', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                         ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                         wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    function.restype = wintypes.BOOL
    # User scope only. Never use CRYPTPROTECT_LOCAL_MACHINE.
    if not function(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dest)):
        raise OSError(ctypes.get_last_error(), 'Windows用户级加密不可用')
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        ctypes.memset(dest.data, 0, dest.size)
        kernel.LocalFree(dest.data)


def encrypted_path():
    from storage import DATA
    return DATA / 'bailian-key.dpapi'


def key_storage():
    if _SESSION_KEY:
        return '仅本次运行内存；服务重启后需重新填写'
    if os.environ.get('DASHSCOPE_API_KEY'):
        return '进程环境变量'
    if encrypted_path().exists():
        return 'Windows用户级DPAPI加密文件'
    return 'Windows凭据管理器'


class CREDENTIAL(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


def get_key():
    if _SESSION_KEY:
        return _SESSION_KEY
    if os.environ.get('DASHSCOPE_API_KEY'):
        return os.environ['DASHSCOPE_API_KEY'].strip()
    if os.name != 'nt':
        return ''
    path = encrypted_path()
    if path.exists():
        try:
            return crypt_data(path.read_bytes(), decrypt=True).decode('utf-16-le')
        except OSError:
            return ''
    adv = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
    ptr = ctypes.POINTER(CREDENTIAL)()
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    if not adv.CredReadW(TARGET, 1, 0, ctypes.byref(ptr)):
        return ''
    try:
        c = ptr.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode('utf-16-le')
    finally:
        adv.CredFree(ptr)


def set_key(value):
    global _SESSION_KEY
    value = value.strip()
    if not value or '\n' in value or len(value) > 2500:
        raise ValueError('API Key为空或格式不正确')
    if os.name != 'nt':
        raise RuntimeError('当前凭据存储要求Windows')
    raw = value.encode('utf-16-le')
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    cred = CREDENTIAL()
    cred.Type, cred.TargetName, cred.Comment = 1, TARGET, 'Long video editor: DashScope API'
    cred.CredentialBlobSize, cred.CredentialBlob = len(raw), buf
    cred.Persist, cred.UserName = 2, 'DashScope'
    adv = ctypes.WinDLL('Advapi32.dll', use_last_error=True)
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        try:
            encrypted = crypt_data(raw)
        except OSError:
            # Restricted host processes may lack both credential logon and DPAPI profile.
            # Retain only in this server process; no plaintext persistence fallback.
            _SESSION_KEY = value
            return '仅本次运行内存（服务重启后需重新填写）'
        # Verify encryption before committing; never write the original value.
        if crypt_data(encrypted, decrypt=True) != raw:
            raise RuntimeError('Windows用户级加密校验失败，密钥未保存')
        path = encrypted_path()
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(encrypted)
        os.replace(temporary, path)
        _SESSION_KEY = ''
        return 'Windows用户级DPAPI加密文件'
    # Remove an obsolete fallback only after a successful credential write.
    encrypted_path().unlink(missing_ok=True)
    _SESSION_KEY = ''
    return 'Windows凭据管理器'


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'set':
        warnings.simplefilter('error', getpass.GetPassWarning)
        set_key(getpass.getpass('Bailian API Key (hidden): '))
        print('CREDENTIAL_SAVED')
    else:
        print('CREDENTIAL_CONFIGURED' if get_key() else 'CREDENTIAL_NOT_CONFIGURED')
