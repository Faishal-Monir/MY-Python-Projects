import ctypes
import html
import re
import subprocess
import sys
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


def is_windows_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if sys.platform != "win32" or is_windows_admin():
        return

    params = subprocess.list2cmdline(sys.argv)
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1,
    )
    if result <= 32:
        raise RuntimeError("Administrator permission is required to generate the battery report.")
    sys.exit(0)


def generate_battery_report():
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    latest = None
    for row in rows[1:]:
        if len(row) >= 3 and row[1] != "-" and row[2] != "-":
            latest = row
    if not latest:
        return {}
    return {
        "period": latest[0],
        "full_charge_capacity": latest[1],
        "design_capacity": latest[2],
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

    design_text = battery_info.get("DESIGN CAPACITY") or capacity_history.get("design_capacity", "Not reported")
    full_text = battery_info.get("FULL CHARGE CAPACITY") or capacity_history.get("full_charge_capacity", "Not reported")

    design_capacity = parse_capacity(design_text) if isinstance(design_text, str) else None
    full_charge_capacity = parse_capacity(full_text) if isinstance(full_text, str) else None

    metrics["design_capacity"] = design_capacity
    metrics["full_charge_capacity"] = full_charge_capacity
    metrics["design_capacity_text"] = design_text
    metrics["full_charge_capacity_text"] = full_text
    metrics["capacity_period"] = capacity_history.get("period", "Latest period not found")
    metrics["life_estimate"] = life_estimate

    if metrics["battery_present"] is False:
        metrics["health_percent"] = None
        metrics["status_title"] = "Battery Missing"
        metrics["status_text"] = "Windows reports that no battery is currently installed on this system."
        metrics["status_color"] = BAD
        metrics["charge_text"] = "No battery found, so no charge capacity can be calculated."
        metrics["wear_percent"] = None
        return metrics

    if design_capacity and full_charge_capacity:
        health_percent = (full_charge_capacity / design_capacity) * 100
        wear_percent = 100 - health_percent
    else:
        health_percent = None
        wear_percent = None

    metrics["health_percent"] = health_percent
    metrics["wear_percent"] = wear_percent

    if design_capacity and full_charge_capacity and full_charge_capacity > design_capacity:
        metrics["status_title"] = "Invalid Battery Data"
        metrics["status_text"] = "The full charge capacity is greater than the design capacity in the report."
        metrics["status_color"] = WARN
        metrics["charge_text"] = "The report data appears inconsistent, so the real charge percentage should be verified."
    elif design_capacity and full_charge_capacity and full_charge_capacity == design_capacity:
        metrics["status_title"] = "Battery Healthy"
        metrics["status_text"] = "The battery can still charge to 100% of its original design capacity."
        metrics["status_color"] = GOOD
        metrics["charge_text"] = "Your battery is healthy and can still be charged to 100% of its original capacity."
    elif health_percent is not None:
        summary, color = assessment_from_percent(health_percent)
        metrics["status_title"] = "Battery Wear Detected"
        metrics["status_text"] = summary
        metrics["status_color"] = color
        metrics["charge_text"] = f"Your battery can currently charge to {health_percent:.2f}% of its original design capacity."
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
        tk.Label(self, text=subtitle, bg=CARD, fg=MUTED, font=("Segoe UI", 10), wraplength=220, justify="left").pack(anchor="w")


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
        self.report_data = None

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

        self.system_info_button = tk.Button(
            button_bar,
            text="Open System Info",
            command=self.open_system_info,
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
        self.system_info_button.pack(side="left", padx=(10, 0))

        content = tk.Frame(outer, bg=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(1, weight=1)

        hero = tk.Frame(content, bg=SURFACE, padx=24, pady=22)
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

        metrics_frame = tk.Frame(content, bg=BG)
        metrics_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        for index in range(2):
            metrics_frame.columnconfigure(index, weight=1)

        self.metric_cards = []
        for row in range(2):
            for column in range(2):
                card = MetricCard(metrics_frame, "Loading", "--", "Collecting metrics...")
                card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6, ipadx=10, ipady=10)
                self.metric_cards.append(card)

        side_panel = tk.Frame(content, bg=SURFACE, padx=22, pady=22)
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
        try:
            subprocess.Popen(["msinfo32"])
        except FileNotFoundError:
            messagebox.showerror(APP_TITLE, "Windows System Information (msinfo32) was not found.")
        except Exception as error:
            messagebox.showerror(APP_TITLE, f"Could not open System Information.\n\n{error}")

    def render_report(self, data):
        status_color = data.get("status_color", ACCENT)
        self.status_var.set(data.get("status_title", "Battery Status"))
        self.status_pill.configure(bg=status_color, fg="white" if status_color != ACCENT else "#082f49")
        self.subtitle_var.set(f"Report generated at {data.get('report_time', 'unknown time')} and saved to {REPORT_PATH}")

        interpretation_parts = [data.get("status_text", "No interpretation available.")]
        charge_text = data.get("charge_text")
        if charge_text:
            interpretation_parts.append(charge_text)
        runtime_ratio = data.get("runtime_ratio")
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
                data.get("full_charge_capacity_text", "Not reported"),
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
                "Cycle Count",
                str(data.get("cycle_count", "Not reported")),
                "Charge cycles may be missing on some laptop models.",
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
            f"Capacity history period used: {data.get('capacity_period', 'Not reported')}",
            f"Saved report path: {data.get('report_path', str(REPORT_PATH))}",
        ]

        system_info = data.get("system_info", {})
        if system_info:
            details.append("")
            details.append("System info")
            details.append("Open the 'System Info' button for full CPU, RAM, GPU, motherboard, storage, and other Windows hardware details.")
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
