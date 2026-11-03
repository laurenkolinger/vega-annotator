"""Choose where SAM3 runs on this computer.

The numbers behind the rules were measured on 2 Nov 2026 on an M3 Max with VEGA's
own engine and 8 real masks from a labeling project:

- Mac GPU, bfloat16: 1.4 s per frame, about 9 GB of memory during encoding. It ran
  out of memory under a simulated 8 GB limit, and Find similar ran out under a
  simulated 16 GB limit.
- CPU, float32: 3.6 s per frame on 4 threads, 2.2 GB of memory.
- CPU, bfloat16: 67 s per frame, so bfloat16 is never used on a CPU.

Device strings carry an index ("mps:0", "cpu:0") because VEGA's engine reads the
last character of the string as a card number when it logs GPU memory.
"""

import os
import platform as platform_module
import subprocess
from dataclasses import asdict, dataclass

MAC_GPU_MIN_RAM_GB = 24
NVIDIA_MIN_VRAM_GB = 4
NVIDIA_EXEMPLAR_MIN_VRAM_GB = 12
EXEMPLAR_WARMUP_MIN_RAM_GB = 24
BYTES_PER_GB = 1024 ** 3  # A computer sold as 16 GB has 16 x 1024^3 bytes.
APPLE_MAX_THREADS = 6
OTHER_MAX_THREADS = 8
MIN_THREADS = 2
NVIDIA_SMI_TIMEOUT_S = 10
SYSCTL_TIMEOUT_S = 3
GPU_DTYPE = "bfloat16"
CPU_DTYPE = "float32"


@dataclass(frozen=True)
class MachineFacts:
    """What the device rules need to know about a computer.

    Attributes:
        system: "Darwin", "Windows" or "Linux".
        machine: "arm64", "x86_64", "AMD64" and so on.
        ram_gb: installed memory in GB.
        physical_cores: CPU cores to size the thread count from; on an Apple
            chip, the performance cores only.
        nvidia_vram_gb: memory of the largest NVIDIA card, 0 when there is none.
        nvidia_name: the card's name, "" when there is none.
    """

    system: str
    machine: str
    ram_gb: float
    physical_cores: int
    nvidia_vram_gb: float = 0
    nvidia_name: str = ""


@dataclass(frozen=True)
class DevicePlan:
    """Where and how SAM3 runs.

    Attributes:
        tracker: device string for the click and box model.
        exemplar: device string for the Find similar model.
        tracker_dtype: "bfloat16" or "float32".
        threads: CPU threads for PyTorch.
        warm_exemplar: whether to load the Find similar model at start instead
            of at its first use.
        label: the one line the home screen shows.
    """

    tracker: str
    exemplar: str
    tracker_dtype: str
    threads: int
    warm_exemplar: bool
    label: str

    def as_dict(self):
        """Return the plan as a plain dict for JSON."""
        return asdict(self)


def _sysctl_int(name):
    """Return one integer sysctl value, or None when it is unavailable."""
    try:
        result = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                                timeout=SYSCTL_TIMEOUT_S, check=False)
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _windows_ram_bytes():
    """Return installed memory on Windows, or None when the call fails."""
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys
    except (OSError, AttributeError, ImportError):
        pass
    return None


def parse_nvidia_smi(output):
    """Find the largest card in nvidia-smi's CSV output.

    Parameters:
        output: text with one "name, memory in MiB" line per card.
    Returns:
        tuple: (name, memory in GB); ("", 0) when no line parses.
    """
    best = ("", 0.0)
    for line in (output or "").splitlines():
        parts = [part.strip() for part in line.rsplit(",", 1)]
        if len(parts) != 2:
            continue
        try:
            vram = float(parts[1].split()[0]) / 1024
        except (ValueError, IndexError):
            continue
        if vram > best[1]:
            best = (parts[0], vram)
    return best


def _nvidia_card():
    """Ask nvidia-smi for the largest card, returning ("", 0) when there is none."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return "", 0.0
    return parse_nvidia_smi(result.stdout) if result.returncode == 0 else ("", 0.0)


def read_machine_facts():
    """Read this computer's facts without importing PyTorch, so the home screen stays quick.

    Returns:
        MachineFacts: the facts; a value that cannot be read is 0.
    """
    system = platform_module.system()
    machine = platform_module.machine()
    logical = os.cpu_count() or 2
    cores = max(logical // 2, 1)
    ram_bytes = None
    if system == "Darwin":
        ram_bytes = _sysctl_int("hw.memsize")
        cores = _sysctl_int("hw.perflevel0.physicalcpu") or cores
    elif system == "Windows":
        ram_bytes = _windows_ram_bytes()
    elif hasattr(os, "sysconf"):
        try:
            ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (OSError, ValueError):
            ram_bytes = None
    name, vram = ("", 0.0) if system == "Darwin" else _nvidia_card()
    return MachineFacts(system=system, machine=machine, ram_gb=(ram_bytes or 0) / BYTES_PER_GB,
                        physical_cores=cores, nvidia_vram_gb=vram, nvidia_name=name)


def _thread_count(facts):
    """Return the CPU thread count for these facts."""
    apple = facts.system == "Darwin" and facts.machine == "arm64"
    ceiling = APPLE_MAX_THREADS if apple else OTHER_MAX_THREADS
    return max(MIN_THREADS, min(facts.physical_cores, ceiling))


def _cpu_plan(facts, label):
    """Return the CPU plan with the given home screen line."""
    return DevicePlan(tracker="cpu:0", exemplar="cpu:0", tracker_dtype=CPU_DTYPE,
                      threads=_thread_count(facts),
                      warm_exemplar=facts.ram_gb >= EXEMPLAR_WARMUP_MIN_RAM_GB, label=label)


def plan_for(facts, preference="auto"):
    """Apply the device rules.

    Parameters:
        facts: a MachineFacts.
        preference: "auto", "cpu", or "gpu". "gpu" uses the graphics chip even
            below the memory thresholds; "cpu" never uses it.
    Returns:
        DevicePlan: the plan.
    Raises:
        ValueError: on an unknown preference.
    """
    if preference not in ("auto", "cpu", "gpu"):
        raise ValueError(f"Unknown device preference: {preference}")
    if preference == "cpu":
        return _cpu_plan(facts, "SAM3 runs on the processor, as chosen in settings.")
    threads = _thread_count(facts)
    if facts.system == "Darwin" and facts.machine == "arm64":
        if preference == "gpu" or facts.ram_gb >= MAC_GPU_MIN_RAM_GB:
            return DevicePlan(tracker="mps:0", exemplar="cpu:0", tracker_dtype=GPU_DTYPE,
                              threads=threads,
                              warm_exemplar=facts.ram_gb >= EXEMPLAR_WARMUP_MIN_RAM_GB,
                              label="SAM3 runs on the Mac's graphics chip.")
        return _cpu_plan(facts, f"SAM3 runs on the processor, because this Mac has "
                                f"{facts.ram_gb:.0f} GB of memory.")
    if facts.nvidia_vram_gb >= NVIDIA_MIN_VRAM_GB or (preference == "gpu" and facts.nvidia_vram_gb > 0):
        big_card = facts.nvidia_vram_gb >= NVIDIA_EXEMPLAR_MIN_VRAM_GB
        return DevicePlan(tracker="cuda:0", exemplar="cuda:0" if big_card else "cpu:0",
                          tracker_dtype=GPU_DTYPE, threads=threads, warm_exemplar=big_card,
                          label=f"SAM3 runs on the {facts.nvidia_name or 'NVIDIA card'} "
                                f"({facts.nvidia_vram_gb:.0f} GB).")
    return _cpu_plan(facts, "SAM3 runs on the processor, because this computer has no usable "
                            "NVIDIA card.")


def unsupported_reason(facts=None):
    """Say why this computer cannot run the app, or return "".

    Parameters:
        facts: a MachineFacts; read from the machine when None.
    Returns:
        str: one sentence, or "" when the computer is supported.
    """
    facts = facts or read_machine_facts()
    if facts.system == "Darwin" and facts.machine != "arm64":
        return ("This Mac has an Intel processor. PyTorch 2.11, which SAM3 needs, runs only on "
                "Macs with Apple chips.")
    return ""
