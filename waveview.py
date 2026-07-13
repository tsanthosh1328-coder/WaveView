import sys
import serial
import threading
import queue
import numpy as np
import collections
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore

# ── Configuration ─────────────────────────────────────────────
PORT           = "/dev/ttyUSB0"
BAUD           = 115200
SAMPLE_RATE    = 10000
BUFFER_SIZE    = 256
VREF           = 3.3
ADC_MAX        = 4095

HISTORY_SIZE   = SAMPLE_RATE * 5
SYNC_A = 0xAA
SYNC_B = 0x55

# ── Shared queue ───────────────────────────────────────────────
data_queue = queue.Queue(maxsize=20)

# ── Packet reader ──────────────────────────────────────────────
def read_packet(ser):
    while True:
        b = ser.read(1)
        if not b:
            return None
        if b[0] == SYNC_A:
            b2 = ser.read(1)
            if not b2:
                return None
            if b2[0] == SYNC_B:
                break
            if b2[0] == SYNC_A:
                b3 = ser.read(1)
                if b3 and b3[0] == SYNC_B:
                    break

    count_bytes = ser.read(2)
    if len(count_bytes) < 2:
        return None
    count = (count_bytes[0] << 8) | count_bytes[1]

    raw = ser.read(count * 2)
    if len(raw) < count * 2:
        return None

    chk_byte = ser.read(1)
    if not chk_byte:
        return None

    computed = 0
    for byte in raw:
        computed ^= byte
    if computed != chk_byte[0]:
        return None

    samples = []
    for i in range(count):
        hi = raw[i * 2]
        lo = raw[i * 2 + 1]
        samples.append((hi << 8) | lo)

    return samples

def serial_thread_func():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=2)
        import time
        time.sleep(1)
        ser.reset_input_buffer()

        while True:
            samples = read_packet(ser)
            if samples:
                voltage = [(s / ADC_MAX) * VREF for s in samples]
                if not data_queue.full():
                    data_queue.put(voltage)

    except serial.SerialException as e:
        print(f"Serial error: {e}")

# ── Measurement panel widget ───────────────────────────────────
class MeasurementPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background: #0a0a0a;")
        self.setFixedHeight(90)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(0)

        self.value_labels = {}
        self.title_labels = {}

        measurements = [
            ("Vpp",    "--"),
            ("Vmax",   "--"),
            ("Vmin",   "--"),
            ("Freq",   "--"),
            ("Period", "--"),
            ("Duty",   "--"),
        ]

        for name, default in measurements:
            box = QtWidgets.QWidget()
            box.setStyleSheet(
                "background: #111111; "
                "border: 1px solid #333333; "
                "border-radius: 4px; "
                "margin: 2px;"
            )
            box_layout = QtWidgets.QVBoxLayout(box)
            box_layout.setContentsMargins(12, 4, 12, 4)
            box_layout.setSpacing(2)

            title = QtWidgets.QLabel(name)
            title.setStyleSheet(
                "color: #aaaaaa; font-size: 15px; "
                "font-weight: bold; border: none;"
            )
            title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            value = QtWidgets.QLabel(default)
            value.setStyleSheet(
                "color: #00ff88; font-size: 22px; "
                "font-family: monospace; font-weight: bold; border: none;"
            )
            value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            box_layout.addWidget(title)
            box_layout.addWidget(value)

            layout.addWidget(box)
            self.value_labels[name] = value
            self.title_labels[name] = title

    def update(self, vpp, vmax, vmin, freq, period, duty):
        self.value_labels["Vpp"].setText(vpp)
        self.value_labels["Vmax"].setText(vmax)
        self.value_labels["Vmin"].setText(vmin)
        self.value_labels["Freq"].setText(freq)
        self.value_labels["Period"].setText(period)
        self.value_labels["Duty"].setText(duty)

    def set_cursor_mode(self, active):
        # Change titles and colors for cursor-derived boxes
        if active:
            self.title_labels["Freq"].setText("1/ΔT")
            self.title_labels["Period"].setText("ΔT")
            self.title_labels["Duty"].setText("ΔV")
            color = "#00ccff"
        else:
            self.title_labels["Freq"].setText("Freq")
            self.title_labels["Period"].setText("Period")
            self.title_labels["Duty"].setText("Duty")
            color = "#00ff88"

        for name in ("Freq", "Period", "Duty"):
            self.value_labels[name].setStyleSheet(
                f"color: {color}; font-size: 22px; "
                f"font-family: monospace; font-weight: bold; border: none;"
            )

# ── Oscilloscope GUI ───────────────────────────────────────────
class Oscilloscope(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WaveView")
        self.resize(1100, 700)

        # ── History buffer ─────────────────────────────────────
        self.history = collections.deque(maxlen=HISTORY_SIZE)

        # ── View state ─────────────────────────────────────────
        self.paused          = False
        self._view_dirty     = False
        self.scroll_pos      = 0
        self.zoom_levels     = [128, 256, 512, 1024, 2048, 4096, 8192]
        self.zoom_index      = 3
        self.paused_snapshot = None

        # ── Cursor state ───────────────────────────────────────
        self.cursor_mode     = False
        self.cursor_selected = 1
        self.cursor1_pos     = 0.25
        self.cursor2_pos     = 0.75
        self.cursor_step     = 0.01

        # ── PyQtGraph ──────────────────────────────────────────
        pg.setConfigOption("background", "#1a1a1a")
        pg.setConfigOption("foreground", "#cccccc")

        central = QtWidgets.QWidget()
        central.setStyleSheet("background: #141414;")
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # ── Measurement panel (top) ────────────────────────────
        self.meas_panel = MeasurementPanel()
        layout.addWidget(self.meas_panel)

        # ── Plot ───────────────────────────────────────────────
        self.plot_widget = pg.PlotWidget()
        layout.addWidget(self.plot_widget, stretch=3)
        self.plot_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setFocus()

        self.plot_widget.setLabel("left",   "Voltage", units="V")
        self.plot_widget.setLabel("bottom", "Time",    units="ms")
        self.plot_widget.setYRange(-0.1, VREF + 0.3, padding=0)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setMouseEnabled(x=False, y=False)

        self.curve = self.plot_widget.plot(
            [], [],
            pen=pg.mkPen(color=(0, 255, 136), width=1)
        )

        # ── Cursor lines ───────────────────────────────────────
        self.cursor1_line = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(color=(0, 200, 255), width=1,
                         style=QtCore.Qt.PenStyle.DashLine),
            label="C1",
            labelOpts={"color": (0, 200, 255), "position": 0.95}
        )
        self.cursor2_line = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(color=(255, 220, 0), width=1,
                         style=QtCore.Qt.PenStyle.DashLine),
            label="C2",
            labelOpts={"color": (255, 220, 0), "position": 0.90}
        )
        self.cursor1_line.setVisible(False)
        self.cursor2_line.setVisible(False)
        self.plot_widget.addItem(self.cursor1_line)
        self.plot_widget.addItem(self.cursor2_line)

        # ── Status bar ─────────────────────────────────────────
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet(
            "color: #cccccc; font-size: 15px; "
            "padding: 6px 10px; background: #0a0a0a; "
            "border-top: 1px solid #333333;"
        )
        self.status.setFixedHeight(36)
        layout.addWidget(self.status)
        self.update_status()

        # ── Window cache for cursor lookup ─────────────────────
        self.current_window    = None
        self.current_time_axis = None

        # ── QTimer ─────────────────────────────────────────────
        self.qtimer = QtCore.QTimer()
        self.qtimer.timeout.connect(self.update_plot)
        self.qtimer.start(33)

    # ── Get display window ─────────────────────────────────────
    def get_window(self):
        view_size = self.zoom_levels[self.zoom_index]
        source    = self.paused_snapshot if self.paused else list(self.history)

        if len(source) == 0:
            return np.zeros(view_size)

        if self.paused:
            if self.scroll_pos == 0:
                window = source[-view_size:]
            else:
                end   = len(source) - self.scroll_pos
                start = max(end - view_size, 0)
                window = source[start:end]
            arr = np.array(window, dtype=float)
            if len(arr) < view_size:
                pad = np.full(view_size - len(arr), arr[0] if len(arr) > 0 else 0.0)
                arr = np.concatenate([pad, arr])
            return arr

        buf = list(self.history)
        if len(buf) >= view_size:
            return np.array(buf[-view_size:], dtype=float)
        return np.array(buf, dtype=float)

    # ── Auto measurements ──────────────────────────────────────
    def compute_measurements(self, window):
        if len(window) == 0:
            return "N/A", "N/A", "N/A"

        vmin     = float(np.min(window))
        vmax     = float(np.max(window))
        midpoint = (vmin + vmax) / 2

        if vmax - vmin < 0.2:
            return "N/A", "N/A", "N/A"

        view_size  = self.zoom_levels[self.zoom_index]
        duration_s = view_size / SAMPLE_RATE
        dt_ms      = 1000.0 / SAMPLE_RATE

        crossings = []
        for i in range(1, len(window)):
            if window[i - 1] < midpoint <= window[i]:
                crossings.append((i, 'R'))
            elif window[i - 1] > midpoint >= window[i]:
                crossings.append((i, 'F'))

        rising = [idx for idx, typ in crossings if typ == 'R']

        freq_str = period_str = duty_str = "N/A"

        if len(rising) >= 2:
            freq       = len(rising) / duration_s
            period_ms  = (rising[-1] - rising[0]) / (len(rising) - 1) * dt_ms
            freq_str   = f"{freq:.1f} Hz"
            period_str = f"{period_ms:.2f} ms"

        for i in range(len(crossings) - 2):
            if crossings[i][1] == 'R' and crossings[i+1][1] == 'F' and crossings[i+2][1] == 'R':
                high     = crossings[i + 1][0] - crossings[i][0]
                total    = crossings[i + 2][0] - crossings[i][0]
                duty_str = f"{(high / total) * 100:.1f}%"
                break

        return freq_str, period_str, duty_str

    # ── Cursor measurements ────────────────────────────────────
    def compute_cursor_measurements(self):
        if self.current_window is None or self.current_time_axis is None:
            return "N/A", "N/A", "N/A"

        t_total = self.current_time_axis[-1] - self.current_time_axis[0]
        t1      = self.current_time_axis[0] + self.cursor1_pos * t_total
        t2      = self.current_time_axis[0] + self.cursor2_pos * t_total
        dt_ms   = abs(t2 - t1)

        n  = len(self.current_window)
        i1 = max(0, min(int(self.cursor1_pos * (n - 1)), n - 1))
        i2 = max(0, min(int(self.cursor2_pos * (n - 1)), n - 1))
        v1 = self.current_window[i1]
        v2 = self.current_window[i2]
        dv = abs(v2 - v1)

        freq     = (1.0 / (dt_ms / 1000.0)) if dt_ms > 0 else 0
        inv_dt   = f"{freq:.1f} Hz"
        delta_t  = f"{dt_ms:.2f} ms"
        delta_v  = f"{dv:.3f} V"

        return inv_dt, delta_t, delta_v

    # ── Update cursor line positions ───────────────────────────
    def update_cursors(self):
        if self.current_time_axis is None:
            return
        t_total = self.current_time_axis[-1] - self.current_time_axis[0]
        t_start = self.current_time_axis[0]
        self.cursor1_line.setValue(t_start + self.cursor1_pos * t_total)
        self.cursor2_line.setValue(t_start + self.cursor2_pos * t_total)

    # ── Status bar ─────────────────────────────────────────────
    def update_status(self):
        view_size = self.zoom_levels[self.zoom_index]
        window_ms = (view_size / SAMPLE_RATE) * 1000
        scroll_ms = (self.scroll_pos / SAMPLE_RATE) * 1000
        pause_str = "⏸ PAUSED" if self.paused else "▶ LIVE"

        if self.cursor_mode:
            cur_str = (
                f"M: exit cursors    |    "
                f"1/2: select cursor    |    "
                f"← →: move cursor    |    "
                f"Ctrl+← →: scroll    |    "
                f"Active: {'C1 (cyan)' if self.cursor_selected == 1 else 'C2 (yellow)'}"
            )
        else:
            cur_str = (
                f"Space: pause/resume    |    "
                f"← →: scroll    |    "
                f"+  -: zoom    |    "
                f"M: cursors    |    "
                f"Window: {window_ms:.0f}ms    |    "
                f"Scrolled: {scroll_ms:.0f}ms"
            )

        self.status.setText(f"{pause_str}    |    {cur_str}")

    # ── Keyboard ───────────────────────────────────────────────
    def keyPressEvent(self, event):
        key         = event.key()
        modifiers   = event.modifiers()
        view_size   = self.zoom_levels[self.zoom_index]
        scroll_step = max(view_size // 4, 64)

        if key == QtCore.Qt.Key.Key_M:
            self.cursor_mode = not self.cursor_mode
            self.cursor1_line.setVisible(self.cursor_mode)
            self.cursor2_line.setVisible(self.cursor_mode)
            self.meas_panel.set_cursor_mode(self.cursor_mode)
            if self.cursor_mode and not self.paused:
                self.paused          = True
                self.paused_snapshot = list(self.history)
                self.scroll_pos      = 0
            self._view_dirty = True
            self.update_status()
            return

        if self.cursor_mode:
            if key == QtCore.Qt.Key.Key_1:
                self.cursor_selected = 1
                self.update_status()
                return
            if key == QtCore.Qt.Key.Key_2:
                self.cursor_selected = 2
                self.update_status()
                return

        if key == QtCore.Qt.Key.Key_Left:
            if self.cursor_mode and not (modifiers & QtCore.Qt.KeyboardModifier.ControlModifier):
                if self.cursor_selected == 1:
                    self.cursor1_pos = max(0.0, self.cursor1_pos - self.cursor_step)
                else:
                    self.cursor2_pos = max(0.0, self.cursor2_pos - self.cursor_step)
                self.update_cursors()
                self._view_dirty = True
                self.update_status()
                return
            if self.paused and self.paused_snapshot:
                max_scroll      = len(self.paused_snapshot) - view_size
                self.scroll_pos = min(self.scroll_pos + scroll_step, max(max_scroll, 0))

        elif key == QtCore.Qt.Key.Key_Right:
            if self.cursor_mode and not (modifiers & QtCore.Qt.KeyboardModifier.ControlModifier):
                if self.cursor_selected == 1:
                    self.cursor1_pos = min(1.0, self.cursor1_pos + self.cursor_step)
                else:
                    self.cursor2_pos = min(1.0, self.cursor2_pos + self.cursor_step)
                self.update_cursors()
                self._view_dirty = True
                self.update_status()
                return
            if self.paused:
                self.scroll_pos = max(self.scroll_pos - scroll_step, 0)

        elif key == QtCore.Qt.Key.Key_Space:
            self.paused = not self.paused
            if self.paused:
                self.paused_snapshot = list(self.history)
                self.scroll_pos      = 0
            else:
                self.paused_snapshot = None
                self.scroll_pos      = 0
                if self.cursor_mode:
                    self.cursor_mode = False
                    self.cursor1_line.setVisible(False)
                    self.cursor2_line.setVisible(False)
                    self.meas_panel.set_cursor_mode(False)

        elif key in (QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Equal):
            self.zoom_index = max(self.zoom_index - 1, 0)

        elif key in (QtCore.Qt.Key.Key_Minus, QtCore.Qt.Key.Key_Underscore):
            self.zoom_index = min(self.zoom_index + 1, len(self.zoom_levels) - 1)

        self._view_dirty = True
        self.update_status()

    # ── Plot update ────────────────────────────────────────────
    def update_plot(self):
        new_samples = []
        while not data_queue.empty():
            try:
                new_samples.extend(data_queue.get_nowait())
            except queue.Empty:
                break

        if new_samples:
            self.history.extend(new_samples)

        if len(self.history) == 0:
            return

        if self.paused and not self._view_dirty:
            return

        self._view_dirty = False

        window    = self.get_window()
        view_size = self.zoom_levels[self.zoom_index]
        time_axis = np.linspace(0, (view_size / SAMPLE_RATE) * 1000, len(window))

        self.current_window    = window
        self.current_time_axis = time_axis

        self.curve.setData(time_axis, window)
        self.plot_widget.setXRange(0, (view_size / SAMPLE_RATE) * 1000, padding=0)

        vpp  = float(np.max(window) - np.min(window))
        vmax = float(np.max(window))
        vmin = float(np.min(window))

        if self.cursor_mode:
            self.update_cursors()
            freq_str, period_str, duty_str = self.compute_cursor_measurements()
        else:
            freq_str, period_str, duty_str = self.compute_measurements(window)

        self.meas_panel.update(
            f"{vpp:.3f}V",
            f"{vmax:.3f}V",
            f"{vmin:.3f}V",
            freq_str,
            period_str,
            duty_str
        )

        self.update_status()

# ── Entry point ───────────────────────────────────────────────
def main():
    t = threading.Thread(target=serial_thread_func, daemon=True)
    t.start()

    app = QtWidgets.QApplication(sys.argv)
    window = Oscilloscope()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
