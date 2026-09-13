"""Windows Credential Manager references; plaintext credentials never enter YAML."""
import ctypes as C
from ctypes import wintypes as W
import os
import re

class SecretStore:
    PREFIX='LocalAgentAIStation/'
    @staticmethod
    def target(reference):
        if not isinstance(reference,str) or not re.fullmatch(r'[a-zA-Z0-9._/-]{1,160}',reference):
            raise ValueError('Invalid credential reference')
        return SecretStore.PREFIX+reference
    @staticmethod
    def api():
        if os.name!='nt':raise RuntimeError('Windows Credential Manager is unavailable on this platform')
        class Credential(C.Structure):
            _fields_=[('Flags',W.DWORD),('Type',W.DWORD),('TargetName',W.LPWSTR),('Comment',W.LPWSTR),('LastWritten',W.FILETIME),
                ('CredentialBlobSize',W.DWORD),('CredentialBlob',C.POINTER(C.c_ubyte)),('Persist',W.DWORD),('AttributeCount',W.DWORD),
                ('Attributes',C.c_void_p),('TargetAlias',W.LPWSTR),('UserName',W.LPWSTR)]
        api=C.WinDLL('advapi32',use_last_error=True)
        api.CredWriteW.argtypes=[C.POINTER(Credential),W.DWORD]
        api.CredReadW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.POINTER(C.POINTER(Credential))]
        api.CredFree.argtypes=[C.c_void_p]
        api.CredDeleteW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD]
        return api,Credential
    def put(self,reference,value):
        target=self.target(reference);api,cls=self.api();raw=value.encode('utf-8')
        if len(raw)>2560:raise ValueError('Credential exceeds Windows size limit')
        buffer=(C.c_ubyte*len(raw)).from_buffer_copy(raw)
        entry=cls(Type=1,TargetName=target,CredentialBlobSize=len(raw),CredentialBlob=buffer,Persist=2,UserName='Local Agent AI Station')
        try:
            if not api.CredWriteW(C.byref(entry),0):raise OSError(C.get_last_error(),'Credential Manager write failed')
        finally:
            C.memset(buffer,0,len(raw))
    def get(self,reference):
        target=self.target(reference);api,cls=self.api();pointer=C.POINTER(cls)()
        if not api.CredReadW(target,1,0,C.byref(pointer)):
            raise OSError(C.get_last_error(),'Credential reference is unavailable')
        try:return C.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
        finally:api.CredFree(pointer)
    def delete(self,reference):
        target=self.target(reference);api,_=self.api()
        if not api.CredDeleteW(target,1,0) and C.get_last_error()!=1168:
            raise OSError(C.get_last_error(),'Credential Manager delete failed')

secret_store=SecretStore()
