"""
Entry point for the renal-DWI-ROI-Annotator application.
"""

import sys
import os
import json

from PyQt6.QtWidgets import QApplication

from src.viewer import DicomViewer


def _default_data_path():
    """
    Resolve the default data directory from settings.json.

    The value in settings.json is treated as relative to the project root
    (one level above src/). This lets users change the default folder
    without touching any Python code.
    """
    settings_path = os.path.join(os.path.dirname(__file__), 'settings.json')
    try:
        with open(settings_path) as f:
            settings = json.load(f)
        rel = settings.get('default_data_path', 'DATA')
    except (FileNotFoundError, json.JSONDecodeError):
        rel = 'DATA'

    return os.path.join(os.path.dirname(__file__), rel)


def main():
    """
    Parse arguments, build the viewer, and start the Qt event loop.

    - Expects an optional directory path as the first CLI argument.
    - Falls back to the default path from settings.json.
    - Creates the QApplication before the viewer (required by Qt).
    """
    data_path = sys.argv[1] if len(sys.argv) > 1 else _default_data_path()

    if not os.path.isdir(data_path):
        print(f'Error: {data_path} is not a valid directory.')
        sys.exit(1)

    app = QApplication(sys.argv)
    viewer = DicomViewer(data_path)
    viewer.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
