from __future__ import annotations

import sys
from threading import Event
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl

from ykv_transform.service import JobItem, JobResult, collect_jobs, convert_job, resolve_ffmpeg

STATUS_WAITING = "等待"
STATUS_RUNNING = "转换中"
STATUS_SUCCESS = "成功"
STATUS_FAILED = "失败"
STATUS_SKIPPED = "跳过"
STATUS_CANCELLED = "已中断"


class DropArea(QLabel):
    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText("将 .ykv / .kux 文件或文件夹拖放到此处")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #888; border-radius: 8px; color: #555; padding: 12px; }"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
        event.acceptProposedAction()


class ConvertWorker(QThread):
    progress = Signal(int, str, object, int)
    finished_all = Signal()

    def __init__(
        self,
        jobs: list[JobItem],
        mode: str,
        force: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self.mode = mode
        self.force = force
        self._ffmpeg = resolve_ffmpeg()
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        total = max(len(self.jobs), 1)
        for index, job in enumerate(self.jobs):
            if self._cancel_event.is_set():
                self.progress.emit(index, STATUS_CANCELLED, None, int((index / total) * 100))
                break
            self.progress.emit(index, STATUS_RUNNING, None, int((index / total) * 100))

            def _on_file_progress(file_percent: int) -> None:
                total_percent = int(((index + (file_percent / 100.0)) / total) * 100)
                self.progress.emit(index, STATUS_RUNNING, None, min(99, total_percent))

            result = convert_job(
                job,
                mode=self.mode,
                ffmpeg_path=self._ffmpeg,
                force=self.force,
                progress_callback=_on_file_progress,
                cancel_requested=lambda: self._cancel_event.is_set(),
            )
            status = STATUS_SUCCESS if result.success else STATUS_FAILED
            if result.success and result.message.startswith("跳过"):
                status = STATUS_SKIPPED
            if not result.success and "用户已取消转换" in result.message:
                status = STATUS_CANCELLED
            done_percent = int(((index + 1) / total) * 100)
            self.progress.emit(index, status, result, done_percent)
            if status == STATUS_CANCELLED:
                break
        self.finished_all.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YKV 转 MP4")
        self.resize(960, 680)
        self.jobs: list[JobItem] = []
        self.worker: ConvertWorker | None = None
        self.completed_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.output_dirs: list[Path] = []
        self.current_index = -1
        self.progress_dialog: QProgressDialog | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.drop_area = DropArea()
        self.drop_area.paths_dropped.connect(self.add_paths)
        layout.addWidget(self.drop_area)

        button_row = QHBoxLayout()
        add_files_btn = QPushButton("添加文件")
        add_files_btn.clicked.connect(self.choose_files)
        add_folder_btn = QPushButton("添加文件夹")
        add_folder_btn.clicked.connect(self.choose_folder)
        remove_selected_btn = QPushButton("删除选中")
        remove_selected_btn.clicked.connect(self.remove_selected_jobs)
        clear_btn = QPushButton("清空列表")
        clear_btn.clicked.connect(self.clear_jobs)
        button_row.addWidget(add_files_btn)
        button_row.addWidget(add_folder_btn)
        button_row.addWidget(remove_selected_btn)
        button_row.addStretch()
        button_row.addWidget(clear_btn)
        layout.addLayout(button_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "输出", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        options = QGroupBox("转换说明")
        options_layout = QVBoxLayout(options)
        options_layout.addWidget(QLabel("转换模式: 自动按兼容 MP4 输出（H.264 + AAC）"))
        options_layout.addWidget(QLabel("建议单次总时长不超过 120 分钟，避免处理时间过长。"))
        options_layout.addWidget(QLabel("输出位置: 源文件同目录"))
        layout.addWidget(options)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("转换 MP4")
        self.start_button.clicked.connect(self.start_conversion)
        self.start_button.setStyleSheet(
            "QPushButton { background-color: #0b6cfb; color: white; font-weight: 600; "
            "border-radius: 6px; padding: 8px 18px; }"
            "QPushButton:hover { background-color: #0959cc; }"
            "QPushButton:disabled { background-color: #9fbbe9; color: #f3f6ff; }"
        )
        action_row.addWidget(self.start_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.progress_label = QLabel("进度: 0/0")
        layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m")
        layout.addWidget(self.progress_bar)

        layout.addWidget(QLabel("日志"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.log("就绪。可拖入 .ykv / .kux 文件或文件夹。")

    def log(self, message: str) -> None:
        self.log_view.append(message)

    def add_paths(self, paths: list[str]) -> None:
        new_jobs = collect_jobs([Path(path) for path in paths])
        if not new_jobs:
            self.log("未找到可转换的 YKV/KUX 文件。")
            return

        existing = {job.input_path for job in self.jobs}
        added = 0
        for job in new_jobs:
            if job.input_path in existing:
                continue
            existing.add(job.input_path)
            self.jobs.append(job)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(job.input_path)))
            self.table.setItem(row, 1, QTableWidgetItem(str(job.output_path)))
            self.table.setItem(row, 2, QTableWidgetItem(STATUS_WAITING))
            added += 1

        self.log(f"已添加 {added} 个文件。")

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 YKV/KUX 文件",
            "",
            "YKV Files (*.ykv *.kux);;All Files (*.*)",
        )
        if paths:
            self.add_paths(paths)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            self.add_paths([folder])

    def clear_jobs(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.jobs.clear()
        self.table.setRowCount(0)
        self.progress_bar.setMaximum(1)
        self.progress_bar.setValue(0)
        self.progress_label.setText("进度: 0/0")
        self.log("已清空列表。")

    def remove_selected_jobs(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        ranges = self.table.selectedRanges()
        if not ranges:
            return
        rows: set[int] = set()
        for selected in ranges:
            for row in range(selected.topRow(), selected.bottomRow() + 1):
                rows.add(row)
        for row in sorted(rows, reverse=True):
            if 0 <= row < len(self.jobs):
                self.jobs.pop(row)
                self.table.removeRow(row)
        self.log(f"已删除 {len(rows)} 个文件。")

    def start_conversion(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.jobs:
            QMessageBox.information(self, "提示", "请先添加要转换的文件或文件夹。")
            return

        try:
            resolve_ffmpeg()
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"无法找到 FFmpeg:\n{exc}")
            return
        self.log("时长提示: 本次任务时长未知，已直接开始转换（可随时中断）。")

        self.start_button.setEnabled(False)
        self._set_inputs_enabled(False)
        self.completed_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.output_dirs = []
        self.current_index = -1
        self.progress_bar.setMaximum(len(self.jobs))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"进度: 0/{len(self.jobs)}")
        self._create_progress_dialog()
        self.log("开始转换...")
        self.worker = ConvertWorker(self.jobs, "karaoke", True, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_all.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, index: int, status: str, result: JobResult | None, total_percent: int) -> None:
        self.table.item(index, 2).setText(status)
        self._update_progress_dialog(index, total_percent)
        if result is None:
            if self.current_index != index:
                self.current_index = index
                self.log(f"正在处理: {self.jobs[index].input_path.name}")
            return

        self.completed_count += 1
        self.progress_bar.setValue(self.completed_count)
        self.progress_label.setText(f"进度: {self.completed_count}/{len(self.jobs)}")

        if result.vip_warning:
            self.log(f"警告 [{result.input_path.name}]: {result.vip_warning}")
        if result.success:
            if status == STATUS_SKIPPED:
                self.skipped_count += 1
            else:
                self.success_count += 1
            if result.output_path is not None:
                output_dir = result.output_path.parent
                if output_dir not in self.output_dirs:
                    self.output_dirs.append(output_dir)
            self.log(result.message)
            if result.compatibility_hint:
                self.log(result.compatibility_hint)
        else:
            self.failed_count += 1
            self.log(result.message)
        if status == STATUS_CANCELLED:
            self.log("用户已中断转换。")

    def on_finished(self) -> None:
        self.start_button.setEnabled(True)
        self._set_inputs_enabled(True)
        self._close_progress_dialog()
        self.log("全部任务完成。")
        summary = (
            f"成功 {self.success_count} 个，跳过 {self.skipped_count} 个，失败 {self.failed_count} 个。"
        )
        self.log(f"结果汇总: {summary}")
        self._show_completion_dialog(summary)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self.drop_area.setEnabled(enabled)
        self.start_button.setEnabled(enabled)

    def _create_progress_dialog(self) -> None:
        dialog = QProgressDialog("准备开始转换...", "中断转换", 0, 100, self)
        dialog.setWindowTitle("正在转换")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)
        dialog.canceled.connect(self._cancel_conversion)
        dialog.show()
        self.progress_dialog = dialog

    def _update_progress_dialog(self, index: int, percent: int) -> None:
        if self.progress_dialog is None:
            return
        safe_percent = max(0, min(100, percent))
        filename = self.jobs[index].input_path.name if 0 <= index < len(self.jobs) else ""
        self.progress_dialog.setLabelText(f"正在转换: {filename}\n进度: {safe_percent}%")
        self.progress_dialog.setValue(safe_percent)

    def _close_progress_dialog(self) -> None:
        if self.progress_dialog is None:
            return
        self.progress_dialog.setValue(100)
        self.progress_dialog.close()
        self.progress_dialog = None

    def _cancel_conversion(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.log("收到中断请求，正在停止转换...")
            self.worker.request_cancel()

    def _show_completion_dialog(self, summary: str) -> None:
        box = QMessageBox(self)
        title = "转换完成" if self.failed_count == 0 else "转换完成（有失败）"
        detail = summary if self.failed_count == 0 else f"{summary}\n请查看日志中的失败原因。"
        box.setWindowTitle(title)
        box.setText(detail)
        box.setIcon(
            QMessageBox.Icon.Information if self.failed_count == 0 else QMessageBox.Icon.Warning
        )
        open_btn: QAbstractButton | None = None
        if self.output_dirs:
            open_btn = box.addButton("打开文件夹", QMessageBox.ButtonRole.ActionRole)
        box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

        if open_btn is not None and box.clickedButton() is open_btn:
            folder = self.output_dirs[0]
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def run_gui() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
