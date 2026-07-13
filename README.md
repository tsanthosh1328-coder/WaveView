# WaveView

An ESP32-based oscilloscope with a PyQtGraph GUI.

## Features
- Live waveform display at 10kHz sample rate
- 5 second scrollable history
- Pause, zoom, and scroll
- Auto measurements: Vpp, Vmax, Vmin, Frequency, Period, Duty cycle
- Cursor measurements: ΔT, 1/ΔT, ΔV
- Hardware front-end protection circuit (up to 5V input)

## Requirements
- ESP32 DevKit
- Python 3 with pyqtgraph, pyserial, numpy

## Usage
```bash
cd python
python3 oscilloscope.py
```
