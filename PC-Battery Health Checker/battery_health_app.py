import ctypes
from datetime import datetime
import html
import json
import platform
import re
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


APP_TITLE = "Laptop Battery Health Checker"
REPORT_PATH = Path.home() / "Downloads" / "battery-report.html"
BG = "#0f172a"
SURFACE = "#111827"
CARD = "#1e293b"
CARD_ALT = "#334155"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"
GOOD = "#22c55e"
WARN = "#f59e0b"
BAD = "#ef4444"
BORDER = "#475569"
SCAN_BATCH_NAME = "battery_health_corruption_scan.cmd"


def is_windows_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if sys.platform != "win32" or is_windows_admin():
        return

    if getattr(sys, "frozen", False):
        executable = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        executable = sys.executable
        script_path = str(Path(__file__).resolve())
        params = subprocess.list2cmdline([script_path, *sys.argv[1:]])

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        params,
        None,
        1,
    )
    if result <= 32:
        raise RuntimeError("Administrator permission is required to generate the battery report.")
    sys.exit(0)


def generate_battery_report():
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if REPORT_PATH.exists():
        REPORT_PATH.unlink()
    command = ["powercfg", "/batteryreport", "/output", str(REPORT_PATH)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        error_text = completed.stderr.strip() or completed.stdout.strip() or "Unknown error from powercfg."
        raise RuntimeError(error_text)
    if not REPORT_PATH.exists():
        raise FileNotFoundError(f"Battery report was not created at {REPORT_PATH}")
    return REPORT_PATH


def clean_html_text(raw_text):
    text = re.sub(r"<br\s*/?>", "\n", raw_text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def section_html(document, heading):
    pattern = re.compile(
        rf"<h2>\s*{re.escape(heading)}\s*</h2>(.*?)(?=<h2>|</body>)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(document)
    return match.group(1) if match else ""


def parse_table_rows(section):
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", section, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        cleaned = [clean_html_text(cell) for cell in cells]
        if cleaned:
            rows.append(cleaned)
    return rows


def parse_capacity(value):
    match = re.search(r"([\d,]+)\s*mWh", value, flags=re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    match = re.search(r"([\d,]+)", value)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def parse_duration_to_hours(value):
    match = re.search(r"(\d+):(\d+):(\d+)", value)
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    return hours + (minutes / 60) + (seconds / 3600)


def format_wmi_datetime(value):
    if not value:
        return "Not reported"

    date_match = re.search(r"/Date\((\d+)", str(value))
    if date_match:
        try:
            timestamp = int(date_match.group(1)) / 1000
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %I:%M %p")
        except (OSError, OverflowError, ValueError):
            return str(value)

    cim_match = re.match(r"(\d{14})\.", str(value))
    if cim_match:
        try:
            return datetime.strptime(cim_match.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %I:%M %p")
        except ValueError:
            return str(value)

    return str(value)


def format_bytes_to_gb(value):
    try:
        return f"{int(value) / (1024 ** 3):.2f} GB"
    except (TypeError, ValueError):
        return "Not reported"


def assessment_from_percent(percent):
    if percent is None:
        return "Battery health could not be calculated from the report.", MUTED
    if percent >= 95:
        return "Battery is in excellent condition and is still near its original capacity.", GOOD
    if percent >= 80:
        return "Battery is healthy, but some wear is visible compared with its original design.", ACCENT
    if percent >= 60:
        return "Battery wear is significant. Runtime will be noticeably lower than when the laptop was new.", WARN
    return "Battery health is poor. Replacement should be considered if backup time is important.", BAD


def extract_system_info(document):
    first_table_match = re.search(r"<h1>.*?</h1><table.*?>(.*?)</table>", document, flags=re.IGNORECASE | re.DOTALL)
    info = {}
    if not first_table_match:
        return info
    rows = parse_table_rows(first_table_match.group(1))
    for row in rows:
        if len(row) >= 2:
            info[row[0].title()] = row[1]
    return info


def extract_installed_battery_info(document):
    installed_section = section_html(document, "Installed batteries")
    if not installed_section:
        return {"battery_present": None}

    no_battery = re.search(r"No batteries are currently installed", installed_section, flags=re.IGNORECASE)
    if no_battery:
        return {"battery_present": False}

    rows = parse_table_rows(installed_section)
    battery_info = {"battery_present": True}
    for row in rows:
        if len(row) >= 2:
            label = row[0].upper()
            value = row[1]
            battery_info[label] = value
    return battery_info


def extract_capacity_history(document):
    section = section_html(document, "Battery capacity history")
    rows = parse_table_rows(section)
    history_rows = []
    for row in rows[1:]:
        if len(row) >= 3 and row[1] != "-" and row[2] != "-":
            full_capacity = parse_capacity(row[1])
            design_capacity = parse_capacity(row[2])
            if full_capacity or design_capacity:
                history_rows.append(
                    {
                        "period": row[0],
                        "full_charge_capacity": row[1],
                        "design_capacity": row[2],
                        "full_charge_capacity_value": full_capacity,
                        "design_capacity_value": design_capacity,
                    }
                )

    if not history_rows:
        return {}

    latest = history_rows[-1]
    baseline = None
    baseline_value = None
    for row in history_rows:
        candidate_values = [value for value in (row["full_charge_capacity_value"], row["design_capacity_value"]) if value]
        if not candidate_values:
            continue
        candidate_value = max(candidate_values)
        if baseline is None or candidate_value > baseline_value:
            baseline = row
            baseline_value = candidate_value

    return {
        "latest_period": latest["period"],
        "latest_full_charge_capacity": latest["full_charge_capacity"],
        "latest_design_capacity": latest["design_capacity"],
        "latest_full_charge_capacity_value": latest["full_charge_capacity_value"],
        "latest_design_capacity_value": latest["design_capacity_value"],
        "baseline_period": baseline["period"] if baseline else latest["period"],
        "baseline_capacity": (
            f"{baseline_value:,} mWh"
            if baseline_value is not None
            else latest["design_capacity"]
        ),
        "baseline_capacity_value": baseline_value,
    }


def extract_life_estimate(document):
    section = section_html(document, "Battery life estimates")
    rows = parse_table_rows(section)
    current_section_match = re.search(
        r"Current estimate of battery life based on all observed drains since OS install(.*?)(?:</table>|<br/>)",
        section,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if current_section_match:
        final_rows = parse_table_rows(current_section_match.group(1))
        if final_rows and len(final_rows[-1]) >= 6:
            row = final_rows[-1]
            return {
                "observed_at_full_charge": row[1],
                "observed_connected_standby": row[2],
                "observed_at_design_capacity": row[4],
                "observed_design_connected_standby": row[5],
            }

    for row in reversed(rows):
        if len(row) >= 6 and row[1] != "-":
            return {
                "observed_at_full_charge": row[1],
                "observed_connected_standby": row[2],
                "observed_at_design_capacity": row[4],
                "observed_design_connected_standby": row[5],
            }
    return {}


def run_powershell_json(script):
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "PowerShell command failed.")

    output = completed.stdout.strip()
    if not output:
        return {}
    return json.loads(output)


def collect_live_system_info():
    scripts = {
        "computer_system": (
            "Get-CimInstance Win32_ComputerSystem | "
            "Select-Object Manufacturer, Model, SystemType, TotalPhysicalMemory, UserName | "
            "ConvertTo-Json -Compress"
        ),
        "bios": (
            "Get-CimInstance Win32_BIOS | "
            "Select-Object SMBIOSBIOSVersion, Manufacturer, ReleaseDate, SerialNumber | "
            "ConvertTo-Json -Compress"
        ),
        "os": (
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime | "
            "ConvertTo-Json -Compress"
        ),
        "processor": (
            "Get-CimInstance Win32_Processor | "
            "Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed | "
            "ConvertTo-Json -Compress"
        ),
        "gpu": (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name, AdapterRAM, DriverVersion, VideoProcessor | "
            "ConvertTo-Json -Compress"
        ),
    }

    collected = {}
    for key, script in scripts.items():
        try:
            collected[key] = run_powershell_json(script)
        except Exception:
            collected[key] = {}

    computer_system = collected.get("computer_system") or {}
    bios = collected.get("bios") or {}
    operating_system = collected.get("os") or {}
    processor = collected.get("processor") or {}
    gpu_data = collected.get("gpu") or []
    if isinstance(gpu_data, dict):
        gpu_entries = [gpu_data]
    elif isinstance(gpu_data, list):
        gpu_entries = gpu_data
    else:
        gpu_entries = []

    total_memory = computer_system.get("TotalPhysicalMemory")
    try:
        total_memory_text = f"{int(total_memory) / (1024 ** 3):.2f} GB"
    except (TypeError, ValueError):
        total_memory_text = "Not reported"

    gpu_names = [entry.get("Name") for entry in gpu_entries if entry.get("Name")]
    primary_gpu = gpu_entries[0] if gpu_entries else {}
    gpu_name_text = " | ".join(gpu_names) if gpu_names else "GPU not installed or not reported by Windows"

    info = {
        "Computer Name": platform.node() or "Not reported",
        "Manufacturer": computer_system.get("Manufacturer", "Not reported"),
        "Model": computer_system.get("Model", "Not reported"),
        "System Type": computer_system.get("SystemType", "Not reported"),
        "Installed RAM": total_memory_text,
        "Processor": processor.get("Name", "Not reported"),
        "CPU Cores": processor.get("NumberOfCores", "Not reported"),
        "Logical Processors": processor.get("NumberOfLogicalProcessors", "Not reported"),
        "Max Clock Speed": (
            f"{processor['MaxClockSpeed']} MHz"
            if processor.get("MaxClockSpeed")
            else "Not reported"
        ),
        "OS": operating_system.get("Caption", "Not reported"),
        "Version": operating_system.get("Version", "Not reported"),
        "Build Number": operating_system.get("BuildNumber", "Not reported"),
        "Architecture": operating_system.get("OSArchitecture", "Not reported"),
        "Last Boot Time": format_wmi_datetime(operating_system.get("LastBootUpTime")),
        "BIOS Version": bios.get("SMBIOSBIOSVersion", "Not reported"),
        "BIOS Manufacturer": bios.get("Manufacturer", "Not reported"),
        "BIOS Release Date": format_wmi_datetime(bios.get("ReleaseDate")),
        "Device Serial Number": bios.get("SerialNumber", "Not reported"),
        "GPU": gpu_name_text,
        "GPU Driver Version": primary_gpu.get("DriverVersion", "Not reported") if primary_gpu else "Not reported",
        "GPU Processor": primary_gpu.get("VideoProcessor", "Not reported") if primary_gpu else "Not reported",
        "GPU Memory": format_bytes_to_gb(primary_gpu.get("AdapterRAM")) if primary_gpu else "Not reported",
    }

    return info


def build_metrics(document):
    system_info = extract_system_info(document)
    battery_info = extract_installed_battery_info(document)
    capacity_history = extract_capacity_history(document)
    life_estimate = extract_life_estimate(document)

    metrics = {
        "system_info": system_info,
        "battery_present": battery_info.get("battery_present"),
        "battery_name": battery_info.get("NAME", "Not reported"),
        "manufacturer": battery_info.get("MANUFACTURER", "Not reported"),
        "serial_number": battery_info.get("SERIAL NUMBER", "Not reported"),
        "chemistry": battery_info.get("CHEMISTRY", "Not reported"),
        "cycle_count": battery_info.get("CYCLE COUNT", "Not reported"),
        "report_path": str(REPORT_PATH),
        "report_time": system_info.get("Report Time", "Not reported"),
    }

    design_text = battery_info.get("DESIGN CAPACITY") or capacity_history.get("latest_design_capacity", "Not reported")
    full_text = battery_info.get("FULL CHARGE CAPACITY") or capacity_history.get("latest_full_charge_capacity", "Not reported")

    design_capacity = parse_capacity(design_text) if isinstance(design_text, str) else None
    full_charge_capacity = parse_capacity(full_text) if isinstance(full_text, str) else None
    original_capacity_baseline = capacity_history.get("baseline_capacity_value") or design_capacity

    metrics["design_capacity"] = design_capacity
    metrics["full_charge_capacity"] = full_charge_capacity
    metrics["design_capacity_text"] = design_text
    metrics["full_charge_capacity_text"] = full_text
    metrics["current_full_charge_capacity"] = full_charge_capacity
    metrics["current_full_charge_capacity_text"] = full_text
    metrics["original_capacity_baseline"] = original_capacity_baseline
    metrics["original_capacity_text"] = (
        capacity_history.get("baseline_capacity")
        or (f"{original_capacity_baseline:,} mWh" if original_capacity_baseline else "Not reported")
    )
    metrics["baseline_period"] = capacity_history.get("baseline_period", "Baseline period not found")
    metrics["capacity_period"] = capacity_history.get("latest_period", "Latest period not found")
    metrics["life_estimate"] = life_estimate

    if metrics["battery_present"] is False:
        metrics["health_percent"] = None
        metrics["status_title"] = "Battery Missing"
        metrics["status_text"] = "Windows reports that no battery is currently installed on this system."
        metrics["status_color"] = BAD
        metrics["charge_text"] = "No battery found, so no charge capacity can be calculated."
        metrics["wear_percent"] = None
        return metrics

    if original_capacity_baseline and full_charge_capacity:
        health_percent = (full_charge_capacity / original_capacity_baseline) * 100
        wear_percent = 100 - health_percent
    else:
        health_percent = None
        wear_percent = None

    metrics["health_percent"] = health_percent
    metrics["wear_percent"] = wear_percent

    if original_capacity_baseline and full_charge_capacity and full_charge_capacity > original_capacity_baseline:
        metrics["status_title"] = "Invalid Battery Data"
        metrics["status_text"] = "The current full charge capacity is greater than the original baseline derived from the report."
        metrics["status_color"] = WARN
        metrics["charge_text"] = "The report data appears inconsistent, so the real charge percentage should be verified."
    elif original_capacity_baseline and full_charge_capacity and full_charge_capacity == original_capacity_baseline:
        metrics["status_title"] = "Battery Healthy"
        metrics["status_text"] = "The battery can still charge to 100% of the original baseline capacity seen in the report history."
        metrics["status_color"] = GOOD
        metrics["charge_text"] = "Your battery is healthy and can still be charged to 100% of its original observed capacity."
    elif health_percent is not None:
        summary, color = assessment_from_percent(health_percent)
        metrics["status_title"] = "Battery Wear Detected"
        metrics["status_text"] = summary
        metrics["status_color"] = color
        metrics["charge_text"] = (
            f"Your battery can currently charge to {health_percent:.2f}% of its original observed capacity "
            f"({metrics['original_capacity_text']}, baseline period: {metrics['baseline_period']})."
        )
    else:
        metrics["status_title"] = "Battery Detected"
        metrics["status_text"] = "A battery was detected, but the report did not include enough data to calculate health."
        metrics["status_color"] = WARN
        metrics["charge_text"] = "Battery capacity values are missing, so no charge percentage could be calculated."

    observed_runtime = life_estimate.get("observed_at_full_charge")
    design_runtime = life_estimate.get("observed_at_design_capacity")
    if observed_runtime and design_runtime and observed_runtime != "-" and design_runtime != "-":
        observed_hours = parse_duration_to_hours(observed_runtime)
        design_hours = parse_duration_to_hours(design_runtime)
        if observed_hours and design_hours:
            metrics["runtime_ratio"] = (observed_hours / design_hours) * 100
        else:
            metrics["runtime_ratio"] = None
    else:
        metrics["runtime_ratio"] = None

    return metrics


class MetricCard(tk.Frame):
    def __init__(self, master, title, value, subtitle="", value_color=TEXT):
        super().__init__(master, bg=CARD, highlightthickness=0, bd=0)
        tk.Label(self, text=title, bg=CARD, fg=MUTED, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Label(self, text=value, bg=CARD, fg=value_color, font=("Segoe UI Semibold", 22)).pack(anchor="w", pady=(8, 2))
        tk.Label(
            self,
            text=subtitle,
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 10),
            wraplength=220,
            justify="left",
        ).pack(anchor="w")


class InfoPair(tk.Frame):
    def __init__(self, master, label, value, accent_color=ACCENT):
        super().__init__(master, bg=CARD, highlightthickness=0, bd=0)
        tk.Label(self, text=label, bg=CARD, fg=MUTED, font=("Segoe UI Semibold", 9)).pack(anchor="w")
        tk.Label(
            self,
            text=value,
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 11),
            wraplength=280,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        tk.Frame(self, bg=accent_color, height=2).pack(fill="x", pady=(10, 0))


class BatteryHealthApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x760")
        self.root.minsize(980, 680)
        self.root.configure(bg=BG)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Initializing...")
        self.subtitle_var = tk.StringVar(value="Generating battery report with administrator privileges.")
        self.health_chip_var = tk.StringVar(value="Health --")
        self.capacity_chip_var = tk.StringVar(value="Capacity --")
        self.runtime_chip_var = tk.StringVar(value="Runtime --")
        self.report_data = None
        self.current_view = "dashboard"
        self.system_info_cards = []
        self.system_battery_cards = []
        self.tools_menu_visible = False

        self._build_styles()
        self._build_layout()
        self.refresh_report()

    def _build_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))

    def _build_layout(self):
        outer = ttk.Frame(self.root, style="App.TFrame", padding=24)
        outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = tk.Frame(outer, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.columnconfigure(0, weight=1)

        tk.Label(
            header,
            text="Battery Health Dashboard",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 28),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            textvariable=self.subtitle_var,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 11),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        button_bar = tk.Frame(header, bg=BG)
        button_bar.grid(row=0, column=1, rowspan=2, sticky="e")

        self.refresh_button = tk.Button(
            button_bar,
            text="Refresh Report",
            command=self.refresh_report,
            bg=ACCENT,
            fg="#082f49",
            activebackground="#7dd3fc",
            activeforeground="#082f49",
            relief="flat",
            padx=16,
            pady=10,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.refresh_button.pack(side="left", padx=(0, 10))

        self.open_report_button = tk.Button(
            button_bar,
            text="Open Full Report",
            command=self.open_report_in_browser,
            bg=CARD_ALT,
            fg=TEXT,
            activebackground="#475569",
            activeforeground=TEXT,
            relief="flat",
            padx=16,
            pady=10,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.open_report_button.pack(side="left")

        self.tools_button = tk.Button(
            button_bar,
            text="Tools",
            command=self.toggle_tools_menu,
            bg=CARD_ALT,
            fg=TEXT,
            activebackground="#475569",
            activeforeground=TEXT,
            relief="flat",
            padx=16,
            pady=10,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.tools_button.pack(side="left", padx=(10, 0))

        self.tools_dropdown = tk.Frame(header, bg=SURFACE, padx=12, pady=12)
        self.tools_dropdown.grid(row=2, column=1, sticky="e", pady=(12, 0))
        self.tools_dropdown.grid_remove()

        tk.Label(
            self.tools_dropdown,
            text="Maintenance Tools",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        tk.Label(
            self.tools_dropdown,
            text="Run repair, view hardware details, or open GitHub.",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            wraplength=240,
        ).pack(anchor="w", pady=(4, 10))

        self.run_scan_button = tk.Button(
            self.tools_dropdown,
            text="Run Corruption Scan",
            command=self.run_corruption_scan,
            bg=ACCENT,
            fg="#082f49",
            activebackground="#7dd3fc",
            activeforeground="#082f49",
            relief="flat",
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.run_scan_button.pack(anchor="w", fill="x")

        self.tools_system_info_button = tk.Button(
            self.tools_dropdown,
            text="System Info",
            command=self.open_system_info,
            bg=CARD_ALT,
            fg=TEXT,
            activebackground="#475569",
            activeforeground=TEXT,
            relief="flat",
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.tools_system_info_button.pack(anchor="w", fill="x", pady=(8, 0))

        self.github_button = tk.Button(
            self.tools_dropdown,
            text="Open GitHub",
            command=self.open_github_profile,
            bg=CARD_ALT,
            fg=TEXT,
            activebackground="#475569",
            activeforeground=TEXT,
            relief="flat",
            padx=14,
            pady=9,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.github_button.pack(anchor="w", fill="x", pady=(8, 0))

        self.content_host = tk.Frame(outer, bg=BG)
        self.content_host.grid(row=1, column=0, sticky="nsew")
        self.content_host.columnconfigure(0, weight=1)
        self.content_host.rowconfigure(0, weight=1)

        self.dashboard_view = tk.Frame(self.content_host, bg=BG)
        self.dashboard_view.grid(row=0, column=0, sticky="nsew")
        self.dashboard_view.columnconfigure(0, weight=3)
        self.dashboard_view.columnconfigure(1, weight=2)
        self.dashboard_view.rowconfigure(1, weight=1)

        hero = tk.Frame(self.dashboard_view, bg=SURFACE, padx=24, pady=22)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        hero.columnconfigure(0, weight=1)

        self.status_pill = tk.Label(
            hero,
            textvariable=self.status_var,
            bg=ACCENT,
            fg="#082f49",
            font=("Segoe UI Semibold", 10),
            padx=12,
            pady=6,
        )
        self.status_pill.grid(row=0, column=0, sticky="w")

        self.interpretation_label = tk.Label(
            hero,
            text="Waiting for report...",
            bg=SURFACE,
            fg=TEXT,
            justify="left",
            wraplength=900,
            font=("Segoe UI", 14),
        )
        self.interpretation_label.grid(row=1, column=0, sticky="w", pady=(16, 0))

        self.metrics_frame = tk.Frame(self.dashboard_view, bg=BG)
        self.metrics_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        for index in range(2):
            self.metrics_frame.columnconfigure(index, weight=1)

        self.metric_cards = []
        for row in range(2):
            for column in range(2):
                card = MetricCard(self.metrics_frame, "Loading", "--", "Collecting metrics...")
                card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6, ipadx=10, ipady=10)
                self.metric_cards.append(card)

        side_panel = tk.Frame(self.dashboard_view, bg=SURFACE, padx=22, pady=22)
        side_panel.grid(row=1, column=1, sticky="nsew")
        side_panel.columnconfigure(0, weight=1)
        side_panel.rowconfigure(2, weight=1)

        tk.Label(
            side_panel,
            text="Battery Details",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 18),
        ).grid(row=0, column=0, sticky="w")

        self.detail_text = tk.Text(
            side_panel,
            bg=SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            font=("Segoe UI", 10),
            highlightthickness=0,
            bd=0,
            padx=0,
            pady=0,
        )
        self.detail_text.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        self.detail_text.configure(state="disabled")

        self.system_view = tk.Frame(self.content_host, bg=BG)
        self.system_view.grid(row=0, column=0, sticky="nsew")
        self.system_view.columnconfigure(0, weight=1)
        self.system_view.rowconfigure(2, weight=1)

        system_header = tk.Frame(self.system_view, bg=SURFACE, padx=20, pady=18)
        system_header.grid(row=0, column=0, sticky="ew")
        system_header.columnconfigure(0, weight=1)

        self.system_title = tk.Label(
            system_header,
            text="System Information",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 22),
        )
        self.system_title.grid(row=0, column=0, sticky="w")
        self.system_subtitle = tk.Label(
            system_header,
            text="Hardware and battery details inside the same app window.",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 10),
        )
        self.system_subtitle.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.home_button = tk.Button(
            system_header,
            text="Home",
            command=self.close_system_info,
            bg=ACCENT,
            fg="#082f49",
            activebackground="#7dd3fc",
            activeforeground="#082f49",
            relief="flat",
            padx=16,
            pady=10,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        )
        self.home_button.grid(row=0, column=1, rowspan=2, sticky="e")

        self.system_canvas_holder = tk.Frame(self.system_view, bg=BG)
        self.system_canvas_holder.grid(row=1, column=0, rowspan=2, sticky="nsew", pady=(18, 0))
        self.system_canvas_holder.columnconfigure(0, weight=1)
        self.system_canvas_holder.rowconfigure(0, weight=1)

        self.system_canvas = tk.Canvas(
            self.system_canvas_holder,
            bg=BG,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        self.system_canvas.grid(row=0, column=0, sticky="nsew")

        self.system_scrollbar = tk.Scrollbar(
            self.system_canvas_holder,
            orient="vertical",
            command=self.system_canvas.yview,
        )
        self.system_scrollbar.grid(row=0, column=1, sticky="ns")
        self.system_canvas.configure(yscrollcommand=self.system_scrollbar.set)

        self.system_scroll_content = tk.Frame(self.system_canvas, bg=BG)
        self.system_canvas_window = self.system_canvas.create_window(
            (0, 0),
            window=self.system_scroll_content,
            anchor="nw",
        )
        self.system_scroll_content.bind(
            "<Configure>",
            lambda event: self.system_canvas.configure(scrollregion=self.system_canvas.bbox("all")),
        )
        self.system_canvas.bind(
            "<Configure>",
            lambda event: self.system_canvas.itemconfigure(self.system_canvas_window, width=event.width),
        )

        self.system_summary = tk.Frame(self.system_scroll_content, bg=BG)
        self.system_summary.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.system_scroll_content.columnconfigure(0, weight=1)
        for index in range(3):
            self.system_summary.columnconfigure(index, weight=1)

        self.system_body = tk.Frame(self.system_scroll_content, bg=BG)
        self.system_body.grid(row=1, column=0, sticky="nsew")
        self.system_body.columnconfigure(0, weight=3)
        self.system_body.columnconfigure(1, weight=2)
        self.system_body.rowconfigure(0, weight=1)

        self.system_left = tk.Frame(self.system_body, bg=SURFACE, padx=18, pady=18)
        self.system_left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.system_left.columnconfigure(0, weight=1)
        self.system_left.columnconfigure(1, weight=1)

        self.system_right = tk.Frame(self.system_body, bg=SURFACE, padx=18, pady=18)
        self.system_right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.system_right.columnconfigure(0, weight=1)

        tk.Label(
            self.system_left,
            text="Device",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            self.system_right,
            text="Battery",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=0, sticky="w")

        self.system_note = tk.Label(
            self.system_right,
            text="Health uses the latest full charge capacity against the best historical baseline.",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 10),
            wraplength=290,
            justify="left",
        )
        self.system_note.grid(row=1, column=0, sticky="w", pady=(8, 14))

        self._bind_system_scroll()
        self.show_dashboard_view()

    def show_dashboard_view(self):
        self.current_view = "dashboard"
        self.dashboard_view.tkraise()
        self.refresh_button.configure(state="normal")
        self.open_report_button.configure(state="normal")
        self.tools_button.configure(state="normal")
        self.run_scan_button.configure(state="normal")
        self.tools_system_info_button.configure(state="normal")
        self.github_button.configure(state="normal")

    def show_system_view(self):
        self.current_view = "system_info"
        self.system_view.tkraise()
        self.refresh_button.configure(state="disabled")
        self.open_report_button.configure(state="disabled")
        self.tools_button.configure(state="disabled")
        self.github_button.configure(state="normal")
        self.hide_tools_menu()
        self.system_canvas.yview_moveto(0)

    def _bind_system_scroll(self):
        def on_mousewheel(event):
            if self.current_view == "system_info":
                self.system_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.system_canvas.bind_all("<MouseWheel>", on_mousewheel)

    def _clear_frame_children(self, frame, keep=0):
        children = frame.winfo_children()
        for child in children[keep:]:
            child.destroy()

    def toggle_tools_menu(self):
        if self.tools_menu_visible:
            self.hide_tools_menu()
        else:
            self.tools_dropdown.grid()
            self.tools_menu_visible = True

    def hide_tools_menu(self):
        self.tools_dropdown.grid_remove()
        self.tools_menu_visible = False

    def open_github_profile(self):
        self.hide_tools_menu()
        webbrowser.open("https://github.com/Faishal-Monir")

    def _build_corruption_scan_script(self):
        script_path = Path(tempfile.gettempdir()) / SCAN_BATCH_NAME
        script_body = "\n".join(
            [
                "@echo off",
                "title Battery Health Corruption Scan",
                "echo Running CHKDSK (read-only mode)...",
                "chkdsk",
                "echo.",
                "echo Checking Windows Image Health...",
                "DISM /Online /Cleanup-Image /CheckHealth",
                "echo.",
                "echo Scanning Windows Image Health...",
                "DISM /Online /Cleanup-Image /ScanHealth",
                "echo.",
                "echo Restoring Windows Image Health...",
                "DISM /Online /Cleanup-Image /RestoreHealth",
                "echo.",
                "echo Running System File Checker...",
                "sfc /scannow",
                "echo.",
                "echo All operations completed.",
                "pause",
            ]
        )
        script_path.write_text(script_body, encoding="utf-8")
        return script_path

    def run_corruption_scan(self):
        if not is_windows_admin():
            messagebox.showerror(
                APP_TITLE,
                "This tool needs administrator privileges. Please restart the app as administrator.",
            )
            return

        should_run = messagebox.askyesno(
            APP_TITLE,
            "Run CHKDSK, DISM, and SFC now in an elevated Command Prompt window? This can take a while.",
        )
        if not should_run:
            return

        self.hide_tools_menu()
        try:
            script_path = self._build_corruption_scan_script()
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                "cmd.exe",
                f'/k "{script_path}"',
                None,
                1,
            )
            if result <= 32:
                raise RuntimeError("The elevated Command Prompt could not be opened.")
        except Exception as error:
            messagebox.showerror(APP_TITLE, f"Could not start corruption scan.\n\n{error}")

    def _render_system_info_view(self, info, battery_data):
        self._clear_frame_children(self.system_summary)
        self._clear_frame_children(self.system_left, keep=1)
        self._clear_frame_children(self.system_right, keep=2)

        health_text = (
            f"{battery_data['health_percent']:.2f}%"
            if battery_data.get("health_percent") is not None
            else "Unknown"
        )
        wear_text = (
            f"{battery_data['wear_percent']:.2f}%"
            if battery_data.get("wear_percent") is not None
            else "Unknown"
        )
        health_color = battery_data.get("status_color", ACCENT)

        summary_cards = [
            ("Health", health_text, "Current vs original baseline", health_color),
            ("Full Charge", battery_data.get("current_full_charge_capacity_text", "Not reported"), "Latest measured", TEXT),
            ("Baseline", battery_data.get("original_capacity_text", "Not reported"), "Best history record", TEXT),
        ]
        for index, (title, value, subtitle, color) in enumerate(summary_cards):
            card = MetricCard(self.system_summary, title, value, subtitle, value_color=color)
            card.grid(row=0, column=index, sticky="nsew", padx=6)

        device_fields = [
            ("Computer Name", info.get("Computer Name", "Not reported")),
            ("Manufacturer", info.get("Manufacturer", "Not reported")),
            ("Model", info.get("Model", "Not reported")),
            ("System Type", info.get("System Type", "Not reported")),
            ("GPU", info.get("GPU", "GPU not installed or not reported by Windows")),
            ("GPU Driver", info.get("GPU Driver Version", "Not reported")),
            ("GPU Processor", info.get("GPU Processor", "Not reported")),
            ("GPU Memory", info.get("GPU Memory", "Not reported")),
            ("Processor", info.get("Processor", "Not reported")),
            ("Installed RAM", info.get("Installed RAM", "Not reported")),
            ("CPU Cores", str(info.get("CPU Cores", "Not reported"))),
            ("Logical Processors", str(info.get("Logical Processors", "Not reported"))),
            ("OS", info.get("OS", "Not reported")),
            ("Build", info.get("Build Number", "Not reported")),
            ("Last Boot", info.get("Last Boot Time", "Not reported")),
            ("BIOS", info.get("BIOS Version", "Not reported")),
        ]
        for index, (label, value) in enumerate(device_fields):
            card = InfoPair(self.system_left, label, value)
            card.grid(row=1 + (index // 2), column=index % 2, sticky="nsew", padx=6, pady=6)

        battery_fields = [
            ("Current full charge", battery_data.get("current_full_charge_capacity_text", "Not reported"), ACCENT),
            ("Reported design capacity", battery_data.get("design_capacity_text", "Not reported"), TEXT),
            ("Original baseline", battery_data.get("original_capacity_text", "Not reported"), GOOD),
            ("Baseline period", battery_data.get("baseline_period", "Not reported"), TEXT),
            ("Battery wear", wear_text, WARN if wear_text != "Unknown" else TEXT),
            ("Cycle count", str(battery_data.get("cycle_count", "Not reported")), TEXT),
        ]
        for index, (label, value, color) in enumerate(battery_fields):
            card = InfoPair(self.system_right, label, value, accent_color=color)
            card.grid(row=2 + index, column=0, sticky="ew", pady=6)

    def refresh_report(self):
        self.refresh_button.configure(state="disabled")
        self.status_var.set("Refreshing report")
        self.subtitle_var.set("Running powercfg and reading the latest battery-report.html from Downloads.")
        self.root.update_idletasks()

        try:
            generate_battery_report()
            document = REPORT_PATH.read_text(encoding="utf-8", errors="ignore")
            self.report_data = build_metrics(document)
            self.render_report(self.report_data)
        except Exception as error:
            self.report_data = None
            self.status_var.set("Report failed")
            self.status_pill.configure(bg=BAD, fg="white")
            self.subtitle_var.set("The battery report could not be generated or parsed.")
            self.interpretation_label.configure(text=str(error))
            self._set_detail_text(f"Error:\n{error}")
            messagebox.showerror(APP_TITLE, str(error))
        finally:
            self.refresh_button.configure(state="normal")

    def open_report_in_browser(self):
        if REPORT_PATH.exists():
            webbrowser.open(REPORT_PATH.as_uri())
        else:
            messagebox.showwarning(APP_TITLE, "Battery report not found in Downloads.")

    def open_system_info(self):
        info = collect_live_system_info()
        battery_data = self.report_data or {}
        self._render_system_info_view(info, battery_data)
        self.show_system_view()

    def close_system_info(self):
        self.show_dashboard_view()

    def render_report(self, data):
        status_color = data.get("status_color", ACCENT)
        self.status_var.set(data.get("status_title", "Battery Status"))
        self.status_pill.configure(bg=status_color, fg="white" if status_color != ACCENT else "#082f49")
        self.subtitle_var.set(f"Report generated at {data.get('report_time', 'unknown time')} and saved to {REPORT_PATH}")
        runtime_ratio = data.get("runtime_ratio")

        interpretation_parts = [data.get("status_text", "No interpretation available.")]
        charge_text = data.get("charge_text")
        if charge_text:
            interpretation_parts.append(charge_text)
        if runtime_ratio is not None:
            interpretation_parts.append(
                f"Observed battery runtime is about {runtime_ratio:.2f}% of the estimated runtime at original design capacity."
            )
        self.interpretation_label.configure(text=" ".join(interpretation_parts))

        cards = [
            (
                "Current Charge Health",
                "Missing" if data.get("battery_present") is False else (
                    f"{data['health_percent']:.2f}%"
                    if data.get("health_percent") is not None
                    else "Unknown"
                ),
                "How much the battery can actually charge compared with its original design.",
                status_color,
            ),
            (
                "Full Charge Capacity",
                data.get("current_full_charge_capacity_text", "Not reported"),
                "Latest capacity Windows reported after a complete charge.",
                TEXT,
            ),
            (
                "Design Capacity",
                data.get("design_capacity_text", "Not reported"),
                "Original factory battery capacity from the report.",
                TEXT,
            ),
            (
                "Original Baseline",
                data.get("original_capacity_text", "Not reported"),
                f"Used for health calculation from report history. Period: {data.get('baseline_period', 'Not reported')}",
                TEXT,
            ),
        ]

        for card, values in zip(self.metric_cards, cards):
            card.destroy()

        parent = self.metric_cards[0].master
        self.metric_cards = []
        for index, (title, value, subtitle, value_color) in enumerate(cards):
            card = MetricCard(parent, title, value, subtitle, value_color=value_color)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=6, pady=6, ipadx=10, ipady=10)
            self.metric_cards.append(card)

        details = [
            f"Battery present: {'Yes' if data.get('battery_present') else 'No' if data.get('battery_present') is False else 'Unknown'}",
            f"Battery name: {data.get('battery_name', 'Not reported')}",
            f"Manufacturer: {data.get('manufacturer', 'Not reported')}",
            f"Serial number: {data.get('serial_number', 'Not reported')}",
            f"Chemistry: {data.get('chemistry', 'Not reported')}",
            f"Cycle count: {data.get('cycle_count', 'Not reported')}",
            f"Latest history period found: {data.get('capacity_period', 'Not reported')}",
            f"Original baseline capacity: {data.get('original_capacity_text', 'Not reported')}",
            f"Baseline period used for health: {data.get('baseline_period', 'Not reported')}",
            f"Saved report path: {data.get('report_path', str(REPORT_PATH))}",
        ]

        system_info = data.get("system_info", {})
        if system_info:
            details.append("")
            details.append("System info")
            details.append("Open 'Tools > System Info' to view live Windows hardware and OS details inside the app.")
            for key in ("Computer Name", "System Product Name", "Bios", "Os Build", "Platform Role", "Connected Standby"):
                if key in system_info:
                    details.append(f"{key}: {system_info[key]}")

        life_estimate = data.get("life_estimate", {})
        if life_estimate:
            details.append("")
            details.append("Battery life estimates")
            details.append(f"Observed at full charge: {life_estimate.get('observed_at_full_charge', 'Not reported')}")
            details.append(f"Observed at design capacity: {life_estimate.get('observed_at_design_capacity', 'Not reported')}")
            standby = life_estimate.get("observed_connected_standby")
            if standby and standby != "-":
                details.append(f"Connected standby at full charge: {standby}")

        wear = data.get("wear_percent")
        if wear is not None:
            details.append("")
            details.append(f"Battery wear: {wear:.2f}%")

        self._set_detail_text("\n".join(details))

    def _set_detail_text(self, text):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")


def main():
    relaunch_as_admin()
    root = tk.Tk()
    app = BatteryHealthApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
