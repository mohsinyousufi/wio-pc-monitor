#include <Arduino.h>
#include <TFT_eSPI.h>

TFT_eSPI tft = TFT_eSPI();

// UI constants
const int SCREEN_W = 320;
const int SCREEN_H = 240;
const int PADDING = 8;

struct Metrics {
  float cpu = 0.0f;
  float tempC = -1.0f; // -1 => N/A
  float ram = 0.0f;
  float gpu = -1.0f;   // -1 => N/A
  float gpuTempC = 0.0f;
};

Metrics current;
String lineBuf;
String lastLineShown;
bool receivedOnce = false;
unsigned long lastRxMillis = 0;

// Layout constants
const int LABEL_W = 70;
const int BAR_H = 22;

// Precomputed Y positions
int Y_CPU = 0;
int Y_TEMP = 0;
int Y_RAM = 0;
int Y_GPU = 0;
int Y_GPUTEMP = 0;

// Last drawn bar widths (pixels)
int lastCpuW = -1;
int lastRamW = -1;
int lastGpuW = -1;

static inline void barGeom(int y, int &barX, int &barW) {
  int x = PADDING;
  barX = x + LABEL_W + 6;
  barW = SCREEN_W - barX - PADDING;
}

void drawStaticLabelsAndSlots() {
  // Labels
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.setCursor(PADDING, Y_CPU);
  tft.print("CPU:");
  tft.setCursor(PADDING, Y_TEMP);
  tft.print("TEMP:");
  tft.setCursor(PADDING, Y_RAM);
  tft.print("RAM:");
  tft.setCursor(PADDING, Y_GPU);
  tft.print("GPU:");
  tft.setCursor(PADDING, Y_GPUTEMP);
  tft.print("G-TEMP:");

  // Bar backgrounds
  int bx, bw;
  barGeom(Y_CPU, bx, bw);
  tft.fillRect(bx, Y_CPU, bw, BAR_H, TFT_DARKGREY);
  barGeom(Y_RAM, bx, bw);
  tft.fillRect(bx, Y_RAM, bw, BAR_H, TFT_DARKGREY);
  barGeom(Y_GPU, bx, bw);
  tft.fillRect(bx, Y_GPU, bw, BAR_H, TFT_DARKGREY);
}

void updateBarFill(int y, float value, uint16_t color, int &lastWRef) {
  int bx, bw; barGeom(y, bx, bw);
  float v = value; if (v < 0) v = 0; if (v > 100) v = 100;
  int newW = (int)(bw * (v / 100.0f));
  if (lastWRef < 0) lastWRef = 0; // initial
  if (newW == lastWRef) return;
  if (newW > lastWRef) {
    // Grow: fill the added segment
    tft.fillRect(bx + lastWRef, y, newW - lastWRef, BAR_H, color);
  } else {
    // Shrink: erase trailing segment to background (slot color)
    tft.fillRect(bx + newW, y, lastWRef - newW, BAR_H, TFT_DARKGREY);
  }
  lastWRef = newW;

  // Draw value text at right with padding to erase previous text cleanly
  char buf[16];
  snprintf(buf, sizeof(buf), "%.0f%%", value);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextDatum(MR_DATUM);
  tft.setTextPadding(44);
  tft.drawString(buf, SCREEN_W - PADDING, y + BAR_H/2);
  tft.setTextDatum(TL_DATUM);
}

void drawHeader() {
  // Draw static header only once or on demand (leave background intact elsewhere)
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.setCursor(PADDING, PADDING);
  tft.println("Wio PC Monitor");
  tft.drawLine(PADDING, PADDING + 20, SCREEN_W - PADDING, PADDING + 20, TFT_DARKGREY);
}

void drawTemp(int y, float tempC) {
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextDatum(MR_DATUM);
  if (tempC < 0) {
    tft.setTextPadding(64);
    tft.drawString("N/A", SCREEN_W - PADDING, y);
  } else {
    char buf[24];
    snprintf(buf, sizeof(buf), "%.1f C", tempC);
    tft.setTextPadding(64);
    tft.drawString(buf, SCREEN_W - PADDING, y);
  }
  tft.setTextDatum(TL_DATUM);
}

// Cached last drawn values to avoid full redraw flicker
struct LastDrawn {
  int cpu = -1000;
  int ram = -1000;
  int gpu = -1000;
  int tempC = -1000;     // tenths to avoid float compares
  int gpuTempC = -1000;
} lastDrawn;

void drawStaticLayoutOnce() {
  tft.fillScreen(TFT_BLACK);
  drawHeader();
  // Compute Y layout
  Y_CPU = PADDING + 28;
  Y_TEMP = Y_CPU + 32;
  Y_RAM = Y_TEMP + 28;
  Y_GPU = Y_RAM + 32;
  Y_GPUTEMP = Y_GPU + 32;
  // Draw labels and bar slots once
  drawStaticLabelsAndSlots();
}

void drawStatus() {
  int y = SCREEN_H - 28;
  tft.fillRect(PADDING, y - 4, SCREEN_W - 2 * PADDING, 28, TFT_BLACK);
  bool fresh = (millis() - lastRxMillis) < 1500;
  uint16_t dot = fresh ? TFT_GREEN : TFT_RED;
  tft.fillCircle(PADDING + 6, y + 6, 5, dot);
  tft.setCursor(PADDING + 18, y);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(1);
  if (receivedOnce) tft.print(fresh ? "RX: fresh" : "RX: stale");
  else tft.print("Waiting for data...");
}

void updateBarsAndTemps(const Metrics &m) {
  tft.setTextSize(2);
  // Bars: update only deltas
  updateBarFill(Y_CPU, m.cpu, TFT_GREEN, lastCpuW);
  // Temps: update only when changed, with padding
  int tempTenths = (m.tempC < 0) ? -1 : (int)(m.tempC * 10);
  if (tempTenths != lastDrawn.tempC) {
    drawTemp(Y_TEMP, m.tempC);
    lastDrawn.tempC = tempTenths;
  }
  updateBarFill(Y_RAM, m.ram, TFT_CYAN, lastRamW);
  int gpuInt = (m.gpu < 0) ? -1 : (int)(m.gpu + 0.5f);
  if (gpuInt != lastDrawn.gpu) {
    updateBarFill(Y_GPU, m.gpu < 0 ? 0.0f : m.gpu, TFT_ORANGE, lastGpuW);
    lastDrawn.gpu = gpuInt;
  }
  int gpuTempTenths = (m.gpuTempC < 0) ? -1 : (int)(m.gpuTempC * 10);
  if (gpuTempTenths != lastDrawn.gpuTempC) {
    // Draw only value, label is static
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextDatum(MR_DATUM);
    tft.setTextPadding(64);
    if (m.gpuTempC < 0) tft.drawString("N/A", SCREEN_W - PADDING, Y_GPUTEMP);
    else {
      char buf[24];
      snprintf(buf, sizeof(buf), "%.1f C", m.gpuTempC);
      tft.drawString(buf, SCREEN_W - PADDING, Y_GPUTEMP);
    }
    tft.setTextDatum(TL_DATUM);
    lastDrawn.gpuTempC = gpuTempTenths;
  }
}

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(3); // landscape
  tft.fillScreen(TFT_BLACK);
  tft.setTextSize(2);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextDatum(TL_DATUM);
  tft.setSwapBytes(true);

  drawStaticLayoutOnce();
  //tft.drawCentreString("Waiting for data...", SCREEN_W/2, SCREEN_H/2 - 8, 2);
  // Flush any stale serial input
  delay(10);
  while (Serial.available()) (void)Serial.read();
}

bool parseLine(const String &line, Metrics &out) {
  // Expect: CPU,TEMP,RAM,GPU,GPUTEMP\n
  // Split by commas
  int idx1 = line.indexOf(',');
  int idx2 = line.indexOf(',', idx1 + 1);
  int idx3 = line.indexOf(',', idx2 + 1);
  int idx4 = line.indexOf(',', idx3 + 1);
  if (idx1 < 0 || idx2 < 0 || idx3 < 0 || idx4 < 0) return false;

  String sCPU = line.substring(0, idx1);
  String sTemp = line.substring(idx1 + 1, idx2);
  String sRAM = line.substring(idx2 + 1, idx3);
  String sGPU = line.substring(idx3 + 1, idx4);
  String sGPUTemp = line.substring(idx4 + 1);

  out.cpu = sCPU.toFloat();
  out.tempC = sTemp.toFloat();
  out.ram = sRAM.toFloat();
  out.gpu = sGPU.toFloat();
  out.gpuTempC = sGPUTemp.toFloat();
  return true;
}

unsigned long lastRender = 0;

void loop() {
  // Read incoming serial line
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      String trimmed = lineBuf;
      trimmed.trim();
      Metrics m;
      if (parseLine(trimmed, m)) {
        current = m;
        receivedOnce = true;
        lastRxMillis = millis();
        lastLineShown = trimmed;
        updateBarsAndTemps(current);
        drawStatus();
      }
      lineBuf = "";
    } else if (c != '\r') {
      // guard against runaway buffer
      if (lineBuf.length() < 128) lineBuf += c;
      else lineBuf = "";
    }
  }

  // Optionally redraw periodically even without new data
  unsigned long now = millis();
  if (now - lastRender > 1000) {
    drawStatus();
    lastRender = now;
  }
}
