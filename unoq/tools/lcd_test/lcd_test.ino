// Grove LCD RGB V4 test with our OWN driver (no library): text chip at 0x3E, backlight at 0x62.
// Slow, safe HD44780-style init + prints each I2C result code to the serial monitor (0 = OK).
// Expected: "NEURONEST" / "LCD TEST n" and the backlight cycling red -> green -> white.
#include <Arduino_RouterBridge.h>
#include <Wire.h>

const uint8_t LCD_ADDR = 0x3E, RGB_ADDR = 0x62;
int lastErr = 0;

void lcdCmd(uint8_t c) {
  Wire.beginTransmission(LCD_ADDR);
  Wire.write(0x80);            // control byte: command
  Wire.write(c);
  lastErr = Wire.endTransmission();
  delay(2);                    // most commands need ~40 us, clear/home need ~1.6 ms
}

void lcdData(uint8_t d) {
  Wire.beginTransmission(LCD_ADDR);
  Wire.write(0x40);            // control byte: data
  Wire.write(d);
  lastErr = Wire.endTransmission();
  delayMicroseconds(200);
}

void lcdPrint(const char *s) { while (*s) lcdData((uint8_t)*s++); }
void lcdSetCursor(uint8_t col, uint8_t row) { lcdCmd(0x80 | (col + (row ? 0x40 : 0x00))); }

void rgbReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(RGB_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

void rgb(uint8_t r, uint8_t g, uint8_t b) { rgbReg(0x04, r); rgbReg(0x03, g); rgbReg(0x02, b); }

void lcdInit() {
  delay(100);                  // power-on settle
  lcdCmd(0x28); delay(5);      // function set: 2 lines, 5x8 font
  lcdCmd(0x28); delay(1);
  lcdCmd(0x28);
  lcdCmd(0x0C);                // display ON, cursor off, blink off
  lcdCmd(0x01); delay(3);      // clear
  lcdCmd(0x06);                // entry mode: left to right
  Monitor.print("LCD init last I2C result: "); Monitor.println(lastErr);
  rgbReg(0x00, 0x00);          // MODE1: normal
  rgbReg(0x01, 0x00);          // MODE2
  rgbReg(0x08, 0xAA);          // LEDOUT: all channels PWM-controlled
}

int n = 0;

void setup() {
  Bridge.begin();
  Monitor.begin();
  Wire.begin();
  lcdInit();
  rgb(255, 0, 0);
  lcdSetCursor(0, 0);
  lcdPrint("NEURONEST");
  Monitor.print("print result: "); Monitor.println(lastErr);
}

void loop() {
  const uint8_t cols[3][3] = {{255, 0, 0}, {0, 255, 0}, {255, 255, 255}};
  rgb(cols[n % 3][0], cols[n % 3][1], cols[n % 3][2]);
  char buf[17];
  snprintf(buf, sizeof(buf), "LCD TEST %d     ", n);
  lcdSetCursor(0, 1);
  lcdPrint(buf);
  Monitor.print("tick "); Monitor.print(n); Monitor.print(" i2c="); Monitor.println(lastErr);
  n++;
  delay(2000);
}
