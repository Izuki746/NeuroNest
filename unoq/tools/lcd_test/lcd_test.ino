// Grove LCD RGB text-visibility test (0x3E text, 0x62 backlight), own driver, no library.
// I2C works (all ACKs) but no text → try the ST7032-style init with software CONTRAST,
// sweeping contrast 0..63 slowly. Watch the screen and note the "C=" value when text appears.
// Serial monitor prints the current mode and contrast every step.
#include <Arduino_RouterBridge.h>
#include <Wire.h>

const uint8_t LCD_ADDR = 0x3E, RGB_ADDR = 0x62;

void lcdCmd(uint8_t c) {
  Wire.beginTransmission(LCD_ADDR); Wire.write(0x80); Wire.write(c); Wire.endTransmission();
  delay(2);
}
void lcdData(uint8_t d) {
  Wire.beginTransmission(LCD_ADDR); Wire.write(0x40); Wire.write(d); Wire.endTransmission();
  delayMicroseconds(300);
}
void lcdPrint(const char *s) { while (*s) lcdData((uint8_t)*s++); }
void lcdSetCursor(uint8_t col, uint8_t row) { lcdCmd(0x80 | (col + (row ? 0x40 : 0x00))); }
void rgbReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(RGB_ADDR); Wire.write(reg); Wire.write(val); Wire.endTransmission();
}

// ST7032-compatible init with contrast (harmless on plain HD44780-style chips)
void initWithContrast(uint8_t c) {
  lcdCmd(0x38);                         // function set, 8-bit, 2 lines, IS=0
  lcdCmd(0x39);                         // function set, IS=1 (extended instructions)
  lcdCmd(0x14);                         // internal OSC frequency
  lcdCmd(0x70 | (c & 0x0F));            // contrast low bits
  lcdCmd(0x5C | ((c >> 4) & 0x03));     // ICON on, booster on, contrast high bits
  lcdCmd(0x6C); delay(250);             // follower control
  lcdCmd(0x38);                         // back to IS=0
  lcdCmd(0x0C);                         // display on
  lcdCmd(0x01); delay(3);               // clear
  lcdCmd(0x06);                         // entry mode
}

void setup() {
  Bridge.begin();
  Monitor.begin();
  Wire.begin();
  delay(200);
  rgbReg(0x00, 0x00); rgbReg(0x01, 0x00); rgbReg(0x08, 0xAA);
  rgbReg(0x04, 255); rgbReg(0x03, 255); rgbReg(0x02, 255);   // backlight on (yellow if blue is dead)
  Monitor.println("Contrast sweep starting: watch the LCD");
}

uint8_t c = 0;

void loop() {
  initWithContrast(c);
  char l1[17], l2[17];
  snprintf(l1, sizeof(l1), "NEURONEST  C=%02d", c);
  snprintf(l2, sizeof(l2), "################");
  lcdSetCursor(0, 0); lcdPrint(l1);
  lcdSetCursor(0, 1); lcdPrint(l2);
  Monitor.print("contrast C="); Monitor.println(c);
  c = (c + 4) % 64;                     // 0,4,8,...,60 then repeat
  delay(1500);
}
