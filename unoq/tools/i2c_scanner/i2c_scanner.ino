// I2C scanner for the UNO Q: scans BOTH I2C buses (Wire and Wire1) to find which one the
// header SDA/SCL pins use. Grove LCD = 0x3E (text) + 0x62 or 0x30 (backlight).
// If this fails to compile with "'Wire1' was not declared", delete the three Wire1 lines.
// Output: App Lab serial monitor (via Monitor).
#include <Arduino_RouterBridge.h>
#include <Wire.h>

void scan(TwoWire &bus, const char *name) {
  int found = 0;
  Monitor.print("Scanning "); Monitor.print(name); Monitor.println("...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    bus.beginTransmission(addr);
    if (bus.endTransmission() == 0) {
      Monitor.print("  "); Monitor.print(name); Monitor.print(" found 0x");
      if (addr < 16) Monitor.print("0");
      Monitor.println(String(addr, HEX));
      found++;
    }
  }
  if (found == 0) { Monitor.print("  "); Monitor.print(name); Monitor.println(": nothing found"); }
}

void setup() {
  Bridge.begin();
  Monitor.begin();
  Wire.begin();
  Wire1.begin();
}

void loop() {
  scan(Wire, "Wire");
  scan(Wire1, "Wire1");
  delay(3000);
}
