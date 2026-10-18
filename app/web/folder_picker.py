from __future__ import annotations

from pathlib import Path


def pick_folder_dialog(initial_dir: str | None = None) -> str | None:
    """Open the native folder picker dialog (Windows/macOS/Linux)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            title="选择本地素材文件夹",
            initialdir=initial_dir or str(Path.home()),
            mustexist=True,
        )
    finally:
        root.destroy()
    if not selected:
        return None
    return str(Path(selected).resolve())


def resolve_image_picker_initial_dir(initial_dir: str | None = None) -> str:
    if initial_dir:
        candidate = Path(initial_dir)
        if candidate.is_file():
            return str(candidate.parent.resolve())
        images_dir = candidate / "商品图片"
        if images_dir.is_dir():
            return str(images_dir.resolve())
        if candidate.is_dir():
            return str(candidate.resolve())
    pictures = Path.home() / "Pictures"
    if pictures.is_dir():
        return str(pictures.resolve())
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return str(desktop.resolve())
    return str(Path.home())


def pick_image_files_dialog(initial_dir: str | None = None) -> list[str]:
    """Open the native image file picker and return absolute file paths."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return []

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()
    root.lift()
    root.focus_force()
    start_dir = resolve_image_picker_initial_dir(initial_dir)
    try:
        selected = filedialog.askopenfilenames(
            title="选择商品参考图片（可多选）",
            initialdir=start_dir,
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.webp *.gif *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
    finally:
        root.destroy()
    if not selected:
        return []
    return [str(Path(item).resolve()) for item in selected]


def pick_reference_files_dialog(initial_dir: str | None = None) -> list[str]:
    """Open the native file picker for reference materials (images + documents)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return []

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()
    root.lift()
    root.focus_force()
    start_dir = resolve_image_picker_initial_dir(initial_dir)
    try:
        selected = filedialog.askopenfilenames(
            title="选择参考资料（图片/Excel/文档，可多选）",
            initialdir=start_dir,
            filetypes=[
                (
                    "AI 可分析的资料",
                    "*.jpg *.jpeg *.png *.webp *.gif *.bmp "
                    "*.xlsx *.xls *.csv *.txt *.md *.json *.pdf *.doc *.docx",
                ),
                ("图片", "*.jpg *.jpeg *.png *.webp *.gif *.bmp"),
                ("表格与文档", "*.xlsx *.xls *.csv *.txt *.md *.json *.pdf *.doc *.docx"),
                ("所有文件", "*.*"),
            ],
        )
    finally:
        root.destroy()
    if not selected:
        return []
    return [str(Path(item).resolve()) for item in selected]
