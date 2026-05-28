from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ykv_transform.service import JobItem, JobResult, collect_jobs, convert_job, resolve_ffmpeg

STATUS_WAITING = "等待"
STATUS_RUNNING = "转换中"
STATUS_SUCCESS = "成功"
STATUS_FAILED = "失败"
STATUS_SKIPPED = "跳过"


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
    progress = Signal(int, str, object)
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

    def run(self) -> None:
        for index, job in enumerate(self.jobs):
            self.progress.emit(index, STATUS_RUNNING, None)
            result = convert_job(
                job,
                mode=self.mode,
                ffmpeg_path=self._ffmpeg,
                force=self.force,
            )
            status = STATUS_SUCCESS if result.success else STATUS_FAILED
            if result.success and result.message.startswith("跳过"):
                status = STATUS_SKIPPED
            self.progress.emit(index, status, result)
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
        clear_btn = QPushButton("清空列表")
        clear_btn.clicked.connect(self.clear_jobs)
        button_row.addWidget(add_files_btn)
        button_row.addWidget(add_folder_btn)
        button_row.addStretch()
        button_row.addWidget(clear_btn)
        layout.addLayout(button_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "输出", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        options = QGroupBox("转换选项")
        options_layout = QVBoxLayout(options)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("转换模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("点唱机模式 (H.264 + AAC)", "karaoke")
        self.mode_combo.addItem("快速模式 (无损封装)", "copy")
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        options_layout.addLayout(mode_row)

        self.force_checkbox = QCheckBox("覆盖已存在的 MP4 文件")
        options_layout.addWidget(self.force_checkbox)
        options_layout.addWidget(QLabel("输出位置: 源文件同目录"))
        layout.addWidget(options)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("开始转换")
        self.start_button.clicked.connect(self.start_conversion)
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

        self.start_button.setEnabled(False)
        self._set_inputs_enabled(False)
        self.completed_count = 0
        self.success_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.progress_bar.setMaximum(len(self.jobs))
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"进度: 0/{len(self.jobs)}")
        self.log("开始转换...")
        mode = self.mode_combo.currentData()
        self.worker = ConvertWorker(self.jobs, mode, self.force_checkbox.isChecked(), self)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_all.connect(self.on_finished)
        self.worker.start()

    def on_progress(self, index: int, status: str, result: JobResult | None) -> None:
        self.table.item(index, 2).setText(status)
        if result is None:
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
            self.log(result.message)
            if result.compatibility_hint:
                self.log(result.compatibility_hint)
        else:
            self.failed_count += 1
            self.log(result.message)

    def on_finished(self) -> None:
        self.start_button.setEnabled(True)
        self._set_inputs_enabled(True)
        self.log("全部任务完成。")
        summary = (
            f"成功 {self.success_count} 个，跳过 {self.skipped_count} 个，失败 {self.failed_count} 个。"
        )
        self.log(f"结果汇总: {summary}")
        if self.failed_count > 0:
            QMessageBox.warning(self, "转换完成（有失败）", f"{summary}\n请查看日志中的失败原因。")
        else:
            QMessageBox.information(self, "转换完成", summary)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        self.drop_area.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.force_checkbox.setEnabled(enabled)


def run_gui() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
