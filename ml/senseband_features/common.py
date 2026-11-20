"""Constants shared by every feature function."""
FEATURE_VERSION = 2   # 2: R-peak detector band 8-25 Hz + abs envelope (RR + ECG-quality features changed)

IMU_FS = 50                 # Hz, waist IMU
IMU_WINDOW_S = 2.56         # 128 samples, 50 % overlap (docs/04)
ECG_FS = 250                # Hz, AD8232 via ADS1115
ECG_QUALITY_WINDOW_S = 5.0
RHYTHM_WINDOW_S = 30.0
FALL_WINDOW_S = 4.0         # 2 s before / 2 s after the impact trigger
EPS = 1e-9
