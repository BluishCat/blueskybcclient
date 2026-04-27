import os
import sys

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        if hasattr(sys, '_MEIPASS'):
            path = os.path.join(sys._MEIPASS, relative_path)
            if os.path.exists(path):
                return path
    except Exception:
        pass

    # Fallback to dev mode or external path relative to the executable
    return os.path.join(get_app_dir(), relative_path)

def get_app_dir():
    """Get the directory where the application is located (for config files)"""
    if getattr(sys, 'frozen', False):
        # Bundled mode
        return os.path.dirname(sys.executable)
    else:
        # Development mode
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
