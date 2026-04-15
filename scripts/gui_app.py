from __future__ import annotations

import subprocess
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dev_common import PROJECT_ROOT
from fastapi import HTTPException
from bridge_server.config import (
    WORK_DIR_OUTSIDE_ALLOWED_ROOTS_MESSAGE,
    is_path_within_allowed_roots,
)
from bridge_server.results import is_terminal_job_status
from bridge_server.schemas import CreateJobRequest
from gui_helpers import (
    EMPTY_TEXT,
    ENV_PATH,
    JOB_STATUS_FILTER_OPTIONS,
    ServiceStatus,
    UNAVAILABLE_TEXT,
    build_artifact_paths,
    build_service_summary_text,
    current_timestamp_text,
    create_job_with_service,
    load_job_result,
    load_job_metadata_text,
    load_job_prompt_text,
    load_recent_jobs,
    read_text_file_tail,
    load_service_status,
    load_settings,
    normalize_job_status_filter,
    open_in_file_manager,
    save_root_configuration,
)


WINDOW_GEOMETRY = "1200x800"
AUTO_REFRESH_MS = 1500
LOG_REFRESH_MS = 4000
MAX_RECENT_JOBS = 30


class ControlPanelApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex Bridge Local Control Panel")
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(1000, 700)

        self.service_action_in_progress = False
        self.status: ServiceStatus | None = None
        self.jobs_by_id: dict[str, object] = {}
        self.selected_job_id: str | None = None
        self.current_work_dir: Path | None = None
        self.current_artifact_dir: Path | None = None
        self.current_result_path: Path | None = None
        self.current_artifact_paths: dict[str, Path] = {}
        self.active_job_id: str | None = None
        self.ui_queue: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self.last_log_refresh_at_monotonic = 0.0

        self.service_state_var = tk.StringVar(value="stopped")
        self.health_url_var = tk.StringVar()
        self.local_mcp_url_var = tk.StringVar()
        self.public_mcp_url_var = tk.StringVar()
        self.developer_mode_address_var = tk.StringVar(value=UNAVAILABLE_TEXT)
        self.logs_path_var = tk.StringVar()
        self.artifacts_path_var = tk.StringVar()
        self.session_file_path_var = tk.StringVar()
        self.server_log_path_var = tk.StringVar(value=UNAVAILABLE_TEXT)
        self.tunnel_log_path_var = tk.StringVar(value=UNAVAILABLE_TEXT)
        self.mcp_server_pid_var = tk.StringVar(value=UNAVAILABLE_TEXT)
        self.tunnel_pid_var = tk.StringVar(value=UNAVAILABLE_TEXT)
        self.action_message_var = tk.StringVar(value="Idle")
        self.connection_message_var = tk.StringVar(value="Ready")
        self.run_task_work_dir_var = tk.StringVar()
        self.run_task_status_var = tk.StringVar(value="Ready")
        self.job_status_filter_var = tk.StringVar(value="all")
        self.logs_status_var = tk.StringVar(value="Logs not refreshed yet.")

        self.default_work_dir_var = tk.StringVar()
        self.detail_job_id_var = tk.StringVar()
        self.detail_status_var = tk.StringVar()
        self.detail_return_code_var = tk.StringVar()
        self.detail_duration_var = tk.StringVar()
        self.detail_timed_out_var = tk.StringVar()
        self.detail_result_file_present_var = tk.StringVar()
        self.detail_created_at_var = tk.StringVar()
        self.detail_started_at_var = tk.StringVar()
        self.detail_finished_at_var = tk.StringVar()
        self.detail_work_dir_var = tk.StringVar()
        self.detail_artifact_dir_var = tk.StringVar()
        self.detail_artifact_names_var = tk.StringVar()

        self._build_ui()
        self.reload_paths_from_env(show_errors=False)
        self.refresh_status(show_errors=False)
        self.refresh_jobs(show_errors=False)
        self.refresh_logs(show_errors=False)
        self.last_log_refresh_at_monotonic = time.monotonic()
        self._schedule_ui_queue_drain()
        self._schedule_auto_refresh()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        self._build_status_frame(container)
        self._build_run_task_frame(container)

        bottom_pane = ttk.Panedwindow(container, orient=tk.HORIZONTAL)
        bottom_pane.grid(row=2, column=0, sticky="nsew", pady=(12, 0))

        paths_frame = ttk.LabelFrame(bottom_pane, text="Allowed Paths")
        paths_frame.columnconfigure(0, weight=1)
        paths_frame.rowconfigure(1, weight=1)
        self._build_paths_frame(paths_frame)
        bottom_pane.add(paths_frame, weight=1)

        results_frame = ttk.LabelFrame(bottom_pane, text="Recent Jobs and Results")
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        self._build_results_frame(results_frame)
        bottom_pane.add(results_frame, weight=3)

    def _build_status_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Service Status")
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        self._add_readonly_row(frame, "Current State", self.service_state_var, 0, 0)
        self._add_readonly_row(frame, "Local Health URL", self.health_url_var, 1, 0)
        self._add_readonly_row(frame, "Local MCP URL", self.local_mcp_url_var, 2, 0)
        self._add_readonly_row(frame, "Public MCP URL", self.public_mcp_url_var, 3, 0)
        self._add_readonly_row(frame, "Developer Mode Address", self.developer_mode_address_var, 4, 0)
        self._add_readonly_row(frame, "MCP Server PID", self.mcp_server_pid_var, 5, 0)
        self._add_readonly_row(frame, "Logs Path", self.logs_path_var, 0, 2)
        self._add_readonly_row(frame, "Artifacts Path", self.artifacts_path_var, 1, 2)
        self._add_readonly_row(frame, "Session File Path", self.session_file_path_var, 2, 2)
        self._add_readonly_row(frame, "Server Log Path", self.server_log_path_var, 3, 2)
        self._add_readonly_row(frame, "Tunnel Log Path", self.tunnel_log_path_var, 4, 2)
        self._add_readonly_row(frame, "Tunnel PID", self.tunnel_pid_var, 5, 2)

        button_row = ttk.Frame(frame)
        button_row.grid(row=6, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 4))
        for index in range(4):
            button_row.columnconfigure(index, weight=1)

        self.start_button = ttk.Button(button_row, text="Start Server", command=self.start_server)
        self.start_button.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.stop_button = ttk.Button(button_row, text="Stop Server", command=self.stop_server)
        self.stop_button.grid(row=0, column=1, padx=6, sticky="ew")
        self.restart_button = ttk.Button(button_row, text="Restart Server", command=self.restart_server)
        self.restart_button.grid(row=0, column=2, padx=6, sticky="ew")
        self.refresh_status_button = ttk.Button(
            button_row,
            text="Refresh Status",
            command=lambda: self.refresh_status(show_errors=True),
        )
        self.refresh_status_button.grid(row=0, column=3, padx=(6, 0), sticky="ew")

        connection_button_row = ttk.Frame(frame)
        connection_button_row.grid(row=7, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 4))
        for index in range(3):
            connection_button_row.columnconfigure(index, weight=1)

        ttk.Button(
            connection_button_row,
            text="Copy Local MCP URL",
            command=lambda: self.copy_to_clipboard("local MCP URL", self.local_mcp_url_var.get()),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(
            connection_button_row,
            text="Copy Public MCP URL",
            command=lambda: self.copy_to_clipboard("public MCP URL", self.public_mcp_url_var.get()),
        ).grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        ttk.Button(
            connection_button_row,
            text="Copy Developer Mode Address",
            command=lambda: self.copy_to_clipboard(
                "Developer Mode address",
                self.developer_mode_address_var.get(),
            ),
        ).grid(row=0, column=2, sticky="ew", padx=(4, 0), pady=2)
        ttk.Button(
            connection_button_row,
            text="Open Logs Dir",
            command=lambda: self.open_display_path("Logs Dir", self.logs_path_var.get()),
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(
            connection_button_row,
            text="Open Server Log",
            command=lambda: self.open_display_path("Server Log", self.server_log_path_var.get()),
        ).grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        ttk.Button(
            connection_button_row,
            text="Open Tunnel Log",
            command=lambda: self.open_display_path("Tunnel Log", self.tunnel_log_path_var.get()),
        ).grid(row=1, column=2, sticky="ew", padx=(4, 0), pady=2)

        ttk.Label(frame, textvariable=self.action_message_var).grid(
            row=8,
            column=0,
            columnspan=4,
            sticky="w",
            padx=8,
            pady=(0, 2),
        )
        ttk.Label(frame, textvariable=self.connection_message_var).grid(
            row=9,
            column=0,
            columnspan=4,
            sticky="w",
            padx=8,
            pady=(0, 8),
        )

    def _build_run_task_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Run Task")
        frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        prompt_frame = ttk.Frame(frame)
        prompt_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 8))
        prompt_frame.columnconfigure(0, weight=1)
        prompt_frame.rowconfigure(1, weight=1)

        ttk.Label(prompt_frame, text="Prompt").grid(row=0, column=0, sticky="w")
        self.prompt_text = ScrolledText(prompt_frame, height=7, wrap=tk.WORD)
        self.prompt_text.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        work_dir_frame = ttk.Frame(frame)
        work_dir_frame.grid(row=1, column=0, sticky="ew", padx=8)
        work_dir_frame.columnconfigure(1, weight=1)

        ttk.Label(work_dir_frame, text="Work Dir").grid(row=0, column=0, sticky="w")
        self.run_task_work_dir_entry = ttk.Entry(work_dir_frame, textvariable=self.run_task_work_dir_var)
        self.run_task_work_dir_entry.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(work_dir_frame, text="Browse...", command=self.browse_run_task_work_dir).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(work_dir_frame, text="Use Default", command=self.use_default_work_dir).grid(row=0, column=3)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        for index in range(3):
            button_frame.columnconfigure(index, weight=1)

        ttk.Button(button_frame, text="Run Task", command=self.run_task).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(button_frame, text="Clear", command=self.clear_run_task_form).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(button_frame, text="Load From Selected Job", command=self.load_from_selected_job).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(4, 0),
        )

        ttk.Label(frame, textvariable=self.run_task_status_var).grid(
            row=3,
            column=0,
            sticky="w",
            padx=8,
            pady=(0, 8),
        )

    def _build_paths_frame(self, parent: ttk.LabelFrame) -> None:
        default_frame = ttk.Frame(parent)
        default_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 8))
        default_frame.columnconfigure(1, weight=1)

        ttk.Label(default_frame, text="Default Work Dir").grid(row=0, column=0, sticky="w")
        default_entry = ttk.Entry(default_frame, textvariable=self.default_work_dir_var, state="readonly")
        default_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        list_frame = ttk.Frame(parent)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=8)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.roots_listbox = tk.Listbox(list_frame, exportselection=False)
        self.roots_listbox.grid(row=0, column=0, sticky="nsew")
        roots_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.roots_listbox.yview)
        roots_scrollbar.grid(row=0, column=1, sticky="ns")
        self.roots_listbox.configure(yscrollcommand=roots_scrollbar.set)

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        ttk.Button(button_frame, text="Add Root...", command=self.add_root).grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(button_frame, text="Remove Selected", command=self.remove_selected_root).grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=2)
        ttk.Button(button_frame, text="Move Up", command=lambda: self.move_selected_root(-1)).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(button_frame, text="Move Down", command=lambda: self.move_selected_root(1)).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=2)
        ttk.Button(button_frame, text="Set Default Work Dir...", command=self.set_default_work_dir).grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=2)
        ttk.Button(button_frame, text="Save", command=self.save_paths).grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=2)
        ttk.Button(button_frame, text="Reload From Env", command=lambda: self.reload_paths_from_env(show_errors=True)).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 2),
        )

    def _build_results_frame(self, parent: ttk.LabelFrame) -> None:
        pane = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        pane.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        jobs_frame = ttk.Frame(pane)
        jobs_frame.columnconfigure(0, weight=1)
        jobs_frame.rowconfigure(1, weight=1)
        pane.add(jobs_frame, weight=2)

        filter_frame = ttk.Frame(jobs_frame)
        filter_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        filter_frame.columnconfigure(1, weight=1)

        ttk.Label(filter_frame, text="Status Filter").grid(row=0, column=0, sticky="w")
        filter_combobox = ttk.Combobox(
            filter_frame,
            textvariable=self.job_status_filter_var,
            values=JOB_STATUS_FILTER_OPTIONS,
            state="readonly",
        )
        filter_combobox.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(filter_frame, text="Apply Filter", command=self.apply_job_filter).grid(
            row=0,
            column=2,
            sticky="ew",
        )

        columns = ("status", "job_id", "created_at", "work_dir")
        self.jobs_tree = ttk.Treeview(jobs_frame, columns=columns, show="headings", selectmode="browse")
        self.jobs_tree.heading("status", text="Status")
        self.jobs_tree.heading("job_id", text="Job ID")
        self.jobs_tree.heading("created_at", text="Created At")
        self.jobs_tree.heading("work_dir", text="Work Dir")
        self.jobs_tree.column("status", width=90, anchor=tk.W, stretch=False)
        self.jobs_tree.column("job_id", width=220, anchor=tk.W)
        self.jobs_tree.column("created_at", width=170, anchor=tk.W, stretch=False)
        self.jobs_tree.column("work_dir", width=320, anchor=tk.W)
        self.jobs_tree.grid(row=1, column=0, sticky="nsew")
        jobs_scrollbar = ttk.Scrollbar(jobs_frame, orient=tk.VERTICAL, command=self.jobs_tree.yview)
        jobs_scrollbar.grid(row=1, column=1, sticky="ns")
        self.jobs_tree.configure(yscrollcommand=jobs_scrollbar.set)
        self.jobs_tree.bind("<<TreeviewSelect>>", self.on_job_selected)

        detail_frame = ttk.Frame(pane)
        detail_frame.columnconfigure(1, weight=1)
        detail_frame.rowconfigure(3, weight=2)
        detail_frame.rowconfigure(4, weight=1)
        pane.add(detail_frame, weight=3)

        action_frame = ttk.Frame(detail_frame)
        action_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for index in range(2):
            action_frame.columnconfigure(index, weight=1)

        ttk.Button(action_frame, text="Refresh Jobs", command=lambda: self.refresh_jobs(show_errors=True)).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(action_frame, text="Open Work Dir", command=lambda: self.open_target_path(self.current_work_dir)).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 0),
        )

        detail_fields = ttk.Frame(detail_frame)
        detail_fields.grid(row=1, column=0, columnspan=2, sticky="ew")
        detail_fields.columnconfigure(1, weight=1)
        detail_fields.columnconfigure(3, weight=1)

        self._add_readonly_row(detail_fields, "Job ID", self.detail_job_id_var, 0, 0)
        self._add_readonly_row(detail_fields, "Status", self.detail_status_var, 0, 2)
        self._add_readonly_row(detail_fields, "Return Code", self.detail_return_code_var, 1, 0)
        self._add_readonly_row(detail_fields, "Duration Seconds", self.detail_duration_var, 1, 2)
        self._add_readonly_row(detail_fields, "Created At", self.detail_created_at_var, 2, 0)
        self._add_readonly_row(detail_fields, "Started At", self.detail_started_at_var, 2, 2)
        self._add_readonly_row(detail_fields, "Finished At", self.detail_finished_at_var, 3, 0)
        self._add_readonly_row(detail_fields, "Timed Out", self.detail_timed_out_var, 3, 2)
        self._add_readonly_row(detail_fields, "Result File Present", self.detail_result_file_present_var, 4, 0)
        self._add_readonly_row(detail_fields, "Work Dir", self.detail_work_dir_var, 5, 0, columnspan=4)
        self._add_readonly_row(detail_fields, "Artifact Dir", self.detail_artifact_dir_var, 6, 0, columnspan=4)
        self._add_readonly_row(detail_fields, "Artifact Names", self.detail_artifact_names_var, 7, 0, columnspan=4)

        artifact_frame = ttk.LabelFrame(detail_frame, text="Artifacts")
        artifact_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        artifact_frame.columnconfigure(0, weight=1)
        artifact_frame.rowconfigure(0, weight=1)

        artifact_list_frame = ttk.Frame(artifact_frame)
        artifact_list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        artifact_list_frame.columnconfigure(0, weight=1)
        artifact_list_frame.rowconfigure(0, weight=1)

        self.artifacts_listbox = tk.Listbox(artifact_list_frame, exportselection=False, height=5)
        self.artifacts_listbox.grid(row=0, column=0, sticky="nsew")
        artifacts_scrollbar = ttk.Scrollbar(
            artifact_list_frame,
            orient=tk.VERTICAL,
            command=self.artifacts_listbox.yview,
        )
        artifacts_scrollbar.grid(row=0, column=1, sticky="ns")
        self.artifacts_listbox.configure(yscrollcommand=artifacts_scrollbar.set)

        artifact_button_frame = ttk.Frame(artifact_frame)
        artifact_button_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        for index in range(3):
            artifact_button_frame.columnconfigure(index, weight=1)

        ttk.Button(
            artifact_button_frame,
            text="Open Selected Artifact",
            command=self.open_selected_artifact,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            artifact_button_frame,
            text="Open Artifact Dir",
            command=lambda: self.open_target_path(self.current_artifact_dir),
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(
            artifact_button_frame,
            text="Open result.json",
            command=lambda: self.open_target_path(self.current_result_path),
        ).grid(row=0, column=2, sticky="ew", padx=(4, 0))

        notebook = ttk.Notebook(detail_frame)
        notebook.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

        self.prompt_view_text = self._build_text_tab(notebook, "Prompt")
        self.summary_text = self._build_text_tab(notebook, "Summary")
        self.stdout_text = self._build_text_tab(notebook, "Stdout Tail")
        self.stderr_text = self._build_text_tab(notebook, "Stderr Tail")
        self.metadata_text = self._build_text_tab(notebook, "Metadata")

        logs_frame = ttk.LabelFrame(detail_frame, text="Logs")
        logs_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        logs_frame.columnconfigure(0, weight=1)
        logs_frame.rowconfigure(1, weight=1)

        logs_action_frame = ttk.Frame(logs_frame)
        logs_action_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        logs_action_frame.columnconfigure(0, weight=1)
        logs_action_frame.columnconfigure(1, weight=1)

        ttk.Button(
            logs_action_frame,
            text="Refresh Logs",
            command=lambda: self.refresh_logs(show_errors=True),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(logs_action_frame, textvariable=self.logs_status_var).grid(
            row=0,
            column=1,
            sticky="e",
        )

        logs_notebook = ttk.Notebook(logs_frame)
        logs_notebook.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        self.server_log_text = self._build_text_tab(logs_notebook, "Server Log")
        self.tunnel_log_text = self._build_text_tab(logs_notebook, "Tunnel Log")
        self.service_summary_text = self._build_text_tab(logs_notebook, "Service Summary")

    def _build_text_tab(self, notebook: ttk.Notebook, title: str) -> ScrolledText:
        frame = ttk.Frame(notebook)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text_widget = ScrolledText(frame, wrap=tk.WORD)
        text_widget.grid(row=0, column=0, sticky="nsew")
        text_widget.configure(state=tk.DISABLED)
        notebook.add(frame, text=title)
        return text_widget

    def _add_readonly_row(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        row: int,
        column: int,
        *,
        columnspan: int = 2,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=8, pady=4)
        entry = ttk.Entry(parent, textvariable=variable, state="readonly")
        entry.grid(row=row, column=column + 1, columnspan=max(columnspan - 1, 1), sticky="ew", padx=(0, 8), pady=4)

    def _schedule_auto_refresh(self) -> None:
        self.refresh_status(show_errors=False)
        self._refresh_active_job()
        now = time.monotonic()
        if now - self.last_log_refresh_at_monotonic >= LOG_REFRESH_MS / 1000:
            self.refresh_logs(show_errors=False)
            self.last_log_refresh_at_monotonic = now
        self.root.after(AUTO_REFRESH_MS, self._schedule_auto_refresh)

    def _schedule_ui_queue_drain(self) -> None:
        self._drain_ui_queue()
        self.root.after(100, self._schedule_ui_queue_drain)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                callback = self.ui_queue.get_nowait()
            except Empty:
                return
            callback()

    def refresh_status(self, *, show_errors: bool) -> None:
        try:
            settings = load_settings(ENV_PATH)
            self.status = load_service_status(settings)
        except Exception as exc:
            self.action_message_var.set(f"Failed to refresh status: {exc}")
            if show_errors:
                messagebox.showerror("Refresh Status Failed", str(exc))
            return

        assert self.status is not None
        self.service_state_var.set(self.status.state)
        self.health_url_var.set(self.status.health_url)
        self.local_mcp_url_var.set(self.status.local_mcp_url)
        self.public_mcp_url_var.set(self.status.public_mcp_url)
        self.developer_mode_address_var.set(self.status.developer_mode_address)
        self.logs_path_var.set(self.status.logs_path)
        self.artifacts_path_var.set(self.status.artifacts_path)
        self.session_file_path_var.set(self.status.session_file_path)
        self.server_log_path_var.set(self.status.server_log_path)
        self.tunnel_log_path_var.set(self.status.tunnel_log_path)
        self.mcp_server_pid_var.set(self.status.mcp_server_pid)
        self.tunnel_pid_var.set(self.status.tunnel_pid)
        self._update_service_buttons()

    def _update_service_buttons(self) -> None:
        if self.service_action_in_progress:
            state = tk.DISABLED
            self.start_button.configure(state=state)
            self.stop_button.configure(state=state)
            self.restart_button.configure(state=state)
            self.refresh_status_button.configure(state=state)
            return

        current_state = self.service_state_var.get()
        self.start_button.configure(state=tk.NORMAL if current_state != "running" else tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL if current_state == "running" else tk.DISABLED)
        self.restart_button.configure(state=tk.NORMAL if current_state == "running" else tk.DISABLED)
        self.refresh_status_button.configure(state=tk.NORMAL)

    def _run_service_action(
        self,
        description: str,
        worker: Callable[[], None],
        *,
        show_completion_message: bool = False,
    ) -> None:
        if self.service_action_in_progress:
            return

        self.service_action_in_progress = True
        self.action_message_var.set(f"{description}...")
        self._update_service_buttons()

        def target() -> None:
            error_message: str | None = None
            try:
                worker()
            except Exception as exc:
                error_message = str(exc)

            def finish() -> None:
                self.service_action_in_progress = False
                self.refresh_status(show_errors=False)
                if error_message is not None:
                    self.action_message_var.set(f"{description} failed")
                    messagebox.showerror(f"{description} Failed", error_message)
                else:
                    self.action_message_var.set(f"{description} finished")
                    if show_completion_message:
                        messagebox.showinfo("Service Action Complete", f"{description} finished.")
                self._update_service_buttons()

            self.ui_queue.put(finish)

        threading.Thread(target=target, daemon=True).start()

    def start_server(self) -> None:
        def worker() -> None:
            subprocess.Popen(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "dev_up.py")],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(0.5)

        self._run_service_action("Start Server", worker)

    def stop_server(self) -> None:
        def worker() -> None:
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "dev_down.py")],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(0.5)

        self._run_service_action("Stop Server", worker)

    def restart_server(self) -> None:
        def worker() -> None:
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "dev_down.py")],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(1.0)
            subprocess.Popen(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "dev_up.py")],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(0.5)

        self._run_service_action("Restart Server", worker)

    def browse_run_task_work_dir(self) -> None:
        selected_path = filedialog.askdirectory(title="Select Task Work Directory", mustexist=False)
        if not selected_path:
            return
        self.run_task_work_dir_var.set(str(Path(selected_path).expanduser().resolve()))

    def use_default_work_dir(self) -> None:
        self.run_task_work_dir_var.set(self.default_work_dir_var.get())

    def clear_run_task_form(self) -> None:
        self.prompt_text.delete("1.0", tk.END)
        self.run_task_work_dir_var.set("")
        self.run_task_status_var.set("Ready")

    def copy_to_clipboard(self, label: str, value: str) -> None:
        normalized_value = value.strip()
        if not normalized_value or normalized_value == UNAVAILABLE_TEXT:
            messagebox.showinfo("Copy Failed", f"{label} is unavailable.")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(normalized_value)
        self.root.update_idletasks()
        self.connection_message_var.set(f"Copied {label}")

    def open_display_path(self, label: str, value: str) -> None:
        target_path = self._path_from_display_value(value)
        if target_path is None:
            messagebox.showinfo("Open Path", f"{label} is unavailable.")
            return
        self.open_target_path(target_path)

    def apply_job_filter(self) -> None:
        try:
            normalized_filter = normalize_job_status_filter(self.job_status_filter_var.get())
        except ValueError as exc:
            messagebox.showerror("Apply Filter Failed", str(exc))
            return

        self.job_status_filter_var.set(normalized_filter)
        preferred_selection = self.active_job_id or self.selected_job_id
        self.refresh_jobs(show_errors=True, preferred_selection=preferred_selection)

    def load_from_selected_job(self) -> None:
        if not self.detail_work_dir_var.get().strip():
            messagebox.showinfo("Load From Selected Job", "Select a job first.")
            return
        self.run_task_work_dir_var.set(self.detail_work_dir_var.get().strip())

    def open_selected_artifact(self) -> None:
        selection = self.artifacts_listbox.curselection()
        if not selection:
            messagebox.showinfo("Open Artifact", "Select an artifact first.")
            return

        artifact_name = self.artifacts_listbox.get(selection[0])
        artifact_path = self.current_artifact_paths.get(artifact_name)
        if artifact_path is None:
            messagebox.showerror("Open Artifact Failed", f"Artifact '{artifact_name}' is not available.")
            return

        self.open_target_path(artifact_path)

    def run_task(self) -> None:
        self.refresh_status(show_errors=False)
        if self.service_state_var.get() != "running":
            self.run_task_status_var.set("Submission failed")
            messagebox.showerror(
                "Run Task Failed",
                "Server is not running. Start the server before submitting a task.",
            )
            return

        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            self.run_task_status_var.set("Submission failed")
            messagebox.showerror("Run Task Failed", "Prompt must not be empty.")
            return

        try:
            settings = load_settings(ENV_PATH)
        except Exception as exc:
            self.run_task_status_var.set("Submission failed")
            messagebox.showerror("Run Task Failed", str(exc))
            return

        requested_work_dir = self.run_task_work_dir_var.get().strip()
        if requested_work_dir:
            resolved_work_dir = Path(requested_work_dir).expanduser().resolve()
        else:
            resolved_work_dir = settings.default_work_dir
            self.run_task_work_dir_var.set(str(resolved_work_dir))

        if not is_path_within_allowed_roots(resolved_work_dir, settings.allowed_work_roots):
            self.run_task_status_var.set("Submission failed")
            messagebox.showerror("Run Task Failed", WORK_DIR_OUTSIDE_ALLOWED_ROOTS_MESSAGE)
            return

        self.run_task_status_var.set("Submitting task...")
        self.root.update_idletasks()

        try:
            job = create_job_with_service(
                settings,
                CreateJobRequest(prompt=prompt, work_dir=str(resolved_work_dir)),
            )
        except HTTPException as exc:
            self.run_task_status_var.set("Submission failed")
            detail = exc.detail if isinstance(exc.detail, str) else "Failed to create job."
            messagebox.showerror("Run Task Failed", detail)
            return
        except Exception as exc:
            self.run_task_status_var.set("Submission failed")
            messagebox.showerror("Run Task Failed", str(exc))
            return

        self.active_job_id = job.job_id
        self.run_task_status_var.set(f"Task submitted: {job.job_id}")
        self.refresh_jobs(show_errors=False, preferred_selection=job.job_id)
        self.load_selected_job_detail(job.job_id, show_errors=False)

    def reload_paths_from_env(self, *, show_errors: bool) -> None:
        try:
            settings = load_settings(ENV_PATH)
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Reload Configuration Failed", str(exc))
            return

        self.roots_listbox.delete(0, tk.END)
        for root in settings.allowed_work_roots:
            self.roots_listbox.insert(tk.END, str(root))
        self.default_work_dir_var.set(str(settings.default_work_dir))

    def add_root(self) -> None:
        selected_path = filedialog.askdirectory(title="Select Allowed Work Root", mustexist=True)
        if not selected_path:
            return
        normalized_path = str(Path(selected_path).expanduser().resolve())
        if normalized_path not in self._root_items():
            self.roots_listbox.insert(tk.END, normalized_path)

    def remove_selected_root(self) -> None:
        selection = self.roots_listbox.curselection()
        if not selection:
            return
        self.roots_listbox.delete(selection[0])

    def move_selected_root(self, direction: int) -> None:
        selection = self.roots_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = index + direction
        if new_index < 0 or new_index >= self.roots_listbox.size():
            return

        value = self.roots_listbox.get(index)
        self.roots_listbox.delete(index)
        self.roots_listbox.insert(new_index, value)
        self.roots_listbox.selection_set(new_index)

    def set_default_work_dir(self) -> None:
        selected_path = filedialog.askdirectory(title="Select Default Work Directory", mustexist=True)
        if not selected_path:
            return
        self.default_work_dir_var.set(str(Path(selected_path).expanduser().resolve()))

    def save_paths(self) -> None:
        roots = self._root_items()
        default_work_dir = self.default_work_dir_var.get().strip()
        try:
            save_root_configuration(roots, default_work_dir, path=ENV_PATH)
        except Exception as exc:
            messagebox.showerror("Save Configuration Failed", str(exc))
            return

        self.reload_paths_from_env(show_errors=False)
        self.refresh_status(show_errors=False)
        if self.service_state_var.get() == "running":
            restart_now = messagebox.askyesno(
                "Configuration Saved",
                "Configuration saved to .env.\nRestart the service now so the changes fully take effect?",
            )
            if restart_now:
                self.restart_server()
                return
        messagebox.showinfo(
            "Configuration Saved",
            "Configuration saved to .env.\nRestart the service for the changes to fully take effect.",
        )

    def _root_items(self) -> list[str]:
        return [self.roots_listbox.get(index) for index in range(self.roots_listbox.size())]

    def refresh_jobs(self, *, show_errors: bool, preferred_selection: str | None = None) -> None:
        previous_selection = preferred_selection or self.selected_job_id
        try:
            settings = load_settings(ENV_PATH)
            jobs = load_recent_jobs(
                settings,
                limit=MAX_RECENT_JOBS,
                status_filter=self.job_status_filter_var.get(),
            )
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Refresh Jobs Failed", str(exc))
            return

        self.jobs_by_id.clear()
        for item_id in self.jobs_tree.get_children():
            self.jobs_tree.delete(item_id)

        for job in jobs:
            self.jobs_by_id[job.job_id] = job
            self.jobs_tree.insert(
                "",
                tk.END,
                iid=job.job_id,
                values=(job.status.value, job.job_id, job.created_at, job.work_dir),
            )

        keep_active_detail = (
            self.active_job_id is not None
            and previous_selection == self.active_job_id
            and previous_selection not in self.jobs_by_id
        )

        if previous_selection and previous_selection in self.jobs_by_id:
            self.jobs_tree.selection_set(previous_selection)
            self.jobs_tree.focus(previous_selection)
            self.load_selected_job_detail(previous_selection, show_errors=False)
        elif keep_active_detail:
            self.jobs_tree.selection_remove(self.jobs_tree.selection())
        elif jobs:
            first_job_id = jobs[0].job_id
            self.jobs_tree.selection_set(first_job_id)
            self.jobs_tree.focus(first_job_id)
            self.load_selected_job_detail(first_job_id, show_errors=False)
        else:
            self.clear_job_detail()

    def on_job_selected(self, event: object) -> None:
        del event
        selection = self.jobs_tree.selection()
        if not selection:
            if self.active_job_id is not None and self.selected_job_id == self.active_job_id:
                return
            self.clear_job_detail()
            return
        self.load_selected_job_detail(selection[0], show_errors=True)

    def _refresh_active_job(self) -> None:
        if self.active_job_id is None:
            return

        active_job_id = self.active_job_id
        try:
            settings = load_settings(ENV_PATH)
            _, result, _ = load_job_result(settings, active_job_id)
        except Exception:
            return

        self.refresh_jobs(show_errors=False, preferred_selection=active_job_id)
        self.load_selected_job_detail(active_job_id, show_errors=False)

        if is_terminal_job_status(result.status):
            completed_status = self.detail_status_var.get().strip() or str(result.status)
            self.run_task_status_var.set(f"Task completed: {active_job_id} ({completed_status})")
            self.active_job_id = None

    def refresh_logs(self, *, show_errors: bool) -> None:
        if self.status is None:
            try:
                settings = load_settings(ENV_PATH)
                self.status = load_service_status(settings)
            except Exception as exc:
                if show_errors:
                    messagebox.showerror("Refresh Logs Failed", str(exc))
                return

        refreshed_at = current_timestamp_text()
        try:
            server_log_content = read_text_file_tail(self._path_from_display_value(self.server_log_path_var.get()))
            tunnel_log_content = read_text_file_tail(self._path_from_display_value(self.tunnel_log_path_var.get()))
            summary_content = build_service_summary_text(self.status, refreshed_at=refreshed_at)
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Refresh Logs Failed", str(exc))
            return

        self._set_text_widget(self.server_log_text, server_log_content)
        self._set_text_widget(self.tunnel_log_text, tunnel_log_content)
        self._set_text_widget(self.service_summary_text, summary_content)
        self.logs_status_var.set(f"Logs refreshed at {refreshed_at}")

    def load_selected_job_detail(self, job_id: str, *, show_errors: bool) -> None:
        try:
            settings = load_settings(ENV_PATH)
            job, result, result_file_present = load_job_result(settings, job_id)
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Load Job Result Failed", str(exc))
            return

        artifact_paths = build_artifact_paths(result.artifact_dir, result.artifact_names)
        prompt_content = load_job_prompt_text(job) or "(empty)"
        metadata_content = load_job_metadata_text(
            result.artifact_dir,
            fallback_payload=result.metadata,
        )

        self.selected_job_id = job_id
        self.detail_job_id_var.set(result.job_id)
        self.detail_status_var.set(result.status)
        self.detail_return_code_var.set("" if result.return_code is None else str(result.return_code))
        self.detail_duration_var.set("" if result.duration_seconds is None else str(result.duration_seconds))
        self.detail_timed_out_var.set("" if result.timed_out is None else str(result.timed_out))
        self.detail_result_file_present_var.set(str(result_file_present))
        self.detail_created_at_var.set(result.created_at or "")
        self.detail_started_at_var.set(result.started_at or "")
        self.detail_finished_at_var.set(result.finished_at or "")
        self.detail_work_dir_var.set(result.work_dir)
        self.detail_artifact_dir_var.set(result.artifact_dir)
        self.detail_artifact_names_var.set(", ".join(result.artifact_names))

        self.current_work_dir = Path(result.work_dir).expanduser().resolve()
        self.current_artifact_dir = Path(result.artifact_dir).expanduser().resolve()
        self.current_result_path = self.current_artifact_dir / "result.json"
        self.current_artifact_paths = {
            artifact_name: artifact_path
            for artifact_name, artifact_path in artifact_paths
        }

        self.artifacts_listbox.delete(0, tk.END)
        for artifact_name, _ in artifact_paths:
            self.artifacts_listbox.insert(tk.END, artifact_name)

        self._set_text_widget(self.prompt_view_text, prompt_content)
        self._set_text_widget(self.summary_text, result.summary)
        self._set_text_widget(self.stdout_text, result.stdout_tail)
        self._set_text_widget(self.stderr_text, result.stderr_tail)
        self._set_text_widget(self.metadata_text, metadata_content)

    def clear_job_detail(self) -> None:
        self.selected_job_id = None
        for variable in (
            self.detail_job_id_var,
            self.detail_status_var,
            self.detail_return_code_var,
            self.detail_duration_var,
            self.detail_timed_out_var,
            self.detail_result_file_present_var,
            self.detail_created_at_var,
            self.detail_started_at_var,
            self.detail_finished_at_var,
            self.detail_work_dir_var,
            self.detail_artifact_dir_var,
            self.detail_artifact_names_var,
        ):
            variable.set("")

        self.current_work_dir = None
        self.current_artifact_dir = None
        self.current_result_path = None
        self.current_artifact_paths = {}
        self.artifacts_listbox.delete(0, tk.END)
        self._set_text_widget(self.prompt_view_text, "")
        self._set_text_widget(self.summary_text, "")
        self._set_text_widget(self.stdout_text, "")
        self._set_text_widget(self.stderr_text, "")
        self._set_text_widget(self.metadata_text, "")

    def _set_text_widget(self, widget: ScrolledText, content: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", content)
        widget.configure(state=tk.DISABLED)

    def open_target_path(self, path: Path | None) -> None:
        if path is None:
            messagebox.showinfo("Open Path", "No path is available for the current selection.")
            return

        try:
            open_in_file_manager(path)
        except Exception as exc:
            messagebox.showerror("Open Path Failed", str(exc))

    def _path_from_display_value(self, value: str) -> Path | None:
        normalized_value = value.strip()
        if not normalized_value or normalized_value == UNAVAILABLE_TEXT:
            return None
        return Path(normalized_value).expanduser().resolve()


def main() -> int:
    root = tk.Tk()
    ControlPanelApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
