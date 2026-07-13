"""
PyQt6-based ROI annotator for renal DWI series.
"""

import sys
import json
import os

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtGui import (
    QImage, QPixmap, QIcon, QPainter,
    QKeyEvent, QWheelEvent, QMouseEvent,
    QPen, QBrush, QColor,
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QPushButton, QComboBox, QToolButton, QLineEdit,
    QGraphicsView, QGraphicsScene, QGraphicsEllipseItem,
    QGraphicsTextItem, QGraphicsSimpleTextItem, QGraphicsPixmapItem, QSizePolicy, QApplication,
    QDialog, QSpinBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QMessageBox, QMenu,
)

import math
import re

import nibabel as nib

from .loader import (
    find_series, load_series, load_dwi_series,
    is_tracew_series, get_tracew_b_values, format_tracew_label,
)


SETTINGS_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'settings.json'
)


def _load_settings():
    default = {
        'bg_color': '#2b2b2b',
        'default_data_path': 'DATA',
        'roi_counts': {'L_Cortex': 2, 'L_Medulla': 3, 'R_Cortex': 2, 'R_Medulla': 3},
        'cortex_diameter': 10.0,
        'medulla_diameter': 10.0,
        'last_b_value': None,
    }
    path = os.path.abspath(SETTINGS_PATH)
    if os.path.exists(path):
        with open(path) as f:
            return {**default, **json.load(f)}
    return default


def _save_settings(updates):
    path = os.path.abspath(SETTINGS_PATH)
    current = _load_settings()
    current.update(updates)
    with open(path, 'w') as f:
        json.dump(current, f, indent=4)


SETTINGS = _load_settings()


class ImageView(QGraphicsView):
    """QGraphicsView subclass that handles mouse-wheel slice scrolling,
    ROI stamp placement, and middle-button window/level adjustment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._viewer = None
        self.setMouseTracking(True)

    def set_viewer(self, viewer):
        self._viewer = viewer

    def wheelEvent(self, event: QWheelEvent):
        if self._viewer is not None:
            self._viewer._on_wheel(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._viewer is not None:
            self._viewer._on_mouse_move(event)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self._viewer is not None:
            if event.button() == Qt.MouseButton.MiddleButton:
                self._viewer._on_mouse_middle_press(event)
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if self._viewer._on_mouse_press(event):
                    return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._viewer is not None:
            self._viewer._on_mouse_release(event)
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._viewer is not None:
            self._viewer._fit_image()


class SettingsDialog(QDialog):
    def __init__(self, parent=None, roi_counts=None,
                 cortex_diameter=5.0, medulla_diameter=7.1):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setFixedSize(320, 380)

        if roi_counts is None:
            roi_counts = {'L_Cortex': 2, 'L_Medulla': 3, 'R_Cortex': 2, 'R_Medulla': 3}

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # --- ROI counts ---------------------------------------------------
        counts_group = QGroupBox('Number of ROIs')
        counts_form = QFormLayout(counts_group)

        self.roi_spins = {}
        for label, key in [('Left Cortex', 'L_Cortex'), ('Left Medulla', 'L_Medulla'),
                           ('Right Cortex', 'R_Cortex'), ('Right Medulla', 'R_Medulla')]:
            spin = QSpinBox()
            spin.setRange(0, 10)
            spin.setValue(roi_counts.get(key, 2 if 'Cortex' in key else 3))
            self.roi_spins[key] = spin
            counts_form.addRow(label, spin)

        layout.addWidget(counts_group)

        # --- Cortex size --------------------------------------------------
        self._cortex_diameter, self._cortex_area = self._create_size_group(
            layout, 'Default ROI Size - Cortex', cortex_diameter)

        # --- Medulla size -------------------------------------------------
        self._medulla_diameter, self._medulla_area = self._create_size_group(
            layout, 'Default ROI Size - Medulla', medulla_diameter)

        # --- Buttons ------------------------------------------------------
        btn_layout = QHBoxLayout()
        delete_all_btn = QPushButton('Delete All ROIs')
        delete_all_btn.setStyleSheet(
            'QPushButton { background-color: #6b1a1a; color: white; '
            'border: none; padding: 6px 14px; font-size: 11px; }'
            'QPushButton:hover { background-color: #8b2a2a; }'
        )
        delete_all_btn.clicked.connect(self._delete_all_rois)
        btn_layout.addWidget(delete_all_btn)
        reset_btn = QPushButton('Reset Defaults')
        reset_btn.setStyleSheet(
            'QPushButton { background-color: #5a5a5a; color: white; '
            'border: none; padding: 6px 14px; font-size: 11px; }'
            'QPushButton:hover { background-color: #6a6a6a; }'
        )
        reset_btn.clicked.connect(self._reset_defaults)
        btn_layout.addWidget(reset_btn)
        btn_layout.addStretch()
        ok_btn = QPushButton('OK')
        ok_btn.setStyleSheet(
            'QPushButton { background-color: #4a4a4a; color: white; '
            'border: none; padding: 6px 20px; font-size: 12px; }'
            'QPushButton:hover { background-color: #5a5a5a; }'
        )
        ok_btn.clicked.connect(self._validate_and_accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def _reset_defaults(self):
        for key, spin in self.roi_spins.items():
            spin.setValue(2 if 'Cortex' in key else 3)
        self._cortex_diameter.setValue(5.0)
        self._medulla_diameter.setValue(7.1)

        btn = self.sender()
        if btn:
            orig_text = btn.text()
            orig_style = btn.styleSheet()
            btn.setText('Reset!')
            btn.setStyleSheet(
                'QPushButton { background-color: #2d7d2d; color: white; '
                'border: none; padding: 6px 14px; font-size: 11px; }'
            )
            QTimer.singleShot(800, lambda: (
                btn.setText(orig_text),
                btn.setStyleSheet(orig_style),
            ))

    def _validate_and_accept(self):
        if all(s.value() == 0 for s in self.roi_spins.values()):
            QMessageBox.warning(self, 'Invalid',
                                'At least one ROI count must be ≥ 1.')
            return
        super().accept()

    def _delete_all_rois(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('Confirm Mass Deletion')
        dlg.setFixedSize(380, 170)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        msg = QLabel(
            'This will permanently delete ALL ROI masks\n'
            'from ALL cases. This cannot be undone.\n\n'
            'Type MEGAKILL to confirm:')
        msg.setWordWrap(True)
        layout.addWidget(msg)
        textbox = QLineEdit()
        textbox.setPlaceholderText('MEGAKILL')
        layout.addWidget(textbox)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton('Cancel')
        cancel_btn.setStyleSheet(
            'QPushButton { background-color: #5a5a5a; color: white; '
            'border: none; padding: 6px 20px; font-size: 12px; }'
            'QPushButton:hover { background-color: #6a6a6a; }'
        )
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        confirm_btn = QPushButton('Delete Everything')
        confirm_btn.setStyleSheet(
            'QPushButton { background-color: #8b1a1a; color: white; '
            'border: none; padding: 6px 20px; font-size: 12px; }'
            'QPushButton:hover { background-color: #ab3a3a; }'
        )
        confirm_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)
        if dlg.exec() == QDialog.DialogCode.Accepted and textbox.text() == 'MEGAKILL':
            self._confirmed_delete_all = True
            super().accept()

    def _create_size_group(self, parent_layout, title, initial_diameter=10.0):
        group = QGroupBox(title)
        form = QFormLayout(group)

        diameter = QDoubleSpinBox()
        diameter.setRange(1, 100)
        diameter.setDecimals(1)
        diameter.setSuffix(' mm')
        diameter.setValue(initial_diameter)

        area = QDoubleSpinBox()
        area.setRange(0.01, 100)
        area.setDecimals(2)
        area.setSingleStep(0.1)
        area.setSuffix(' cm²')
        area.setValue(self._diameter_to_area(initial_diameter))

        def on_diameter(d, area_spin=area):
            area_spin.blockSignals(True)
            area_spin.setValue(self._diameter_to_area(d))
            area_spin.blockSignals(False)

        def on_area(a, diameter_spin=diameter):
            diameter_spin.blockSignals(True)
            diameter_spin.setValue(self._area_to_diameter(a))
            diameter_spin.blockSignals(False)

        diameter.valueChanged.connect(on_diameter)
        area.valueChanged.connect(on_area)

        form.addRow('Diameter', diameter)
        form.addRow('Area', area)
        parent_layout.addWidget(group)

        return diameter, area

    @staticmethod
    def _diameter_to_area(d):
        return round(math.pi * d * d / 400, 2)

    @staticmethod
    def _area_to_diameter(a):
        return round(20 * math.sqrt(a / math.pi), 1)

    def get_roi_counts(self):
        return {k: s.value() for k, s in self.roi_spins.items()}

    def get_cortex_diameter(self):
        return self._cortex_diameter.value()

    def get_cortex_area(self):
        return self._cortex_area.value()

    def get_medulla_diameter(self):
        return self._medulla_diameter.value()

    def get_medulla_area(self):
        return self._medulla_area.value()


class WindowDialog(QDialog):
    """Popup to view/edit window center/width and reset to DICOM defaults."""

    def __init__(self, parent=None, center=None, width=None,
                 default_center=None, default_width=None):
        super().__init__(parent)
        self.setWindowTitle('Window / Level')
        self.setFixedSize(260, 180)

        self._default_center = default_center
        self._default_width = default_width

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        self.center_spin = QDoubleSpinBox()
        self.center_spin.setRange(-99999, 99999)
        self.center_spin.setDecimals(1)
        self.center_spin.setValue(center if center is not None else 0)
        form.addRow('Center (WC)', self.center_spin)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(1, 99999)
        self.width_spin.setDecimals(1)
        self.width_spin.setValue(width if width is not None else 1)
        form.addRow('Width (WW)', self.width_spin)

        layout.addLayout(form)

        btn_row = QHBoxLayout()

        reset_btn = QPushButton('Reset to Default')
        reset_btn.setStyleSheet(
            'QPushButton { background-color: #6b1a1a; color: white; '
            'border: none; padding: 4px 10px; font-size: 11px; }'
            'QPushButton:hover { background-color: #8b2a2a; }'
        )
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()

        ok_btn = QPushButton('OK')
        ok_btn.setStyleSheet(
            'QPushButton { background-color: #4a4a4a; color: white; '
            'border: none; padding: 4px 20px; font-size: 12px; }'
            'QPushButton:hover { background-color: #5a5a5a; }'
        )
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

    def _reset_defaults(self):
        if self._default_center is not None:
            self.center_spin.setValue(self._default_center)
        if self._default_width is not None:
            self.width_spin.setValue(self._default_width)

    def get_values(self):
        return (self.center_spin.value(), self.width_spin.value())


class DicomViewer(QMainWindow):
    """PyQt6-based annotator for browsing renal DWI series and drawing ROIs."""

    def __init__(self, data_path):
        super().__init__()
        self.data_path = data_path

        # --- Load data -------------------------------------------------------
        self.series_list = find_series(data_path)
        if not self.series_list:
            print('No DICOM series found.')
            sys.exit(1)

        print('Series found:')
        for i, s in enumerate(self.series_list):
            print(f'  [{i}] {s["name"]} - {s["description"]} '
                  f'({len(s["files"])} slices)')

        self.current_series_idx = 0
        self._scroll_accumulator = 0

        # ROI settings (persisted in settings.json)
        self._roi_counts = SETTINGS.get('roi_counts',
            {'L_Cortex': 2, 'L_Medulla': 3, 'R_Cortex': 2, 'R_Medulla': 3})
        self._cortex_diameter = SETTINGS.get('cortex_diameter', 5.0)
        self._cortex_area = self._diameter_to_area(self._cortex_diameter)
        self._medulla_diameter = SETTINGS.get('medulla_diameter', 7.1)
        self._medulla_area = self._diameter_to_area(self._medulla_diameter)

        # DWI b-value support
        self._b_values = []
        self._current_b_value = None
        self._b_value_map = {}      # b_value -> [slices]
        self._images_map = {}       # b_value -> [precomputed uint8 arrays]
        self._raw_images_map = {}   # b_value -> [raw float64 arrays]
        self._raw_images = []       # current b-value's raw float64 arrays

        # TRACEW (trace-weighted) detection
        self._series_is_tracew = False
        self._tracew_b_values = None

        # Window / Level (windowing)
        self._window_center = None
        self._window_width = None
        self._default_window_center = None
        self._default_window_width = None
        self._wl_dragging = False
        self._wl_start_pos = None
        self._wl_start_values = None   # (center, width) at drag start

        # ROI placement state
        self._roi_order = []
        self._current_roi_idx = 0
        self._roi_items = {}      # slice_idx -> {label -> QGraphicsEllipseItem}
        self._stamp_item = None   # QGraphicsEllipseItem cursor preview
        self._stamp_label = None  # QGraphicsTextItem label next to cursor

        self._roi_order_from_counts()

        self._load_current_series()

        # Restore last used b-value from settings
        saved_b = SETTINGS.get('last_b_value')
        if saved_b is not None and self._b_values and saved_b in self._b_values:
            self._current_b_value = saved_b
            self._slices = self._b_value_map[saved_b]
            self._raw_images = self._raw_images_map[saved_b]
            self._images = self._images_map[saved_b]

        # --- Build UI --------------------------------------------------------
        self._init_ui()

        # Load existing NIfTI masks from disk
        self._load_existing_masks()
        self._update_case_btn_color()
        self._rebuild_case_menu()
        self._show_slice()

    def _load_current_series(self):
        series = self.series_list[self.current_series_idx]

        # Try DWI grouping first
        bv_map = load_dwi_series(series['files'])

        # TRACEW fallback: if only one b-value group, try splitting by
        # b-values parsed from the protocol name (e.g. b0_b200_b1500).
        # Many scanners store separate trace-weighted volumes per b-value
        # in the same series, but without reliable per-slice b-value tags.
        if len(bv_map) <= 1:
            all_slices = next(iter(bv_map.values())) if bv_map else load_series(series['files'])
            if all_slices and is_tracew_series(all_slices):
                tracew_vals = get_tracew_b_values(all_slices)
                if tracew_vals and len(tracew_vals) > 1:
                    n = len(all_slices) // len(tracew_vals)
                    bv_map = {}
                    for i, bv in enumerate(tracew_vals):
                        start = i * n
                        end = start + n if i < len(tracew_vals) - 1 else len(all_slices)
                        bv_map[bv] = all_slices[start:end]

        if len(bv_map) > 1:
            self._b_value_map = bv_map
            self._b_values = sorted(bv_map.keys())

            # Store raw float arrays and precompute uint8 images for every b-value group
            self._images_map = {}
            self._raw_images_map = {}
            for bv, bv_slices in self._b_value_map.items():
                raw = [ds.pixel_array.astype(np.float64) for ds in bv_slices]
                self._raw_images_map[bv] = raw
                self._images_map[bv] = self._precompute_images(raw)

            # Default to the lowest b-value (best anatomical contrast)
            self._current_b_value = self._b_values[0]
            self._slices = self._b_value_map[self._current_b_value]
            self._raw_images = self._raw_images_map[self._current_b_value]
            self._images = self._images_map[self._current_b_value]
        else:
            self._b_values = []
            self._current_b_value = None
            self._b_value_map = {}
            self._raw_images_map = {}
            # Reuse already-loaded slices from bv_map to avoid re-reading
            all_slices = next(iter(bv_map.values())) if bv_map else load_series(series['files'])
            raw = [ds.pixel_array.astype(np.float64) for ds in all_slices]
            self._raw_images = raw
            self._slices = all_slices
            self._images = self._precompute_images(raw)

        # Detect TRACEW (trace-weighted) series
        self._series_is_tracew = is_tracew_series(self._slices)
        self._tracew_b_values = get_tracew_b_values(self._slices) if self._series_is_tracew else None

        self.num_slices = len(self._slices)
        self._slice_idx = 0

        # Initial window/level from first slice's DICOM tags
        self._window_center = None
        self._window_width = None
        self._default_window_center = None
        self._default_window_width = None
        if self._slices:
            ds = self._slices[0]
            wc = ds.get('WindowCenter', None)
            ww = ds.get('WindowWidth', None)
            if wc is not None and ww is not None:
                c = float(wc[0]) if isinstance(wc, (list, tuple)) else float(wc)
                w = float(ww[0]) if isinstance(ww, (list, tuple)) else float(ww)
                self._window_center = c
                self._window_width = w
                self._default_window_center = c
                self._default_window_width = w

        # Restore saved W/L for this series
        saved = self._load_windowing()
        if saved is not None:
            self._window_center = saved[0]
            self._window_width = saved[1]

    @staticmethod
    def _diameter_to_area(d):
        return round(math.pi * d * d / 400, 2)

    # ------------------------------------------------------------------
    # ROI placement
    # ------------------------------------------------------------------

    def _roi_order_from_counts(self):
        order = []
        for prefix in ['L', 'R']:
            for zone in ['Cortex', 'Medulla']:
                n = self._roi_counts.get(f'{prefix}_{zone}', 0)
                for i in range(1, n + 1):
                    order.append(f'{prefix}_{zone}{i}')
        self._roi_order = order
        self._current_roi_idx = 0

    # ------------------------------------------------------------------
    # NIfTI mask helpers
    # ------------------------------------------------------------------

    @property
    def _mask_dir(self):
        if not self.series_list:
            return None
        patient_dir = os.path.dirname(
            self.series_list[self.current_series_idx]['files'][0])
        mask_dir = os.path.join(patient_dir, 'roi_masks')
        os.makedirs(mask_dir, exist_ok=True)
        return mask_dir

    def _mask_filename(self, label, radius_px=None):
        parts = [label]
        if radius_px is not None:
            parts.append(f'r{radius_px:.2f}')
        return os.path.join(self._mask_dir, '_'.join(parts) + '.nii')

    def _compute_affine(self):
        if not self._slices:
            return np.eye(4)
        ds0 = self._slices[0]
        h, w = ds0.pixel_array.shape[:2]
        try:
            ps = ds0.PixelSpacing
            dr = float(ps[0])
            dc = float(ps[1])
        except Exception:
            dr = dc = 1.0
        try:
            orient = ds0.ImageOrientationPatient
            rx, ry, rz = [float(x) for x in orient[:3]]
            cx, cy, cz = [float(x) for x in orient[3:]]
        except Exception:
            rx, ry, rz = 1, 0, 0
            cx, cy, cz = 0, 1, 0
        nx, ny, nz = np.cross([rx, ry, rz], [cx, cy, cz])
        dz = 1.0
        if len(self._slices) > 1:
            try:
                p0 = [float(x) for x in self._slices[0].ImagePositionPatient]
                p1 = [float(x) for x in self._slices[1].ImagePositionPatient]
                dz = np.linalg.norm([p1[i] - p0[i] for i in range(3)])
            except Exception:
                pass
        try:
            pos = [float(x) for x in ds0.ImagePositionPatient]
        except Exception:
            pos = [0, 0, 0]
        affine = np.array([
            [dc * cx, dr * rx, dz * nx, pos[0]],
            [dc * cy, dr * ry, dz * ny, pos[1]],
            [dc * cz, dr * rz, dz * nz, pos[2]],
            [0, 0, 0, 1],
        ])
        return affine

    def _save_roi_mask(self, label, slice_idx):
        if slice_idx not in self._roi_items or label not in self._roi_items[slice_idx]:
            return
        ellipse = self._roi_items[slice_idx][label]
        rect = ellipse.rect()
        h, w = self._slices[0].pixel_array.shape[:2]
        d = len(self._slices)
        mask = np.zeros((w, h, d), dtype=np.uint8)
        cx = rect.x() + rect.width() / 2
        cy = rect.y() + rect.height() / 2
        rx = rect.width() / 2
        ry = rect.height() / 2
        yy, xx = np.mgrid[:h, :w]
        inside = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1
        mask[xx[inside], yy[inside], slice_idx] = 1
        affine = self._compute_affine()
        nii = nib.Nifti1Image(mask, affine)
        fpath = self._mask_filename(label, rx)
        nib.save(nii, fpath)
        print(f'[mask] Saved {fpath}')
        ellipse.setData(3, fpath)

    def _delete_roi_mask_file(self, label=None, filepath=None):
        if filepath is not None:
            if os.path.exists(filepath):
                os.remove(filepath)
                print(f'[mask] Deleted {filepath}')
            return
        if label is not None:
            fpath = self._mask_filename(label)
            if os.path.exists(fpath):
                os.remove(fpath)
                print(f'[mask] Deleted {fpath}')

    def _load_existing_masks(self):
        if not self._slices:
            return
        mask_dir = self._mask_dir
        if not mask_dir or not os.path.isdir(mask_dir):
            return
        for fname in os.listdir(mask_dir):
            if not fname.endswith('.nii'):
                continue
            stem = fname[:-4]
            file_r = None
            m_r = re.search(r'_r(\d+\.?\d*)$', stem)
            if m_r:
                file_r = float(m_r.group(1))
                stem = stem[:m_r.start()]
            stem = re.sub(r'_b\d+$', '', stem)
            label = stem
            if label not in self._roi_order:
                print(f'[mask] Skipping unknown label: {label}')
                continue
            fpath = os.path.join(mask_dir, fname)
            try:
                nii = nib.load(fpath)
                data = nii.get_fdata()
            except Exception as e:
                print(f'[mask] Error loading {fpath}: {e}')
                continue
            if data.ndim != 3:
                continue
            w, h, d = data.shape
            placed = False
            for slice_idx in range(d):
                mask_slice = data[:, :, slice_idx]
                if not np.any(mask_slice):
                    continue
                xs, ys = np.where(mask_slice)
                if len(xs) == 0:
                    continue
                cx = float(xs.min() + xs.max()) / 2
                cy = float(ys.min() + ys.max()) / 2
                if file_r is not None:
                    r = file_r
                else:
                    # Legacy file without radius: force circle from bbox
                    r = max(float(xs.max() - xs.min() + 1),
                            float(ys.max() - ys.min() + 1)) / 2
                scheme = self._roi_color_scheme(label)
                rgb = scheme['rgb']
                pen = QPen(QColor(*rgb), 0.5)
                brush = QBrush(QColor(*rgb, 40))
                ellipse = QGraphicsEllipseItem(
                    cx - r, cy - r, r * 2, r * 2)
                ellipse.setPen(pen)
                ellipse.setBrush(brush)
                ellipse.setFlag(
                    QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable, True)
                ellipse.setZValue(50)
                ellipse.setData(0, label)
                ellipse.setData(1, slice_idx)
                ellipse.setData(3, fpath)
                if label in self._roi_items.get(slice_idx, {}):
                    print(f'[mask] Skipping duplicate: {label}')
                    placed = True
                    break
                self.scene.addItem(ellipse)
                # Number label in center of ROI
                m = re.search(r'(\d+)$', label)
                if m:
                    num_item = QGraphicsSimpleTextItem(m.group(1))
                    num_item.setBrush(QBrush(QColor(*rgb)))
                    num_item.setZValue(55)
                    font = num_item.font()
                    font.setPointSize(4)
                    num_item.setFont(font)
                    num_item.setParentItem(ellipse)
                    br = num_item.boundingRect()
                    num_item.setPos(cx - br.width() / 2,
                                    cy - br.height() / 2)
                    ellipse.setData(4, num_item)
                self._roi_items.setdefault(slice_idx, {})[label] = ellipse
                if label in self._roi_buttons:
                    s = self._roi_color_scheme(label)
                    self._roi_buttons[label].setStyleSheet(
                        'QPushButton {'
                        f'  background-color: {s["filled"]}; color: white;'
                        '  border: none; padding: 4px 8px; font-size: 10px;'
                        '}'
                    )
                    self._roi_buttons[label].setEnabled(True)
                if label in self._roi_order:
                    idx = self._roi_order.index(label)
                    if idx >= self._current_roi_idx:
                        self._current_roi_idx = idx + 1
                print(f'[mask] Loaded {label} on slice {slice_idx}')
                placed = True
                break
            if not placed:
                print(f'[mask] Warning: {fname} has no non-zero voxels')
        self._update_case_btn_color()
        self._rebuild_case_menu()

    def _update_case_btn_color(self):
        total = len(self._roi_order)
        placed = sum(len(items) for items in self._roi_items.values())
        if placed == 0:
            bg = '#4a4a4a'
            hover = '#5a5a5a'
        elif placed >= total:
            bg = '#1a6b1a'
            hover = '#2a8b2a'
        else:
            bg = '#8b1a1a'
            hover = '#ab3a3a'
        self.case_btn.setStyleSheet(
            'QToolButton {'
            f'  background-color: {bg}; color: white;'
            '  border: none; padding: 4px 12px; font-size: 12px;'
            '}'
            f'QToolButton:hover {{ background-color: {hover}; }}'
            'QToolButton::menu-indicator {'
            '  subcontrol-position: right center; width: 6px; }'
        )

    def _get_case_completion(self, series_idx):
        series = self.series_list[series_idx]
        patient_dir = os.path.dirname(series['files'][0])
        mask_dir = os.path.join(patient_dir, 'roi_masks')
        if not os.path.isdir(mask_dir):
            return 'empty'
        expected = len(self._roi_order)
        if expected == 0:
            return 'complete'
        count = len([f for f in os.listdir(mask_dir) if f.endswith('.nii')])
        if count == 0:
            return 'empty'
        elif count >= expected:
            return 'complete'
        return 'partial'

    @staticmethod
    def _make_status_icon(status):
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {'complete': '#1a6b1a', 'partial': '#8b1a1a', 'empty': '#666666'}
        color = QColor(colors.get(status, '#666666'))
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 10, 10)
        p.end()
        return QIcon(pixmap)

    @staticmethod
    def _roi_color_scheme(label):
        if label.startswith('L'):
            if 'Cortex' in label:
                return {'unfilled': '#1a4a1a', 'filled': '#2a8a2a', 'rgb': (60, 220, 60)}
            return {'unfilled': '#1a4a4a', 'filled': '#1a8a7a', 'rgb': (60, 220, 180)}
        if 'Cortex' in label:
            return {'unfilled': '#4a1a1a', 'filled': '#8b1a1a', 'rgb': (220, 60, 60)}
        return {'unfilled': '#4a2a1a', 'filled': '#993300', 'rgb': (220, 160, 60)}

    def _rebuild_case_menu(self):
        total_cases = len(self.series_list)
        menu = QMenu()
        menu.setStyleSheet(
            'QMenu { background-color: #4a4a4a; color: white; }'
            'QMenu::item:selected { background-color: #5a5a5a; }'
        )
        for i, s in enumerate(self.series_list):
            status = self._get_case_completion(i)
            icon = self._make_status_icon(status)
            action = menu.addAction(
                icon, f'Case {i + 1}/{total_cases} - {s["name"]}')
            action.setIconVisibleInMenu(True)
            action.setData(i)
        menu.triggered.connect(self._on_case_action)
        self.case_btn.setMenu(menu)

    def _roi_diameter_for(self, label):
        return self._cortex_diameter if 'Cortex' in label else self._medulla_diameter

    def _hide_stamp(self):
        if self._stamp_item is not None:
            self._stamp_item.setVisible(False)
        if self._stamp_label is not None:
            self._stamp_label.setVisible(False)

    def _update_stamp(self, scene_pos):
        if self._current_roi_idx >= len(self._roi_order):
            self._hide_stamp()
            return
        label = self._roi_order[self._current_roi_idx]
        sidx = self._slice_idx
        if sidx in self._roi_items and label in self._roi_items[sidx]:
            self._hide_stamp()
            return

        d_mm = self._roi_diameter_for(label)
        ds = self._slices[self._slice_idx] if self._slices else None
        if ds is not None:
            try:
                spacing = ds.PixelSpacing
                px_per_mm = 1.0 / float(spacing[0])
            except (AttributeError, IndexError, ValueError):
                px_per_mm = 1.0
        else:
            px_per_mm = 1.0

        radius_px = d_mm * px_per_mm / 2.0

        if self._stamp_item is None:
            self._stamp_item = QGraphicsEllipseItem()
            self._stamp_item.setZValue(100)
            self.scene.addItem(self._stamp_item)

        if self._stamp_label is None:
            self._stamp_label = QGraphicsTextItem()
            self._stamp_label.setZValue(100)
            self._stamp_label.setDefaultTextColor(QColor(255, 255, 255))
            self.scene.addItem(self._stamp_label)

        scheme = self._roi_color_scheme(label)
        rgb = scheme['rgb']
        self._stamp_item.setPen(QPen(QColor(*rgb), 0.5))
        self._stamp_item.setBrush(QBrush(QColor(*rgb, 40)))

        self._stamp_item.setRect(
            scene_pos.x() - radius_px, scene_pos.y() - radius_px,
            radius_px * 2, radius_px * 2,
        )
        self._stamp_item.setVisible(True)

        # Update label
        self._stamp_label.setPlainText(label)
        self._stamp_label.setDefaultTextColor(QColor(*rgb))
        # Semi-transparent dark background
        self._stamp_label.setHtml(
            f'<div style="background: rgba(0,0,0,51); padding: 1px 4px; '
            f'border-radius: 2px; color: rgb{rgb}; font-size: 8px;">{label}</div>'
        )
        text_rect = self._stamp_label.boundingRect()
        if label.startswith('L'):
            label_x = scene_pos.x() + radius_px + 4
        else:
            label_x = scene_pos.x() - radius_px - 4 - text_rect.width()
        self._stamp_label.setPos(
            label_x,
            scene_pos.y() - text_rect.height() / 2,
        )
        self._stamp_label.setVisible(True)

    def _place_roi(self, scene_pos):
        if self._current_roi_idx >= len(self._roi_order):
            return False
        label = self._roi_order[self._current_roi_idx]
        sidx = self._slice_idx
        if sidx in self._roi_items and label in self._roi_items[sidx]:
            return False

        d_mm = self._roi_diameter_for(label)
        ds = self._slices[self._slice_idx] if self._slices else None
        if ds is not None:
            try:
                spacing = ds.PixelSpacing
                px_per_mm = 1.0 / float(spacing[0])
            except (AttributeError, IndexError, ValueError):
                px_per_mm = 1.0
        else:
            px_per_mm = 1.0

        radius_px = d_mm * px_per_mm / 2.0

        scheme = self._roi_color_scheme(label)
        rgb = scheme['rgb']
        pen = QPen(QColor(*rgb), 0.5)
        brush = QBrush(QColor(*rgb, 40))
        ellipse = QGraphicsEllipseItem(
            scene_pos.x() - radius_px, scene_pos.y() - radius_px,
            radius_px * 2, radius_px * 2,
        )
        ellipse.setPen(pen)
        ellipse.setBrush(brush)
        ellipse.setFlag(
            QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable, True)
        ellipse.setZValue(50)
        ellipse.setData(0, label)
        ellipse.setData(1, sidx)
        self.scene.addItem(ellipse)

        # Number label in center of ROI
        m = re.search(r'(\d+)$', label)
        if m:
            num_item = QGraphicsSimpleTextItem(m.group(1))
            num_item.setBrush(QBrush(QColor(*rgb)))
            num_item.setZValue(55)
            font = num_item.font()
            font.setPointSize(4)
            num_item.setFont(font)
            num_item.setParentItem(ellipse)
            br = num_item.boundingRect()
            num_item.setPos(scene_pos.x() - br.width() / 2,
                            scene_pos.y() - br.height() / 2)
            ellipse.setData(4, num_item)

        self._roi_items.setdefault(sidx, {})[label] = ellipse

        # Mark button as filled
        if label in self._roi_buttons:
            s = self._roi_color_scheme(label)
            self._roi_buttons[label].setStyleSheet(
                'QPushButton {'
                f'  background-color: {s["filled"]}; color: white;'
                '  border: none; padding: 4px 8px; font-size: 10px;'
                '}'
            )
            self._roi_buttons[label].setEnabled(True)

        # Save NIfTI mask
        self._save_roi_mask(label, sidx)
        self._update_case_btn_color()
        self._rebuild_case_menu()

        # Advance to next
        self._current_roi_idx += 1

        self._hide_stamp()

        return True

    def _on_roi_selection_changed(self):
        for sidx, items in self._roi_items.items():
            for item in items.values():
                label = item.data(0)
                scheme = self._roi_color_scheme(label)
                rgb = scheme['rgb']
                if item.isSelected():
                    item.setPen(QPen(QColor(180, 80, 255), 1))
                    # Show label below
                    label_item = item.data(2)
                    if label_item is None:
                        label_item = QGraphicsTextItem()
                        label_item.setHtml(
                            f'<div style="color: rgb(180,80,255); '
                            f'font-size: 7px;">{label}</div>'
                        )
                        label_item.setZValue(60)
                        self.scene.addItem(label_item)
                        item.setData(2, label_item)
                    ir = item.rect()
                    label_item.setPos(
                        ir.x() + ir.width() / 2 - label_item.boundingRect().width() / 2,
                        ir.y() + ir.height() + 2,
                    )
                    label_item.setVisible(True)
                    # Button background purple while selected
                    btn = self._roi_buttons.get(label)
                    if btn is not None and btn.property('_orig_style') is None:
                        btn.setProperty('_orig_style', btn.styleSheet())
                        btn.setStyleSheet(
                            'QPushButton { background-color: rgb(180,80,255); color: white;'
                            ' border: none; padding: 4px 8px; font-size: 10px; }'
                        )
                    # Number turns purple too
                    num_item = item.data(4)
                    if num_item is not None:
                        num_item.setBrush(QBrush(QColor(180, 80, 255)))
                else:
                    item.setPen(QPen(QColor(*rgb), 0.5))
                    # Restore button background
                    btn = self._roi_buttons.get(label)
                    if btn is not None:
                        stored = btn.property('_orig_style')
                        if stored is not None:
                            btn.setStyleSheet(stored)
                            btn.setProperty('_orig_style', None)
                    label_item = item.data(2)
                    if label_item is not None:
                        label_item.setVisible(False)
                    # Restore number color
                    num_item = item.data(4)
                    if num_item is not None:
                        num_item.setBrush(QBrush(QColor(*rgb)))

    def _on_roi_button_clicked(self, label):
        found_sidx = None
        found_item = None
        for sidx, items in self._roi_items.items():
            if label in items:
                found_sidx = sidx
                found_item = items[label]
                break
        if found_item is None:
            return
        # Navigate to the slice if needed
        if found_sidx != self._slice_idx:
            self._slice_idx = found_sidx
            self.slider.blockSignals(True)
            self.slider.setValue(self._slice_idx)
            self.slider.blockSignals(False)
            self._show_slice()
        self.scene.clearSelection()
        found_item.setSelected(True)

    def _on_mouse_move(self, event):
        if self._wl_dragging:
            # Adjust window/level based on mouse delta
            pos = event.pos()
            dx = pos.x() - self._wl_start_pos.x()
            dy = pos.y() - self._wl_start_pos.y()
            base_c, base_w = self._wl_start_values
            # Sensitivity: 1 pixel ≈ 0.5% of width for width, 1 pixel ≈ 0.5 for center
            sens = 0.5
            w = max(1.0, base_w + dx * sens)
            c = base_c + dy * sens
            self._apply_window(c, w)
            return

        scene_pos = self.view.mapToScene(event.pos())
        if self.pixmap_item.pixmap() is None:
            return
        self._update_stamp(scene_pos)

    def _on_mouse_middle_press(self, event):
        if self._raw_images is None:
            return
        self._wl_dragging = True
        self._wl_start_pos = event.pos()
        c = self._window_center
        w = self._window_width
        if c is None:
            c = float(self._raw_images[self._slice_idx].mean())
        if w is None or w <= 0:
            w = float(self._raw_images[self._slice_idx].ptp())
            if w <= 0:
                w = 1.0
        self._wl_start_values = (c, w)

    def _on_mouse_release(self, event):
        if self._wl_dragging:
            self._wl_dragging = False
            self._wl_start_pos = None
            self._wl_start_values = None
            self._save_windowing()

    def _on_mouse_press(self, event):
        scene_pos = self.view.mapToScene(event.pos())
        # Check if clicking on an existing ROI (select it)
        for item in self.scene.items(scene_pos):
            if isinstance(item, QGraphicsEllipseItem) and item != self._stamp_item:
                self.scene.clearSelection()
                item.setSelected(True)
                return True

        # Otherwise try to place a new ROI
        if self.pixmap_item.pixmap() is None:
            return True
        self.scene.clearSelection()
        self._place_roi(scene_pos)
        return True

    def _delete_selected_roi(self):
        selected = self.scene.selectedItems()
        if not selected:
            return
        for item in selected:
            if not isinstance(item, QGraphicsEllipseItem) or item == self._stamp_item:
                continue
            label = item.data(0)
            sidx = item.data(1)
            # Delete NIfTI mask file
            fpath = item.data(3)
            self._delete_roi_mask_file(filepath=fpath)
            if sidx in self._roi_items and label in self._roi_items[sidx]:
                del self._roi_items[sidx][label]
                if not self._roi_items[sidx]:
                    del self._roi_items[sidx]
            # Remove associated label
            label_item = item.data(2)
            if label_item is not None:
                self.scene.removeItem(label_item)
            self.scene.removeItem(item)

            # Reset button to unfilled color
            if label in self._roi_buttons:
                s = self._roi_color_scheme(label)
                self._roi_buttons[label].setStyleSheet(
                    'QPushButton {'
                    f'  background-color: {s["unfilled"]}; color: white;'
                    '  border: none; padding: 4px 8px; font-size: 10px;'
                    '}'
                )
                self._roi_buttons[label].setEnabled(False)

            # Find the position in order and reset current_roi_idx
            if label in self._roi_order:
                idx = self._roi_order.index(label)
                if idx < self._current_roi_idx:
                    self._current_roi_idx = idx
        self._update_case_btn_color()
        self._rebuild_case_menu()

    def _confirm_reset_rois(self):
        reply = QMessageBox.question(
            self, 'Confirm Reset',
            'This will delete all placed ROIs for the current case.\n'
            'Are you sure?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._delete_all_mask_files()
            self._reset_all_rois()
            self._update_case_btn_color()
            self._rebuild_case_menu()

    def _delete_all_mask_files(self):
        for sidx, items in list(self._roi_items.items()):
            for label, item in list(items.items()):
                fpath = item.data(3)
                self._delete_roi_mask_file(filepath=fpath)

    def _delete_all_masks_all_cases(self):
        deleted = 0
        for series in self.series_list:
            patient_dir = os.path.dirname(series['files'][0])
            mask_dir = os.path.join(patient_dir, 'roi_masks')
            if not os.path.isdir(mask_dir):
                continue
            for fname in os.listdir(mask_dir):
                if not fname.endswith('.nii'):
                    continue
                os.remove(os.path.join(mask_dir, fname))
                deleted += 1
                print(f'[mask] Deleted {fname}')
        self._reset_all_rois()
        self._update_case_btn_color()
        self._rebuild_case_menu()
        print(f'[mask] Mass deletion complete: {deleted} files removed')

    def _reset_all_rois(self):
        for sidx, items in list(self._roi_items.items()):
            for label, item in list(items.items()):
                label_item = item.data(2)
                if label_item is not None:
                    self.scene.removeItem(label_item)
                self.scene.removeItem(item)
        self._roi_items.clear()
        self._current_roi_idx = 0
        for label, btn in self._roi_buttons.items():
            s = self._roi_color_scheme(label)
            btn.setStyleSheet(
                'QPushButton {'
                f'  background-color: {s["unfilled"]}; color: white;'
                '  border: none; padding: 4px 8px; font-size: 10px;'
                '}'
            )
            btn.setEnabled(False)

    def _precompute_images(self, raw_arrays):
        """Normalise raw float64 arrays to uint8 using DICOM windowing.

        Args:
            raw_arrays: List of float64 numpy arrays (pixel data).

        Returns:
            List of uint8 numpy arrays.
        """
        images = []
        for arr in raw_arrays:
            vmin = arr.min()
            vmax = arr.max()
            if vmax == vmin:
                vmax = vmin + 1
            arr = np.clip(arr, vmin, vmax)
            arr = ((arr - vmin) / (vmax - vmin) * 255).astype(np.uint8)
            images.append(arr)
        return images

    # ------------------------------------------------------------------
    # Window / Level
    # ------------------------------------------------------------------

    def _apply_window(self, center, width):
        """Re-normalise the current slice with *center* / *width* and refresh."""
        self._window_center = center
        self._window_width = width
        self._update_window_btn()
        if self._raw_images is None or self._slice_idx >= len(self._raw_images):
            return
        arr = self._raw_images[self._slice_idx].copy()
        if center is not None and width is not None and width > 0:
            vmin = center - width / 2
            vmax = center + width / 2
        else:
            vmin = arr.min()
            vmax = arr.max()
        if vmax == vmin:
            vmax = vmin + 1
        arr = np.clip(arr, vmin, vmax)
        arr = ((arr - vmin) / (vmax - vmin) * 255).astype(np.uint8)
        pixmap = self._numpy_to_pixmap(arr)
        self.pixmap_item.setPixmap(pixmap)

    def _windowing_path(self):
        if not self.series_list:
            return None
        d = self._mask_dir
        if d is None:
            return None
        return os.path.join(d, 'windowing.json')

    def _save_windowing(self):
        path = self._windowing_path()
        if path is None:
            return
        if self._window_center is not None and self._window_width is not None:
            try:
                with open(path, 'w') as f:
                    json.dump({
                        'window_center': self._window_center,
                        'window_width': self._window_width,
                    }, f)
            except OSError:
                pass

    def _load_windowing(self):
        path = self._windowing_path()
        if path is None or not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            wc = data.get('window_center')
            ww = data.get('window_width')
            if wc is not None and ww is not None:
                return (float(wc), float(ww))
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        return None

    def _update_window_btn(self):
        if self._window_center is not None and self._window_width is not None:
            self.window_btn.setText(
                f'W/L: {self._window_center:.1f} / {self._window_width:.1f}')
        else:
            self.window_btn.setText('W/L: auto')

    def _open_window_dialog(self):
        c = self._window_center
        w = self._window_width
        dc = self._default_window_center
        dw = self._default_window_width
        dialog = WindowDialog(self, center=c, width=w,
                              default_center=dc, default_width=dw)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_c, new_w = dialog.get_values()
            self._apply_window(new_c, new_w)
            self._save_windowing()

    def _init_ui(self):
        name = self.series_list[0]['name']
        self.setWindowTitle(f'renal-DWI-ROI-Annotator - {name}')
        self.setStyleSheet(f'background-color: {SETTINGS["bg_color"]};')

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # --- Image row (view + slider) --------------------------------------
        img_row = QHBoxLayout()
        img_row.setSpacing(6)

        self.scene = QGraphicsScene()
        self.scene.selectionChanged.connect(self._on_roi_selection_changed)
        self.view = ImageView()
        self.view.set_viewer(self)
        self.view.setScene(self.scene)
        self.view.setStyleSheet('border: none; background-color: black;')
        self.view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)

        img_row.addWidget(self.view, 1)

        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, self.num_slices - 1)
        self.slider.setValue(0)
        self.slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.setFixedWidth(30)
        img_row.addWidget(self.slider)

        root.addLayout(img_row, 1)

        # --- Top row: Case + Slice (left), B + Settings (right) --------------
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        menu_btn_style = (
            'QToolButton { background-color: #4a4a4a; color: white; '
            'border: none; padding: 4px 12px; font-size: 12px; }'
            'QToolButton:hover { background-color: #5a5a5a; }'
            'QToolButton::menu-indicator { subcontrol-position: right center; width: 6px; }'
        )
        btn_style = (
            'QPushButton { background-color: #4a4a4a; color: white; '
            'border: none; padding: 4px 12px; font-size: 12px; }'
            'QPushButton:hover { background-color: #5a5a5a; }'
        )

        total_cases = len(self.series_list)
        self.case_btn = QToolButton()
        self.case_btn.setText(
            f'Case 1/{total_cases} - {self.series_list[0]["name"]}')
        self.case_btn.setStyleSheet(menu_btn_style)
        self.case_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._rebuild_case_menu()
        top_row.addWidget(self.case_btn)

        self.slice_btn = QToolButton()
        self.slice_btn.setText('Slice 1/1')
        self.slice_btn.setStyleSheet(menu_btn_style)
        self.slice_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._rebuild_slice_menu()
        top_row.addWidget(self.slice_btn)

        top_row.addStretch()

        self.prev_btn = QPushButton('Previous Case (Q)')
        self.next_btn = QPushButton('Next Case (E)')
        self.prev_btn.setStyleSheet(btn_style)
        self.next_btn.setStyleSheet(btn_style)
        self.prev_btn.clicked.connect(self._prev_series)
        self.next_btn.clicked.connect(self._next_series)
        top_row.addWidget(self.prev_btn)
        top_row.addWidget(self.next_btn)

        top_row.addStretch()

        self.bvalue_btn = QToolButton()
        if self._b_values:
            self.bvalue_btn.setText(f'b={self._current_b_value}')
        elif self._series_is_tracew:
            self.bvalue_btn.setText(
                format_tracew_label(self._tracew_b_values))
        else:
            self.bvalue_btn.setText('b-value N/A')
        self.bvalue_btn.setStyleSheet(menu_btn_style)
        self.bvalue_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._rebuild_bvalue_menu()
        top_row.addWidget(self.bvalue_btn)

        self.window_btn = QPushButton()
        self.window_btn.setStyleSheet(btn_style)
        self.window_btn.clicked.connect(self._open_window_dialog)
        self._update_window_btn()
        top_row.addWidget(self.window_btn)

        self.reset_rois_btn = QPushButton('Reset')
        self.reset_rois_btn.setStyleSheet(btn_style)
        self.reset_rois_btn.clicked.connect(self._confirm_reset_rois)
        self.reset_rois_btn.setFixedWidth(80)
        top_row.addWidget(self.reset_rois_btn)

        self.settings_btn = QPushButton('Settings')
        self.settings_btn.setStyleSheet(btn_style)
        self.settings_btn.clicked.connect(self._open_settings)
        self.settings_btn.setFixedWidth(80)
        top_row.addWidget(self.settings_btn)

        root.addLayout(top_row)

        # --- Bottom 2-column layout ------------------------------------------
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        self._left_container = QWidget()
        self._left_container.setStyleSheet(
            'background-color: #1e1e1e; border-radius: 4px;')

        self._right_container = QWidget()
        self._right_container.setStyleSheet(
            'background-color: #1e1e1e; border-radius: 4px;')

        self._bottom_row = bottom_row
        bottom_row.addWidget(self._left_container, 1)
        bottom_row.addWidget(self._right_container, 1)
        root.addLayout(bottom_row)

        # Build ROI buttons into left/right panels
        self._build_roi_buttons()

        # --- Show first slice ------------------------------------------------
        self._show_slice()

        # --- Window size & position ------------------------------------------
        self.resize(900, 900)
        self._center_on_screen()

    def _center_on_screen(self):
        screen = QApplication.screens()[0].availableGeometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

    # ------------------------------------------------------------------
    # ROI buttons
    # ------------------------------------------------------------------

    def _build_roi_buttons(self):
        self._roi_buttons = {}
        self._roi_order_from_counts()

        def make_button(label):
            s = self._roi_color_scheme(label)
            style = (
                'QPushButton {'
                f'  background-color: {s["unfilled"]}; color: white;'
                '  border: none; padding: 4px 8px; font-size: 10px;'
                '}'
            )
            btn = QPushButton(label)
            btn.setStyleSheet(style)
            btn.setFixedWidth(75)
            btn.setEnabled(False)
            btn.clicked.connect(lambda: self._on_roi_button_clicked(label))
            self._roi_buttons[label] = btn
            return btn

        def side_labels(prefix, zone):
            n = self._roi_counts.get(f'{prefix}_{zone}', 0)
            return [f'{prefix}_{zone}{i}' for i in range(1, n + 1)]

        def build_side(container, prefix):
            layout = QVBoxLayout(container)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(4)

            all_labels = side_labels(prefix, 'Cortex') + side_labels(prefix, 'Medulla')

            for chunk_start in range(0, len(all_labels), 5):
                row = QHBoxLayout()
                row.setSpacing(4)
                chunk = all_labels[chunk_start:chunk_start + 5]
                if prefix == 'L':
                    row.addStretch()
                for lbl in chunk:
                    row.addWidget(make_button(lbl))
                if prefix == 'R':
                    row.addStretch()
                layout.addLayout(row)

            layout.addStretch()

        build_side(self._left_container, 'R')
        build_side(self._right_container, 'L')

    def _rebuild_roi_buttons(self):
        self._reset_all_rois()
        for container in (self._left_container, self._right_container):
            idx = self._bottom_row.indexOf(container)
            self._bottom_row.removeWidget(container)
            container.deleteLater()
        self._left_container = QWidget()
        self._left_container.setStyleSheet(
            'background-color: #1e1e1e; border-radius: 4px;')
        self._right_container = QWidget()
        self._right_container.setStyleSheet(
            'background-color: #1e1e1e; border-radius: 4px;')
        self._build_roi_buttons()
        self._bottom_row.insertWidget(0, self._left_container, 1)
        self._bottom_row.insertWidget(1, self._right_container, 1)

    # ------------------------------------------------------------------
    # Image helpers
    # ------------------------------------------------------------------

    def _numpy_to_pixmap(self, arr):
        h, w = arr.shape
        qimg = QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8)
        return QPixmap.fromImage(qimg)

    def _show_slice(self):
        self._apply_window(self._window_center, self._window_width)
        self._fit_image()

        # Show ROIs for current slice, hide others
        for sidx, items in self._roi_items.items():
            visible = (sidx == self._slice_idx)
            for item in items.values():
                item.setVisible(visible)

        self._hide_stamp()

        self.slice_btn.setText(
            f'Slice {self._slice_idx + 1}/{self.num_slices}')

    def _fit_image(self):
        if self.pixmap_item.pixmap() is not None:
            self.view.fitInView(
                self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _open_settings(self):
        dialog = SettingsDialog(
            self,
            roi_counts=self._roi_counts,
            cortex_diameter=self._cortex_diameter,
            medulla_diameter=self._medulla_diameter,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if getattr(dialog, '_confirmed_delete_all', False):
                self._delete_all_masks_all_cases()
                return
            self._roi_counts = dialog.get_roi_counts()
            self._cortex_diameter = dialog.get_cortex_diameter()
            self._cortex_area = dialog.get_cortex_area()
            self._medulla_diameter = dialog.get_medulla_diameter()
            self._medulla_area = dialog.get_medulla_area()
            _save_settings({
                'roi_counts': self._roi_counts,
                'cortex_diameter': self._cortex_diameter,
                'medulla_diameter': self._medulla_diameter,
            })
            self._rebuild_roi_buttons()
            self._load_existing_masks()

    def _rebuild_slice_menu(self):
        menu = QMenu()
        menu.setStyleSheet(
            'QMenu { background-color: #4a4a4a; color: white; }'
            'QMenu::item:selected { background-color: #5a5a5a; }'
        )
        for i in range(self.num_slices):
            action = menu.addAction(f'Slice {i + 1}/{self.num_slices}')
            action.setData(i)
        menu.triggered.connect(self._on_slice_action)
        self.slice_btn.setMenu(menu)
        self.slice_btn.setText(f'Slice {self._slice_idx + 1}/{self.num_slices}')

    def _on_slice_action(self, action):
        idx = action.data()
        if idx == self._slice_idx:
            return
        self._slice_idx = idx
        self.slider.blockSignals(True)
        self.slider.setValue(self._slice_idx)
        self.slider.blockSignals(False)
        self._show_slice()

    def _rebuild_bvalue_menu(self):
        menu = QMenu()
        menu.setStyleSheet(
            'QMenu { background-color: #4a4a4a; color: white; }'
            'QMenu::item:selected { background-color: #5a5a5a; }'
        )
        if self._b_values:
            for bv in self._b_values:
                action = menu.addAction(f'b={bv}')
                action.setData(bv)
            menu.triggered.connect(self._on_b_value_action)
        elif self._series_is_tracew:
            action = menu.addAction(
                format_tracew_label(self._tracew_b_values))
            action.setEnabled(False)
        self.bvalue_btn.setMenu(menu)

    def _on_b_value_action(self, action):
        bv = action.data()
        if bv == self._current_b_value:
            return
        self._current_b_value = bv
        _save_settings({'last_b_value': bv})
        self.bvalue_btn.setText(f'b={bv}')
        self._slices = self._b_value_map[bv]
        self._raw_images = self._raw_images_map[bv]
        self._images = self._images_map[bv]
        self.num_slices = len(self._slices)
        self._slice_idx = min(self._slice_idx, self.num_slices - 1)
        self.slider.blockSignals(True)
        self.slider.setRange(0, self.num_slices - 1)
        self.slider.setValue(self._slice_idx)
        self.slider.blockSignals(False)
        self._reset_all_rois()
        self._load_existing_masks()
        self._rebuild_slice_menu()
        self._show_slice()

    def _on_case_action(self, action):
        idx = action.data()
        if idx == self.current_series_idx:
            return
        self._switch_to_series(idx)

    def _on_slider(self, value):
        self._slice_idx = value
        self._show_slice()

    def _on_wheel(self, event: QWheelEvent):
        # angleDelta() returns eighths of a degree; 120 = 1 mouse notch.
        # On macOS trackpads angleDelta() is often 0 and pixelDelta() is
        # the actual scroll distance — we fall back to that and scale it.
        delta = event.angleDelta().y()
        if delta == 0:
            # Trackpad: scale pixel delta (~1 px ≈ 3°) so a normal
            # swipe accumulates ≈120 per "slice step".
            delta = event.pixelDelta().y() * 3

        self._scroll_accumulator += delta

        steps = self._scroll_accumulator // 120
        if steps != 0:
            self._scroll_accumulator -= steps * 120
            self._slice_idx = max(0, min(self.num_slices - 1,
                                         self._slice_idx - steps))
            self.slider.blockSignals(True)
            self.slider.setValue(self._slice_idx)
            self.slider.blockSignals(False)
            self._show_slice()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Q:
            self._prev_series()
        elif event.key() == Qt.Key.Key_E:
            self._next_series()
        elif event.key() == Qt.Key.Key_B and self._b_values:
            self.bvalue_btn.showMenu()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected_roi()
        super().keyPressEvent(event)

    def _prev_series(self):
        if self.current_series_idx > 0:
            self._switch_to_series(self.current_series_idx - 1)

    def _next_series(self):
        if self.current_series_idx < len(self.series_list) - 1:
            self._switch_to_series(self.current_series_idx + 1)

    def _switch_to_series(self, idx):
        self._save_windowing()
        prev_center = self._window_center
        prev_width = self._window_width
        preferred_b = self._current_b_value
        self.current_series_idx = idx
        self._load_current_series()
        # If the new case has no saved W/L file, carry over the W/L
        # from the previous case instead of reverting to DICOM defaults.
        if self._load_windowing() is None:
            if prev_center is not None and prev_width is not None:
                self._window_center = prev_center
                self._window_width = prev_width
        self._reset_all_rois()

        # Update b-value button
        self._rebuild_bvalue_menu()
        if self._b_values:
            self._current_b_value = preferred_b if preferred_b in self._b_values else self._b_values[0]
            self.bvalue_btn.setText(f'b={self._current_b_value}')
            self._slices = self._b_value_map[self._current_b_value]
            self._images = self._images_map[self._current_b_value]
            self._raw_images = self._raw_images_map[self._current_b_value]
        elif self._series_is_tracew:
            self.bvalue_btn.setText(
                format_tracew_label(self._tracew_b_values))
        else:
            self.bvalue_btn.setText('b-value N/A')

        self.num_slices = len(self._slices)
        self._slice_idx = 0
        self.slider.blockSignals(True)
        self.slider.setRange(0, self.num_slices - 1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.case_btn.setText(
            f'Case {idx + 1}/{len(self.series_list)} - '
            f'{self.series_list[idx]["name"]}'
        )
        self.setWindowTitle(
            f'renal-DWI-ROI-Annotator - '
            f'{self.series_list[idx]["name"]}'
        )
        self._rebuild_slice_menu()
        self._load_existing_masks()
        self._show_slice()
