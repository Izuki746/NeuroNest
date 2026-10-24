# NeuroNest: Uno Q (Person B)

| File | Runs on | Purpose |
|---|---|---|
| `sketch/sketch.ino` | Uno Q microcontroller | LED brain map, Grove LCD, servo arm, offline demo. Exposes Bridge functions. |
| `python/main.py` | Uno Q Linux side | Polls `GET <SERVER>/api/device` 10×/s and forwards to the sketch. Falls back to the offline demo if the server is unreachable. |
| `tools/fake_device_server.py` | Any laptop | Fake `/api/device` server for testing before C's server exists. |
| `tools/i2c_scanner/i2c_scanner.ino` | Uno Q microcontroller | Finds I2C devices (Grove LCD = 0x3E + 0x62/0x30). |

## Bridge functions (sketch side; internal to B)
| Name | Args | Notes |
|---|---|---|
| `ping` | none | returns `"pong"` |
| `setLeds` | `String` (104 chars '0'..'9') | mapped to 3-bit grayscale 0..7 |
| `setLcd` | `String` `"line1|line2|r|g|b"` | one string to keep the RPC simple; only redraws on change |
| `setServo` | `int` 0..90 | smoothed in `loop()` (≤3° per 20 ms) |
| `demo` | `int` 1/0 | offline animation on/off |

## Libraries
- Built in: `Arduino_RouterBridge`, `Arduino_LED_Matrix`, `Wire`.
- Install in App Lab's library manager if available: **Grove - LCD RGB Backlight** (`rgb_lcd.h`) and **Servo**.
  The sketch compiles without them (LCD disabled / software servo pulses) and prints a note to the Monitor.

## Wiring (unplug USB first)
```
Uno Q 5V  → breadboard + rail      Uno Q GND → breadboard − rail
Grove LCD: black → −   red → +   white → SDA   yellow → SCL
Servo:     brown → −   red → +   orange → D9
```
