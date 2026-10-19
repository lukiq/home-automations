# Stairs checks

Configurations: `esp-home/esp32-stairs-up.yaml` and `esp-home/esp32-stairs-down.yaml`.

## Local regression scenarios

Use Python 3.9+ with PyYAML (`python3 -m pip install PyYAML`):

```sh
python3 -m unittest discover -s tests -v
esphome config esp-home/esp32-stairs-up.yaml
esphome compile esp-home/esp32-stairs-up.yaml
```

The tests load and execute the actual YAML actions with virtual time. They check both animation directions, all 16 steps and 268 pixels, renewed motion, opposing sensor releases, held sensors, configured delays, manual mode and explicit stop via the existing direction switches. Eight of the eleven tests fail against the previous configuration.

This is a limited action interpreter, not ESPHome itself. It does not model GPIO electrical levels/debounce, LED transitions, network connectivity, boot/persistence or LED driver timing. Compile and simulator/hardware checks complement it.

## Online simulator

Project: https://wokwi.com/projects/478178301624611841

Build a firmware image without using the real `secrets.yaml`:

```sh
python3 tests/wokwi/build.py
```

Open the project, select `diagram.json` and use `tests/wokwi/diagram.json` if restoring the diagram. In the code editor press F1, select **Upload Firmware and Start Simulation…**, and choose:

`.context/wokwi/.esphome/build/esp32-stairs-up-test/.pioenvs/esp32-stairs-up-test/firmware.factory.bin`

The Arduino sketch in the web project is only an upload placeholder; clicking the normal compile button builds that placeholder rather than ESPHome. Reloading may require uploading the firmware again.

The 16 rings represent the 16 step partitions, in row-major order. They contain the same 268 LEDs. Three slide switches substitute for the debounced physical inputs: `first` / GPIO13 (active low), `last` / GPIO34 (active high), `manual` / GPIO23. Change a switch position to trigger it; initial active levels at boot are not equivalent to a fresh motion edge. GPIO23 intentionally retains the existing both-edge toggle behavior.

Wokwi models WS2812. The simulator-only build therefore uses WS2812B/GRB instead of the production WS2811/BRG. Automation logic, sensor pins/polarity, LED count, partitions and delays are unchanged. It also uses dummy secrets, a separate hostname, and disables network/API reboot timeouts. Never upload production firmware containing private credentials to a public simulator.

### Acceptance scenarios

1. Trigger each end separately: all 16 steps light in order, in opposite directions.
2. Keep one sensor active: all steps remain lit beyond the configured hold time.
3. Clear both sensors: hold starts after animation/last release, then all LEDs go dark.
4. Trigger again near the old shutdown deadline: lights stay on for a new full hold after release, without restarting the animation.
5. Trigger and release the opposite sensor during an animation: no premature off or competing animation.
6. Enable manual mode during an animation: sequence stops, all LEDs stay on beyond the old timeout, and motion is ignored.
7. Leave manual mode: lights go off if idle; an active sensor starts the appropriate sequence.

## Physical acceptance before relying on it

Repeat the scenarios on the stairs while watching ESPHome logs. Verify actual sensor polarity, short/long interruptions, the GPIO23 wall-switch type, colors and LED timing. Include two people entering from opposite ends, slow passage, restart, loss of Wi-Fi and HA control. The generic simulator cannot prove WS2811 signal compatibility, power delivery, wiring or PIR/laser behavior.

## Recorded run (2026-10-18)

ESPHome 2025.5.2: validation and production firmware compilation passed; all eleven local scenarios passed. In Wokwi, rendered LED colors confirmed forward and reverse animation, all 268 LEDs lit, held-sensor retention, a second passage extending the original deadline, eventual shutdown, manual interruption/retention beyond 20 seconds, and return to automation with/without active motion. An opposing sensor press/release during forward animation did not decrease the number of lit LEDs. Simulator-only WS2812B/GRB adaptation applies.

The production configuration was then installed by the user on the physical ESP32 using ESPHome 2026.9.1. Both directions and sensors worked. Repeated sensor activity, a descent and a return ascent kept the lights on; all steps switched off after the final sensor release (03:49:35.585 → 03:49:54.631 in received logs, approximately 19 seconds). Physical manual-mode, network-loss and restart scenarios were not tested.

## Downstairs stairs

Project: https://wokwi.com/projects/478182788262582273

```sh
python3 tests/wokwi/build.py down
```

Upload `.context/wokwi-down/.esphome/build/esp32-stairs-down-test/.pioenvs/esp32-stairs-down-test/firmware.factory.bin` with F1 → **Upload Firmware and Start Simulation…**. A convenient copy of the current build is `/Users/lukiq/Downloads/schody-dol-wokwi.bin`. The normal green Start button builds the Arduino placeholder instead.

Restore the diagram from `tests/wokwi/diagram-down.json` if needed. Its 18 rings represent all 321 LEDs, in row-major order. Slide switches replace the first sensor (GPIO18, active low), last sensor (GPIO19, active high) and manual wall switch (GPIO23, both-edge toggle). The same simulator-only WS2812B/GRB adaptation and dummy credentials apply. Use the acceptance scenarios above with 18 steps.

Local checks cover eleven motion/manual/stop scenarios per device. `test_stairs_effect.py` also compiles the actual downstairs `my effect` lambda with checked LED ranges and undefined-behavior/bounds sanitizers, and runs repeated complete cycles and resets. It checks indexing and progression, not the visual appearance or LED driver timing.

Downstairs validation (2026-10-18): all 23 local tests passed; production and simulator firmware compiled with ESPHome 2025.5.2. Online acceptance is still pending: Wokwi reported an IoT Gateway connection failure during the initial run. The user subsequently installed the configuration on the physical ESP32; device logs identify ESPHome 2026.9.1. Received logs show the upper sensor starting steps 18→1 over 4.225 seconds, renewed motion without restarting the sequence, and shutdown about 20.2 seconds after the last sensor release. The reported appearance of several initial steps lighting together remains unresolved; state logs do not prove the physical LED output. Physical manual-mode and effect appearance checks remain pending.
