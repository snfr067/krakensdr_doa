#!/usr/bin/env bash
set -euo pipefail

TARGET="/home/krakenrf/krakensdr_doa/krakensdr_doa/_sdr/_signal_processing/kraken_sdr_signal_processor.py"
PATCHER="/tmp/patch_2dmusic_v4.py"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo bash "$0" "$@"
fi

cat > "$PATCHER" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys
import tempfile
import py_compile
from datetime import datetime

TARGET = Path("/home/krakenrf/krakensdr_doa/krakensdr_doa/_sdr/_signal_processing/kraken_sdr_signal_processor.py")
BASE_DIR = TARGET.parent

FUNC_2D = r'''
# ===== 2D MUSIC L-SHAPED ARRAY PATCH BEGIN =====
@njit(fastmath=True, cache=True)
def gen_scanning_vectors_2d(M, custom_x, custom_y, custom_z, azimuths, elevations):
    """
    2D MUSIC steering vectors for azimuth/elevation search.
    Output shape: (M, len(elevations) * len(azimuths))
    Flatten order: point_idx = el_idx * azimuth_count + az_idx
    """
    az_count = azimuths.size
    el_count = elevations.size
    total_points = az_count * el_count

    scanning_vectors = np.zeros((M, total_points), dtype=np.complex64)
    complex_pi = 1j * 2.0 * np.pi
    point_idx = 0

    for el_idx in range(el_count):
        el_rad = np.deg2rad(elevations[el_idx])
        cos_el = np.cos(el_rad)
        sin_el = np.sin(el_rad)

        for az_idx in range(az_count):
            az_rad = np.deg2rad(azimuths[az_idx])
            ux = cos_el * np.cos(az_rad)
            uy = cos_el * np.sin(az_rad)
            uz = sin_el

            for m in range(M):
                phase = custom_x[m] * ux + custom_y[m] * uy + custom_z[m] * uz
                scanning_vectors[m, point_idx] = np.exp(complex_pi * phase)

            point_idx += 1

    return np.ascontiguousarray(scanning_vectors)
# ===== 2D MUSIC L-SHAPED ARRAY PATCH END =====
'''

INIT_2D = r'''
        # ===== 2D MUSIC L-SHAPED ARRAY PATCH BEGIN =====
        self.DOA_2D_MUSIC_enabled = True

        # Azimuth axis. Keep DOA_theta as azimuth axis for old GUI/output compatibility.
        self.DOA_azimuth = np.linspace(0, 359, 360).astype(np.float32)

        # Elevation axis. For faster Raspberry Pi testing, change this to np.linspace(-60, 60, 121).
        self.DOA_elevation = np.linspace(-90, 90, 181).astype(np.float32)

        self.DOA_theta = self.DOA_azimuth

        # 5-channel L-shaped array.
        # Physical layout:
        # CH2
        # CH1
        # CH0  CH3  CH4
        # Unit: wavelength ratio. d = 0.5 means half-wavelength spacing.
        d_2dmusic = self.DOA_inter_elem_space
        self.custom_array_x = np.array([0.0, 0.0, 0.0, d_2dmusic, 2.0 * d_2dmusic], dtype=np.float32)
        self.custom_array_y = np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self.custom_array_z = np.array([0.0, d_2dmusic, 2.0 * d_2dmusic, 0.0, 0.0], dtype=np.float32)

        # 2D MUSIC spectrum: elevation x azimuth.
        self.DOA_2D = np.ones((self.DOA_elevation.size, self.DOA_azimuth.size), dtype=np.complex64)
        # ===== 2D MUSIC L-SHAPED ARRAY PATCH END =====
'''

FORCE_2D_SCANNING = r'''
        # ===== 2D MUSIC L-SHAPED ARRAY PATCH BEGIN =====
        # Force 2D steering vectors for MUSIC.
        if self.DOA_2D_MUSIC_enabled and self.DOA_algorithm == "MUSIC":
            scanning_vectors = gen_scanning_vectors_2d(
                M,
                self.custom_array_x * frq_ratio,
                self.custom_array_y * frq_ratio,
                self.custom_array_z * frq_ratio,
                self.DOA_azimuth,
                self.DOA_elevation,
            )
        # ===== 2D MUSIC L-SHAPED ARRAY PATCH END =====
'''

MUSIC_ASSIGN_REPLACEMENT = r'''
if self.DOA_2D_MUSIC_enabled:
    self.DOA_2D = DOA_MUSIC_res.reshape(
        (self.DOA_elevation.size, self.DOA_azimuth.size)
    )

    # Keep old GUI/output path alive by reducing 2D spectrum to a 1D azimuth spectrum.
    self.DOA = np.max(np.abs(self.DOA_2D), axis=0).astype(np.complex64)

    peak_index = np.argmax(np.abs(self.DOA_2D))
    el_idx, az_idx = np.unravel_index(peak_index, self.DOA_2D.shape)

    azimuth_0 = float(self.DOA_azimuth[az_idx])
    elevation_0 = float(self.DOA_elevation[el_idx])

    return azimuth_0, elevation_0
else:
    self.DOA = DOA_MUSIC_res
'''


def fail(msg: str) -> None:
    print("[2D-MUSIC PATCH ERROR] " + msg, file=sys.stderr)
    sys.exit(1)


def compile_ok(path: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as f:
            py_compile.compile(str(path), cfile=f.name, doraise=True)
        return True
    except Exception:
        return False


def choose_clean_source() -> Path:
    candidates = []
    # Prefer the earliest backup before v2/v3/v1 patches, because the current file may already be broken.
    patterns = [
        "kraken_sdr_signal_processor.py.bak_before_2dmusic_v2_*",
        "kraken_sdr_signal_processor.py.bak_before_2dmusic_v3_*",
        "kraken_sdr_signal_processor.py.bak_before_2dmusic_*",
        "kraken_sdr_signal_processor.py.bak_2dmusic",
    ]
    for pat in patterns:
        candidates.extend(sorted(BASE_DIR.glob(pat), key=lambda p: p.stat().st_mtime))
    candidates.append(TARGET)

    seen = set()
    unique = []
    for p in candidates:
        if p.exists() and p not in seen:
            seen.add(p)
            unique.append(p)

    for p in unique:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "SyntaxError" in txt:
            continue
        if compile_ok(p):
            print("[2D-MUSIC PATCH] clean source selected: " + str(p))
            return p
    fail("No compile-able clean source or backup found")


def get_func_region(text: str, func_def: str):
    start = text.find(func_def)
    if start < 0:
        fail("Cannot find function: " + func_def.strip())
    next_func = text.find("\n    def ", start + 1)
    if next_func < 0:
        next_func = text.find("\nclass ", start + 1)
    if next_func < 0:
        next_func = len(text)
    return start, next_func, text[start:next_func]


def indent_block(block: str, indent: str) -> str:
    out = []
    for line in block.strip("\n").splitlines():
        out.append(indent + line if line.strip() else "")
    return "\n".join(out)


def strip_old_patch_blocks(text: str) -> str:
    # Remove failed old 2D blocks if the selected source was already partially patched.
    text = re.sub(
        r"\n?# ===== 2D MUSIC L-SHAPED ARRAY PATCH BEGIN =====.*?# ===== 2D MUSIC L-SHAPED ARRAY PATCH END =====\n?",
        "\n",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\n?[ \t]*# ===== 2D MUSIC UDP INSERT POINT BEGIN =====.*?# ===== 2D MUSIC UDP INSERT POINT END =====\n?",
        "\n",
        text,
        flags=re.DOTALL,
    )
    # Remove the bad orphan line from v2/v3 if it exists.
    text = re.sub(r"^\s*elevation_0\s*=\s*0\.0\s*$\n?", "", text, flags=re.MULTILINE)
    return text


def patch_init(text: str) -> str:
    if "self.DOA_2D_MUSIC_enabled" in text:
        return text
    pattern = re.compile(
        r"^[ \t]*self\.DOA_theta\s*=.*\n"
        r"(?:^[ \t]*self\.custom_array_x\s*=.*\n)?"
        r"(?:^[ \t]*self\.custom_array_y\s*=.*\n)?",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        fail("Cannot find self.DOA_theta/custom_array block in __init__")
    return text[:m.start()] + INIT_2D.rstrip() + "\n" + text[m.end():]


def patch_elevation_list_init(text: str) -> str:
    if "self.elevation_0_list" in text:
        return text
    old = "self.theta_0_list = []"
    if old not in text:
        fail("Cannot find self.theta_0_list = []")
    return text.replace(old, "self.theta_0_list = []\n        self.elevation_0_list = []", 1)


def patch_insert_2d_func(text: str) -> str:
    if "def gen_scanning_vectors_2d(" in text:
        return text
    marker = "\n@njit(fastmath=True, cache=True)\ndef DOA_MUSIC("
    if marker not in text:
        fail("Cannot find DOA_MUSIC marker")
    return text.replace(marker, "\n" + FUNC_2D.rstrip() + marker, 1)


def patch_force_2d_scanning(text: str) -> str:
    if "Force 2D steering vectors for MUSIC" in text:
        return text
    start, end, body = get_func_region(text, "    def estimate_DOA(self, processed_signal, vfo_freq):")
    m = re.search(r"^[ \t]*if self\.DOA_algorithm == ", body, re.MULTILINE)
    if not m:
        fail("Cannot find first DOA_algorithm branch inside estimate_DOA")
    new_body = body[:m.start()] + FORCE_2D_SCANNING.rstrip() + "\n\n" + body[m.start():]
    return text[:start] + new_body + text[end:]


def patch_music_result(text: str) -> str:
    start, end, body = get_func_region(text, "    def estimate_DOA(self, processed_signal, vfo_freq):")
    if "return azimuth_0, elevation_0" in body:
        return text
    target = "self.DOA = DOA_MUSIC_res"
    idx = body.find(target)
    if idx < 0:
        fail("Cannot find self.DOA = DOA_MUSIC_res inside estimate_DOA")
    line_start = body.rfind("\n", 0, idx) + 1
    line_end = body.find("\n", idx)
    if line_end < 0:
        line_end = len(body)
    indent = body[line_start:idx]
    replacement = indent_block(MUSIC_ASSIGN_REPLACEMENT, indent)
    new_body = body[:line_start] + replacement + body[line_end:]
    return text[:start] + new_body + text[end:]


def patch_estimate_return(text: str) -> str:
    start, end, body = get_func_region(text, "    def estimate_DOA(self, processed_signal, vfo_freq):")
    if "return float(theta_0), 0.0" in body:
        return text
    if "return theta_0" not in body:
        fail("Cannot find final return theta_0 inside estimate_DOA")
    body = body.replace("return theta_0", "return float(theta_0), 0.0", 1)
    return text[:start] + body + text[end:]


def patch_elevation_list_clear(text: str) -> str:
    if "self.elevation_0_list.clear()" in text:
        return text
    pattern = re.compile(r"^(?P<indent>[ \t]*)self\.theta_0_list\.clear\(\)\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        fail("Cannot find self.theta_0_list.clear()")
    indent = m.group("indent")
    replacement = indent + "self.theta_0_list.clear()\n" + indent + "self.elevation_0_list.clear()"
    return text[:m.start()] + replacement + text[m.end():]


def patch_run_call(text: str) -> str:
    if "2D MUSIC UDP INSERT POINT BEGIN" in text:
        return text
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)theta_0\s*=\s*self\.estimate_DOA\(vfo_channel,\s*self\.vfo_freq\[i\]\)\s*$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        fail("Cannot find active run-loop estimate_DOA call")
    indent = m.group("indent")
    replacement = (
        indent + "theta_0, elevation_0 = self.estimate_DOA(vfo_channel, self.vfo_freq[i])\n\n"
        + indent + "# ===== 2D MUSIC UDP INSERT POINT BEGIN =====\n"
        + indent + "# UDP sending should be inserted here later.\n"
        + indent + "# Available values:\n"
        + indent + "#   theta_0          = azimuth angle\n"
        + indent + "#   elevation_0      = elevation angle\n"
        + indent + "#   self.vfo_freq[i] = current VFO frequency\n"
        + indent + "# Example later:\n"
        + indent + "#   send_udp_2dmusic(theta_0, elevation_0, self.vfo_freq[i])\n"
        + indent + "logging.info(\n"
        + indent + "    f\"[2D-MUSIC] azimuth={theta_0:.2f}, elevation={elevation_0:.2f}, freq={self.vfo_freq[i]}\"\n"
        + indent + ")\n"
        + indent + "# ===== 2D MUSIC UDP INSERT POINT END ====="
    )
    return text[:m.start()] + replacement + text[m.end():]


def patch_elevation_list_append(text: str) -> str:
    if "self.elevation_0_list.append(elevation_0)" in text:
        return text
    pattern = re.compile(r"^(?P<indent>[ \t]*)self\.theta_0_list\.append\(theta_0\)\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        fail("Cannot find self.theta_0_list.append(theta_0)")
    indent = m.group("indent")
    replacement = indent + "self.theta_0_list.append(theta_0)\n" + indent + "self.elevation_0_list.append(elevation_0)"
    return text[:m.start()] + replacement + text[m.end():]


def patch_queue_elevation(text: str) -> str:
    if '["DoA Elevation", elevation_0]' in text:
        return text
    pattern = re.compile(r'^(?P<indent>[ \t]*)que_data_packet\.append\(\["DoA Max", theta_0\]\)\s*$', re.MULTILINE)
    m = pattern.search(text)
    if not m:
        print("[2D-MUSIC PATCH WARN] Cannot find DoA Max queue line; skip queue elevation append")
        return text
    indent = m.group("indent")
    replacement = indent + 'que_data_packet.append(["DoA Max", theta_0])\n' + indent + 'que_data_packet.append(["DoA Elevation", elevation_0])'
    return text[:m.start()] + replacement + text[m.end():]


def main() -> None:
    if not TARGET.exists():
        fail("Target file does not exist: " + str(TARGET))

    source = choose_clean_source()
    original_backup = TARGET.with_suffix(TARGET.suffix + ".bak_before_2dmusic_v4_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(TARGET, original_backup)

    text = source.read_text(encoding="utf-8", errors="ignore")
    text = strip_old_patch_blocks(text)

    patched = text
    patched = patch_init(patched)
    patched = patch_elevation_list_init(patched)
    patched = patch_insert_2d_func(patched)
    patched = patch_force_2d_scanning(patched)
    patched = patch_music_result(patched)
    patched = patch_estimate_return(patched)
    patched = patch_elevation_list_clear(patched)
    patched = patch_run_call(patched)
    patched = patch_elevation_list_append(patched)
    patched = patch_queue_elevation(patched)

    tmp_py = TARGET.with_suffix(".py.tmp_2dmusic_v4")
    tmp_py.write_text(patched, encoding="utf-8")

    try:
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as f:
            py_compile.compile(str(tmp_py), cfile=f.name, doraise=True)
    except Exception as e:
        print("[2D-MUSIC PATCH ERROR] patched file failed py_compile; target was not overwritten", file=sys.stderr)
        print(str(e), file=sys.stderr)
        print("Temp patched file left at: " + str(tmp_py), file=sys.stderr)
        sys.exit(1)

    shutil.copy2(tmp_py, TARGET)
    tmp_py.unlink(missing_ok=True)

    print("[2D-MUSIC PATCH OK]")
    print("Target : " + str(TARGET))
    print("Backup : " + str(original_backup))
    print("Clean source : " + str(source))
    print("UDP insert point: grep -n '2D MUSIC UDP INSERT POINT BEGIN\\|2D-MUSIC' " + str(TARGET))


if __name__ == "__main__":
    main()
PY

python3 "$PATCHER"
python3 - <<'PY'
import py_compile, tempfile
path = "/home/krakenrf/krakensdr_doa/krakensdr_doa/_sdr/_signal_processing/kraken_sdr_signal_processor.py"
with tempfile.NamedTemporaryFile(suffix='.pyc', delete=True) as f:
    py_compile.compile(path, cfile=f.name, doraise=True)
print('[2D-MUSIC COMPILE OK]')
PY
grep -n "2D MUSIC UDP INSERT POINT BEGIN\|2D-MUSIC" "$TARGET" || true

