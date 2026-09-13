"""DXGI adapter enumeration (including AMD/Intel); no guessed VRAM usage."""
import ctypes as C
from ctypes import wintypes as W
import uuid

class LUID(C.Structure):
    _fields_ = [('LowPart', W.DWORD), ('HighPart', W.LONG)]

class AdapterDesc(C.Structure):
    _fields_ = [('Description', W.WCHAR * 128), ('VendorId', W.UINT),
        ('DeviceId', W.UINT), ('SubSysId', W.UINT), ('Revision', W.UINT),
        ('DedicatedVideoMemory', C.c_size_t), ('DedicatedSystemMemory', C.c_size_t),
        ('SharedSystemMemory', C.c_size_t), ('AdapterLuid', LUID), ('Flags', W.UINT)]

class OutputDesc(C.Structure):
    _fields_ = [('DeviceName', W.WCHAR * 32), ('DesktopCoordinates', W.RECT),
        ('AttachedToDesktop', W.BOOL), ('Rotation', W.UINT), ('Monitor', W.HANDLE)]

def method(ptr, index, result, *types):
    table = C.cast(ptr, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *types)(table[index])

def release(ptr):
    method(ptr, 2, W.ULONG)(ptr)

def enumerate_adapters():
    factory = C.c_void_p()
    iid = (C.c_ubyte * 16).from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    dll = C.WinDLL('dxgi.dll')
    create = dll.CreateDXGIFactory1
    create.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
    create.restype = W.LONG
    if create(C.byref(iid), C.byref(factory)) < 0:
        raise RuntimeError('DXGI factory unavailable')
    rows = []
    try:
        index = 0
        while True:
            adapter = C.c_void_p()
            if method(factory, 12, W.LONG, W.UINT, C.POINTER(C.c_void_p))(factory, index, C.byref(adapter)) < 0:
                break
            try:
                desc = AdapterDesc()
                if method(adapter, 10, W.LONG, C.POINTER(AdapterDesc))(adapter, C.byref(desc)) < 0:
                    continue
                if desc.Flags & 2:  # Software renderer is not a GPU.
                    continue
                vendor = {0x10DE: 'NVIDIA', 0x1002: 'AMD', 0x8086: 'Intel'}.get(desc.VendorId, 'Other')
                luid = f'{desc.AdapterLuid.HighPart & 0xffffffff:08x}:{desc.AdapterLuid.LowPart:08x}'
                attached = False
                output_index = 0
                while True:
                    output = C.c_void_p()
                    if method(adapter, 7, W.LONG, W.UINT, C.POINTER(C.c_void_p))(adapter, output_index, C.byref(output)) < 0:
                        break
                    try:
                        info = OutputDesc()
                        if method(output, 7, W.LONG, C.POINTER(OutputDesc))(output, C.byref(info)) >= 0:
                            attached = attached or bool(info.AttachedToDesktop)
                    finally:
                        release(output)
                    output_index += 1
                rows.append(dict(index=index, uuid='DXGI-' + luid, dxgi_luid=luid,
                    name=desc.Description, vendor=vendor, pci_bus_id='', driver_mode='WDDM',
                    pending_driver_mode='WDDM', is_tcc=False, tcc_supported=False if vendor != 'NVIDIA' else None,
                    display_active=attached, vram_total_mib=desc.DedicatedVideoMemory // 1048576,
                    vram_free_mib=None, vram_used_mib=None, vram_used_pct=None,
                    temp_c=None, load_percent=None, nvlink_active=False, nvlink_links=0))
            finally:
                release(adapter)
                index += 1
    finally:
        release(factory)
    return rows
