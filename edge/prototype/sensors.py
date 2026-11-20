"""Low-level drivers: MPU6050, ADS1115, DS18B20 / MLX90614."""
import glob
import threading
import time

I2C_LOCK = threading.Lock()   # several threads share the I2C bus


def _s16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v & 0x8000 else v


class MPU6050:
    def __init__(self, bus, addr=0x68):
        self.bus, self.addr = bus, addr
        with I2C_LOCK:
            bus.write_byte_data(addr, 0x6B, 0x00)  # wake up
            bus.write_byte_data(addr, 0x1A, 0x03)  # low-pass ~44 Hz
            bus.write_byte_data(addr, 0x1B, 0x00)  # gyro ±250 deg/s
            bus.write_byte_data(addr, 0x1C, 0x00)  # accel ±2 g
        self.gbias = (0.0, 0.0, 0.0)
        time.sleep(0.1)

    def read(self):
        """Returns ax, ay, az (g) and gx, gy, gz (deg/s, bias removed)."""
        with I2C_LOCK:
            d = self.bus.read_i2c_block_data(self.addr, 0x3B, 14)
        ax, ay, az = (_s16(d[i], d[i + 1]) / 16384.0 for i in (0, 2, 4))
        gx, gy, gz = (_s16(d[i], d[i + 1]) / 131.0 for i in (8, 10, 12))
        bx, by, bz = self.gbias
        return ax, ay, az, gx - bx, gy - by, gz - bz

    def calibrate_gyro(self, n=200):
        """Keep the band still while this runs (~1 s)."""
        self.gbias = (0.0, 0.0, 0.0)
        sx = sy = sz = 0.0
        for _ in range(n):
            _, _, _, gx, gy, gz = self.read()
            sx, sy, sz = sx + gx, sy + gy, sz + gz
            time.sleep(0.005)
        self.gbias = (sx / n, sy / n, sz / n)


class ADS1115:
    """16-bit ADC. Continuous mode, 860 samples/s, ±4.096 V range."""
    MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}

    def __init__(self, bus, addr=0x48):
        self.bus, self.addr, self.channel = bus, addr, None
        self.select(0)

    def select(self, ch):
        if ch == self.channel:
            return
        cfg = self.MUX[ch] | 0x0200 | 0x00E0 | 0x0003
        with I2C_LOCK:
            self.bus.write_i2c_block_data(self.addr, 0x01, [cfg >> 8, cfg & 0xFF])
        self.channel = ch
        time.sleep(0.003)  # first conversion on the new channel

    def volts(self, ch):
        self.select(ch)
        with I2C_LOCK:
            d = self.bus.read_i2c_block_data(self.addr, 0x00, 2)
        return _s16(d[0], d[1]) * 4.096 / 32768.0


class TempSensor:
    def __init__(self, kind, bus=None, mlx_addr=0x5A, offset=0.0):
        self.kind, self.bus, self.addr, self.offset = kind, bus, mlx_addr, offset

    def read(self):
        try:
            if self.kind == "ds18b20":
                files = glob.glob("/sys/bus/w1/devices/28-*/w1_slave")
                if not files:
                    return None
                with open(files[0]) as f:
                    lines = f.read().splitlines()
                if not lines or not lines[0].endswith("YES"):
                    return None
                t = int(lines[1].split("t=")[1]) / 1000.0
            elif self.kind == "mlx90614":
                with I2C_LOCK:
                    raw = self.bus.read_word_data(self.addr, 0x07)
                t = raw * 0.02 - 273.15
            else:
                return None
            return round(t + self.offset, 2)
        except Exception as e:
            print("Temp read error:", e)
            return None
