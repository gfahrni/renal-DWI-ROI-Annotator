"""
Data-loading utilities for renal-DWI-ROI-Annotator.
"""

import os
import pydicom
from pydicom.misc import is_dicom


def _list_dicom_files(dir_path):
    """
    Return all files in *dir_path* that are valid DICOM files,
    regardless of file extension. Uses pydicom's preamble check
    (128 zero bytes + "DICM") which is fast and doesn't read the
    entire file.
    """
    files = []
    for entry in os.listdir(dir_path):
        full = os.path.join(dir_path, entry)
        if os.path.isfile(full) and is_dicom(full):
            files.append(full)
    return sorted(files)


def find_series(data_path):
    """
    Scan *data_path* and return a list of found DICOM series.

    A "series" is a group of .dcm files that belong together (e.g. one MRI
    scan sequence). This function handles two layouts:

        1. Flat layout  – .dcm files are directly inside *data_path*.
        2. Nested layout – *data_path* contains subdirectories, each holding
           one series' worth of .dcm files.

    Each series is returned as a dict:
        {
            'name':        str  – folder name (or leaf of data_path),
            'description': str  – DICOM SeriesDescription tag, if present,
            'files':       list – sorted absolute paths to .dcm files,
        }

    Args:
        data_path: Path to a directory containing DICOM data.

    Returns:
        A list of series dicts, or an empty list if nothing was found.
    """
    series = []

    # --- Try flat layout first ------------------------------------------------
    # Look for .dcm files straight in data_path (no subfolders).
    dcm_files = _list_dicom_files(data_path)
    if dcm_files:
        # Read just the metadata of the first file (stop_before_pixels=True
        # skips the large pixel array, making this fast).
        ds = pydicom.dcmread(dcm_files[0], stop_before_pixels=True)

        # Get the SeriesDescription tag, falling back to the folder name.
        desc = ds.get('SeriesDescription', os.path.basename(data_path))

        series.append({
            'name': os.path.basename(data_path),
            'description': desc,
            'files': dcm_files,
        })
        # Flat layout found files → we're done, no need to check subfolders.
        return series

    # --- Try nested layout ----------------------------------------------------
    # No .dcm files at the top level → look inside each subdirectory.
    for entry in sorted(os.listdir(data_path)):
        subdir = os.path.join(data_path, entry)

        # Skip files, only descend into directories.
        if not os.path.isdir(subdir):
            continue

        # Gather .dcm files inside this subdirectory.
        dcm_files = _list_dicom_files(subdir)
        if not dcm_files:
            # This subdir has no DICOM files → skip it.
            continue

        # Read metadata from the first file to get a human-readable name.
        ds = pydicom.dcmread(dcm_files[0], stop_before_pixels=True)
        desc = ds.get('SeriesDescription', entry)

        series.append({
            'name': entry,
            'description': desc,
            'files': dcm_files,
        })

    return series


def _get_b_value(ds):
    """Extract the b-value from a DICOM dataset, or None if absent.

    Covers all major MRI manufacturers (tags from NAMIC DTI DICOM spec):
      1. (0018,9087) DiffusionBValue       — Standard DICOM (public)
      2. (0018,9089) within DiffusionGradientDirectionSequence  — public
      3. Siemens    (0019,100C) B_value     — private (VB/VD/VE series)
      4. Siemens    (0019,000C) B_value     — private (older)
      5. GE         (0043,1039) SlopInt_6_9 — private (GEMS_PARM_01), needs % 1000000000
      6. Philips    (2001,1003) Diffusion B-Factor  — private
      7. UIH        (0065,1009) B_value     — private
      8. Canon/Toshiba uses standard tag (0018,9087) or ImageComments (0020,4000)
    """
    # ── Helper ──────────────────────────────────────────────────────────
    def _to_int(val):
        v = val[0] if isinstance(val, (list, tuple)) else val
        return int(float(v))

    # 1. Standard public tag (0018,9087)
    try:
        elem = ds.get('DiffusionBValue') or ds.get((0x0018, 0x9087))
        if elem is not None:
            return int(float(elem.value[0] if isinstance(elem.value, (list, tuple)) else elem.value))
    except (ValueError, TypeError, AttributeError):
        pass

    # 2. DiffusionGradientDirectionSequence → DiffusionBValueDouble
    try:
        seq = ds.get('DiffusionGradientDirectionSequence')
        if seq:
            for item in seq:
                for tag in ('DiffusionBValueDouble', 'DiffusionBValue'):
                    bv = item.get(tag)
                    if bv is not None:
                        return int(float(bv[0] if isinstance(bv, (list, tuple)) else bv))
    except (ValueError, TypeError, AttributeError):
        pass

    # 3. Siemens (0019,100C) — B_value (current)
    for siemens_tag in ((0x0019, 0x100c), (0x0019, 0x000c)):
        try:
            elem = ds.get(siemens_tag)
            if elem is not None:
                return int(float(str(elem.value).strip()))
        except (ValueError, TypeError, AttributeError):
            pass

    # 4. GE (0043,1039) — SlopInt_6_9, masked with bias 1000000000
    try:
        elem = ds.get((0x0043, 0x1039))
        if elem is not None:
            raw = elem.value
            if isinstance(raw, (list, tuple)):
                raw = raw[0]
            raw = int(float(str(raw).strip()))
            if raw > 1000000:
                raw = raw % 1000000000
            return raw
    except (ValueError, TypeError, AttributeError, IndexError):
        pass

    # 5. Philips (2001,1003) — Diffusion B-Factor
    try:
        elem = ds.get((0x2001, 0x1003))
        if elem is not None:
            return int(float(elem.value[0] if isinstance(elem.value, (list, tuple)) else elem.value))
    except (ValueError, TypeError, AttributeError):
        pass

    # 6. UIH (0065,1009) — B_value
    try:
        elem = ds.get((0x0065, 0x1009))
        if elem is not None:
            return int(float(elem.value[0] if isinstance(elem.value, (list, tuple)) else elem.value))
    except (ValueError, TypeError, AttributeError):
        pass

    # 7. Canon/Toshiba — ImageComments (0020,4000) fallback
    try:
        comments = ds.get('ImageComments', '')
        if comments and 'b=' in str(comments):
            import re
            m = re.search(r'b=(\d+)', str(comments))
            if m:
                return int(m.group(1))
    except (ValueError, TypeError, AttributeError):
        pass

    return None


def get_available_b_values(files):
    """Return sorted list of unique b-values found in the given DICOM files.

    Prints results to help debug detection issues.
    """
    b_vals = set()
    for f in files:
        ds = pydicom.dcmread(f, stop_before_pixels=True)
        bv = _get_b_value(ds)
        if bv is not None:
            b_vals.add(bv)
    result = sorted(b_vals)
    print(f'[loader] get_available_b_values: {result}')
    return result


def load_series(files):
    """
    Fully read a list of DICOM files and return them sorted by slice order.

    DICOM slices are typically ordered by the (0020,0013) InstanceNumber tag.
    Sorting is important because the file system order may not match the
    anatomical order (e.g. files might be named IM-0001-0001.dcm, etc.).

    Args:
        files: List of paths to .dcm files belonging to one series.

    Returns:
        A list of pydicom Dataset objects, sorted by InstanceNumber.
    """
    slices = []

    # Read every file completely (pixel data included this time).
    for f in files:
        ds = pydicom.dcmread(f)
        slices.append(ds)

    # Sort by numeric InstanceNumber (DICOM IS is a string, so we force int).
    slices.sort(key=lambda x: int(x.get('InstanceNumber', 0) or 0))

    return slices


def load_dwi_series(files):
    """
    Load DICOM files and group them by b-value.

    Prints the detected groups to help debug DWI detection.

    Returns:
        dict: b_value -> list of pydicom Dataset objects (sorted by InstanceNumber).
    """
    slices = load_series(files)

    # Debug: inspect first file's relevant tags
    if slices:
        ds0 = slices[0]
        raw = ds0.get('DiffusionBValue', '<MISSING>')
        seq = ds0.get('DiffusionGradientDirectionSequence', '<MISSING>')
        print(f'[loader] First file tags: DiffusionBValue={raw}, '
              f'DiffusionGradientDirectionSequence={seq}')

    groups = {}
    for ds in slices:
        bv = _get_b_value(ds)
        if bv is None:
            bv = 0
        groups.setdefault(bv, []).append(ds)

    print(f'[loader] load_dwi_series groups: {list(groups.keys())} '
          f'({sum(len(v) for v in groups.values())} total slices)')
    return groups
