"""Build an isolated Wokwi image from the production YAML, using no real secrets."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('stairs', choices=['up', 'down'], nargs='?', default='up')
stairs = parser.parse_args().stairs
name = f'esp32-stairs-{stairs}'
TARGET = ROOT / '.context' / ('wokwi' if stairs == 'up' else 'wokwi-down')
TARGET.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'esp-home' / f'{name}.yaml').read_text()
# Wokwi models WS2812, not the real WS2811 strip. Logic, pins and partitions stay identical.
source = source.replace(f'name: "{name}"', f'name: "{name}-test"')
source = source.replace('chipset: WS2811', 'chipset: WS2812B').replace('rgb_order: BRG', 'rgb_order: GRB')
source = source.replace('api:\n', 'api:\n  reboot_timeout: 0s\n')
source = source.replace('wifi:\n', 'wifi:\n  reboot_timeout: 0s\n')
secrets = {
    'wifi_ssid': 'Wokwi-GUEST',
    'wifi_password': '',
    ('api_key_stairs_up' if stairs == 'up' else 'api_encryption_key'): 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
    ('ota_password_stairs_up' if stairs == 'up' else 'ota_password'): 'simulation-only',
    ('ap_password_fallback' if stairs == 'up' else 'wifi_password_fallback'): 'simulation-only',
}
assert set(re.findall(r'!secret (\w+)', source)) == set(secrets), 'Review new secret references before uploading'
(TARGET / 'stairs-test.yaml').write_text(source)
(TARGET / 'secrets.yaml').write_text('\n'.join(f'{k}: {json.dumps(v)}' for k, v in secrets.items()) + '\n')
subprocess.run(['esphome', 'compile', str(TARGET / 'stairs-test.yaml')], check=True)
print(TARGET / f'.esphome/build/{name}-test/.pioenvs/{name}-test/firmware.factory.bin')
