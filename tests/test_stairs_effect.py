"""Run the actual downstairs effect lambda with checked pixel ranges in native C++."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_stairs import ConfigLoader, ROOT
import yaml


class DownstairsEffect(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler required for effect regression')
    def test_complete_cycles_use_valid_ranges_and_all_321_leds(self):
        config = yaml.load((ROOT / 'esp-home/esp32-stairs-down.yaml').read_text(), Loader=ConfigLoader)
        effects = config['light'][0]['effects']
        effect = next(e['addressable_lambda'] for e in effects
                      if e.get('addressable_lambda', {}).get('name') == 'my effect')
        # Stubs implement the public operations used by the lambda, with strict bounds.
        harness = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <stdexcept>
#include <vector>
struct Color {
  int r, g, b;
  Color(int r=0, int g=0, int b=0): r(r), g(g), b(b) {}
  static const Color BLACK;
  void fade_to_black(uint8_t amount) {
    r = r * (255-amount) / 255;
    g = g * (255-amount) / 255;
    b = b * (255-amount) / 255;
  }
};
const Color Color::BLACK{};
struct Strip {
  std::vector<Color> pixels = std::vector<Color>(321);
  struct Range {
    Strip &strip;
    size_t start, end;
    void operator=(const Color &c) {
      std::fill(strip.pixels.begin()+start, strip.pixels.begin()+end, c);
    }
    void fade_to_black(uint8_t amount) {
      for (size_t i=start; i<end; i++) strip.pixels.at(i).fade_to_black(amount);
    }
  };
  Range range(size_t start, size_t end) {
    if (start > end || end > pixels.size()) throw std::out_of_range("LED range");
    return {*this, start, end};
  }
  Range all() { return range(0, pixels.size()); }
};
void tick(Strip &it, bool initial_run) {
LAMBDA_BODY
}
int main() {
  Strip strip;
  const int lengths[] = {17,17,17,17,17,18,23,19,16,19,21,17,17,17,17,17,17,18};
  for (int cycle=0; cycle<20; cycle++) {
    size_t end=0;
    for (int step=0; step<18; step++) {
      tick(strip, cycle==0 && step==0);
      end += lengths[step];
      for (size_t i=0; i<strip.pixels.size(); i++)
        assert((strip.pixels[i].r > 0) == (i < end));
    }
    for (int step=0; step<18; step++) tick(strip, false);
    for (const auto &c: strip.pixels) assert(c.r==0 && c.g==0 && c.b==0);
  }
  // Restarting the effect also resets its static indices.
  tick(strip, true);
  tick(strip, true);
  assert(std::count_if(strip.pixels.begin(),strip.pixels.end(),
    [](const Color &c) { return c.r>0; }) == 17);
}
'''.replace('LAMBDA_BODY', effect['lambda'])
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'effect.cpp'
            binary = Path(folder) / 'effect'
            source.write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-fsanitize=undefined,bounds',
                            '-Werror=constant-conversion', str(source), '-o', str(binary)],
                           check=True, capture_output=True, text=True, timeout=20)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=20)
