// Minimal Grove LCD RGB (V4: 0x3E text + 0x62 backlight) test for the UNO Q.
// Expected: backlight cycles red -> green -> blue every 2 s, text "NEURONEST" / "LCD TEST n".
#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include <rgb_lcd.h>

rgb_lcd lcd;
int n = 0;

void setup() {
  Bridge.begin();
  Monitor.begin();
  Wire.begin();
  delay(200);                 // give the LCD time after power-up
  lcd.begin(16, 2);
  delay(50);
  lcd.setRGB(255, 0, 0);
  lcd.setCursor(0, 0);
  lcd.print("NEURONEST");
  Monitor.println("LCD test started");
}

void loop() {
  const int cols[3][3] = {{255, 0, 0}, {0, 255, 0}, {0, 0, 255}};
  lcd.setRGB(cols[n % 3][0], cols[n % 3][1], cols[n % 3][2]);
  lcd.setCursor(0, 1);
  lcd.print("LCD TEST ");
  lcd.print(n);
  lcd.print("   ");
  Monitor.print("tick "); Monitor.println(n);
  n++;
  delay(2000);
}
