// I2C scanner for the UNO Q: finds the Grove LCD (0x3E + 0x62 or 0x30) and an I2C misc LCD (0x27/0x3F).
// Output appears in the App Lab serial monitor via Monitor.
#include <Arduino_RouterBridge.h>
#include <Wire.h>

void setup() {
  Bridge.begin();
  Monitor.begin();
  Wire.begin();
}

void loop() {
  int found = 0;
  Monitor.println("Scanning I2C...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Monitor.print("  found 0x");
      if (addr < 16) Monitor.print("0");
      Monitor.println(String(addr, HEX));
      found++;
    }
  }
  if (found == 0) Monitor.println("  nothing found: check SDA/SCL, 5V, GND");
  delay(3000);
}
